from __future__ import annotations

import math
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from wcwidth import wcwidth


RGB = tuple[int, int, int]

DEFAULT_FG: RGB = (218, 224, 231)
DEFAULT_BG: RGB = (12, 16, 22)
FOOTER_BG: RGB = (20, 27, 36)
FOOTER_BORDER: RGB = (58, 70, 86)
BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

ANSI_16: list[RGB] = [
    (0, 0, 0),
    (205, 49, 49),
    (13, 188, 121),
    (229, 229, 16),
    (36, 114, 200),
    (188, 63, 188),
    (17, 168, 205),
    (229, 229, 229),
    (102, 102, 102),
    (241, 76, 76),
    (35, 209, 139),
    (245, 245, 67),
    (59, 142, 234),
    (214, 112, 214),
    (41, 184, 219),
    (255, 255, 255),
]


@dataclass
class AnsiStyle:
    fg: RGB | None = None
    bg: RGB | None = None
    bold: bool = False
    inverse: bool = False

    def copy(self) -> "AnsiStyle":
        return AnsiStyle(self.fg, self.bg, self.bold, self.inverse)

    def resolved(self) -> tuple[RGB, RGB]:
        fg = self.fg or DEFAULT_FG
        bg = self.bg or DEFAULT_BG
        if self.bold and self.fg is None:
            fg = (245, 247, 250)
        if self.inverse:
            return bg, fg
        return fg, bg


def cell_width(ch: str) -> int:
    if not ch:
        return 0
    width = wcwidth(ch)
    if width >= 0:
        return width
    if unicodedata.category(ch)[0] == "C":
        return 0
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def text_cell_width(text: str) -> int:
    return sum(cell_width(ch) for ch in text)


def _xterm_256(index: int) -> RGB:
    index = max(0, min(255, index))
    if index < 16:
        return ANSI_16[index]
    if index < 232:
        index -= 16
        r = index // 36
        g = (index % 36) // 6
        b = index % 6
        conv = [0, 95, 135, 175, 215, 255]
        return (conv[r], conv[g], conv[b])
    gray = 8 + (index - 232) * 10
    return (gray, gray, gray)


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _apply_sgr(style: AnsiStyle, params_text: str) -> AnsiStyle:
    if not params_text:
        params = [0]
    else:
        params = [_parse_int(part, 0) for part in params_text.replace(":", ";").split(";") if part != ""]
        if not params:
            params = [0]

    i = 0
    while i < len(params):
        code = params[i]
        if code == 0:
            style = AnsiStyle()
        elif code == 1:
            style.bold = True
        elif code == 22:
            style.bold = False
        elif code == 7:
            style.inverse = True
        elif code == 27:
            style.inverse = False
        elif 30 <= code <= 37:
            style.fg = ANSI_16[code - 30]
        elif 90 <= code <= 97:
            style.fg = ANSI_16[8 + code - 90]
        elif code == 39:
            style.fg = None
        elif 40 <= code <= 47:
            style.bg = ANSI_16[code - 40]
        elif 100 <= code <= 107:
            style.bg = ANSI_16[8 + code - 100]
        elif code == 49:
            style.bg = None
        elif code in (38, 48):
            is_fg = code == 38
            if i + 2 < len(params) and params[i + 1] == 5:
                color = _xterm_256(params[i + 2])
                if is_fg:
                    style.fg = color
                else:
                    style.bg = color
                i += 2
            elif i + 4 < len(params) and params[i + 1] == 2:
                color = (
                    max(0, min(255, params[i + 2])),
                    max(0, min(255, params[i + 3])),
                    max(0, min(255, params[i + 4])),
                )
                if is_fg:
                    style.fg = color
                else:
                    style.bg = color
                i += 4
        i += 1
    return style


def _wrap_plain_line(line: str, max_cols: int) -> list[str]:
    if text_cell_width(line) <= max_cols:
        return [line]
    wrapped: list[str] = []
    current = ""
    width = 0
    for ch in line:
        ch_width = cell_width(ch)
        if width + ch_width > max_cols and current:
            wrapped.append(current)
            current = ch
            width = ch_width
        else:
            current += ch
            width += ch_width
    if current:
        wrapped.append(current)
    return wrapped or [""]


