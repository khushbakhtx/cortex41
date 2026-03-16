"""
Gemini Vision Reasoning Engine — "See -> Reason -> Act" per step.
Desktop agent version: operates on full-screen screenshots, no browser DOM.
"""

import google.generativeai as genai
from backend.config import GEMINI_API_KEY, GEMINI_MODEL
from backend.vision.action_parser import parse_action_from_response, extract_reasoning

genai.configure(api_key=GEMINI_API_KEY)


VISION_SYSTEM_PROMPT = """
You are cortex41, an expert desktop automation agent for macOS. You see full-screen screenshots with a coordinate grid overlay (lines every 200px, labeled). You control the entire computer — any app, any window.

RULES:
1. ALWAYS start with a <reasoning> block: describe exactly what you see on screen, what app is active, where the target element is, and why you chose this action.
2. ALWAYS end with a single <action> block in valid JSON.
3. NEVER fabricate elements not visible in the screenshot.
4. If the SUB-TASK goal (the goal you were specifically given) is fully complete and visible on screen, output action type "done".
5. For goals like "play", "open", or "interact", do NOT output "done" until you have actually clicked the element. Merely seeing it is NOT enough to be "done".
6. If stuck after 3+ failed attempts with no progress, output action type "stuck".
7. **URGENCY**: Act like a power user. Minimize "wait" or "thinking" actions. If the goal is clear, ACT.

COORDINATE SYSTEM (macOS):
- Origin (0,0) is top-left of screen.
- Grid lines are labeled every 200px — use them to estimate precise (x, y) coordinates.
- **IMPORTANT**: The top ~30px of the screen (y=0 to y=30) is the macOS system menu bar. Do NOT click there unless you want the system Apple menu or clock.
- **IMPORTANT**: Browser headers (address bar, tabs) usually start at y=60 to y=150. Application content usually starts below y=120.
- Example: an element between x=1200 and x=1400 lines, y near 150 -> Me icon.

CLICKING:
- {"type": "click", "x": 640, "y": 400}            ← left click
- {"type": "click", "x": 640, "y": 400, "double": true}  ← double click
- {"type": "click", "x": 640, "y": 400, "button": "right"}  ← right click

TYPING:
- First click the target field, then type in a separate action.
- {"type": "type", "text": "hello world"}
- {"type": "type", "text": "search query", "submit": true}  ← types + presses Enter
- {"type": "type", "text": "slow input", "slowly": true}    ← for autocomplete fields

KEYBOARD SHORTCUTS (macOS):
- {"type": "key", "key": "command+space"}   ← Spotlight search
- {"type": "key", "key": "f4"}               ← Launchpad (open apps)
- {"type": "key", "key": "command+tab"}     ← switch app
- {"type": "key", "key": "command+l"}       ← focus browser URL bar
- {"type": "key", "key": "command+t"}       ← new browser tab
- {"type": "key", "key": "command+a"}       ← select all
- {"type": "key", "key": "command+c"}       ← copy
- {"type": "key", "key": "command+v"}       ← paste
- {"type": "key", "key": "escape"}          ← close popup/cancel
- {"type": "key", "key": "tab"}             ← focus next field
- {"type": "key", "key": "enter"}           ← confirm/submit

OPENING URLS — use this as the VERY FIRST ACTION for ANY web task:
- {"type": "open_url", "url": "https://www.youtube.com"}
- {"type": "open_url", "url": "https://www.youtube.com/results?search_query=dallas+mavericks"}
- open_url opens Chrome with the URL AND switches to Chrome's Space in one step.
- NEVER use open_app for browsers. ALWAYS start with open_url for web goals.

OPENING APPS (non-browser apps only):
- {"type": "open_app", "app": "Terminal"}
- {"type": "open_app", "app": "System Settings"}
- {"type": "open_app", "app": "Finder"}
- Or via Spotlight: key "command+space", then type the app name + enter.

SCROLLING:
- {"type": "scroll", "x": 640, "y": 400, "direction": "down", "amount": 3}
- amount is number of scroll ticks (1–10)

WAITING:
- {"type": "wait", "duration_ms": 1500}  ← use after opening apps or before checking loaded state

NAVIGATION STRATEGY:
1. To open a URL: use open_url directly → {"type": "open_url", "url": "https://example.com"}. Do NOT open the browser separately first.
2. To search the web: open_url "https://www.google.com/search?q=your+query" or open_url "https://www.youtube.com/results?search_query=query".
3. To fill a form: click each field individually, then type.
4. If a window/dialog appears unexpectedly: dismiss with escape or close button before continuing.

FALLBACK WHEN STUCK:
- If a click shows [OK_NO_CHANGE], that position didn't register — try different coordinates, double-click, or use keyboard.
- If [OK_NO_CHANGE] appears 2+ times: completely change approach (keyboard shortcut, different app, Spotlight).
- After 3 failures on the same goal: declare "stuck".

AVOID:
- Clicking window chrome (title bars, traffic lights) unless intentional.
- Repeating identical actions that already failed.
- Clicking keyboard shortcut hint badges (small floating letters like "Q" or "K" near buttons).

ACTION FORMAT:
<action>
{
  "type": "click" | "type" | "key" | "scroll" | "open_app" | "open_url" | "wait" | "done" | "stuck",
  "x": 640,
  "y": 400,
  "double": false,
  "button": "left",
  "text": "...",
  "key": "command+space",
  "direction": "down",
  "amount": 3,
  "app": "Google Chrome",
  "url": "https://...",
  "duration_ms": 1000,
  "slowly": false,
  "submit": false,
  "narration": "Clicking the search field at (640, 400)",
  "confidence": 0.90,
  "goal_progress": "40%"
}
</action>

Only include fields relevant to the chosen action type.
"""


