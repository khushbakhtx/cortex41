"""
Full-desktop screen capture for cortex41.

Uses mss to grab the primary monitor at native resolution, then scales down
to logical pixel resolution so that coordinates match pyautogui's coordinate
space (handles macOS Retina 2x displays).

Always returns base64-encoded JPEG for consistent frontend handling.
"""

import io
import base64

import mss
import pyautogui
from PIL import Image, ImageDraw

from backend.config import SCREENSHOT_MAX_SIDE, SCREENSHOT_MAX_BYTES


def capture_screen_base64() -> str:
    """
    Capture the entire primary monitor.
    Returns base64-encoded JPEG, scaled to logical pixel resolution.
    """
    logical_w, logical_h = pyautogui.size()

    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary monitor (0 = all monitors combined)
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # Scale to logical resolution (handles Retina 2x — physical 2560 → logical 1280)
    if img.size != (logical_w, logical_h):
        img = img.resize((logical_w, logical_h), Image.LANCZOS)

    return _encode_jpeg(img)


def highlight_at(screenshot_b64: str, x: int, y: int, radius: int = 20) -> str:
    """
    Draw a red highlight circle at (x, y) on an existing screenshot.
    Returns the modified screenshot as base64 JPEG.
    """
    img_data = base64.b64decode(screenshot_b64)
    img = Image.open(io.BytesIO(img_data)).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.ellipse(
        [x - radius, y - radius, x + radius, y + radius],
        outline="red",
        width=3,
    )
    inner = radius // 3
    draw.ellipse(
        [x - inner, y - inner, x + inner, y + inner],
        fill="red",
    )

    return _encode_jpeg(img)


def _encode_jpeg(img: Image.Image) -> str:
    """Resize if needed, compress to JPEG within size limits, return base64."""
    width, height = img.size
    max_dim = max(width, height)

    if max_dim > SCREENSHOT_MAX_SIDE:
        scale = SCREENSHOT_MAX_SIDE / max_dim
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    if img.mode != "RGB":
        img = img.convert("RGB")

    for quality in [90, 75, 60, 45, 30]:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        out = buf.getvalue()
        if len(out) <= SCREENSHOT_MAX_BYTES:
            return base64.b64encode(out).decode("utf-8")

    return base64.b64encode(out).decode("utf-8")
