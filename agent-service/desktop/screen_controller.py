"""
cortex41 Desktop Screen Controller.

Captures the full screen and drives system-wide mouse/keyboard via pyautogui.
Replaces the browser controller — the agent now sees and controls the entire desktop.

macOS permissions required (one-time):
  System Settings > Privacy & Security > Accessibility  → allow Terminal / python
  System Settings > Privacy & Security > Screen Recording → allow Terminal / python
"""

import asyncio
import base64
import io
import subprocess
from typing import Optional

import pyautogui
from PIL import Image, ImageDraw, ImageFont

# Move mouse to top-left corner to abort (pyautogui safety)
pyautogui.FAILSAFE = True
# Minimal pause between pyautogui calls
pyautogui.PAUSE = 0.03

_GRID_SPACING = 200  # px between grid lines shown in screenshots

# Swift snippet to get Chrome's primary CGWindow ID.
# Returns the first normal-layer Chrome window with width > 300px.
_SWIFT_CHROME_WIN_ID = """\
import CoreGraphics
import Foundation
if let windows = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] {
    for w in windows {
        guard let owner = w["kCGWindowOwnerName"] as? String, owner.contains("Chrome"),
              let layer = w["kCGWindowLayer"] as? Int, layer == 0,
              let bounds = w["kCGWindowBounds"] as? [String: Any],
              let width = bounds["Width"] as? CGFloat, width > 300,
              let wid = w["kCGWindowNumber"] as? Int else { continue }
        print(wid)
        break
    }
}
"""


