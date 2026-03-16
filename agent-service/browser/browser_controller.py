"""
cortex41 Browser Controller — pure Python Playwright.

Inherits techniques from OpenClaw's TypeScript browser module:
  - Screenshot normalization (max 2000px side, 5MB, JPEG compression) from openclaw/src/browser/screenshot.ts
  - Connection retry with exponential backoff (3 attempts) from openclaw/src/browser/pw-session.ts
  - Page state tracking (console, errors, network) from openclaw/src/browser/pw-session.ts
  - Interaction timeout clamping (500ms-60s) from openclaw/src/browser/pw-tools-core.interactions.ts
  - Force-disconnect pattern for stuck pages from openclaw/src/browser/pw-session.ts
"""

import asyncio
import base64
import io
import time
from typing import Optional

from PIL import Image, ImageDraw
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ConsoleMessage,
    Request,
    Response,
)

from backend.config import (
    SCREENSHOT_MAX_SIDE,
    SCREENSHOT_MAX_BYTES,
    CDP_CONNECT_ATTEMPTS,
    CDP_CONNECT_BASE_DELAY_MS,
)


# Inherited from OpenClaw: page state tracking per page object
class PageState:
    def __init__(self):
        self.console: list[dict] = []
        self.errors: list[dict] = []
        self.requests: list[dict] = []
        self.next_request_id = 0

    MAX_CONSOLE = 500
    MAX_ERRORS = 200
    MAX_REQUESTS = 500


def _normalize_screenshot(img_bytes: bytes) -> bytes:
    """
    Inherited from OpenClaw screenshot.ts:
    Normalize browser screenshot — resize if needed, JPEG-compress to stay under limits.
    max side: 2000px, max bytes: 5MB.
    """
    img = Image.open(io.BytesIO(img_bytes))
    width, height = img.size
    max_dim = max(width, height)

    if len(img_bytes) <= SCREENSHOT_MAX_BYTES and max_dim <= SCREENSHOT_MAX_SIDE:
        # Already within limits — return as-is (already JPEG from Playwright)
        return img_bytes

    # Need to reduce: try progressively smaller JPEG quality/size (OpenClaw pattern)
    quality_steps = [90, 75, 60, 45]
    target_side = min(SCREENSHOT_MAX_SIDE, max_dim)

    for quality in quality_steps:
        if max_dim > SCREENSHOT_MAX_SIDE:
            scale = SCREENSHOT_MAX_SIDE / max_dim
            new_w = int(width * scale)
            new_h = int(height * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)
        else:
            resized = img

        if resized.mode != "RGB":
            resized = resized.convert("RGB")

        buffer = io.BytesIO()
        resized.save(buffer, format="JPEG", quality=quality)
        out = buffer.getvalue()

        if len(out) <= SCREENSHOT_MAX_BYTES:
            return out

    # Last resort: aggressive compression
    if resized.mode != "RGB":
        resized = resized.convert("RGB")
    buffer = io.BytesIO()
    resized.save(buffer, format="JPEG", quality=30)
    return buffer.getvalue()


