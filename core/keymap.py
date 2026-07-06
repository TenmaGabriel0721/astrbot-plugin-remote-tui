from __future__ import annotations

from dataclasses import dataclass


PRIMARY_ACTIONS = {"codex", "claude", "up", "down", "enter", "esc"}

KEY_ACTIONS = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "pgup": "PPage",
    "pageup": "PPage",
    "pgdn": "NPage",
    "pagedown": "NPage",
    "ctrlc": "C-c",
    "ctrl-c": "C-c",
    "ctrld": "C-d",
    "ctrl-d": "C-d",
    "backspace": "BSpace",
}

APP_ACTIONS = {"codex", "claude"}
CONTROL_ACTIONS = set(KEY_ACTIONS)
STOP_ACTIONS = {"stop", "quit", "exit"}
REFRESH_ACTIONS = {"", "refresh"}


@dataclass(frozen=True)
class ParsedCommand:
    kind: str
    value: str = ""


def parse_payload(payload: str) -> ParsedCommand:
    text = (payload or "").strip()
    lowered = text.lower()

    if lowered in REFRESH_ACTIONS:
        return ParsedCommand("refresh")
    if lowered in APP_ACTIONS:
        return ParsedCommand("start", lowered)
    if lowered in STOP_ACTIONS:
        return ParsedCommand("stop")
    if lowered in CONTROL_ACTIONS:
        return ParsedCommand("key", KEY_ACTIONS[lowered])

    return ParsedCommand("text", payload.strip())
