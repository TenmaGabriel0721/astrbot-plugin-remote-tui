from __future__ import annotations

import asyncio
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class TmuxError(RuntimeError):
    pass


class TmuxBackend:
    def __init__(self, tmux_path: str = "tmux", timeout: float = 8.0):
        self.tmux_path = tmux_path or "tmux"
        self.timeout = timeout

    async def available(self) -> bool:
        if shutil.which(self.tmux_path) is None and not Path(self.tmux_path).exists():
            return False
        result = await self._run("-V", timeout=3)
        return result.returncode == 0

    async def has_session(self, session_name: str) -> bool:
        result = await self._run("has-session", "-t", session_name, timeout=3)
        return result.returncode == 0

    async def ensure_session(
        self,
        session_name: str,
        app: str,
        command: str,
        cwd: Path,
        cols: int,
        rows: int,
        extra_path_dirs: list[Path] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> bool:
        if await self.has_session(session_name):
            await self.resize(session_name, cols, rows)
            return False

        shell_command = self._build_shell_command(app, command, cwd, extra_path_dirs or [], extra_env or {})
        args = [
            "new-session",
            "-d",
            "-s",
            session_name,
            "-x",
            str(cols),
            "-y",
            str(rows),
            "-c",
            str(cwd),
            shell_command,
        ]
        result = await self._run(*args, timeout=8)
        if result.returncode != 0:
            raise TmuxError(result.stderr.strip() or result.stdout.strip() or "tmux new-session failed")

        await asyncio.sleep(0.35)
        if not await self.has_session(session_name):
            raise TmuxError(f"{app} 启动后已退出，请检查命令和登录状态")
        return True

    async def resize(self, session_name: str, cols: int, rows: int) -> None:
        result = await self._run("resize-window", "-t", session_name, "-x", str(cols), "-y", str(rows), timeout=3)
        if result.returncode != 0:
            return

    async def send_key(self, session_name: str, key: str) -> None:
        result = await self._run("send-keys", "-t", session_name, key, timeout=3)
        if result.returncode != 0:
            raise TmuxError(result.stderr.strip() or f"发送按键失败: {key}")

    async def send_text(
        self,
        session_name: str,
        text: str,
        submit: bool = True,
        submit_delay_seconds: float = 0.2,
    ) -> None:
        if text:
            buffer_name = f"{session_name}_input"
            result = await self._run("set-buffer", "-b", buffer_name, "--", text, timeout=5)
            if result.returncode != 0:
                raise TmuxError(result.stderr.strip() or "写入 tmux buffer 失败")
            result = await self._run("paste-buffer", "-d", "-b", buffer_name, "-t", session_name, timeout=5)
            if result.returncode != 0:
                raise TmuxError(result.stderr.strip() or "粘贴文本失败")
        if submit:
            if text and submit_delay_seconds > 0:
                await asyncio.sleep(max(0.0, min(1.0, submit_delay_seconds)))
            await self.send_key(session_name, "Enter")

    async def capture(self, session_name: str, cols: int, rows: int) -> str:
        await self.resize(session_name, cols, rows)
        result = await self._run("capture-pane", "-p", "-e", "-t", session_name, timeout=5)
        if result.returncode != 0:
            raise TmuxError(result.stderr.strip() or "捕获屏幕失败")
        return result.stdout

    async def stop(self, session_name: str) -> None:
        if not await self.has_session(session_name):
            return
        result = await self._run("kill-session", "-t", session_name, timeout=5)
        if result.returncode != 0:
            raise TmuxError(result.stderr.strip() or "停止会话失败")

    async def list_sessions(self) -> list[str]:
        result = await self._run("list-sessions", "-F", "#{session_name}", timeout=5)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    async def stop_by_prefix(self, prefix: str) -> None:
        for name in await self.list_sessions():
            if name.startswith(prefix):
                await self.stop(name)

    async def _run(self, *args: str, timeout: float | None = None) -> CommandResult:
        executable = self.tmux_path
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"找不到 tmux: {executable}")
        except Exception as exc:
            return CommandResult(1, "", str(exc))

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout or self.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return CommandResult(124, "", "tmux 命令超时")

        return CommandResult(
            proc.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    def _build_shell_command(
        self,
        app: str,
        command: str,
        cwd: Path,
        extra_path_dirs: list[Path],
        extra_env: dict[str, str],
    ) -> str:
        parts = self._validate_command(app, command)
        executable = parts[0]
        path_prefixes = []
        path_prefixes.extend(str(path) for path in extra_path_dirs if str(path))
        executable_dir = str(Path(executable).parent) if "/" in executable else ""
        if executable_dir and executable_dir != ".":
            path_prefixes.append(executable_dir)
        path_prefixes.extend(
            [
                "/root/.local/bin",
                "/root/.cargo/bin",
                "/root/.npm-global/bin",
            ],
        )
        node_root = Path("/root/.nvm/versions/node")
        if node_root.exists():
            path_prefixes.extend(str(path) for path in sorted(node_root.glob("*/bin"), reverse=True))

        path_export = ""
        if path_prefixes:
            unique = []
            for path in path_prefixes:
                if path and path not in unique:
                    unique.append(path)
            path_export = f"PATH={shlex.quote(':'.join(unique))}:$PATH; export PATH; "

        env_export = ""
        for key, value in extra_env.items():
            if not key.replace("_", "").isalnum() or key[0].isdigit():
                continue
            env_export += f"{key}={shlex.quote(str(value))}; export {key}; "

        command_text = shlex.join(parts)
        app_label = "Codex" if app == "codex" else "Claude"
        return (
            "/bin/sh -c "
            + shlex.quote(
                f"cd -- {shlex.quote(str(cwd))} || exit 127; "
                f"{path_export}"
                f"{env_export}"
                f"{command_text}; "
                "status=$?; "
                f"printf '\\n[Remote TUI] {app_label} exited with status %s\\n' \"$status\"; "
                "printf '[Remote TUI] use /t stop, then start again after fixing the issue.\\n'; "
                "while :; do sleep 3600; done",
            )
        )

    @staticmethod
    def _validate_command(app: str, command: str) -> list[str]:
        expected = "codex" if app == "codex" else "claude"
        try:
            parts = shlex.split(command or "")
        except ValueError as exc:
            raise TmuxError(f"{app} 启动命令无法解析: {exc}") from exc
        if not parts:
            raise TmuxError(f"{app} 启动命令为空")

        executable_name = Path(parts[0]).name
        if executable_name != expected:
            raise TmuxError(f"{app} 启动命令必须指向 {expected}")

        return parts
