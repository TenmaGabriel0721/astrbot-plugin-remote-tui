from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api import message_components as Comp


IMAGE_EXTENSION_BY_FORMAT = {
    "BMP": ".bmp",
    "GIF": ".gif",
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


@dataclass(frozen=True)
class CachedInputImage:
    path: Path
    format: str
    size_bytes: int


class InputImageError(RuntimeError):
    pass


class InputImageCache:
    def __init__(self, config: dict, data_dir: Path):
        self.enabled = bool(config.get("image_input_enabled", True))
        self.include_replies = bool(config.get("image_input_include_replies", True))
        self.max_images = max(1, int(config.get("image_input_max_images", 4) or 4))
        self.max_file_size = max(1, int(config.get("image_input_max_file_size_mb", 20) or 20)) * 1024 * 1024
        self.upload_dir = (Path(data_dir) / "uploads").expanduser().resolve(strict=False)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def cache_from_event(self, event: Any) -> list[CachedInputImage]:
        if not self.enabled:
            return []

        images = self._extract_images(event)[: self.max_images]
        cached: list[CachedInputImage] = []
        errors: list[str] = []
        for image in images:
            try:
                cached.append(await self._cache_image(image))
            except Exception as exc:
                errors.append(str(exc))
                logger.warning("Remote TUI 输入图片缓存失败: %s", exc)
                continue
        if images and not cached:
            detail = errors[0] if errors else "没有可用图片"
            raise InputImageError(detail)
        return cached

    def build_prompt(self, text: str, images: list[CachedInputImage]) -> str:
        if not images:
            return text

        paths = [str(image.path.expanduser().resolve(strict=False)) for image in images]
        result = text.strip()
        if result:
            return f"{result}\n{chr(10).join(paths)}"
        return "\n".join(paths)

    def cleanup(self, retention_minutes: int) -> None:
        cutoff = time.time() - max(1, retention_minutes) * 60
        for path in self.upload_dir.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _extract_images(self, event: Any) -> list[Comp.Image]:
        images: list[Comp.Image] = []
        for component in self._get_messages(event):
            if isinstance(component, Comp.Image):
                images.append(component)
                continue
            if self.include_replies and isinstance(component, Comp.Reply):
                for reply_component in getattr(component, "chain", None) or []:
                    if isinstance(reply_component, Comp.Image):
                        images.append(reply_component)
        return images

    @staticmethod
    def _get_messages(event: Any) -> list[Any]:
        try:
            if hasattr(event, "get_messages"):
                return list(event.get_messages() or [])
        except Exception:
            pass
        return list(getattr(getattr(event, "message_obj", None), "message", []) or [])

    async def _cache_image(self, image: Comp.Image) -> CachedInputImage:
        source = Path(await image.convert_to_file_path()).expanduser().resolve(strict=False)
        if not source.exists() or not source.is_file():
            raise ValueError(f"图片文件不存在: {source}")

        size = source.stat().st_size
        if size > self.max_file_size:
            raise ValueError(f"图片过大: {source} ({size} bytes)")

        image_format = self._detect_format(source)
        extension = IMAGE_EXTENSION_BY_FORMAT.get(image_format, source.suffix.lower() or ".img")
        target = self.upload_dir / f"input_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}{extension}"
        shutil.copy2(source, target)
        return CachedInputImage(path=target.resolve(strict=False), format=image_format, size_bytes=size)

    @staticmethod
    def _detect_format(path: Path) -> str:
        with PILImage.open(path) as image:
            image.verify()
            return str(image.format or "").upper()
