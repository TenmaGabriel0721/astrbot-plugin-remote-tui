from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PermissionResult:
    allowed: bool
    reason: str = ""


def _as_str_set(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    try:
        return {str(item) for item in value if str(item)}
    except TypeError:
        return set()


class PermissionChecker:
    def __init__(self, config: dict):
        self.allow_all_users = bool(config.get("allow_all_users", False))
        self.allowed_users = _as_str_set(config.get("allowed_users", []))
        self.allowed_groups = _as_str_set(config.get("allowed_groups", []))
        self.extra_admin_ids = _as_str_set(config.get("extra_admin_ids", []))

    def check_event(self, event) -> PermissionResult:
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")

        if group_id and self.allowed_groups and group_id not in self.allowed_groups:
            return PermissionResult(False, f"未授权群聊: {group_id}")

        if self.allow_all_users:
            return PermissionResult(True)

        if event.is_admin() or sender_id in self.extra_admin_ids:
            return PermissionResult(True)

        if sender_id in self.allowed_users:
            return PermissionResult(True)

        return PermissionResult(False, f"未授权用户: {sender_id or '-'}")


def resolve_allowed_cwd(default_cwd: str, allowed_cwds: list[str]) -> Path:
    requested = Path(default_cwd or "/root").expanduser().resolve()
    roots = [Path(item).expanduser().resolve() for item in (allowed_cwds or ["/root"])]

    for root in roots:
        try:
            requested.relative_to(root)
            requested.mkdir(parents=True, exist_ok=True)
            return requested
        except ValueError:
            continue

    fallback = roots[0] if roots else Path("/root")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