class Cortex41BrowserController:
    """
    Python Playwright browser controller implementing OpenClaw's best patterns:
    - Retry on connect (3 attempts, backoff)
    - Page state tracking (console, errors, network)
    - Screenshot normalization
    - Force-disconnect for stuck state
    """

    def __init__(self, headless: bool = True, viewport_width: int = 1280, viewport_height: int = 800):
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._page_states: dict[int, PageState] = {}  # keyed by id(page)
        self.action_history: list[dict] = []

    async def launch(self):
        """
        Connect to browser. Two modes:
        1. CDP mode (CDP_URL env var set): attach to your existing Chrome instance.
           Launch Chrome with: --remote-debugging-port=9222
        2. Headless mode (default): launch Playwright's own Chromium headlessly.
        """
        import os
        cdp_url = os.getenv("CDP_URL", "").strip()
        if cdp_url:
            await self._launch_cdp(cdp_url)
        else:
            await self._launch_headless()

    async def _launch_cdp(self, cdp_url: str):
        """Attach to an already-running Chrome via CDP (your real browser)."""
        last_err = None
        for attempt in range(CDP_CONNECT_ATTEMPTS):
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
                # Use the first existing context (the user's real browser context)
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                else:
                    self._context = await self._browser.new_context()
                # Open a new tab so we don't disturb existing pages
                self._page = await self._context.new_page()
                # Force viewport to match what the vision model expects (1280x800)
                await self._page.set_viewport_size(
                    {"width": self.viewport_width, "height": self.viewport_height}
                )
                self._attach_page_observers(self._page)
                print(f"[Browser] Connected to existing Chrome via CDP at {cdp_url}")
                return
            except Exception as e:
                last_err = e
                delay = (CDP_CONNECT_BASE_DELAY_MS + attempt * CDP_CONNECT_BASE_DELAY_MS) / 1000
                await asyncio.sleep(delay)
        raise RuntimeError(f"CDP connect to {cdp_url} failed after {CDP_CONNECT_ATTEMPTS} attempts: {last_err}")

    async def _launch_headless(self):
        """Launch Playwright's own Chromium instance with stealth settings to avoid bot detection."""
        last_err = None
        for attempt in range(CDP_CONNECT_ATTEMPTS):
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--autoplay-policy=no-user-gesture-required",
                        # Stealth: remove automation indicators that trigger bot detection
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-infobars",
                        "--window-size=1280,800",
                    ],
                )
                self._context = await self._browser.new_context(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                    # macOS Chrome user agent — matches the machine running the agent
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    # Stealth: spoof locale and timezone to look like a real user
                    locale="en-US",
                    timezone_id="America/Chicago",
                    # Stealth: accept-language header matching locale
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                # Stealth: override navigator.webdriver BEFORE any page loads
                await self._context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    window.chrome = { runtime: {} };
                """)
                self._page = await self._context.new_page()
                self._attach_page_observers(self._page)
                return
            except Exception as e:
                last_err = e
                delay = (CDP_CONNECT_BASE_DELAY_MS + attempt * CDP_CONNECT_BASE_DELAY_MS) / 1000
                await asyncio.sleep(delay)
        raise RuntimeError(f"Browser launch failed after {CDP_CONNECT_ATTEMPTS} attempts: {last_err}")

    def _attach_page_observers(self, page: Page):
        """
        Inherited from OpenClaw's ensurePageState pattern:
        Track console, errors, and network requests per page.
        """
        state = PageState()
        self._page_states[id(page)] = state

        def on_console(msg: ConsoleMessage):
            state.console.append({
                "type": msg.type,
                "text": msg.text,
                "timestamp": time.time(),
            })
            if len(state.console) > PageState.MAX_CONSOLE:
                state.console.pop(0)

        def on_error(err: Exception):
            state.errors.append({
                "message": str(err),
                "timestamp": time.time(),
            })
            if len(state.errors) > PageState.MAX_ERRORS:
                state.errors.pop(0)

        def on_request(req: Request):
            state.next_request_id += 1
            state.requests.append({
                "id": f"r{state.next_request_id}",
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "timestamp": time.time(),
            })
            if len(state.requests) > PageState.MAX_REQUESTS:
                state.requests.pop(0)

        page.on("console", on_console)
        page.on("pageerror", on_error)
        page.on("request", on_request)

        def on_close():
            self._page_states.pop(id(page), None)

        page.on("close", on_close)

    async def get_screenshot_base64(self) -> str:
        """
        Capture current browser state as base64 JPEG for Gemini and frontend.
        Always JPEG for consistent frontend rendering (data:image/jpeg;base64,...).
        """
        page = await self._ensure_page()
        raw = await page.screenshot(type="jpeg", quality=85)
        normalized = _normalize_screenshot(raw)
        return base64.b64encode(normalized).decode("utf-8")

    async def highlight_element(self, x: int, y: int, radius: int = 25) -> str:
        """
        Draw a red highlight circle at (x, y) on current screenshot.
        Returns base64 with highlight overlay.
        """
        screenshot_b64 = await self.get_screenshot_base64()
        img_data = base64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data))
        draw = ImageDraw.Draw(img)
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            outline="red",
            width=4,
        )
        # Inner smaller circle for precision
        inner = radius // 3
        draw.ellipse(
            [x - inner, y - inner, x + inner, y + inner],
            fill="red",
        )
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def get_page_url(self) -> str:
        page = await self._ensure_page()
        return page.url

    async def get_page_title(self) -> str:
        page = await self._ensure_page()
        try:
            return await page.title()
        except Exception:
            return ""

    def get_page_state(self) -> dict:
        """Return recent console/error/network data for debugging."""
        if not self._page:
            return {}
        state = self._page_states.get(id(self._page))
        if not state:
            return {}
        return {
            "console": state.console[-20:],
            "errors": state.errors[-10:],
            "requests": state.requests[-20:],
        }

    async def force_reconnect(self):
        """
        Inherited from OpenClaw's forceDisconnectPlaywrightForTarget pattern:
        Drop and rebuild the browser connection when stuck.
        """
        try:
            if self._page:
                await self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._page_states.clear()

        # Reopen context + page
        if self._browser:
            self._context = await self._browser.new_context(
                viewport={"width": self.viewport_width, "height": self.viewport_height},
            )
            self._page = await self._context.new_page()
            self._attach_page_observers(self._page)

    async def _ensure_page(self) -> Page:
        if not self._page or self._page.is_closed():
            await self.force_reconnect()
        return self._page

    async def get_cdp_session(self):
        """
        Return a cached CDP session for the current page.
        Used for direct CDP clicking (DOM.getNodeForLocation → Runtime.callFunctionOn).
        Playwright exposes this via context.new_cdp_session(page) even in CDP-attach mode.
        """
        page = await self._ensure_page()
        page_id = id(page)
        if not hasattr(self, "_cdp_sessions"):
            self._cdp_sessions: dict = {}
        if page_id not in self._cdp_sessions:
            self._cdp_sessions[page_id] = await page.context.new_cdp_session(page)
            page.on("close", lambda: self._cdp_sessions.pop(page_id, None))
        return self._cdp_sessions[page_id]

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
