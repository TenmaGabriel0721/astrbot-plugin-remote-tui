from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.custom_filter import CustomFilter

from .core.file_sender import FileSendError, FileSender
from .core.input_images import InputImageCache, InputImageError
from .core.keymap import parse_payload
from .core.menu import MenuContext, build_error_lines, build_footer
from .core.permissions import PermissionChecker
from .core.renderer import TerminalRenderer
from .core.session_manager import SessionInfo, SessionManager
from .core.tmux_backend import TmuxBackend
from .core.waiter import CaptureWaiter, WaitResult


PLUGIN_NAME = "astrbot_plugin_remote_tui"
TRIGGER = "/t"


def _trigger_variants() -> list[tuple[str, bool]]:
    trigger = (TRIGGER or "/t").strip() or "/t"
    variants: list[tuple[str, bool]] = [(trigger, False)]
    if trigger.startswith("/"):
        variants.append((trigger[1:], True))

    dedup: list[tuple[str, bool]] = []
    seen = set()
    for value, requires_wake in variants:
        if value and value not in seen:
            dedup.append((value, requires_wake))
            seen.add(value)
    return dedup


class RemoteTuiCommandFilter(CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        message = (event.get_message_str() or "").strip()
        for trigger, requires_wake in _trigger_variants():
            if requires_wake and not event.is_at_or_wake_command:
                continue
            if message == trigger:
                event.set_extra("remote_tui_payload", "")
                event.is_at_or_wake_command = True
                event.is_wake = True
                return True
            if message.startswith(f"{trigger} "):
                event.set_extra("remote_tui_payload", message[len(trigger) :].strip())
                event.is_at_or_wake_command = True
                event.is_wake = True
                return True
        return False


@register(PLUGIN_NAME, "TenmaGabriel0721", "远程控制 Codex / Claude Code TUI，会话画面以图片返回", "v0.4.3")
class RemoteTuiPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        global TRIGGER
        TRIGGER = str(self.config.get("trigger", "/t") or "/t").strip() or "/t"

        base_dir = Path(__file__).resolve().parent
        try:
            astrbot_data_dir = base_dir.parent.parent
            self.data_dir = astrbot_data_dir / "plugin_data" / PLUGIN_NAME
        except Exception:
            self.data_dir = base_dir / "plugin_data"
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.permission = PermissionChecker(self.config)
        self.tmux_path = str(self.config.get("tmux_path", "tmux") or "tmux")
        self.backend = TmuxBackend(self.tmux_path)
        self.sessions = SessionManager(self.config, self.data_dir, self.backend)
        self.file_sender = FileSender(self.config, self.data_dir, self.sessions.cwd)
        self.input_images = InputImageCache(self.config, self.data_dir)
        self.renderer = TerminalRenderer(
            self.cache_dir,
            font_path=str(self.config.get("font_path", "") or ""),
            font_size=int(self.config.get("font_size", 18) or 18),
            cjk_font_path=str(self.config.get("cjk_font_path", "") or ""),
        )
        self.action_delay = max(0.05, min(3.0, int(self.config.get("action_delay_ms", 350) or 350) / 1000))
        self.start_wait_timeout = max(1.0, float(self.config.get("start_wait_timeout_seconds", 10) or 10))
        self.control_wait_timeout = max(1.0, float(self.config.get("control_wait_timeout_seconds", 8) or 8))
        self.submit_wait_timeout = max(3.0, float(self.config.get("submit_wait_timeout_seconds", 120) or 120))
        self.wait_stable_seconds = max(0.2, float(self.config.get("wait_stable_ms", 1200) or 1200) / 1000)
        self.wait_poll_interval = max(0.1, float(self.config.get("wait_poll_interval_ms", 500) or 500) / 1000)
        self.submit_delay = max(0.0, min(1.0, float(self.config.get("submit_delay_ms", 200) or 200) / 1000))
        self.waiter = CaptureWaiter(self.backend, self.sessions.cols, self.sessions.rows)
        self.cache_retention_minutes = max(1, int(self.config.get("cache_retention_minutes", 30) or 30))
        self.idle_timeout_minutes = max(0, int(self.config.get("idle_timeout_minutes", 60) or 60))
        self.kill_sessions_on_unload = bool(self.config.get("kill_sessions_on_unload", False))
        self._locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info("Remote TUI 插件已加载，触发指令: %s", TRIGGER)
        if shutil.which(self.tmux_path) is None and not Path(self.tmux_path).exists():
            logger.warning("Remote TUI 必要系统依赖 tmux 不可用: %s", self.tmux_path)

    @filter.custom_filter(RemoteTuiCommandFilter, False)
    async def remote_tui_entry(self, event: AstrMessageEvent):
        """Remote TUI 入口。用法：/t、/t codex、/t claude、/t 内容、/t up、/t down、/t enter、/t esc"""
        event.should_call_llm(True)
        payload = str(event.get_extra("remote_tui_payload", "") or "")
        user_key = self.sessions.user_key_from_event(event)
        lock = self._locks.setdefault(user_key, asyncio.Lock())

        async with lock:
            try:
                result = await self._handle(event, user_key, payload)
            except FileSendError as exc:
                result = self._render_error("文件发送失败", str(exc))
            except InputImageError as exc:
                result = self._render_error("输入图片处理失败", str(exc))
            except Exception as exc:
                logger.exception("Remote TUI 处理失败: %s", exc)
                result = self._render_error("Remote TUI 处理失败", str(exc))

        if isinstance(result, Path):
            yield event.image_result(str(result)).stop_event()
        else:
            yield event.chain_result(result).stop_event()

    async def _handle(self, event: AstrMessageEvent, user_key: str, payload: str) -> Path | list[Any]:
        permission = self.permission.check_event(event)
        if not permission.allowed:
            return self._render_error("权限不足", permission.reason)

        direct_targets = self.file_sender.extract_direct_targets(payload)
        if direct_targets:
            return self._build_file_response(direct_targets)

        action = parse_payload(payload)
        input_images = []
        if action.kind in {"text", "refresh"}:
            input_images = await self.input_images.cache_from_event(event)

        if not await self.backend.available():
            return self._render_error(
                "tmux 不可用",
                "请安装 tmux，并确认 AstrBot 运行环境可以执行 tmux。",
            )

        if action.kind == "start":
            info = await self.sessions.start_or_switch(user_key, action.value)
            result = await self._wait_for_screen(info, self.start_wait_timeout, min_wait=self.action_delay)
            pending = self._pending_file_response(info)
            if pending:
                return pending
            return self._render_screen(info, result.text, self._status_from_wait(result))

        if action.kind == "key":
            info = await self.sessions.send_key(user_key, action.value)
            result = await self._wait_for_screen(info, self.control_wait_timeout, min_wait=self.action_delay)
            pending = self._pending_file_response(info)
            if pending:
                return pending
            return self._render_screen(info, result.text, self._status_from_wait(result))

        if action.kind == "text":
            if not action.value and not input_images:
                return await self._render_capture(user_key)
            info = await self.sessions.require_current(user_key)
            baseline = await self.backend.capture(info.session_name, self.sessions.cols, self.sessions.rows)
            text_to_send = action.value
            if not input_images:
                text_to_send = self.file_sender.with_usage_hint(text_to_send)
            text_to_send = self.input_images.build_prompt(text_to_send, input_images)
            await self.backend.send_text(
                info.session_name,
                text_to_send,
                submit=True,
                submit_delay_seconds=self.submit_delay,
            )
            self.sessions.touch(user_key, info.app)
            result = await self._wait_for_screen(
                info,
                self.submit_wait_timeout,
                min_wait=max(self.action_delay, 1.0),
                initial_text=baseline,
                require_change=True,
            )
            pending = self._pending_file_response(info)
            if pending:
                return pending
            return self._render_screen(info, result.text, self._status_from_wait(result))

        if action.kind == "refresh" and input_images:
            info = await self.sessions.require_current(user_key)
            baseline = await self.backend.capture(info.session_name, self.sessions.cols, self.sessions.rows)
            text_to_send = self.input_images.build_prompt("", input_images)
            await self.backend.send_text(
                info.session_name,
                text_to_send,
                submit=True,
                submit_delay_seconds=self.submit_delay,
            )
            self.sessions.touch(user_key, info.app)
            result = await self._wait_for_screen(
                info,
                self.submit_wait_timeout,
                min_wait=max(self.action_delay, 1.0),
                initial_text=baseline,
                require_change=True,
            )
            pending = self._pending_file_response(info)
            if pending:
                return pending
            return self._render_screen(info, result.text, self._status_from_wait(result))

        if action.kind == "stop":
            info = await self.sessions.stop_current(user_key)
            message = f"{self._app_display(info.app)} 会话已停止" if info else "没有正在运行的会话"
            return self.renderer.render_message(
                [
                    message,
                    "",
                    f"{TRIGGER} codex 启动 Codex    {TRIGGER} claude 启动 Claude",
                ],
                cols=self.sessions.cols,
                prefix="remote_tui_stop",
            )

        return await self._render_capture(user_key)

    async def _render_capture(self, user_key: str, info: SessionInfo | None = None) -> Path | list[Any]:
        if info is None:
            info, ansi_text = await self.sessions.capture_current(user_key)
        else:
            if info.running:
                ansi_text = await self.backend.capture(info.session_name, self.sessions.cols, self.sessions.rows)
            else:
                ansi_text = ""

        if info is None:
            footer = build_footer(MenuContext(trigger=TRIGGER, running=False))
            return self.renderer.render_terminal(
                "",
                cols=self.sessions.cols,
                rows=10,
                footer_lines=footer,
                prefix="remote_tui_menu",
            )

        if not info.running:
            return self._render_error(
                f"{self._app_display(info.app)} 会话已退出",
                f"使用 {TRIGGER} {info.app} 重新启动。",
            )

        pending = self._pending_file_response(info)
        if pending:
            return pending

        return self._render_screen(info, ansi_text, "运行中")

    async def _wait_for_screen(
        self,
        info: SessionInfo,
        timeout: float,
        min_wait: float,
        initial_text: str = "",
        require_change: bool = False,
    ) -> WaitResult:
        return await self.waiter.wait(
            info.session_name,
            timeout_seconds=timeout,
            stable_seconds=self.wait_stable_seconds,
            poll_interval_seconds=self.wait_poll_interval,
            min_wait_seconds=min_wait,
            initial_text=initial_text,
            require_change=require_change,
        )

    def _render_screen(self, info: SessionInfo, ansi_text: str, status: str) -> Path:
        footer = build_footer(
            MenuContext(
                trigger=TRIGGER,
                app=info.app,
                status=status,
                running=True,
            ),
        )
        return self.renderer.render_terminal(
            ansi_text,
            cols=self.sessions.cols,
            rows=self.sessions.rows,
            footer_lines=footer,
            prefix=f"remote_tui_{info.app}",
        )

    @staticmethod
    def _status_from_wait(result: WaitResult) -> str:
        if result.timed_out:
            return f"{result.reason}，可稍后 /t 刷新"
        return result.reason or "就绪"

    def _render_error(self, title: str, detail: str = "") -> Path:
        return self.renderer.render_message(
            build_error_lines(title, detail, TRIGGER),
            cols=self.sessions.cols,
            prefix="remote_tui_error",
        )

    def _build_file_response(self, targets: list[str]) -> list[Any]:
        prepared = self.file_sender.prepare(targets)
        return prepared.components

    def _pending_file_response(self, info: SessionInfo) -> list[Any] | None:
        targets = self.file_sender.consume_session_requests(info.session_name, info.user_key, info.app)
        if not targets:
            return None
        return self._build_file_response(targets)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                self._cleanup_cache()
                self.file_sender.cleanup_outgoing(self.cache_retention_minutes)
                self.input_images.cleanup(self.cache_retention_minutes)
                await self.sessions.cleanup_idle(self.idle_timeout_minutes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Remote TUI 清理任务失败: %s", exc)

    def _cleanup_cache(self) -> None:
        cutoff = time.time() - self.cache_retention_minutes * 60
        for path in self.cache_dir.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def _app_display(app: str) -> str:
        if app == "codex":
            return "Codex"
        if app == "claude":
            return "Claude"
        return "当前"

    async def terminate(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self.kill_sessions_on_unload:
            try:
                await self.sessions.stop_all_plugin_sessions()
            except Exception as exc:
                logger.warning("Remote TUI 卸载时停止 tmux 会话失败: %s", exc)
