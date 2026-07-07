from __future__ import annotations

import json
import os
import re
import shlex
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import message_components as Comp


DEFAULT_IMAGE_EXTENSIONS = [
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
]

DEFAULT_DENIED_NAMES = [
    ".ssh",
    ".git",
    ".config",
    ".cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
]

DEFAULT_DENIED_KEYWORDS = [
    ".env",
    "token",
    "secret",
    "credential",
    "passwd",
    "password",
    "apikey",
    "api_key",
]

SEND_HINT = (
    "\n\n[Remote TUI file interface]\n"
    "如果需要把本机文件发送到 QQ，请在终端执行: qqsend <文件或目录路径>\n"
    "目录会自动打包；执行后不要输出文件内容。"
)


class FileSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedFiles:
    components: list[Any]
    labels: list[str]


class FileSender:
    def __init__(self, config: dict, data_dir: Path, default_cwd: Path):
        self.config = config
        self.enabled = bool(config.get("file_send_enabled", True))
        self.data_dir = Path(data_dir)
        self.default_cwd = Path(default_cwd).expanduser().resolve(strict=False)
        self.tool_dir = self.data_dir / "bin"
        self.queue_dir = self.data_dir / "send_queue"
        self.outgoing_dir = self.data_dir / "outgoing"

        allowed_roots = config.get("file_send_allowed_roots")
        if not allowed_roots:
            allowed_roots = config.get("allowed_cwds") or [str(self.default_cwd)]
        self.allowed_roots = self._resolve_roots(list(allowed_roots or []))
        if not self.allowed_roots:
            self.allowed_roots = [self.default_cwd]

        self.denied_names = {
            str(item).strip().lower()
            for item in config.get("file_send_denied_names", DEFAULT_DENIED_NAMES)
            if str(item).strip()
        }
        self.denied_keywords = [
            str(item).strip().lower()
            for item in config.get("file_send_denied_keywords", DEFAULT_DENIED_KEYWORDS)
            if str(item).strip()
        ]
        self.image_extensions = {
            self._normalize_ext(item)
            for item in config.get("file_send_image_extensions", DEFAULT_IMAGE_EXTENSIONS)
            if str(item).strip()
        }
        self.max_items = max(1, int(config.get("file_send_max_items", 10) or 10))
        self.max_file_size = max(1, int(config.get("file_send_max_file_size_mb", 50) or 50)) * 1024 * 1024
        self.max_archive_size = (
            max(1, int(config.get("file_send_max_archive_size_mb", 100) or 100)) * 1024 * 1024
        )
        self.max_archive_files = max(1, int(config.get("file_send_max_archive_files", 500) or 500))
        self.append_hint = bool(config.get("file_send_append_hint", True))
        self.install_user_bin = bool(config.get("file_send_install_user_bin", True))

        self.tool_dir.mkdir(parents=True, exist_ok=True)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.outgoing_dir.mkdir(parents=True, exist_ok=True)
        self.install_cli()

    def install_cli(self) -> None:
        source = self._cli_source(str(self.queue_dir))
        script = self.tool_dir / "qqsend"
        self._write_executable(script, source)

        if self.install_user_bin:
            user_script = Path.home() / ".local" / "bin" / "qqsend"
            if not user_script.exists() or self._is_remote_tui_script(user_script):
                user_script.parent.mkdir(parents=True, exist_ok=True)
                self._write_executable(user_script, source)

    def extract_direct_targets(self, payload: str) -> list[str]:
        if not self.enabled:
            return []
        text = (payload or "").strip()
        if not text:
            return []

        lowered = text.lower()
        raw_target = ""
        for prefix in ("qqsend ", "send "):
            if lowered.startswith(prefix):
                raw_target = text[len(prefix) :].strip()
                break

        if not raw_target:
            for prefix in ("发送", "发 "):
                if text.startswith(prefix):
                    raw_target = text[len(prefix) :].strip()
                    break

        if not raw_target and text.startswith("把"):
            match = re.match(r"^把\s*(.+?)\s*(发出来|发送出来|发给我|发到qq|发到QQ|发一下|发来)$", text)
            if match:
                raw_target = match.group(1).strip()

        if not raw_target:
            return []
        return self._split_targets(raw_target)

    def with_usage_hint(self, text: str) -> str:
        if not self.enabled or not self.append_hint or not self._looks_like_file_send_request(text):
            return text
        if "qqsend" in text:
            return text
        return f"{text}{SEND_HINT}"

    def consume_session_requests(self, session_name: str, user_key: str, app: str) -> list[str]:
        if not self.enabled:
            return []
        queue_path = self._queue_path(session_name)
        if not queue_path.exists():
            return []

        processing = queue_path.with_name(f"{queue_path.name}.{int(time.time() * 1000)}.processing")
        try:
            queue_path.replace(processing)
        except FileNotFoundError:
            return []

        targets: list[str] = []
        try:
            for line in processing.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("session_name") != session_name:
                    continue
                data_user_key = str(data.get("user_key") or "").strip()
                if data_user_key and data_user_key != user_key:
                    continue
                data_app = str(data.get("app") or "").strip()
                if data_app and data_app != app:
                    continue
                path = str(data.get("path") or "").strip()
                if path:
                    targets.append(path)
        finally:
            try:
                processing.unlink()
            except OSError:
                pass

        return targets[: self.max_items]

    def prepare(self, targets: list[str]) -> PreparedFiles:
        if not self.enabled:
            raise FileSendError("文件发送接口未启用")
        if not targets:
            raise FileSendError("没有可发送的文件路径")
        if len(targets) > self.max_items:
            raise FileSendError(f"一次最多发送 {self.max_items} 个路径")

        components: list[Any] = []
        labels: list[str] = []
        for raw_target in targets:
            path = self._resolve_target(raw_target)
            component, label = self._prepare_path(path)
            components.append(component)
            labels.append(label)

        return PreparedFiles(components=components, labels=labels)

    def cleanup_outgoing(self, retention_minutes: int) -> None:
        cutoff = time.time() - max(1, retention_minutes) * 60
        for path in self.outgoing_dir.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _prepare_path(self, path: Path) -> tuple[Any, str]:
        self._validate_path(path)
        if path.is_dir():
            archive = self._zip_directory(path)
            return Comp.File(name=archive.name, file=str(archive)), archive.name

        size = self._file_size(path)
        if size > self.max_file_size:
            raise FileSendError(f"文件过大: {path} ({self._format_size(size)})")

        if path.suffix.lower() in self.image_extensions:
            return Comp.Image.fromFileSystem(str(path)), path.name
        return Comp.File(name=path.name, file=str(path)), path.name

    def _zip_directory(self, directory: Path) -> Path:
        included: list[tuple[Path, str]] = []
        total = 0
        base_parent = directory.parent

        for root, dirnames, filenames in os.walk(directory):
            root_path = Path(root)
            dirnames[:] = [
                name
                for name in dirnames
                if not self._is_denied_path(root_path / name) and not (root_path / name).is_symlink()
            ]
            for filename in filenames:
                file_path = root_path / filename
                if file_path.is_symlink() or self._is_denied_path(file_path):
                    continue
                if not file_path.is_file():
                    continue
                size = self._file_size(file_path)
                if size > self.max_file_size:
                    raise FileSendError(f"目录内文件过大: {file_path} ({self._format_size(size)})")
                total += size
                if total > self.max_archive_size:
                    raise FileSendError(
                        f"目录打包内容超过限制: {self._format_size(total)} > {self._format_size(self.max_archive_size)}",
                    )
                if len(included) >= self.max_archive_files:
                    raise FileSendError(f"目录文件数量超过限制: {self.max_archive_files}")
                included.append((file_path, str(file_path.relative_to(base_parent))))

        if not included:
            raise FileSendError(f"目录为空或没有允许发送的文件: {directory}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        archive_name = f"{self._safe_archive_name(directory.name)}-{timestamp}.zip"
        archive_path = self.outgoing_dir / archive_name
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path, arcname in included:
                zf.write(file_path, arcname)
        return archive_path

    def _resolve_target(self, raw_target: str) -> Path:
        target = self._clean_target(raw_target)
        if not target:
            raise FileSendError("文件路径为空")

        candidates: list[Path] = []
        if target.startswith("~"):
            candidates.append(Path(target).expanduser())
        else:
            path = Path(target)
            if path.is_absolute():
                candidates.append(path)
            else:
                candidates.append(self.default_cwd / path)
                for root in self.allowed_roots:
                    candidate = root / path
                    if candidate not in candidates:
                        candidates.append(candidate)

        for candidate in candidates:
            resolved = candidate.expanduser().resolve(strict=False)
            if resolved.exists():
                return resolved

        raise FileSendError(f"找不到文件或目录: {target}")

    def _validate_path(self, path: Path) -> None:
        resolved = path.expanduser().resolve(strict=True)
        if not any(self._is_relative_to(resolved, root) for root in self.allowed_roots):
            roots = ", ".join(str(root) for root in self.allowed_roots)
            raise FileSendError(f"路径不在允许目录内: {resolved}；允许目录: {roots}")
        if self._is_denied_path(resolved):
            raise FileSendError(f"路径命中安全黑名单: {resolved}")
        if not (resolved.is_file() or resolved.is_dir()):
            raise FileSendError(f"只支持发送普通文件或目录: {resolved}")

    def _is_denied_path(self, path: Path) -> bool:
        parts = [part.lower() for part in path.parts]
        if any(part in self.denied_names for part in parts):
            return True
        name = path.name.lower()
        return any(keyword and keyword in name for keyword in self.denied_keywords)

    def _queue_path(self, session_name: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_name)
        return self.queue_dir / f"{safe_name}.jsonl"

    def _split_targets(self, raw_target: str) -> list[str]:
        text = raw_target.strip().strip("，,。；;")
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = [text]
        return [self._clean_target(part) for part in parts if self._clean_target(part)]

    @staticmethod
    def _clean_target(raw_target: str) -> str:
        target = str(raw_target or "").strip()
        target = target.strip("\"'`“”‘’")
        target = target.strip("，,。；;")
        return target.strip()

    @staticmethod
    def _looks_like_file_send_request(text: str) -> bool:
        lowered = (text or "").lower()
        markers = [
            "发出来",
            "发送",
            "发给我",
            "发到qq",
            "发到 qq",
            "qqsend",
            "send file",
            "send the file",
        ]
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _resolve_roots(values: list[Any]) -> list[Path]:
        roots: list[Path] = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            path = Path(text).expanduser().resolve(strict=False)
            if path not in roots:
                roots.append(path)
        return roots

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _normalize_ext(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text.startswith(".") else f".{text}"

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _safe_archive_name(value: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:80].strip("._-")
        return name or "directory"

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    @staticmethod
    def _write_executable(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _is_remote_tui_script(path: Path) -> bool:
        try:
            return "Remote TUI" in path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except OSError:
            return False

    @staticmethod
    def _cli_source(default_queue_dir: str) -> str:
        source = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_QUEUE_DIR = __DEFAULT_QUEUE_DIR__


def _tmux_session_name() -> str:
    pane = os.environ.get("TMUX_PANE", "").strip()
    if not pane:
        return ""
    try:
        proc = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{session_name}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _app_from_session(session_name: str) -> str:
    if session_name.endswith("_codex"):
        return "codex"
    if session_name.endswith("_claude"):
        return "claude"
    return ""


def main() -> int:
    queue_dir = os.environ.get("REMOTE_TUI_QUEUE_DIR", "").strip() or DEFAULT_QUEUE_DIR
    session_name = os.environ.get("REMOTE_TUI_SESSION_NAME", "").strip()
    user_key = os.environ.get("REMOTE_TUI_USER_KEY", "").strip()
    app = os.environ.get("REMOTE_TUI_APP", "").strip()
    if not session_name:
        session_name = _tmux_session_name()
    if not app:
        app = _app_from_session(session_name)
    if not queue_dir or not session_name or not app:
        print("[Remote TUI] qqsend 只能在 Remote TUI 的 Codex/Claude 会话中使用。", file=sys.stderr)
        return 2
    if len(sys.argv) < 2:
        print("用法: qqsend <文件或目录路径> [...]", file=sys.stderr)
        return 2

    queue_path = Path(queue_dir)
    queue_path.mkdir(parents=True, exist_ok=True)
    safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_name)
    output = queue_path / f"{safe_session}.jsonl"

    count = 0
    with output.open("a", encoding="utf-8") as fh:
        for raw in sys.argv[1:]:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            item = {
                "type": "send_file",
                "path": str(path.resolve(strict=False)),
                "session_name": session_name,
                "user_key": user_key,
                "app": app,
                "time": time.time(),
            }
            fh.write(json.dumps(item, ensure_ascii=False) + "\\n")
            count += 1
    print(f"[Remote TUI] 已请求发送 {count} 个路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
        return source.replace("__DEFAULT_QUEUE_DIR__", json.dumps(default_queue_dir))
