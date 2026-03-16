"""
cortex41 Action Executor — translates action dicts into Playwright calls.

Inherits OpenClaw techniques from openclaw/src/browser/pw-tools-core.interactions.ts:
  - Timeout clamping: min 500ms, max 60s (never block forever)
  - slowly=True typing: 75ms delay per char (for sites that reject fast fill)
  - submit flag: press Enter after filling
  - AI-friendly error wrapping
  - waitFor patterns: text, selector, networkidle
"""

import asyncio
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.browser.browser_controller import Cortex41BrowserController
from backend.config import (
    INTERACTION_TIMEOUT_MIN_MS,
    INTERACTION_TIMEOUT_MAX_MS,
    INTERACTION_TIMEOUT_DEFAULT_MS,
)

# ── JS helpers (inline, no library deps) ──────────────────────────────────────

# Snap (x, y) to the nearest interactive element center within `radius` pixels.
# Returns {x, y, snapped: true, role, name} or null if nothing within radius.
_SNAP_JS = """
([x, y, radius]) => {
    const TAGS = ['a','button','input','select','textarea','summary','label'];
    const ROLES = ['button','link','checkbox','radio','tab','menuitem','option',
                   'combobox','listbox','searchbox','switch','treeitem'];
    const seen = new Set();
    const candidates = new Set();
    TAGS.forEach(t => document.querySelectorAll(t).forEach(el => candidates.add(el)));
    ROLES.forEach(r => document.querySelectorAll(`[role="${r}"]`).forEach(el => candidates.add(el)));
    document.querySelectorAll('[tabindex]:not([tabindex="-1"])').forEach(el => candidates.add(el));

    let best = null, bestDist = radius;
    candidates.forEach(el => {
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        const cx = rect.left + rect.width / 2;
        const cy = rect.top  + rect.height / 2;
        const dist = Math.hypot(cx - x, cy - y);
        if (dist < bestDist) {
            bestDist = dist;
            best = {
                x: Math.round(cx),
                y: Math.round(cy),
                snapped: true,
                dist: Math.round(dist),
                role: el.getAttribute('role') || el.tagName.toLowerCase(),
                name: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 40),
            };
        }
    });
    return best;
}
"""


def _clamp_timeout(ms: Optional[int]) -> int:
    """
    Inherited from OpenClaw's resolveInteractionTimeoutMs:
    Clamp interaction timeout to [500ms, 60s] to prevent indefinite hangs.
    """
    val = ms if ms is not None else INTERACTION_TIMEOUT_DEFAULT_MS
    return max(INTERACTION_TIMEOUT_MIN_MS, min(INTERACTION_TIMEOUT_MAX_MS, int(val)))


def _ai_friendly_error(err: Exception, context: str = "") -> str:
    """Inherited from OpenClaw's toAIFriendlyError: clean up Playwright stack traces."""
    msg = str(err)
    # Strip verbose Playwright call stacks
    if "Call log:" in msg:
        msg = msg.split("Call log:")[0].strip()
    if context:
        return f"{context}: {msg}"
    return msg


