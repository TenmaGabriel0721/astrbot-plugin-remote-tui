from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .permissions import resolve_allowed_cwd
from .tmux_backend import TmuxBackend, TmuxError


@dataclass
class SessionInfo:
    user_key: str
    app: str
    session_name: str
    running: bool


class SessionManager:
    def __init__(self, config: dict, data_dir: Path, backend: TmuxBackend):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.tool_dir = self.data_dir / "bin"
        self.queue_dir = self.data_dir / "send_queue"
        self.backend = backend
        self.session_prefix = "astrbot_tui"
        self.cols = max(40, min(220, int(config.get("terminal_cols", 100) or 100)))
        self.rows = max(10, min(80, int(config.get("terminal_rows", 30) or 30)))
        self.cwd = resolve_allowed_cwd(
            str(config.get("default_cwd", "/root")),
            list(config.get("allowed_cwds", ["/root"]) or ["/root"]),
        )
        self.commands = {
            "codex": self._resolve_command(str(config.get("codex_command", "codex") or "codex")),
            "claude": self._resolve_command(str(config.get("claude_command", "claude") or "claude")),
        }
        self.state: dict[str, Any] = self._load_state()

    def user_key_from_event(self, event) -> str:
        platform = str(event.get_platform_id() or event.get_platform_name() or "platform")
        sender = str(event.get_sender_id() or "unknown")
        return f"{platform}:{sender}"

    def active_app(self, user_key: str) -> str:
        value = self.state.get("active_app", {}).get(user_key, "")
        return value if value in {"codex", "claude"} else ""

    async def start_or_switch(self, user_key: str, app: str) -> SessionInfo:
        if app not in {"codex", "claude"}:
            raise TmuxError("未知会话类型")
        session_name = self.session_name(user_key, app)
        await self.backend.ensure_session(
            session_name,
            app,
            self.commands[app],
            self.cwd,
            self.cols,
            self.rows,
            extra_path_dirs=[self.tool_dir],
            extra_env={
                "REMOTE_TUI_QUEUE_DIR": str(self.queue_dir),
                "REMOTE_TUI_USER_KEY": user_key,
                "REMOTE_TUI_SESSION_NAME": session_name,
                "REMOTE_TUI_APP": app,
            },
        )
        self._set_active(user_key, app)
        return SessionInfo(user_key, app, session_name, True)

    async def current(self, user_key: str) -> SessionInfo | None:
        app = self.active_app(user_key)
        if not app:
            return None
        session_name = self.session_name(user_key, app)
        running = await self.backend.has_session(session_name)
        return SessionInfo(user_key, app, session_name, running)

    async def send_text(self, user_key: str, text: str, submit_delay_seconds: float = 0.2) -> SessionInfo:
        info = await self.require_current(user_key)
        await self.backend.send_text(info.session_name, text, submit=True, submit_delay_seconds=submit_delay_seconds)
        self.touch(user_key, info.app)
        return info

    async def send_key(self, user_key: str, key: str) -> SessionInfo:
        info = await self.require_current(user_key)
        await self.backend.send_key(info.session_name, key)
        self.touch(user_key, info.app)
        return info

    async def stop_current(self, user_key: str) -> SessionInfo | None:
        info = await self.current(user_key)
        if info is None:
            return None
        if info.running:
            await self.backend.stop(info.session_name)
        self.state.get("active_app", {}).pop(user_key, None)
        self._save_state()
        return SessionInfo(info.user_key, info.app, info.session_name, False)

    async def capture_current(self, user_key: str) -> tuple[SessionInfo | None, str]:
        info = await self.current(user_key)
        if info is None:
            return None, ""
        if not info.running:
            return info, ""
        return info, await self.backend.capture(info.session_name, self.cols, self.rows)

    async def require_current(self, user_key: str) -> SessionInfo:
        info = await self.current(user_key)
        if info is None:
            raise TmuxError("还没有启动会话，请先使用 /t codex 或 /t claude")
        if not info.running:
            raise TmuxError("当前会话已退出，请重新启动")
        return info

    def session_name(self, user_key: str, app: str) -> str:
        digest = hashlib.sha1(user_key.encode("utf-8")).hexdigest()[:16]
        return f"{self.session_prefix}_{digest}_{app}"

    def touch(self, user_key: str, app: str) -> None:
        sessions = self.state.setdefault("sessions", {})
        sessions[f"{user_key}:{app}"] = {"last_active": time.time()}
        self._save_state()

    async def cleanup_idle(self, idle_timeout_minutes: int) -> list[str]:
        if idle_timeout_minutes <= 0:
            return []
        now = time.time()
        removed: list[str] = []
        sessions = self.state.setdefault("sessions", {})
        for key, data in list(sessions.items()):
            try:
                user_key, app = key.rsplit(":", 1)
            except ValueError:
                sessions.pop(key, None)
                continue
            last_active = float(data.get("last_active", now))
            if now - last_active < idle_timeout_minutes * 60:
                continue
            session_name = self.session_name(user_key, app)
            await self.backend.stop(session_name)
            sessions.pop(key, None)
            if self.state.get("active_app", {}).get(user_key) == app:
                self.state.get("active_app", {}).pop(user_key, None)
            removed.append(session_name)
        if removed:
            self._save_state()
        return removed

    async def stop_all_plugin_sessions(self) -> None:
        await self.backend.stop_by_prefix(f"{self.session_prefix}_")

    def _set_active(self, user_key: str, app: str) -> None:
        self.state.setdefault("active_app", {})[user_key] = app
        self.touch(user_key, app)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"active_app": {}, "sessions": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"active_app": {}, "sessions": {}}
            data.setdefault("active_app", {})
            data.setdefault("sessions", {})
            return data
        except Exception:
            return {"active_app": {}, "sessions": {}}

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _resolve_command(command: str) -> str:
        parts = shlex.split(command or "")
        if not parts:
            return command
        first = parts[0]
        if "/" not in first:
            resolved = shutil.which(first)
            if resolved:
                parts[0] = resolved
            else:
                fallback = SessionManager._find_known_executable(first)
                if fallback:
                    parts[0] = fallback
        return shlex.join(parts)

    @staticmethod
    def _find_known_executable(name: str) -> str:
        candidates = [
            Path("/root/.local/bin") / name,
            Path("/usr/local/bin") / name,
            Path("/usr/bin") / name,
        ]
        node_root = Path("/root/.nvm/versions/node")
        if node_root.exists():
            candidates.extend(sorted(node_root.glob(f"*/bin/{name}"), reverse=True))

        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
        return ""
