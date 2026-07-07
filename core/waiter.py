from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from .tmux_backend import TmuxBackend


ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*(?:\x07|\x1b\\)"
    r"|[@-Z\\-_]"
    r")",
)

VOLATILE_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

READY_PATTERNS = [
    re.compile(r"(?i)\buse\s+/skills\b"),
    re.compile(r"(?i)\btype\s+your\s+message\b"),
]

SELECTION_MENU_PATTERNS = [
    re.compile(r"(?is)\b/resume\b"),
    re.compile(r"(?is)\b/model\b"),
    re.compile(r"(?is)\bresume\s+a\s+previous\s+session\b"),
    re.compile(r"(?is)\benter\s+resume\b"),
    re.compile(r"(?is)\btype\s+to\s+search\b.*\b(filter|sort|model|session)\b"),
    re.compile(r"(?is)\b(browse|filter|sort)\b.*\b(session|option|cwd|model)\b"),
    re.compile(r"(?is)\b(select|choose|switch)\b.*\b(model|session|conversation|option|choice)\b"),
    re.compile(r"(模型|会话|历史会话|恢复会话|选择模型|选择会话|切换模型|选项列表)"),
]

PERMISSION_PATTERNS = [
    re.compile(r"(?is)\b(allow|approve|confirm)\b.{0,100}\b(command|tool|operation|action|bash|shell|edit|write|file)\b"),
    re.compile(r"(?is)\b(command|tool|operation|action|bash|shell|edit|write|file)\b.{0,100}\b(allow|approve|confirm|permission)\b"),
    re.compile(r"(?is)\b(do you want to|would you like to)\b.{0,100}\b(run|execute|apply|modify|edit|write|create|delete|install|use)\b"),
    re.compile(r"(?is)\b(run|execute)\b.{0,60}\b(command|tool)\b"),
    re.compile(r"(?is)\bpermission\b.{0,100}\b(run|execute|use|write|edit|modify|create|delete|bash|shell|tool|command)\b"),
    re.compile(r"(?is)\b(requested|requests?|needs?|wants?)\b.{0,100}\b(permission|approval|command|tool)\b"),
    re.compile(r"(?is)\b(yes|allow|approve)\b.{0,80}\b(no|deny|reject)\b.{0,160}\b(command|tool|permission|bash|shell|edit|write|file)\b"),
    re.compile(r"(是否|确认|允许|批准).{0,100}(执行|运行|使用|调用|命令|工具|写入|修改|创建|删除|操作|权限)"),
    re.compile(r"(执行|运行|使用|调用|命令|工具|写入|修改|创建|删除|操作).{0,100}(权限|确认|允许|批准)"),
]

MENU_PATTERNS = [
    re.compile(r"(?i)\btype\s+to\s+search\b.*\b(filter|sort)\b"),
    re.compile(r"(?i)\b(browse|filter|sort)\b.*\b(session|option|cwd)\b"),
    re.compile(r"(?i)\b(allow|approve|deny|reject|continue|cancel)\b"),
    re.compile(r"(?i)\b(yes|no)\b.*\b(enter|esc|tab)\b"),
    re.compile(r"(?i)\bpress\s+(enter|esc|tab)\b"),
    re.compile(r"(?i)\b(run|execute)\b.*\b(command|tool)\b"),
    re.compile(r"(?i)\bpermission\b"),
    re.compile(r"(?i)\bselect\b.*\b(option|choice)\b"),
    re.compile(r"(确认|允许|拒绝|继续|取消|是否|选择|回车|返回|执行|权限)"),
]

BUSY_PATTERNS = [
    re.compile(r"(?i)\b(thinking|working|running|loading|streaming)\b"),
    re.compile(r"(?i)\bwaiting\s+for\b"),
    re.compile(r"(思考中|运行中|加载中|处理中)"),
]


@dataclass(frozen=True)
class WaitResult:
    text: str
    reason: str
    timed_out: bool = False
    auto_confirmed: int = 0