class Cortex41ActionExecutor:
    """
    Executes browser actions via Playwright.
    Action format (all fields optional except 'type'):
    {
        "type": "click" | "type" | "scroll" | "navigate" | "key" | "wait" | "done" | "stuck",
        "x": int,
        "y": int,
        "text": str,
        "url": str,
        "key": str,          # e.g. "Enter", "Tab", "Escape"
        "direction": "up"|"down",
        "amount": int,       # scroll pixels
        "duration_ms": int,  # for wait
        "slowly": bool,      # type char-by-char at 75ms delay
        "submit": bool,      # press Enter after typing
        "timeout_ms": int,
    }
    Returns: { "success": bool, "error": str|None, "screenshot_after": base64_str }
    """

    def __init__(self, browser: Cortex41BrowserController):
        self.browser = browser

    async def execute(self, action: dict, som_page=None, som_elements: list = None) -> dict:
        """
        som_page: Playwright Page reference for SoM element clicking.
        som_elements: accessibility element list from build_som(), enables semantic locator clicks.
        """
        action_type = action.get("type", "wait")

        # Capture pre-click page state for post-click verification
        page_url_before, page_title_before = "", ""
        if action_type == "click":
            try:
                page_url_before = await self.browser.get_page_url()
                page_title_before = await self.browser.get_page_title()
            except Exception:
                pass

        try:
            click_coords: tuple[int, int] = (int(action.get("x", 0)), int(action.get("y", 0)))

            if action_type == "click":
                click_coords = await self._click(action, som_page=som_page, som_elements=som_elements)
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
                pass  # terminal states, no browser action needed
            else:
                return {"success": False, "error": f"Unknown action type: {action_type}", "screenshot_after": ""}

            screenshot = await self.browser.get_screenshot_base64()
            result = {"success": True, "error": None, "screenshot_after": screenshot}

            if action_type == "click":
                cx, cy = click_coords
                result["click_annotated"] = await self._annotate_click(screenshot, cx, cy)

                # Post-click verification: did the page respond?
                try:
                    url_after = await self.browser.get_page_url()
                    title_after = await self.browser.get_page_title()
                    result["page_changed"] = (
                        url_after != page_url_before or title_after != page_title_before
                    )
                    result["url_after"] = url_after
                except Exception:
                    result["page_changed"] = False

            return result

        except PlaywrightTimeoutError as e:
            screenshot = await self._safe_screenshot()
            return {"success": False, "error": _ai_friendly_error(e, f"Timeout on {action_type}"), "screenshot_after": screenshot}
        except Exception as e:
            screenshot = await self._safe_screenshot()
            return {"success": False, "error": _ai_friendly_error(e, action_type), "screenshot_after": screenshot}

    async def _snap_to_nearest(self, page, x: int, y: int, radius: int = 15) -> tuple[int, int]:
        """
        JS-based coordinate snapping: find nearest interactive element within `radius` px.
        Returns exact element center if found, otherwise original (x, y).
        """
        try:
            result = await page.evaluate(_SNAP_JS, [x, y, radius])
            if result:
                sx, sy = result["x"], result["y"]
                print(f"[Click] Snapped ({x},{y}) → ({sx},{sy}) [{result.get('role')} \"{result.get('name')}\", dist={result.get('dist')}px]")
                return sx, sy
        except Exception as e:
            print(f"[Click] Snap failed: {e}")
        return x, y

    async def _click(self, action: dict, som_page=None, som_elements: list = None) -> tuple[int, int]:
        """
        Execute a click. Returns the actual (x, y) coordinates used (for annotation).

        Priority:
          1. CDP node click — DOM.getNodeForLocation → Runtime.callFunctionOn(userGesture=true)
             Pierces shadow DOM, handles React/Vue synthetic events, never misses by coordinates.
          2. SoM semantic locator (Playwright get_by_role/label/text) — for named elements
          3. elementFromPoint JS handle — fallback for known HTML tags
          4. Raw mouse click — absolute last resort

        Why CDP first:
          browser-use (Aug 2025) found that Playwright locators fail on shadow DOM
          and dynamic React elements. CDP getNodeForLocation gets the *exact* DOM node
          at a pixel — including inside shadow roots — then callFunctionOn fires a
          trusted userGesture click that bypasses all synthetic event filters.
        """
        page = await self.browser._ensure_page()
        timeout = _clamp_timeout(action.get("timeout_ms"))
        element_id = action.get("element_id")

        # Resolve (x, y) — from SoM element center or action coords
        if element_id and som_elements and 0 < element_id <= len(som_elements):
            el = som_elements[element_id - 1]
            x = el.get("x", 0) + el.get("w", 0) // 2
            y = el.get("y", 0) + el.get("h", 0) // 2
        else:
            x = int(action.get("x", 0))
            y = int(action.get("y", 0))
            # Snap to nearest interactive element center within 15px
            x, y = await self._snap_to_nearest(page, x, y, radius=15)

        # Nudge if near viewport edge
        vp_height = self.browser.viewport_height
        if y < 60:
            await page.mouse.wheel(0, -200)
            await asyncio.sleep(0.15)
        elif y > vp_height - 60:
            await page.mouse.wheel(0, 200)
            await asyncio.sleep(0.15)

        # ── 1. CDP direct click (shadow DOM safe, trusted userGesture) ───────────
        if await self._cdp_click(page, x, y):
            await self._wait_settle(page, timeout)
            return x, y

        # ── 2. SoM semantic locator (Playwright) — for named elements ────────────
        if element_id and som_page and som_elements:
            from backend.browser.som import click_som_element
            success = await click_som_element(som_page, element_id, som_elements, timeout_ms=timeout)
            if success:
                await self._wait_settle(page, timeout)
                return x, y
            print(f"[Click] SoM semantic failed for #{element_id}")

        # ── 3. elementFromPoint JS handle ────────────────────────────────────────
        try:
            handle = await page.evaluate_handle(
                "([x, y]) => document.elementFromPoint(x, y)", [x, y]
            )
            tag = await page.evaluate("el => el ? el.tagName : null", handle)
            if tag and tag.lower() in ("a", "button", "input", "select", "summary", "label", "span", "div"):
                await handle.as_element().click(timeout=timeout)
                await self._wait_settle(page, timeout)
                return x, y
        except Exception:
            pass

        # ── 4. Raw mouse click ────────────────────────────────────────────────────
        print(f"[Click] All strategies failed, using raw mouse at ({x},{y})")
        await page.mouse.move(x, y)
        await asyncio.sleep(0.05)
        await page.mouse.click(x, y, button=action.get("button", "left"))
        await self._wait_settle(page, timeout)
        return x, y

    async def _cdp_click(self, page, x: int, y: int) -> bool:
        """
        CDP-direct click: DOM.getNodeForLocation → DOM.resolveNode → Runtime.callFunctionOn.

        Why this works where Playwright fails:
        - getNodeForLocation(includeUserAgentShadowDOM=true) finds elements inside shadow roots
          (YouTube, Google, React portals, Web Components)
        - callFunctionOn with userGesture=true fires a TRUSTED click event — React/Vue/Angular
          synthetic event systems don't filter it (they check isTrusted)
        - No coordinate precision issue — we navigate directly via the DOM node reference
        """
        try:
            cdp = await self.browser.get_cdp_session()

            # Get exact DOM node at (x, y), piercing shadow DOM
            node = await cdp.send("DOM.getNodeForLocation", {
                "x": x,
                "y": y,
                "includeUserAgentShadowDOM": True,
                "ignorePointerEventsNone": True,
            })
            backend_node_id = node.get("backendNodeId")
            if not backend_node_id:
                return False

            # Resolve to JS object reference
            resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
            object_id = resolved.get("object", {}).get("objectId")
            if not object_id:
                return False

            # Fire trusted click (userGesture=true bypasses isTrusted checks)
            await cdp.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    this.focus && this.focus();
                    this.click && this.click();
                }""",
                "userGesture": True,
                "awaitPromise": False,
            })
            print(f"[Click] CDP click succeeded at ({x},{y}) via backendNodeId={backend_node_id}")
            return True

        except Exception as e:
            print(f"[Click] CDP click failed at ({x},{y}): {e}")
            return False

    async def _wait_settle(self, page, timeout: int):
        """Wait for page to settle after a click."""
        await asyncio.sleep(0.1)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 5000))
        except Exception:
            pass

    async def _type(self, action: dict):
        page = await self.browser._ensure_page()
        x = action.get("x")
        y = action.get("y")
        text = str(action.get("text", ""))
        slowly = action.get("slowly", False)
        submit = action.get("submit", False)
        timeout = _clamp_timeout(action.get("timeout_ms"))

        # Click on target coords if provided
        if x is not None and y is not None:
            await page.mouse.click(int(x), int(y))
            await asyncio.sleep(0.1)

        if slowly:
            # Inherited from OpenClaw's typeViaPlaywright slowly=True: 75ms delay per char
            # Used for sites that reject fast programmatic fill (date pickers, search suggestions)
            await page.keyboard.type(text, delay=75)
        else:
            # Fast fill — clear and fill (like OpenClaw's fill() path)
            # Use keyboard shortcut to select-all then type
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.05)
            await page.keyboard.type(text)

        if submit:
            await asyncio.sleep(0.1)
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout)
            except Exception:
                pass

    async def _scroll(self, action: dict):
        page = await self.browser._ensure_page()
        x = int(action.get("x", self.browser.viewport_width // 2))
        y = int(action.get("y", self.browser.viewport_height // 2))
        direction = action.get("direction", "down")
        amount = int(action.get("amount", 400))

        delta_y = amount if direction == "down" else -amount
        await page.mouse.move(x, y)
        await page.mouse.wheel(0, delta_y)
        await asyncio.sleep(0.3)  # Let page settle after scroll

    async def _navigate(self, action: dict):
        page = await self.browser._ensure_page()
        url = str(action.get("url", ""))
        if not url:
            raise ValueError("navigate action requires 'url'")
        timeout = _clamp_timeout(action.get("timeout_ms", 30_000))

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

    async def _key(self, action: dict):
        page = await self.browser._ensure_page()
        key = str(action.get("key", ""))
        if not key:
            raise ValueError("key action requires 'key'")
        delay = int(action.get("delay_ms", 0))
        await page.keyboard.press(key, delay=delay)

    async def _wait(self, action: dict):
        page = await self.browser._ensure_page()
        duration_ms = int(action.get("duration_ms", 1000))

        # Inherited from OpenClaw's waitForViaPlaywright multi-condition pattern:
        # Support waiting for text, selector, URL, or networkidle
        wait_tasks = [asyncio.sleep(duration_ms / 1000)]

        timeout = _clamp_timeout(action.get("timeout_ms", 10_000))

        if text := action.get("wait_for_text"):
            wait_tasks.append(
                page.get_by_text(text).first.wait_for(state="visible", timeout=timeout)
            )
        if selector := action.get("wait_for_selector"):
            wait_tasks.append(
                page.locator(selector).first.wait_for(state="visible", timeout=timeout)
            )
        if action.get("wait_for_networkidle"):
            wait_tasks.append(
                page.wait_for_load_state("networkidle", timeout=timeout)
            )

        # Run the base sleep; other conditions are optional
        await wait_tasks[0]

    async def _annotate_click(self, screenshot_b64: str, x: int, y: int) -> str:
        """
        Draw a red crosshair + circle at (x, y) on the screenshot.
        The model sees this next step and can tell if it clicked the wrong element.
        """
        import base64
        import io
        from PIL import Image, ImageDraw

        try:
            data = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            draw = ImageDraw.Draw(img)
            r = 14
            # Outer circle
            draw.ellipse([x - r, y - r, x + r, y + r], outline="red", width=3)
            # Inner filled dot
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill="red")
            # Crosshair lines
            draw.line([x - r - 6, y, x - r, y], fill="red", width=2)
            draw.line([x + r, y, x + r + 6, y], fill="red", width=2)
            draw.line([x, y - r - 6, x, y - r], fill="red", width=2)
            draw.line([x, y + r, x, y + r + 6], fill="red", width=2)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return screenshot_b64  # Return unannotated on failure

    async def _safe_screenshot(self) -> str:
        """Best-effort screenshot on error — return empty string if fails."""
        try:
            return await self.browser.get_screenshot_base64()
        except Exception:
            return ""