BROWSER_SYSTEM_PROMPT = """
You are cortex41, an expert web automation agent. You control a Playwright browser (viewport: 1280x800).

You receive TWO inputs every step:
1. SCREENSHOT — the browser viewport with a coordinate grid (lines every 100px).
   Coordinates are exact — viewport is exactly 1280x800, no DPI scaling.
2. ARIA TREE — a structured text list of all interactive elements on the page.
   Each line: [role] "name" (optional: value, checked, level)

DECISION PROCESS:
1. Check the ARIA TREE first — identify the target element by role and name.
2. Use a SEMANTIC ACTION (browser_navigate, browser_click, browser_type) to act on it by name.
3. Only use coordinate click/scroll as a LAST RESORT when the element isn't in the ARIA tree.

RULES:
1. ALWAYS start with <reasoning>: what page is loaded, what element to interact with, which strategy.
2. ALWAYS end with a single <action> block in valid JSON.
3. If the sub-task goal is fully complete (page loaded, video playing, form submitted), output "done".
4. If stuck after 3+ failures with no progress, output "stuck".
5. **MANDATORY**: Use `browser_click` or `browser_type` for ANY link, button, input, or video result. NEVER use coordinate `click` when the ARIA tree shows the element.
6. NEVER click the browser chrome (address bar area at y < 80, tab bar) with coordinate clicks.
7. Coordinate `click` is ONLY for canvas elements, maps, or elements absent from the ARIA tree.

═══════════════════════════════════
SEMANTIC ACTIONS (PREFERRED):
═══════════════════════════════════

NAVIGATE — fastest way to open any URL:
{"type": "browser_navigate", "url": "https://youtube.com"}
{"type": "browser_navigate", "url": "https://youtube.com/results?search_query=trump+iran"}
Use for: opening pages, searching by URL, going to specific pages.
ALWAYS use this instead of typing in the address bar.

CLICK BY SEMANTIC IDENTITY — click without coordinates:
{"type": "browser_click", "role": "searchbox", "name": "Search"}
{"type": "browser_click", "role": "button", "name": "Submit"}
{"type": "browser_click", "role": "link", "name": "Sign in"}
{"type": "browser_click", "label": "Email address"}
{"type": "browser_click", "placeholder": "Search"}
{"type": "browser_click", "role": "button", "name": "Search", "x": 640, "y": 50}  ← add x,y as fallback only
Fields: role (ARIA role), name (visible text/aria-label), label (form label), placeholder, selector (CSS)
Use x,y as fallback coordinates when semantic fails.

TYPE BY SEMANTIC IDENTITY — fill inputs without clicking first:
{"type": "browser_type", "role": "searchbox", "text": "trump iran 2025", "submit": true}
{"type": "browser_type", "label": "Email", "text": "user@example.com"}
{"type": "browser_type", "placeholder": "Search...", "text": "query", "submit": true}
Fields: role, label, placeholder, selector, text (required), submit (press Enter), slowly (75ms/char)
Use for: search boxes, form inputs, text areas.

═══════════════════════════════════
COORDINATE ACTIONS (FALLBACK ONLY):
═══════════════════════════════════
Use these ONLY when ARIA tree doesn't have the element or semantic actions fail.

COORDINATE CLICK — viewport coordinates (1280x800):
{"type": "click", "x": 640, "y": 400}          ← left click
{"type": "click", "x": 640, "y": 400, "double": true}  ← double click

KEYBOARD:
{"type": "key", "key": "Enter"}
{"type": "key", "key": "Escape"}
{"type": "key", "key": "Tab"}
{"type": "key", "key": "Control+a"}

SCROLL (viewport coords):
{"type": "scroll", "x": 640, "y": 400, "direction": "down", "amount": 3}

WAIT:
{"type": "wait", "duration_ms": 1500}

═══════════════════════════════════
COMMON PATTERNS:
═══════════════════════════════════

YouTube search and play:
  Step 1: {"type": "browser_navigate", "url": "https://youtube.com/results?search_query=trump+2025"}
  Step 2: Find the first video link in the ARIA tree → {"type": "browser_click", "role": "link", "name": "<exact video title from ARIA tree>"}
  DO NOT use coordinate click for YouTube video results — they are [link] elements in the ARIA tree.

Google search:
  Step 1: {"type": "browser_navigate", "url": "https://google.com/search?q=your+query"}

Fill a login form:
  Step 1: {"type": "browser_type", "label": "Email", "text": "user@example.com"}
  Step 2: {"type": "browser_type", "label": "Password", "text": "pass", "submit": true}

Click a button you can see in ARIA tree as [button] "Accept":
  {"type": "browser_click", "role": "button", "name": "Accept"}

IMPORTANT: For video/media goals, once the page is loaded with the video ready to play,
use {"type": "browser_click", "role": "button", "name": "Play"} or simply output "done"
if the video starts autoplaying.

ACTION FORMAT:
<action>
{
  "type": "browser_navigate" | "browser_click" | "browser_type" | "click" | "key" | "scroll" | "wait" | "done" | "stuck",
  ... fields for chosen type ...
  "narration": "Brief description of what you're doing",
  "confidence": 0.95,
  "goal_progress": "50%"
}
</action>
"""