class TerminalRenderer:
    def __init__(
        self,
        cache_dir: Path,
        font_path: str = "",
        font_size: int = 18,
        cjk_font_path: str = "",
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.font_size = max(10, int(font_size or 18))
        self.font_path = self._find_font(font_path)
        self.cjk_font_path = self._find_cjk_font(cjk_font_path)
        self.font = self._load_font(self.font_path, self.font_size)
        self.bold_font = self._load_font(self._find_bold_font(self.font_path), self.font_size)
        self.cjk_font = self._load_font(self.cjk_font_path, self.font_size)
        self.cjk_bold_font = self._load_font(self._find_bold_font(self.cjk_font_path), self.font_size)
        self.char_width, self.line_height = self._measure_cell()
        self.padding_x = 14
        self.padding_y = 12

    def _find_font(self, configured: str = "") -> str:
        candidates = [
            configured,
            str(BUNDLED_FONT_DIR / "NotoSansMono-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/root/AstrBot/data/plugins/astrbot_plugin_neko_care/fonts/GBK.TTF",
            "/root/astrbot_plugin_neko_care_push/fonts/GBK.TTF",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return ""

    def _find_cjk_font(self, configured: str = "") -> str:
        candidates = [
            configured,
            str(BUNDLED_FONT_DIR / "wqy-zenhei.ttc"),
            "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/root/AstrBot/data/plugins/astrbot_plugin_neko_care/fonts/GBK.TTF",
            "/root/astrbot_plugin_neko_care_push/fonts/GBK.TTF",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return self.font_path

    def _find_bold_font(self, regular: str) -> str:
        path = Path(regular) if regular else Path()
        candidates = []
        if path.name:
            candidates.append(path.with_name(path.name.replace("Regular", "Bold")))
            candidates.append(path.with_name(path.name.replace("Mono.ttf", "Mono-Bold.ttf")))
        candidates.extend(
            [
                BUNDLED_FONT_DIR / "NotoSansMono-Bold.ttf",
                Path("/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
            ],
        )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return regular

    @staticmethod
    def _load_font(path: str, size: int):
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _measure_cell(self) -> tuple[int, int]:
        try:
            bbox = self.font.getbbox("M")
            char_width = max(8, math.ceil(bbox[2] - bbox[0]))
            ascent, descent = self.font.getmetrics()
            line_height = max(char_width + 6, ascent + descent + 4)
            return char_width, line_height
        except Exception:
            return 10, 20

    def render_terminal(
        self,
        ansi_text: str,
        cols: int,
        rows: int,
        footer_lines: Iterable[str] | None = None,
        prefix: str = "tui",
    ) -> Path:
        cols = max(40, min(220, int(cols or 100)))
        rows = max(10, min(80, int(rows or 30)))
        body_lines = ansi_text.splitlines()
        if not body_lines:
            body_lines = [""]
        body_lines = body_lines[:rows]
        while len(body_lines) < rows:
            body_lines.append("")

        footer = list(footer_lines or [])
        footer_height = 0
        if footer:
            footer_height = self.padding_y + len(footer) * self.line_height + self.padding_y

        width = self.padding_x * 2 + cols * self.char_width
        height = self.padding_y * 2 + rows * self.line_height + footer_height
        image = Image.new("RGB", (width, height), DEFAULT_BG)
        draw = ImageDraw.Draw(image)

        y = self.padding_y
        for line in body_lines:
            self._draw_ansi_line(draw, line, self.padding_x, y, cols)
            y += self.line_height

        if footer:
            footer_top = self.padding_y * 2 + rows * self.line_height
            draw.rectangle((0, footer_top, width, height), fill=FOOTER_BG)
            draw.line((0, footer_top, width, footer_top), fill=FOOTER_BORDER, width=1)
            fy = footer_top + self.padding_y
            for line in footer:
                self._draw_plain_line(draw, line, self.padding_x, fy, cols, (198, 210, 224))
                fy += self.line_height

        return self._save(image, prefix)

    def render_message(self, lines: Iterable[str], cols: int = 96, prefix: str = "message") -> Path:
        wrapped: list[str] = []
        for line in lines:
            wrapped.extend(_wrap_plain_line(str(line), max(20, cols - 2)))
        rows = max(10, min(40, len(wrapped) + 2))
        body = "\n".join(wrapped[: rows - 1])
        return self.render_terminal(body, cols=cols, rows=rows, footer_lines=None, prefix=prefix)

    def _draw_ansi_line(self, draw: ImageDraw.ImageDraw, line: str, x: int, y: int, cols: int) -> None:
        style = AnsiStyle()
        col = 0
        i = 0
        while i < len(line) and col < cols:
            ch = line[i]
            if ch == "\x1b":
                new_i, new_style = self._consume_escape(line, i, style)
                style = new_style
                i = new_i
                continue
            if ch == "\r":
                col = 0
                i += 1
                continue
            if ch == "\b":
                col = max(0, col - 1)
                i += 1
                continue
            if ch == "\t":
                spaces = 4 - (col % 4)
                for _ in range(spaces):
                    self._draw_cell(draw, " ", x, y, col, 1, style)
                    col += 1
                i += 1
                continue

            width = cell_width(ch)
            if width <= 0:
                i += 1
                continue
            if col + width > cols:
                break
            self._draw_cell(draw, ch, x, y, col, width, style)
            col += width
            i += 1

    def _consume_escape(self, line: str, start: int, style: AnsiStyle) -> tuple[int, AnsiStyle]:
        if start + 1 >= len(line):
            return start + 1, style
        introducer = line[start + 1]
        if introducer == "[":
            j = start + 2
            while j < len(line) and not ("@" <= line[j] <= "~"):
                j += 1
            if j >= len(line):
                return len(line), style
            final = line[j]
            params = line[start + 2 : j]
            if final == "m":
                return j + 1, _apply_sgr(style.copy(), params)
            return j + 1, style
        if introducer == "]":
            j = start + 2
            while j < len(line):
                if line[j] == "\x07":
                    return j + 1, style
                if line[j] == "\x1b" and j + 1 < len(line) and line[j + 1] == "\\":
                    return j + 2, style
                j += 1
            return len(line), style
        return min(start + 2, len(line)), style

    def _draw_cell(
        self,
        draw: ImageDraw.ImageDraw,
        ch: str,
        base_x: int,
        y: int,
        col: int,
        width: int,
        style: AnsiStyle,
    ) -> None:
        fg, bg = style.resolved()
        x0 = base_x + col * self.char_width
        x1 = x0 + width * self.char_width
        y1 = y + self.line_height
        if bg != DEFAULT_BG:
            draw.rectangle((x0, y, x1, y1), fill=bg)
        if ch != " ":
            font = self._font_for_char(ch, style.bold)
            draw.text((x0, y), ch, font=font, fill=fg)

    def _draw_plain_line(
        self,
        draw: ImageDraw.ImageDraw,
        line: str,
        x: int,
        y: int,
        cols: int,
        fill: RGB,
    ) -> None:
        col = 0
        for ch in line:
            width = cell_width(ch)
            if width <= 0:
                continue
            if col + width > cols:
                break
            font = self._font_for_char(ch, False)
            draw.text((x + col * self.char_width, y), ch, font=font, fill=fill)
            col += width

    def _font_for_char(self, ch: str, bold: bool):
        if self._needs_cjk_font(ch):
            return self.cjk_bold_font if bold else self.cjk_font
        return self.bold_font if bold else self.font

    @staticmethod
    def _needs_cjk_font(ch: str) -> bool:
        if not ch or ord(ch) < 128:
            return False
        if unicodedata.east_asian_width(ch) in {"W", "F"}:
            return True
        return "\u2e80" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"

    def _save(self, image: Image.Image, prefix: str) -> Path:
        name = f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
        path = self.cache_dir / name
        image.save(path, "PNG", optimize=True)
        return path
