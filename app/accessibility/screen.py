"""Screenshots and OCR (screen reading).

OCR uses the built-in Windows OCR engine (no extra install) and falls back to
Tesseract if it is installed.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageGrab

from app.config import get_config
from app.logger import audit, get_logger

log = get_logger(__name__)

TESSERACT_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def capture(region: tuple[int, int, int, int] | None = None, window_only: bool = False) -> Image.Image:
    """Grab the whole screen (all monitors), a (left, top, right, bottom) region, or the active window."""
    if window_only and region is None:
        region = active_window_box()
    return ImageGrab.grab(bbox=region, all_screens=region is None)


def active_window_box() -> tuple[int, int, int, int] | None:
    try:
        import pygetwindow

        w = pygetwindow.getActiveWindow()
        if w and w.width > 50 and w.height > 50:
            return (max(0, w.left), max(0, w.top), w.left + w.width, w.top + w.height)
    except Exception:
        pass
    return None


def save_screenshot(image: Image.Image | None = None, name: str = "") -> Path:
    cfg = get_config()
    image = image or capture()
    folder = cfg.dir(cfg.storage.screenshots_dir)
    path = folder / f"{name or 'screen'}-{datetime.now():%Y%m%d-%H%M%S}.png"
    image.save(path)
    audit("screenshot", path=str(path))
    return path


# ---- OCR --------------------------------------------------------------

async def _windows_ocr_result(image: Image.Image):
    """Run Windows OCR; returns (result, scale) where scale maps OCR coordinates back to the image."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("No Windows OCR language installed")
    # Windows OCR has a max dimension; downscale very large captures
    limit = OcrEngine.max_image_dimension
    scale = 1.0
    if max(image.size) > limit:
        scale = max(image.size) / limit
        image = image.copy()
        image.thumbnail((limit, limit))
    rgba = image.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, *rgba.size)
    return await engine.recognize_async(bitmap), scale


async def _windows_ocr(image: Image.Image) -> str:
    result, _ = await _windows_ocr_result(image)
    return "\n".join(line.text for line in result.lines)


def ocr_lines(image: Image.Image) -> list[tuple[str, tuple[int, int, int, int]]]:
    """OCR each text line with its (left, top, right, bottom) box in image pixels."""
    result, scale = asyncio.run(_windows_ocr_result(image))
    lines = []
    for line in result.lines:
        rects = [w.bounding_rect for w in line.words]
        if not rects:
            continue
        box = (min(r.x for r in rects), min(r.y for r in rects),
               max(r.x + r.width for r in rects), max(r.y + r.height for r in rects))
        lines.append((line.text, tuple(int(v * scale) for v in box)))
    return lines


def _tesseract_ocr(image: Image.Image) -> str:
    import pytesseract

    exe = shutil.which("tesseract") or next((p for p in TESSERACT_PATHS if Path(p).exists()), None)
    if not exe:
        raise RuntimeError("Tesseract is not installed")
    pytesseract.pytesseract.tesseract_cmd = exe
    return pytesseract.image_to_string(image)


def ocr(image: Image.Image) -> str:
    errors = []
    try:
        return asyncio.run(_windows_ocr(image)).strip()
    except Exception as e:
        errors.append(f"Windows OCR: {e}")
    try:
        return _tesseract_ocr(image).strip()
    except Exception as e:
        errors.append(f"Tesseract: {e}")
    log.warning("OCR unavailable: %s", "; ".join(errors))
    raise RuntimeError("Screen reading is unavailable: " + "; ".join(errors))


def read_screen(window_only: bool = True) -> str:
    """OCR the active window (or whole screen) and return the text."""
    text = ocr(capture(window_only=window_only))
    audit("read_screen", chars=len(text))
    return text