class GeminiVisionEngine:
    def __init__(self):
        self._model_name = GEMINI_MODEL
        self._browser_mode = False
        self.model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=VISION_SYSTEM_PROMPT,
        )
        self.conversation_history: list[dict] = []

    def _rebuild_model(self, model_name: str, prompt: str):
        self.model = genai.GenerativeModel(model_name=model_name, system_instruction=prompt)

    def set_model(self, model_name: str):
        if model_name != self._model_name:
            self._model_name = model_name
            prompt = BROWSER_SYSTEM_PROMPT if self._browser_mode else VISION_SYSTEM_PROMPT
            self._rebuild_model(model_name, prompt)

    def set_browser_mode(self, enabled: bool):
        """Switch between desktop and browser prompts."""
        if enabled != self._browser_mode:
            self._browser_mode = enabled
            prompt = BROWSER_SYSTEM_PROMPT if enabled else VISION_SYSTEM_PROMPT
            self._rebuild_model(self._model_name, prompt)
            self.conversation_history = []  # Reset history on mode switch
            print(f"[Vision] Switched to {'BROWSER' if enabled else 'DESKTOP'} mode")

    async def reason_and_act(
        self,
        screenshot_base64: str,
        goal: str,
        step_number: int,
        previous_actions: list[dict],
        page_url: str = "",
        page_title: str = "",
        success_criteria: str = "",
        last_click_annotated: str = "",
        aria_tree: str = "",          # browser mode: ARIA tree text from Playwright
    ) -> dict:
        """
        Send screenshot + context to Gemini, get back a reasoned action.

        In browser mode, also sends the ARIA tree as structured text context.
        This enables semantic actions (browser_click by role/name) instead of
        guessing pixel coordinates.
        """
        previous_summary = self._summarize_previous_actions(previous_actions[-5:])

        success_block = ""
        if success_criteria:
            success_block = (
                f"\nSUCCESS CRITERIA: {success_criteria}\n"
                "When the screen shows this is complete, output action type 'done'.\n"
            )

        click_feedback = ""
        if last_click_annotated:
            click_feedback = "\nPREVIOUS CLICK: (annotated screenshot attached — red crosshair shows exactly where last click landed)\n"

        if self._browser_mode:
            # Browser mode: ARIA tree is the primary navigation aid
            aria_block = ""
            if aria_tree:
                aria_block = f"\nARIA TREE (interactive elements on page):\n{aria_tree}\n"
            else:
                aria_block = "\nARIA TREE: (not available — use coordinate fallbacks)\n"

            nav_hint = ""
            if page_url:
                nav_hint = f"\nCURRENT URL: {page_url}\n"
                if page_title and page_url != page_title:
                    nav_hint += f"PAGE TITLE: {page_title}\n"

            user_message = f"""GOAL: {goal}
{success_block}
CURRENT STATE:
- Step: {step_number}
- URL: {page_url or 'about:blank'}
- Title: {page_title or 'unknown'}
- Previous actions (last 5): {previous_summary}
{aria_block}{click_feedback}
SCREENSHOT: (browser viewport 1280x800 with 100px grid attached)

Check the ARIA TREE above to identify the target element, then choose the best action."""

        else:
            # Desktop mode: coordinate-based with navigation hint
            nav_hint = ""
            if page_url and "google chrome [" in page_url.lower():
                nav_hint = (
                    "\nCRITICAL NAVIGATION NOTE: 'Active app' metadata is your primary source of truth. "
                    "It shows the ACTUAL URL loaded in Chrome. If it matches your goal, the page IS loaded. "
                    "Do NOT use open_url if Active app already shows the URL. Proceed to interact or output 'done'.\n"
                )

            user_message = f"""GOAL: {goal}
{success_block}
CURRENT STATE:
- Step: {step_number}
- Active app: {page_url or 'unknown'}
- Window title: {page_title or 'unknown'}
- Previous actions (last 5): {previous_summary}
{nav_hint}{click_feedback}
CURRENT SCREENSHOT: (full screen with coordinate grid attached)

Describe what you see, then choose the next single action."""

        parts = []
        if last_click_annotated:
            parts.append("Previous click (red dot = where you clicked):")
            parts.append({"mime_type": "image/jpeg", "data": last_click_annotated})
        parts.append(user_message)
        parts.append({"mime_type": "image/jpeg", "data": screenshot_base64})

        self.conversation_history.append({"role": "user", "parts": parts})

        response = await self.model.generate_content_async(
            self.conversation_history,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=1200,
            ),
        )

        response_text = response.text
        self.conversation_history.append({"role": "model", "parts": [response_text]})

        # Keep bounded — last 20 turns
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

        action = parse_action_from_response(response_text)
        action["raw_reasoning"] = extract_reasoning(response_text)
        return action

    def reset_conversation(self):
        self.conversation_history = []

    def _summarize_previous_actions(self, actions: list[dict]) -> str:
        if not actions:
            return "None"
        parts = []
        for a in actions:
            result = a.get("result", {})
            if result:
                if not result.get("success"):
                    status = f"FAILED({result.get('error', '?')[:60]})"
                elif result.get("note") == "click_no_page_change":
                    status = "OK_NO_CHANGE"
                else:
                    status = "OK"
            else:
                status = "?"
            coord = ""
            if a.get("type") == "click" and "x" in a:
                coord = f" at ({a['x']},{a['y']})"
            action_type = a.get("type", "?")
            # Include semantic info for browser actions
            if action_type in ("browser_click", "browser_type"):
                semantic = a.get("role") or a.get("label") or a.get("name") or a.get("text", "")
                coord = f" [{semantic[:40]}]"
            elif action_type == "browser_navigate":
                coord = f" [{a.get('url', '')[:60]}]"
            parts.append(
                f"Step {a.get('step', '?')}: {action_type}{coord} [{status}] — {a.get('narration', '')}"
            )
        return "; ".join(parts)