class DesktopScreenController:
    """
    Full-desktop screen capture + mouse/keyboard control.
    All blocking pyautogui calls are run in a thread executor so they
    don't block the asyncio event loop.
    """

    def __init__(self):
        w, h = pyautogui.size()
        self.screen_width = w
        self.screen_height = h
        print(f"[Desktop] Screen size: {w}x{h}")

    # ── Screenshot ─────────────────────────────────────────────────────────────

    async def get_screenshot_base64(self, grid: bool = True) -> str:
        """
        Capture the full screen as a base64 JPEG.

        Uses macOS `screencapture -D1` as the primary method — this is Apple's
        native screenshot tool and captures EXACTLY what is visible on the primary
        display, regardless of which Python thread calls it or which Space the
        Python process "belongs to". Falls back to mss/pyautogui if unavailable.

        Scales the result to logical pixel dimensions (matching pyautogui coordinates)
        so the model's coordinate estimates are accurate.
        """
        import os
        import tempfile
        loop = asyncio.get_event_loop()
        # Try MSS first (DIRECT MEMORY - FASTEST)
        try:
            import mss
            with mss.mss() as sct:
                # Use mss to capture primary monitor directly to memory
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            pass

        # Fallback to macOS native screencapture (Slower, uses disk)
        if img is None:
            tmp = tempfile.mktemp(suffix=".png")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "screencapture", "-x", "-D1", tmp,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if os.path.exists(tmp):
                    img = await loop.run_in_executor(None, lambda: Image.open(tmp).convert("RGB"))
            except Exception:
                pass
            finally:
                if os.path.exists(tmp):
                    try: os.unlink(tmp)
                    except: pass

        if img is None:
            try:
                import mss as _mss
                with _mss.mss() as sct:
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    raw = await loop.run_in_executor(None, lambda: sct.grab(monitor))
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                img = await loop.run_in_executor(None, pyautogui.screenshot)
                img = img.convert("RGB")

        logical_w, logical_h = self.screen_width, self.screen_height
        phys_w, phys_h = img.size
        if phys_w > logical_w * 1.3:
            img = img.resize((logical_w, logical_h), Image.LANCZOS)

        if grid:
            img = self._draw_grid(img)

        w, h = img.size
        if max(w, h) > 1920:
            scale = 1920 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def _draw_grid(self, img: Image.Image) -> Image.Image:
        """High-visibility coordinate grid + axis labels every 200px."""
        draw = ImageDraw.Draw(img)
        w, h = img.size
        try:
            # Use a slightly larger, bolder-looking font if available, or just standard
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except Exception:
            font = ImageFont.load_default()

        line_color = (120, 120, 120, 128)
        label_bg = (0, 0, 0)
        label_text = (255, 255, 255)

        for x in range(0, w, _GRID_SPACING):
            draw.line([(x, 0), (x, h)], fill=line_color, width=1)
            # Draw background box for label
            text_str = str(x)
            bbox = draw.textbbox((x + 2, 2), text_str, font=font)
            draw.rectangle(bbox, fill=label_bg)
            draw.text((x + 2, 2), text_str, fill=label_text, font=font)

        for y in range(0, h, _GRID_SPACING):
            draw.line([(0, y), (w, y)], fill=line_color, width=1)
            # Draw background box for label
            text_str = str(y)
            bbox = draw.textbbox((2, y + 2), text_str, font=font)
            draw.rectangle(bbox, fill=label_bg)
            draw.text((2, y + 2), text_str, fill=label_text, font=font)

        return img

    # ── Mouse ──────────────────────────────────────────────────────────────────

    async def click(self, x: int, y: int, button: str = "left", double: bool = False):
        loop = asyncio.get_event_loop()
        if double:
            await loop.run_in_executor(None, lambda: pyautogui.doubleClick(x, y, button=button))
        else:
            await loop.run_in_executor(None, lambda: pyautogui.click(x, y, button=button))

    async def right_click(self, x: int, y: int):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: pyautogui.rightClick(x, y))

    async def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3):
        loop = asyncio.get_event_loop()
        clicks = -amount if direction == "down" else amount
        await loop.run_in_executor(None, lambda: pyautogui.scroll(clicks, x=x, y=y))

    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: pyautogui.drag(x2 - x1, y2 - y1, duration=duration, startX=x1, startY=y1)
        )

    # ── Keyboard ───────────────────────────────────────────────────────────────

    async def key_press(self, key: str):
        loop = asyncio.get_event_loop()
        key = key.lower().replace("cmd+", "command+").replace("win+", "command+")
        if "+" in key:
            parts = [p.strip() for p in key.split("+")]
            await loop.run_in_executor(None, lambda: pyautogui.hotkey(*parts))
        else:
            await loop.run_in_executor(None, lambda: pyautogui.press(key))

    async def type_text(self, text: str, slowly: bool = False):
        loop = asyncio.get_event_loop()
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbcopy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate(input=text.encode("utf-8"))
            await loop.run_in_executor(None, lambda: pyautogui.hotkey("command", "v"))
        except Exception:
            interval = 0.07 if slowly else 0.02
            safe = "".join(c for c in text if ord(c) < 128)
            await loop.run_in_executor(None, lambda: pyautogui.typewrite(safe, interval=interval))

    # ── System info ────────────────────────────────────────────────────────────

    async def get_desktop_state(self) -> tuple[str, str, str]:
        """
        Return (active_app_name, window_title, chrome_url) in ONE osascript call.

        Combining the three per-step queries eliminates two extra osascript
        subprocess spawns that would otherwise cause macOS to briefly re-focus
        Terminal (the parent process) on each exit.
        """
        script = '''\
set chromeUrl to ""
try
    tell application "Google Chrome"
        set chromeUrl to URL of active tab of front window
    end tell
end try
tell application "System Events"
    set frontApp to first process whose frontmost is true
    set appName to name of frontApp
    set winTitle to ""
    try
        set winTitle to title of front window of frontApp
    end try
end tell
return appName & "|||" & winTitle & "|||" & chromeUrl
'''
        try:
            result = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=4)
            parts = stdout.decode().strip().split("|||", 2)
            app = parts[0].strip() if len(parts) > 0 else "unknown"
            title = parts[1].strip() if len(parts) > 1 else ""
            chrome_url = parts[2].strip() if len(parts) > 2 else ""
            return app, title, chrome_url
        except Exception as e:
            print(f"[Desktop] get_desktop_state error: {e}")
            return "unknown", "", ""

    async def get_active_app(self) -> str:
        """Return the name of the currently focused application (macOS)."""
        try:
            result = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to get name of first process whose frontmost is true',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=2)
            return stdout.decode().strip()
        except Exception as e:
            print(f"[Desktop] get_active_app error: {e}")
            return "unknown"

    async def get_window_title(self) -> str:
        """Return the title of the frontmost window (macOS)."""
        try:
            result = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to get title of front window of (first process whose frontmost is true)',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=2)
            return stdout.decode().strip()
        except Exception:
            return ""

    async def get_chrome_url(self) -> str:
        """
        Return Chrome's current tab URL via direct AppleScript (no Accessibility needed).
        Returns empty string if Chrome is not running or has no windows.
        """
        try:
            result = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "Google Chrome" to return URL of active tab of front window',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=2)
            return stdout.decode().strip()
        except Exception:
            return ""

    _BROWSER_APPS = {"google chrome", "chrome", "safari", "firefox", "arc", "brave browser", "opera"}

    async def open_app(self, app_name: str):
        """
        Open an application and guarantee its window is visible on the current screen.
        Uses Dock icon click (most reliable for Space switching) with open -a fallback.
        NOTE: Dock click requires Accessibility permission for the calling process.
        """
        script = f"""try
            tell application "System Events"
                tell process "Dock"
                    click button "{app_name}" of list 1
                end tell
            end tell
        on error
            do shell script "open -a \\"{app_name}\\""
        end try"""
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        await proc.wait()
        await asyncio.sleep(2.5)

    async def add_grid_to_b64(self, screenshot_b64: str) -> str:
        """Add coordinate grid to an existing base64 screenshot (for carried screenshots)."""
        loop = asyncio.get_event_loop()
        img_data = base64.b64decode(screenshot_b64)
        img = await loop.run_in_executor(
            None, lambda: Image.open(io.BytesIO(img_data)).convert("RGB")
        )
        phys_w, phys_h = img.size
        if phys_w > self.screen_width * 1.3:
            img = img.resize((self.screen_width, self.screen_height), Image.LANCZOS)
        img = self._draw_grid(img)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    # ── Chrome Space-switch helpers ─────────────────────────────────────────────

    @staticmethod
    def _get_active_app_sync() -> str:
        """Return frontmost app name (blocking)."""
        import subprocess as _sp
        r = _sp.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=3,
            start_new_session=True,
        )
        return r.stdout.strip()

    @staticmethod
    def _get_chrome_window_id_sync() -> str | None:
        """
        Get Chrome's primary CGWindow ID using Swift (ships with macOS, no extra deps).
        Returns the window ID as a string, or None if unavailable.
        Used with `screencapture -l <id>` to capture Chrome's window regardless of Space.
        """
        import subprocess as _sp
        r = _sp.run(["swift", "-"], input=_SWIFT_CHROME_WIN_ID.encode(),
                    capture_output=True, timeout=10)
        wid = r.stdout.decode().strip().splitlines()[0] if r.stdout.strip() else ""
        return wid if wid.isdigit() else None

    @staticmethod
    def _chrome_to_current_space_sync() -> str:
        """
        Bring Chrome's frontmost window to the current macOS Space.

        Methods tried in order:
          1. AXRaise via System Events  — moves window to current Space.
             Requires Accessibility permission for the calling process (Terminal/IDE).
             Grant: System Settings > Privacy & Security > Accessibility → add Terminal.
          2. Dock click via System Events — switches to Chrome's Space.
             Also requires Accessibility.
          3. tell application "Google Chrome" to activate — works without Accessibility
             but only switches Spaces if Mission Control auto-swoosh is enabled.

        Returns a log string for debugging.
        """
        import subprocess as _sp, time

        log = []

        # ── Method 1: AXRaise (needs Accessibility) ──────────────────────────
        ax_script = """\
try
    tell application "System Events"
        tell process "Google Chrome"
            perform action "AXRaise" of window 1
        end tell
    end tell
    return "AXRaise-ok"
on error errMsg
    return "AXRaise-err:" & errMsg
end try"""
        r = _sp.run(["osascript", "-e", ax_script], capture_output=True, text=True, timeout=5,
                    start_new_session=True)
        ax_out = (r.stdout.strip() or r.stderr.strip())[:120]
        log.append(f"AXRaise={ax_out!r}")

        if "AXRaise-ok" in ax_out:
            time.sleep(1.0)
            active = DesktopScreenController._get_active_app_sync()
            log.append(f"active={active!r}")
            return " | ".join(log)

        # ── Method 2: Dock click (needs Accessibility) ───────────────────────
        dock_script = """\
try
    tell application "System Events"
        tell process "Dock"
            set found to false
            repeat with btn in (every button of list 1)
                if name of btn contains "Chrome" then
                    click btn
                    set found to true
                    exit repeat
                end if
            end repeat
            if found then
                return "dock-clicked"
            else
                return "dock-not-found"
            end if
        end tell
    end tell
on error errMsg
    return "dock-err:" & errMsg
end try"""
        r2 = _sp.run(["osascript", "-e", dock_script], capture_output=True, text=True, timeout=6,
                     start_new_session=True)
        dock_out = (r2.stdout.strip() or r2.stderr.strip())[:120]
        log.append(f"DockClick={dock_out!r}")

        if "dock-clicked" in dock_out:
            time.sleep(2.0)
            active2 = DesktopScreenController._get_active_app_sync()
            log.append(f"active={active2!r}")
            return " | ".join(log)

        # ── Method 3: direct activate (no Accessibility needed) ─────────────
        # Works if Mission Control "auto-swoosh" setting is ON (System Settings >
        # Mission Control > "When switching to an application, switch to a Space
        # with open windows for the application"). If OFF, Chrome becomes frontmost
        # but its window stays on its own Space.
        act_script = 'tell application "Google Chrome" to activate'
        _sp.run(["osascript", "-e", act_script], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                start_new_session=True)
        time.sleep(2.5)
        active3 = DesktopScreenController._get_active_app_sync()
        log.append(f"activate_fallback | active={active3!r}")

        return " | ".join(log)

    async def open_url(self, url: str):
        """Open a URL in Google Chrome or activate an existing tab matching the domain."""
        loop = asyncio.get_event_loop()

        def _sync():
            import subprocess as _sp, time
            from urllib.parse import urlparse
            
            domain = urlparse(url).netloc
            print(f"[open_url] Target: {url} (domain: {domain})")

            # AppleScript to find a tab with matching domain and activate it
            find_script = f'''
            tell application "Google Chrome"
                set foundTab to false
                set targetUrl to "{url}"
                set targetDomain to "{domain}"
                
                -- 1. Try to find the EXACT URL match first
                repeat with w in windows
                    set tabIndex to 0
                    repeat with t in tabs of w
                        set tabIndex to tabIndex + 1
                        if (URL of t is targetUrl) then
                            set index of w to 1
                            set active tab index of w to tabIndex
                            set foundTab to true
                            exit repeat
                        end if
                    end repeat
                    if foundTab then exit repeat
                end repeat

                -- 2. If no exact match and target is just a domain root, fallback to domain match
                if not foundTab and (targetUrl ends with targetDomain or targetUrl ends with targetDomain & "/") then
                    repeat with w in windows
                        set tabIndex to 0
                        repeat with t in tabs of w
                            set tabIndex to tabIndex + 1
                            if (URL of t contains targetDomain) then
                                set index of w to 1
                                set active tab index of w to tabIndex
                                set foundTab to true
                                exit repeat
                            end if
                        end repeat
                        if foundTab then exit repeat
                    end repeat
                end if
                
                if foundTab then
                    return "found"
                else
                    return "not-found"
                end if
            end tell
            '''
            r_find = _sp.run(["osascript", "-e", find_script], capture_output=True, text=True, timeout=5,
                             start_new_session=True)
            found_status = r_find.stdout.strip()

            if found_status == "not-found":
                print(f"[open_url] Existing tab not found, opening NEW: {url}")
                r = _sp.run(["open", "-a", "Google Chrome", url],
                            stdout=_sp.DEVNULL, stderr=_sp.PIPE,
                            start_new_session=True)
                if r.returncode != 0:
                    print(f"[open_url] open -a failed rc={r.returncode}: {r.stderr.decode()[:80]}")
                    _sp.run(["open", url], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                            start_new_session=True)
            else:
                print(f"[open_url] Found existing tab, ACTIVATED.")

            time.sleep(1.5)
            space_log = DesktopScreenController._chrome_to_current_space_sync()
            print(f"[open_url] space-switch: {space_log}")
            time.sleep(1.0)

        await loop.run_in_executor(None, _sync)

    async def open_url_and_capture(self, url: str) -> str:
        """
        Open a URL in Chrome, bring the window to the current Space, and return
        a base64 JPEG screenshot.

        IMPORTANT: Everything — open, space-switch, AND screenshot — is done in a
        single run_in_executor thread using mss directly.

        Why: `screencapture` subprocess always fails (TCC Screen Recording permission
        not granted to VS Code / the spawning process). `mss` uses CGDisplayCreateImage
        directly within the Python process (which HAS Screen Recording permission) and
        works reliably. Critically, the mss capture must happen in the SAME thread as
        AXRaise, before asyncio resumes and macOS re-focuses Terminal's Space.
        """
        loop = asyncio.get_event_loop()
        logical_w, logical_h = self.screen_width, self.screen_height

        def _open_switch_capture():
            import subprocess as _sp, time
            import mss as _mss
            from urllib.parse import urlparse

            domain = urlparse(url).netloc
            print(f"[open_url_and_capture] Target: {url} (domain: {domain})")

            # AppleScript to find a tab with matching domain and activate it
            find_script = f'''
            tell application "Google Chrome"
                set foundTab to false
                set targetUrl to "{url}"
                set targetDomain to "{domain}"
                
                repeat with w in windows
                    set tabIndex to 0
                    repeat with t in tabs of w
                        set tabIndex to tabIndex + 1
                        if (URL of t is targetUrl) then
                            set index of w to 1
                            set active tab index of w to tabIndex
                            set foundTab to true
                            exit repeat
                        end if
                    end repeat
                    if foundTab then exit repeat
                end repeat

                if not foundTab and (targetUrl ends with targetDomain or targetUrl ends with targetDomain & "/") then
                    repeat with w in windows
                        set tabIndex to 0
                        repeat with t in tabs of w
                            set tabIndex to tabIndex + 1
                            if (URL of t contains targetDomain) then
                                set index of w to 1
                                set active tab index of w to tabIndex
                                set foundTab to true
                                exit repeat
                            end if
                        end repeat
                        if foundTab then exit repeat
                    end repeat
                end if

                if foundTab then return "found"
                return "not-found"
            end tell
            '''
            r_find = _sp.run(["osascript", "-e", find_script], capture_output=True, text=True, timeout=5,
                             start_new_session=True)
            if r_find.stdout.strip() == "not-found":
                print(f"[open_url_and_capture] Opening NEW {url}")
                r = _sp.run(["open", "-a", "Google Chrome", url],
                            stdout=_sp.DEVNULL, stderr=_sp.PIPE,
                            start_new_session=True)
                if r.returncode != 0:
                    _sp.run(["open", url], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                            start_new_session=True)
            else:
                print(f"[open_url_and_capture] Activated EXISTING tab.")

            time.sleep(1.5)

            # Bring Chrome to the current Space
            space_log = DesktopScreenController._chrome_to_current_space_sync()
            print(f"[open_url_and_capture] space-switch: {space_log}")

            # Wait for content
            time.sleep(2.0)

            # Capture with mss
            try:
                with _mss.mss() as sct:
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    print(f"[open_url_and_capture] mss captured size={img.size}")
                    return img
            except Exception as e:
                print(f"[open_url_and_capture] mss error: {e}")
                return None

        img = await loop.run_in_executor(None, _open_switch_capture)

        if img is None:
            print("[open_url_and_capture] mss failed — falling back to get_screenshot_base64")
            return await self.get_screenshot_base64(grid=False)

        phys_w, phys_h = img.size
        if phys_w > logical_w * 1.3:
            img = img.resize((logical_w, logical_h), Image.LANCZOS)

        w, h = img.size
        if max(w, h) > 1920:
            scale = 1920 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()
