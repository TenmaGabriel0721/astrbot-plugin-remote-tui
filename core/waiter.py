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

MENU_PATTERNS = [
    re.compile(r"(?i)\bresume\s+a\s+previous\s+session\b"),
    re.compile(r"(?i)\benter\s+resume\b"),
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
    plain = strip_ansi(text)
    lines = plain.splitlines()
    physical_tail = lines[-12:]
    nonempty_tail = [line.strip() for line in lines if line.strip()][-12:]
    tail = "\n".join(nonempty_tail or physical_tail)

    for pattern in MENU_PATTERNS:
        if pattern.search(tail):
            return "等待确认"

    if nonempty_tail:
        recent = nonempty_tail[-5:]
        for line in reversed(recent):
            if re.match(r"^[›❯➜]\s*/\S+", line):
                continue
            if re.match(r"^[›❯➜]\s*(?:$|.+)", line):
                return "就绪"
            if re.match(r"^[>$#]\s*$", line):
                return "就绪"

    if any(pattern.search(tail) for pattern in READY_PATTERNS):
        if not any(pattern.search(tail) for pattern in BUSY_PATTERNS):
            return "就绪"

    return ""


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
    ) -> WaitResult:
        timeout_seconds = max(0.5, float(timeout_seconds))
        stable_seconds = max(0.2, float(stable_seconds))
        poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        min_wait_seconds = max(0.0, float(min_wait_seconds))

        started = time.monotonic()
        last_text = ""
        initial_signature = normalize_for_stability(initial_text) if initial_text else ""
        last_signature = initial_signature
        stable_since = started
        last_reason = ""
        seen_change = not require_change

        while True:
            now = time.monotonic()
            text = await self.backend.capture(session_name, self.cols, self.rows)
            signature = normalize_for_stability(text)
            reason = detect_interactive(text)

            if signature != last_signature:
                stable_since = now
                last_signature = signature
                if signature != initial_signature:
                    seen_change = True

            last_text = text
            last_reason = reason or last_reason

            elapsed = now - started
            stable_for = now - stable_since
            if seen_change and elapsed >= min_wait_seconds and reason and stable_for >= stable_seconds:
                return WaitResult(text=text, reason=reason, timed_out=False)

            if elapsed >= timeout_seconds:
                return WaitResult(
                    text=last_text,
                    reason=last_reason or "等待超时",
                    timed_out=True,
                )

            await asyncio.sleep(poll_interval_seconds)