@dataclass(frozen=True)
class InteractiveState:
    kind: str
    reason: str = ""


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def normalize_for_stability(text: str) -> str:
    plain = strip_ansi(text)
    plain = VOLATILE_RE.sub("<time>", plain)
    lines = [line.rstrip() for line in plain.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def detect_interactive(text: str) -> str:
    return classify_interactive(text).reason


def classify_interactive(text: str) -> InteractiveState:
    plain = strip_ansi(text)
    lines = plain.splitlines()
    physical_tail = lines[-12:]
    nonempty_tail = [line.strip() for line in lines if line.strip()][-12:]
    tail = "\n".join(nonempty_tail or physical_tail)

    for pattern in SELECTION_MENU_PATTERNS:
        if pattern.search(tail):
            return InteractiveState("menu", "等待选择")

    for pattern in PERMISSION_PATTERNS:
        if pattern.search(tail):
            return InteractiveState("permission", "等待权限确认")

    for pattern in MENU_PATTERNS:
        if pattern.search(tail):
            return InteractiveState("menu", "等待确认")

    if nonempty_tail:
        recent = nonempty_tail[-5:]
        for line in reversed(recent):
            if re.match(r"^[›❯➜]\s*/\S+", line):
                continue
            if re.match(r"^[›❯➜]\s*(?:$|.+)", line):
                return InteractiveState("ready", "就绪")
            if re.match(r"^[>$#]\s*$", line):
                return InteractiveState("ready", "就绪")

    if any(pattern.search(tail) for pattern in READY_PATTERNS):
        if not any(pattern.search(tail) for pattern in BUSY_PATTERNS):
            return InteractiveState("ready", "就绪")

    return InteractiveState("")


class CaptureWaiter:
    def __init__(self, backend: TmuxBackend, cols: int, rows: int):
        self.backend = backend
        self.cols = cols
        self.rows = rows

    async def wait(
        self,
        session_name: str,
        timeout_seconds: float,
        stable_seconds: float,
        poll_interval_seconds: float,
        min_wait_seconds: float = 0.0,
        initial_text: str = "",
        require_change: bool = False,
        auto_confirm_permissions: bool = False,
        auto_confirm_max: int = 3,
        auto_confirm_delay_seconds: float = 0.2,
    ) -> WaitResult:
        timeout_seconds = max(0.5, float(timeout_seconds))
        stable_seconds = max(0.2, float(stable_seconds))
        poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        min_wait_seconds = max(0.0, float(min_wait_seconds))
        auto_confirm_max = max(0, int(auto_confirm_max))
        auto_confirm_delay_seconds = max(0.0, min(2.0, float(auto_confirm_delay_seconds)))

        started = time.monotonic()
        last_text = ""
        initial_signature = normalize_for_stability(initial_text) if initial_text else ""
        last_signature = initial_signature
        stable_since = started
        last_reason = ""
        seen_change = not require_change
        auto_confirmed = 0
        confirmed_signatures: set[str] = set()

        while True:
            now = time.monotonic()
            text = await self.backend.capture(session_name, self.cols, self.rows)
            signature = normalize_for_stability(text)
            state = classify_interactive(text)
            reason = state.reason

            if signature != last_signature:
                stable_since = now
                last_signature = signature
                if signature != initial_signature:
                    seen_change = True

            last_text = text
            last_reason = reason or last_reason

            elapsed = now - started
            stable_for = now - stable_since
            can_finish = seen_change and elapsed >= min_wait_seconds and reason and stable_for >= stable_seconds
            if (
                can_finish
                and auto_confirm_permissions
                and state.kind == "permission"
                and auto_confirmed < auto_confirm_max
                and signature not in confirmed_signatures
            ):
                await self.backend.send_key(session_name, "Enter")
                auto_confirmed += 1
                confirmed_signatures.add(signature)
                stable_since = time.monotonic()
                last_reason = f"已自动确认权限 {auto_confirmed} 次"
                if auto_confirm_delay_seconds > 0:
                    await asyncio.sleep(auto_confirm_delay_seconds)
                continue

            if can_finish:
                return WaitResult(text=text, reason=reason, timed_out=False, auto_confirmed=auto_confirmed)

            if elapsed >= timeout_seconds:
                return WaitResult(
                    text=last_text,
                    reason=last_reason or "等待超时",
                    timed_out=True,
                    auto_confirmed=auto_confirmed,
                )

            await asyncio.sleep(poll_interval_seconds)
