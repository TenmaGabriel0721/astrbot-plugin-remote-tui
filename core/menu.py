from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MenuContext:
    trigger: str
    app: str = ""
    status: str = ""
    running: bool = False


def build_footer(ctx: MenuContext) -> list[str]:
    trigger = ctx.trigger or "/t"
    if not ctx.running:
        return [
            f"{trigger} codex 启动 Codex    {trigger} claude 启动 Claude",
            "未启动会话",
        ]

    app_name = "Codex" if ctx.app == "codex" else "Claude"
    status = ctx.status or "运行中"
    return [
        f"{trigger} 内容 发送    {trigger} 刷新    {trigger} up    {trigger} down    {trigger} enter    {trigger} esc",
        f"{trigger} left    {trigger} right    {trigger} tab    {trigger} pgup    {trigger} pgdn    {trigger} ctrlc    {trigger} stop",
        f"{app_name} | {status} | {datetime.now().strftime('%H:%M:%S')}",
    ]


def build_error_lines(title: str, detail: str = "", trigger: str = "/t") -> list[str]:
    lines = [title.strip() or "操作失败"]
    if detail:
        lines.extend(str(detail).strip().splitlines())
    lines.append("")
    lines.append(f"{trigger} 刷新    {trigger} codex    {trigger} claude")
    return lines
