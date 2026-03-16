"""
cortex41 Desktop Controller — pyautogui-based system-level input.

Replaces the Playwright-only action executor with full desktop control:
  - click / scroll / type / key  → pyautogui (works on any window, any app)
  - navigate                     → Playwright page.goto() (reliable URL loading)

Coordinates are in logical pixels (match pyautogui.size() and screen captures
produced by screen_capture.py after Retina scaling).

macOS permissions required:
  - Accessibility: System Settings → Privacy & Security → Accessibility → add Terminal
  - Screen Recording: same location → Screen & System Audio Recording (for mss)
"""

import asyncio
import platform
import subprocess
import sys

import pyautogui

from backend.browser.browser_controller import Cortex41BrowserController
from backend.config import INTERACTION_TIMEOUT_DEFAULT_MS

# Prevent accidental abort if mouse hits a corner (keep True for safety during dev)
pyautogui.FAILSAFE = False
# Small inter-action pause (prevents races on slow UI)
pyautogui.PAUSE = 0.03

_IS_MACOS = platform.system() == "Darwin"


class DesktopController:
    """
    Executes agent actions via pyautogui (system-level) + Playwright (URL navigation).
    Drop-in replacement for Cortex41ActionExecutor.
    """

    def __init__(self, browser: Cortex41BrowserController):
        self.browser = browser

    async def execute(self, action: dict) -> dict:
        action_type = action.get("type", "wait")
        try:
            if action_type == "click":
                await self._click(action)
            elif action_type == "type":
                await self._type(action)
            elif action_type == "scroll":
                await self._scroll(action)
            elif action_type == "navigate":
                await self._navigate(action)
            elif action_type == "key":
                await self._key(action)
            elif action_type == "wait":
                await self._wait(action)
            elif action_type in ("done", "stuck"):
                pass
            else:
                return {"success": False, "error": f"Unknown action type: {action_type}"}

            return {"success": True, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    async def _click(self, action: dict):
        x = int(action.get("x", 0))
        y = int(action.get("y", 0))
        button = action.get("button", "left")
        double = action.get("double", False)

        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.15)
        await asyncio.sleep(0.05)

        if double:
            await asyncio.to_thread(pyautogui.doubleClick, x, y, button=button)
        else:
            await asyncio.to_thread(pyautogui.click, x, y, button=button)

        await asyncio.sleep(0.15)

    async def _type(self, action: dict):
        x = action.get("x")
        y = action.get("y")
        text = str(action.get("text", ""))
        submit = action.get("submit", False)
        slowly = action.get("slowly", False)

        # Click into the target field first
        if x is not None and y is not None:
            await asyncio.to_thread(pyautogui.click, int(x), int(y))
            await asyncio.sleep(0.15)

        # Select all existing text before typing
        if _IS_MACOS:
            await asyncio.to_thread(pyautogui.hotkey, "command", "a")
        else:
            await asyncio.to_thread(pyautogui.hotkey, "ctrl", "a")
        await asyncio.sleep(0.05)

        # Use clipboard paste for reliable unicode support
        await asyncio.to_thread(_paste_text, text)
        await asyncio.sleep(0.1)

        if submit:
            await asyncio.to_thread(pyautogui.press, "enter")
            await asyncio.sleep(0.5)

    async def _scroll(self, action: dict):
        x = int(action.get("x", 0)) or _screen_center()[0]
        y = int(action.get("y", 0)) or _screen_center()[1]
        direction = action.get("direction", "down")
        amount = int(action.get("amount", 3))

        # pyautogui scroll: positive = up, negative = down
        clicks = -amount if direction == "down" else amount
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.1)
        await asyncio.to_thread(pyautogui.scroll, clicks, x=x, y=y)
        await asyncio.sleep(0.2)

    async def _navigate(self, action: dict):
        """Navigate the Playwright browser to a URL and bring it to focus."""
        url = str(action.get("url", ""))
        if not url:
            raise ValueError("navigate action requires 'url'")

        page = await self.browser._ensure_page()
        timeout = int(action.get("timeout_ms", 30_000))
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        # Give page time to render, then focus the browser window
        await asyncio.sleep(1.0)
        await _focus_browser(page)

    async def _key(self, action: dict):
        key_str = str(action.get("key", ""))
        if not key_str:
            raise ValueError("key action requires 'key'")

        keys = _map_key_combo(key_str)
        if len(keys) == 1:
            await asyncio.to_thread(pyautogui.press, keys[0])
        else:
            await asyncio.to_thread(pyautogui.hotkey, *keys)

    async def _wait(self, action: dict):
        duration_ms = int(action.get("duration_ms", 1000))
        await asyncio.sleep(duration_ms / 1000)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _paste_text(text: str):
    """Write text via clipboard — handles unicode on macOS and Linux."""
    try:
        if _IS_MACOS:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            pyautogui.hotkey("command", "v")
        else:
            # Linux: use xclip or xdotool
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=True,
            )
            pyautogui.hotkey("ctrl", "v")
    except Exception:
        # Fallback: direct typewrite (ASCII only, no unicode)
        pyautogui.write(text, interval=0.04)


def _screen_center() -> tuple[int, int]:
    w, h = pyautogui.size()
    return w // 2, h // 2


def _map_key_combo(key_str: str) -> list[str]:
    """
    Convert Playwright-style key names ('Enter', 'Control+a') to
    pyautogui key names, with macOS Command substitution.
    """
    # Split combos like "Control+Shift+a"
    parts = key_str.split("+")

    mapping = {
        "control": "command" if _IS_MACOS else "ctrl",
        "ctrl": "command" if _IS_MACOS else "ctrl",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "escape": "escape",
        "esc": "escape",
        "backspace": "backspace",
        "delete": "delete",
        "arrowup": "up",
        "arrowdown": "down",
        "arrowleft": "left",
        "arrowright": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "shift": "shift",
        "alt": "alt",
        "meta": "command",
        "command": "command",
    }

    result = []
    for part in parts:
        lower = part.lower()
        result.append(mapping.get(lower, lower))
    return result


async def _focus_browser(page):
    """Bring the Playwright browser window to the foreground."""
    try:
        await page.bring_to_front()
    except Exception:
        pass
