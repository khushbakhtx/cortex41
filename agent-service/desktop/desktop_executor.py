"""
cortex41 Desktop Action Executor.

Translates action dicts from the vision model into system-level
mouse/keyboard operations via DesktopScreenController.

Action types:
  click      — left/right/double click at (x, y)
  type       — type text (optionally slow, submit=Enter)
  key        — keyboard press / hotkey
  scroll     — scroll at (x, y)
  open_app   — open a macOS application by name
  open_url   — open a URL in the default browser (most reliable for web navigation)
  wait       — pause for duration_ms
  done/stuck — terminal states (no-op)
"""

import asyncio
import base64
import io

from backend.desktop.screen_controller import DesktopScreenController


class DesktopActionExecutor:
    def __init__(self, screen: DesktopScreenController):
        self.screen = screen

    async def execute(self, action: dict) -> dict:
        action_type = action.get("type", "wait")

        try:
            if action_type == "click":
                await self._click(action)
            elif action_type == "type":
                await self._type(action)
            elif action_type == "key":
                await self._key(action)
            elif action_type == "scroll":
                await self._scroll(action)
            elif action_type == "open_app":
                await self._open_app(action)
            elif action_type == "open_url":
                # Use atomic open+capture: Chrome activation and screencapture happen
                # inside ONE executor call so the asyncio event loop never yields
                # between them (preventing macOS from switching back to Terminal's Space).
                url = str(action.get("url", ""))
                if url:
                    screenshot = await self.screen.open_url_and_capture(url)
                else:
                    await self._open_url(action)
                    screenshot = await self.screen.get_screenshot_base64(grid=False)
                return {"success": True, "error": None, "screenshot_after": screenshot}
            elif action_type == "wait":
                await asyncio.sleep(action.get("duration_ms", 1000) / 1000)
            elif action_type in ("done", "stuck"):
                pass
            else:
                return {
                    "success": False,
                    "error": f"Unknown action type: {action_type}",
                    "screenshot_after": "",
                }

    # Let screen settle before screenshot.
            settle = 0.6 if action_type == "open_app" else 0.35
            await asyncio.sleep(settle)
            screenshot = await self.screen.get_screenshot_base64(grid=False)
            result = {"success": True, "error": None, "screenshot_after": screenshot}

            if action_type == "click":
                x = int(action.get("x", 0))
                y = int(action.get("y", 0))
                result["click_annotated"] = _annotate_click(screenshot, x, y)

            return result

        except Exception as e:
            screenshot = await self._safe_screenshot()
            return {"success": False, "error": str(e)[:300], "screenshot_after": screenshot}

    async def _click(self, action: dict):
        x = int(action.get("x", 0))
        y = int(action.get("y", 0))
        button = action.get("button", "left")
        double = bool(action.get("double", False))
        if button == "right":
            await self.screen.right_click(x, y)
        else:
            await self.screen.click(x, y, button=button, double=double)

    async def _type(self, action: dict):
        text = str(action.get("text", ""))
        slowly = bool(action.get("slowly", False))
        submit = bool(action.get("submit", False))

        # If target coords given, click first to focus
        x, y = action.get("x"), action.get("y")
        if x is not None and y is not None:
            await self.screen.click(int(x), int(y))
            await asyncio.sleep(0.15)

        # Select all existing text then replace
        await self.screen.key_press("command+a")
        await asyncio.sleep(0.05)
        await self.screen.type_text(text, slowly=slowly)

        if submit:
            await asyncio.sleep(0.1)
            await self.screen.key_press("return")

    async def _key(self, action: dict):
        key = str(action.get("key", ""))
        if key:
            await self.screen.key_press(key)

    async def _scroll(self, action: dict):
        x = int(action.get("x", self.screen.screen_width // 2))
        y = int(action.get("y", self.screen.screen_height // 2))
        direction = action.get("direction", "down")
        amount = int(action.get("amount", 3))
        await self.screen.scroll(x, y, direction=direction, amount=amount)

    async def _open_app(self, action: dict):
        app = str(action.get("app", ""))
        if app:
            await self.screen.open_app(app)

    async def _open_url(self, action: dict):
        url = str(action.get("url", ""))
        if url:
            await self.screen.open_url(url)

    async def _safe_screenshot(self) -> str:
        try:
            return await self.screen.get_screenshot_base64(grid=False)
        except Exception:
            return ""


def _annotate_click(screenshot_b64: str, x: int, y: int) -> str:
    """Draw a red crosshair at (x, y) so the model can verify where it clicked."""
    try:
        from PIL import ImageDraw
        img = __import__("PIL.Image", fromlist=["Image"]).Image.open(
            io.BytesIO(base64.b64decode(screenshot_b64))
        ).convert("RGB")
        draw = ImageDraw.Draw(img)
        r = 16
        draw.ellipse([x - r, y - r, x + r, y + r], outline="red", width=3)
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill="red")
        draw.line([x - r - 6, y, x - r, y], fill="red", width=2)
        draw.line([x + r, y, x + r + 6, y], fill="red", width=2)
        draw.line([x, y - r - 6, x, y - r], fill="red", width=2)
        draw.line([x, y + r, x, y + r + 6], fill="red", width=2)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return screenshot_b64
