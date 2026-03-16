"""
cortex41 Browser Agent — Playwright-native execution mode.

For ALL web/browser tasks, this replaces the DesktopScreenController approach.

Why this is vastly more reliable than pyautogui:
- Screenshots come from the BROWSER VIEWPORT (fixed 1280x800) — no DPI issues,
  no macOS Space fighting, no wrong-window captures.
- Actions use CDP direct click (trustedUserGesture, pierces shadow DOM) or
  Playwright semantic locators (get_by_role, get_by_label, get_by_text) —
  no pixel coordinate guesswork.
- ARIA tree gives Gemini structural context about the page — enables "click the
  search box" instead of "click at (640, 180)".
- Browser owns its Space — we never fight Mission Control.
"""

import asyncio
import base64
import io
import json
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from backend.browser.browser_controller import Cortex41BrowserController
from backend.browser.action_executor import Cortex41ActionExecutor


_GRID_SPACING = 100  # px — tighter grid for browser viewport (1280x800)


def _draw_grid(img: Image.Image) -> Image.Image:
    """Coordinate grid overlay for browser viewport screenshots."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()

    line_color = (100, 100, 100, 100)
    label_bg = (0, 0, 0)
    label_fg = (255, 255, 255)

    for x in range(0, w, _GRID_SPACING):
        draw.line([(x, 0), (x, h)], fill=line_color, width=1)
        bbox = draw.textbbox((x + 2, 2), str(x), font=font)
        draw.rectangle(bbox, fill=label_bg)
        draw.text((x + 2, 2), str(x), fill=label_fg, font=font)

    for y in range(0, h, _GRID_SPACING):
        draw.line([(0, y), (w, y)], fill=line_color, width=1)
        bbox = draw.textbbox((2, y + 2), str(y), font=font)
        draw.rectangle(bbox, fill=label_bg)
        draw.text((2, y + 2), str(y), fill=label_fg, font=font)

    return img


def _serialize_aria(node: dict, depth: int = 0, max_depth: int = 6) -> str:
    """
    Serialize Playwright accessibility snapshot to compact human-readable text.

    Example output:
      [main]
        [searchbox] "Search YouTube"
        [button] "Search"
        [link] "Home"
        [link] "Trending"
        [heading] "Recommended videos" level=2
          [article] "Trump speech latest 2025 · 2.1M views"
    """
    if not node or depth > max_depth:
        return ""

    role = node.get("role", "")
    name = (node.get("name") or "").strip()[:80]
    value = (node.get("value") or "").strip()[:60]
    checked = node.get("checked")
    level = node.get("level")
    expanded = node.get("expanded")

    # Skip purely structural noise
    SKIP_ROLES = {"none", "presentation", "generic", "img", "separator",
                  "group", "region", "complementary"}
    if role in SKIP_ROLES and not name:
        # Still recurse into children
        children = node.get("children") or []
        return "".join(_serialize_aria(c, depth, max_depth) for c in children)

    indent = "  " * depth
    parts = [f"[{role}]"]
    if name:
        parts.append(f'"{name}"')
    if value:
        parts.append(f"value={value!r}")
    if checked is not None:
        parts.append(f"checked={checked}")
    if level is not None:
        parts.append(f"level={level}")
    if expanded is not None:
        parts.append(f"expanded={expanded}")

    line = indent + " ".join(parts)
    lines = [line] if role not in ("", "none") else []

    children = node.get("children") or []
    for child in children[:30]:  # cap children per node to avoid massive trees
        child_text = _serialize_aria(child, depth + 1, max_depth)
        if child_text:
            lines.append(child_text)

    return "\n".join(lines)


class BrowserAgent:
    """
    Self-contained browser execution mode using Playwright.

    Lifecycle:
        agent = BrowserAgent()
        await agent.launch()
        screenshot, aria = await agent.get_screenshot_and_context()
        result = await agent.execute(action)
        await agent.close()
    """

    def __init__(self, headless: bool = False):
        # headless=False so the browser is visible during demos.
        # For Cloud Run deployment, set headless=True.
        self.controller = Cortex41BrowserController(
            headless=headless,
            viewport_width=1280,
            viewport_height=800,
        )
        self.executor = Cortex41ActionExecutor(self.controller)
        self._launched = False

    async def launch(self):
        await self.controller.launch()
        self._launched = True
        print("[BrowserAgent] Playwright browser launched (viewport 1280x800)")

    async def get_screenshot_and_context(self, grid: bool = True) -> tuple[str, str]:
        """
        Returns (screenshot_b64, aria_tree_text).

        screenshot_b64: browser viewport JPEG with coordinate grid.
                        Coordinates are exact — 1280x800, no DPI scaling needed.
        aria_tree_text: compact text of all interactive elements on the page.
                        Gemini uses this to pick semantic actions over coordinates.
        """
        screenshot_b64, aria_text = await asyncio.gather(
            self.controller.get_screenshot_base64(),
            self._get_aria_tree(),
        )

        if grid:
            screenshot_b64 = _add_grid_to_b64(screenshot_b64)

        return screenshot_b64, aria_text

    async def get_url(self) -> str:
        try:
            return await self.controller.get_page_url()
        except Exception:
            return ""

    async def get_title(self) -> str:
        try:
            return await self.controller.get_page_title()
        except Exception:
            return ""

    async def execute(self, action: dict) -> dict:
        """
        Execute an action. Supports both semantic browser actions and
        standard coordinate-based actions (via Cortex41ActionExecutor).

        Semantic actions (preferred for web tasks):
          browser_navigate  — goto URL directly via Playwright
          browser_click     — click by role/text/label (no coordinates)
          browser_type      — fill input by role/label

        Standard actions (fallback, coordinate-based):
          click, type, scroll, key, wait, navigate, done, stuck
        """
        action_type = action.get("type", "wait")

        try:
            if action_type == "browser_navigate":
                return await self._browser_navigate(action)
            elif action_type == "browser_click":
                return await self._browser_click(action)
            elif action_type == "browser_type":
                return await self._browser_type(action)
            else:
                # Delegate to standard executor (handles click via CDP, type, scroll, key, wait)
                return await self.executor.execute(action)
        except Exception as e:
            screenshot = await self._safe_screenshot()
            return {"success": False, "error": str(e)[:300], "screenshot_after": screenshot}

    async def _browser_navigate(self, action: dict) -> dict:
        """Navigate directly to a URL — fastest, most reliable web navigation."""
        url = action.get("url", "")
        if not url:
            return {"success": False, "error": "browser_navigate requires 'url'", "screenshot_after": ""}

        page = await self.controller._ensure_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Wait for the page to settle visually
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            screenshot = await self.controller.get_screenshot_base64()
            return {"success": True, "error": None, "screenshot_after": screenshot}
        except Exception as e:
            screenshot = await self._safe_screenshot()
            return {"success": False, "error": str(e)[:200], "screenshot_after": screenshot}

    async def _browser_click(self, action: dict) -> dict:
        """
        Click an element by semantic identity rather than coordinates.

        Resolution order:
          1. role + name (most precise: get_by_role)
          2. text content (get_by_text)
          3. label (get_by_label — best for form inputs)
          4. placeholder (get_by_placeholder — search boxes)
          5. CSS selector if provided
          6. Fallback to CDP coordinate click
        """
        page = await self.controller._ensure_page()
        role    = action.get("role", "").strip()
        name    = action.get("name", "").strip() or action.get("text", "").strip()
        label   = action.get("label", "").strip()
        placeholder = action.get("placeholder", "").strip()
        selector = action.get("selector", "").strip()
        x = action.get("x")
        y = action.get("y")
        double = bool(action.get("double", False))
        timeout = 6_000

        click_kwargs = {}
        if double:
            click_kwargs["click_count"] = 2

        errors = []
        locator = None

        # Priority 1: role + name
        if role and name:
            try:
                locator = page.get_by_role(role, name=name)
                await locator.first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"role+name: {e}")
                locator = None

        # Priority 2: role alone
        if role and not locator:
            try:
                locator = page.get_by_role(role)
                await locator.first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"role: {e}")
                locator = None

        # Priority 3: label
        if label:
            try:
                await page.get_by_label(label).first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"label: {e}")

        # Priority 4: placeholder
        if placeholder:
            try:
                await page.get_by_placeholder(placeholder).first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"placeholder: {e}")

        # Priority 5: text content
        if name and not locator:
            try:
                await page.get_by_text(name, exact=False).first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"text: {e}")

        # Priority 6: CSS selector
        if selector:
            try:
                await page.locator(selector).first.click(timeout=timeout, **click_kwargs)
                return await self._after_click(action, x, y)
            except Exception as e:
                errors.append(f"selector: {e}")

        # Priority 7: CDP coordinate click (last resort)
        if x is not None and y is not None:
            print(f"[BrowserAgent] Semantic click failed ({'; '.join(str(e)[:60] for e in errors)}), falling back to CDP click at ({x},{y})")
            success = await self.executor._cdp_click(page, int(x), int(y))
            return await self._after_click(action, int(x), int(y), fallback=not success)

        screenshot = await self._safe_screenshot()
        return {
            "success": False,
            "error": f"browser_click: no resolution strategy worked. Errors: {'; '.join(str(e)[:80] for e in errors)}",
            "screenshot_after": screenshot,
        }

    async def _browser_type(self, action: dict) -> dict:
        """
        Type text into an input, identified by role/label/placeholder.
        Much more reliable than click-then-type with coordinates.
        """
        page = await self.controller._ensure_page()
        text        = str(action.get("text", ""))
        role        = action.get("role", "searchbox").strip()
        label       = action.get("label", "").strip()
        placeholder = action.get("placeholder", "").strip()
        selector    = action.get("selector", "").strip()
        submit      = bool(action.get("submit", False))
        slowly      = bool(action.get("slowly", False))
        timeout     = 6_000

        locator = None
        errors  = []

        # Resolution order: label > placeholder > role > selector
        if label:
            try:
                locator = page.get_by_label(label).first
                await locator.click(timeout=timeout)
            except Exception as e:
                errors.append(f"label: {e}")
                locator = None

        if not locator and placeholder:
            try:
                locator = page.get_by_placeholder(placeholder).first
                await locator.click(timeout=timeout)
            except Exception as e:
                errors.append(f"placeholder: {e}")
                locator = None

        if not locator and role:
            try:
                locator = page.get_by_role(role).first
                await locator.click(timeout=timeout)
            except Exception as e:
                errors.append(f"role: {e}")
                locator = None

        if not locator and selector:
            try:
                locator = page.locator(selector).first
                await locator.click(timeout=timeout)
            except Exception as e:
                errors.append(f"selector: {e}")
                locator = None

        if locator:
            try:
                if slowly:
                    await locator.fill("", timeout=timeout)
                    await page.keyboard.type(text, delay=75)
                else:
                    await locator.fill(text, timeout=timeout)
            except Exception as e:
                errors.append(f"fill: {e}")
                # Try keyboard as fallback
                await page.keyboard.press("Control+a")
                await page.keyboard.type(text)
        else:
            # Fallback: just type with keyboard (assumes focus is already on an input)
            print(f"[BrowserAgent] browser_type: no locator found ({'; '.join(str(e)[:60] for e in errors)}), typing at keyboard")
            await page.keyboard.press("Control+a")
            if slowly:
                await page.keyboard.type(text, delay=75)
            else:
                await page.keyboard.type(text)

        if submit:
            await asyncio.sleep(0.1)
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

        await asyncio.sleep(0.5)
        screenshot = await self.controller.get_screenshot_base64()
        return {"success": True, "error": None, "screenshot_after": screenshot}

    async def _after_click(self, action: dict, x: int, y: int, fallback: bool = False) -> dict:
        """Post-click wait and screenshot."""
        try:
            page = await self.controller._ensure_page()
            await asyncio.sleep(0.1)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass
            await asyncio.sleep(0.4)
        except Exception:
            pass

        screenshot = await self.controller.get_screenshot_base64()
        result = {
            "success": not fallback,
            "error": None,
            "screenshot_after": screenshot,
        }
        # Annotate click position if we have coordinates
        if x is not None and y is not None:
            result["click_annotated"] = await self.executor._annotate_click(screenshot, x, y)
        return result

    async def _get_aria_tree(self) -> str:
        """
        Extract page accessibility tree as compact text.
        Gives Gemini structural knowledge of page elements without needing to
        visually locate them by pixel coordinates.

        In Playwright 1.47+, aria_snapshot() is on Locator (not Page directly).
        Use page.locator("body").aria_snapshot() to get the full page tree.
        """
        page = await self.controller._ensure_page()
        try:
            raw = await page.locator("body").aria_snapshot()
            if not raw:
                return ""
            # Truncate at line boundary
            if len(raw) > 3000:
                cutoff = raw.rfind("\n", 0, 3000)
                raw = raw[:cutoff if cutoff > 0 else 3000] + "\n... (truncated)"
            return raw
        except Exception as e:
            print(f"[BrowserAgent] ARIA snapshot failed: {e}")
            return ""

    async def _safe_screenshot(self) -> str:
        try:
            return await self.controller.get_screenshot_base64()
        except Exception:
            return ""

    async def close(self):
        await self.controller.close()
        self._launched = False


def _add_grid_to_b64(screenshot_b64: str) -> str:
    """Add coordinate grid to base64 screenshot."""
    try:
        data = base64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = _draw_grid(img)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return screenshot_b64


def is_browser_task(goal: str, page_url: str = "", sub_task_desc: str = "") -> bool:
    """
    Heuristic: should this task use the Browser agent instead of Desktop?
    Returns True if the task involves web browsing.
    """
    combined = f"{goal} {page_url} {sub_task_desc}".lower()
    WEB_SIGNALS = [
        "http://", "https://", "www.", ".com", ".org", ".net", ".io",
        "youtube", "google", "twitter", "x.com", "reddit", "github",
        "facebook", "instagram", "linkedin", "amazon", "gmail", "wikipedia",
        "arxiv", "stackoverflow", "news", "article", "paper", "blog", "docs",
        "search", "browse", "website", "web", "open url", "open link",
        "navigate to", "go to", "visit", "find online", "look up", "look up",
        "chrome", "safari", "browser", "tab",
    ]
    return any(sig in combined for sig in WEB_SIGNALS)
