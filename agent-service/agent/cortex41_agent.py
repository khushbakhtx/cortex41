"""
cortex41 Desktop Agent Orchestrator.

Architecture:
  - Full-desktop screen capture via DesktopScreenController (pyautogui + mss)
  - System-wide mouse/keyboard control via DesktopActionExecutor
  - Two-level Plan→Execute loop (Gemini Pro plans once, Flash executes steps)
  - Loop detection: breaks out on narration repeat or consecutive no-change clicks
"""

import asyncio
from typing import Callable, Awaitable, Optional

import google.generativeai as genai
import imagehash
from PIL import Image
import io
import base64

from backend.config import GEMINI_API_KEY, MAX_SUBTASK_ATTEMPTS
from backend.desktop.screen_controller import DesktopScreenController
from backend.desktop.desktop_executor import DesktopActionExecutor
from backend.browser.browser_agent import BrowserAgent, is_browser_task
from backend.vision.gemini_vision import GeminiVisionEngine
from backend.memory.firestore_memory import FirestoreMemory
from backend.cache.semantic_cache import SemanticCache
from backend.cache.model_router import ModelRouter
from backend.agent.planner import Planner, TaskPlan, SubTask
from backend.skills.skill_injector import SkillInjector
from backend.skills.skill_extractor import SkillExtractor

genai.configure(api_key=GEMINI_API_KEY)

SendFn = Callable[[dict], Awaitable[None]]


class Cortex41AgentRunner:
    def __init__(self, session_id: str, websocket_send_fn: Optional[SendFn] = None):
        self.session_id = session_id
        self.websocket_send_fn = websocket_send_fn

        # Desktop mode — pyautogui + mss (for native macOS apps)
        self.screen = DesktopScreenController()
        self.desktop_executor = DesktopActionExecutor(self.screen)

        # Browser mode — Playwright (for all web/browser tasks)
        self.browser_agent = BrowserAgent(headless=False)

        self.vision = GeminiVisionEngine()
        self.memory = FirestoreMemory()
        self.cache = SemanticCache(session_id)
        self.router = ModelRouter()
        self.planner = Planner()
        self.skill_injector = SkillInjector()

        self.is_interrupted = False
        self._current_goal: Optional[str] = None
        self._browser_launched = False

        # Keep executor pointing at desktop by default (for backward compat in cleanup)
        self.executor = self.desktop_executor

    async def initialize(self):
        """Launch Playwright browser and verify desktop screen capture."""
        # Verify desktop screen capture
        try:
            await self.screen.get_screenshot_base64(grid=False)
            print("[Desktop] Screen capture OK")
        except Exception as e:
            raise RuntimeError(f"Screen capture failed — check Screen Recording permission: {e}")

        # Launch Playwright browser (non-blocking — failure is non-fatal)
        try:
            await self.browser_agent.launch()
            self._browser_launched = True
            await self._emit("info", "Browser agent ready (Playwright)")
            print("[Browser] Playwright browser ready")
        except Exception as e:
            print(f"[Browser] headless=False launch failed: {e} — trying headless=True")
            try:
                self.browser_agent = BrowserAgent(headless=True)
                await self.browser_agent.launch()
                self._browser_launched = True
                await self._emit("info", "Browser agent ready (headless)")
                print("[Browser] Playwright browser ready (headless mode)")
            except Exception as e2:
                await self._emit("info", "Browser unavailable — desktop mode only. Run: playwright install chromium")
                print(f"[Browser] Playwright launch failed (desktop mode only): {e2}")
                self._browser_launched = False

    async def run_goal(self, goal: str, user_id: str = "default", max_steps: int = 80):
        """
        Main two-level execution loop.
        Level 1 — PLANNING: Gemini Pro plans sub-tasks once.
        Level 2 — EXECUTION: See→Reason→Act loop per sub-task.
        """
        self.is_interrupted = False
        self._current_goal = goal
        self.vision.reset_conversation()
        all_completed_steps: list[dict] = []

        await self._emit("thinking", f"cortex41 is planning: '{goal}'")

        # --- CONVERSATIONAL CONTEXT ---
        await self.memory.log_message(self.session_id, "human", goal, user_id)
        history = await self.memory.get_conversation_history(self.session_id, limit=6)

        # --- SKILL INJECTION ---
        relevant_skills = await self.skill_injector.get_relevant_skills(goal, user_id)
        if relevant_skills:
            await self._emit("info", f"Loaded {len(relevant_skills)} skill(s): {', '.join(s['name'] for s in relevant_skills)}")

        # --- PLANNING ---
        screenshot = await self.screen.get_screenshot_base64(grid=True)
        active_app, window_title, _ = await self.screen.get_desktop_state()

        plan = await self.planner.create_plan(
            goal=goal,
            screenshot_b64=screenshot,
            page_url=active_app,
            page_title=window_title,
            injected_skills=[s["content"] for s in relevant_skills],
            history=history,
        )

        await self._emit("plan", f"Plan ready ({len(plan.sub_tasks)} sub-tasks)", plan.to_dict())
        for risk in plan.risks:
            await self._emit("info", f"Risk noted: {risk}")

        # --- EXECUTION ---
        global_step = 0
        # Browser routing is decided once per goal (not per sub-task) to prevent
        # mid-goal mode switches when later sub-task descriptions lack web signals.
        use_browser_for_goal = self._browser_launched and is_browser_task(goal)

        while not plan.is_complete and global_step < max_steps:
            if self.is_interrupted:
                await self._emit("info", "Task interrupted.")
                break

            sub_task = plan.current_sub_task
            await self._emit(
                "subtask",
                f"Sub-task {sub_task.id}/{len(plan.sub_tasks)}: {sub_task.description}",
                {"id": sub_task.id},
            )

            sub_task.status = "in_progress"

            # --- HYBRID ROUTING: use goal-level decision (set once above) ---
            use_browser = use_browser_for_goal
            if use_browser:
                self.vision.set_browser_mode(True)
                await self._emit("info", f"Browser mode: sub-task routed to Playwright")
            else:
                self.vision.set_browser_mode(False)

            sub_task_done = False
            sub_task_steps: list[dict] = []
            consecutive_cache_hits = 0
            last_click_annotated: str = ""
            consecutive_no_change = 0
            last_narrations: list[str] = []
            carried_screenshot: str = ""
            prev_active_app = ""

            for _ in range(20):
                if self.is_interrupted:
                    break

                global_step += 1

                # ── SCREENSHOT + CONTEXT ─────────────────────────────────────
                aria_tree = ""
                if use_browser:
                    # Browser mode: viewport screenshot + ARIA tree
                    screenshot, aria_tree = await self.browser_agent.get_screenshot_and_context(grid=True)
                    active_app = await self.browser_agent.get_url() or "browser"
                    window_title = await self.browser_agent.get_title() or ""
                    print(f"[agent] step {global_step}: BROWSER mode url={active_app!r}")
                else:
                    # Desktop mode: full-screen capture
                    active_app, window_title, chrome_url = await self.screen.get_desktop_state()
                    if chrome_url and "chrome" not in active_app.lower():
                        effective_app = f"Google Chrome [{chrome_url}]"
                    elif "chrome" in active_app.lower() and chrome_url:
                        effective_app = f"Google Chrome [{chrome_url}]"
                    else:
                        effective_app = active_app

                    if carried_screenshot and effective_app != prev_active_app:
                        print(f"[agent] step {global_step}: discarding carried_screenshot (app changed)")
                        carried_screenshot = ""

                    prev_active_app = effective_app
                    active_app = effective_app

                    if carried_screenshot:
                        screenshot = carried_screenshot
                        carried_screenshot = ""
                        print(f"[agent] step {global_step}: using carried screenshot")
                    else:
                        raw_app, _, _ = await self.screen.get_desktop_state()
                        if chrome_url and "chrome" not in raw_app.lower():
                            await self.screen.open_app("Google Chrome")
                        screenshot = await self.screen.get_screenshot_base64(grid=True)
                        print(f"[agent] step {global_step}: fresh desktop screenshot")

                    print(f"[agent] step {global_step}: DESKTOP mode active_app={active_app!r}")

                await self._emit_screenshot(screenshot)

                # Store initial hash for change detection
                initial_hash = imagehash.phash(Image.open(io.BytesIO(base64.b64decode(screenshot))))

                # --- Cache lookup ---
                cached_action, cache_tier = await self.cache.lookup(screenshot, goal, active_app)
                if cached_action and consecutive_cache_hits < 2:
                    consecutive_cache_hits += 1
                    action = cached_action.copy()
                    action["step"] = global_step
                    action["cache_tier"] = cache_tier
                    last_click_annotated = ""
                else:
                    consecutive_cache_hits = 0
                    prior = sub_task_steps[-1] if sub_task_steps else {}
                    model_to_use = self.router.select_model(
                        prior_confidence=prior.get("confidence", 0.5),
                        prior_action_type=prior.get("type"),
                        page_url=active_app,
                        page_title=window_title,
                        step_number=global_step,
                        goal=sub_task.description,
                    )
                    self.vision.set_model(model_to_use)

                    action = await self.vision.reason_and_act(
                        screenshot_base64=screenshot,
                        goal=sub_task.description,
                        step_number=global_step,
                        previous_actions=sub_task_steps[-5:],
                        page_url=active_app,
                        page_title=window_title,
                        success_criteria=sub_task.success_criteria,
                        last_click_annotated=last_click_annotated,
                        aria_tree=aria_tree,
                    )
                    action["step"] = global_step

                await self._emit("action", action.get("narration", ""), action)

                if action["type"] == "done":
                    sub_task_done = True
                    break
                if action["type"] == "stuck":
                    break

                # --- Loop detection ---
                narration = action.get("narration", "")
                last_narrations.append(narration)
                if len(last_narrations) > 4:
                    last_narrations.pop(0)
                if len(last_narrations) >= 3 and len(set(last_narrations[-3:])) == 1:
                    await self._emit("info", f"Loop detected (same action x3): '{narration[:60]}' — forcing stuck")
                    break

                # --- Execute via appropriate agent ---
                if use_browser:
                    result = await self.browser_agent.execute(action)
                else:
                    result = await self.desktop_executor.execute(action)

                # Visual change detection
                page_changed = False
                if result.get("screenshot_after"):
                    after_img = Image.open(io.BytesIO(base64.b64decode(result["screenshot_after"])))
                    after_hash = imagehash.phash(after_img)
                    if (initial_hash - after_hash) > 2:
                        page_changed = True

                if not use_browser and not page_changed:
                    new_active_app = await self.screen.get_active_app()
                    if "chrome" in new_active_app.lower():
                        new_url = await self.screen.get_chrome_url()
                        if f"[{new_url}]" not in active_app:
                            page_changed = True

                if action.get("type") == "click" and not page_changed and result.get("success"):
                    result["note"] = "click_no_page_change"
                    consecutive_no_change += 1
                    if consecutive_no_change >= 3:
                        await self._emit("info", "3 consecutive clicks with no change — forcing re-plan")
                        break
                else:
                    consecutive_no_change = 0

                action["result"] = result
                sub_task_steps.append(action)
                all_completed_steps.append(action)
                self.memory.push_action_to_cache(self.session_id, action)

                last_click_annotated = result.get("click_annotated", "")
                post_screenshot = last_click_annotated or result.get("screenshot_after", "")
                if post_screenshot:
                    await self._emit_screenshot(post_screenshot)

                # Carry screenshot forward (desktop mode only — browser mode always re-captures)
                if not use_browser and result.get("screenshot_after"):
                    if action.get("type") == "open_url" and "chrome" in active_app.lower():
                        carried_screenshot = ""
                    else:
                        carried_screenshot = await self.screen.add_grid_to_b64(result["screenshot_after"])

                if global_step % 5 == 0:
                    await self._emit("stats", "Performance snapshot", {
                        "cache": self.cache.stats(),
                        "router": self.router.stats(),
                        "total_steps": global_step,
                    })

                await asyncio.sleep(0.3 if use_browser else 0.5)

            # --- Sub-task outcome ---
            if sub_task_done:
                sub_task.completed_steps = sub_task_steps
                plan.advance()
                await self._emit("subtask_done", f"Sub-task {sub_task.id} complete", {"id": sub_task.id})
            else:
                plan.mark_failed()
                await self._emit("info", f"Sub-task {sub_task.id} failed (attempt {sub_task.attempts})")

                if sub_task.attempts >= MAX_SUBTASK_ATTEMPTS:
                    await self._emit("thinking", "Re-planning from current state...")
                    screenshot = await self.screen.get_screenshot_base64(grid=True)
                    active_app, _, _ = await self.screen.get_desktop_state()
                    history = await self.memory.get_conversation_history(self.session_id, limit=6)
                    plan = await self.planner.replan(
                        goal=goal,
                        original_plan=plan,
                        stuck_sub_task=sub_task,
                        screenshot_b64=screenshot,
                        page_url=active_app,
                        completed_so_far=all_completed_steps,
                        history=history,
                    )
                    await self._emit("plan", f"Re-plan ready ({len(plan.sub_tasks)} remaining)", plan.to_dict())

        # --- POST-TASK ---
        if plan.is_complete:
            await self._emit("success", f"Goal complete! Took {global_step} steps across {len(plan.sub_tasks)} sub-tasks.")
            await self.memory.log_message(self.session_id, "assistant", f"Goal accomplished: {goal}", user_id)
            await self.memory.save_workflow(goal, all_completed_steps, user_id, self.session_id)
            asyncio.create_task(self._extract_and_save_skill(goal, plan, all_completed_steps, user_id))
        else:
            await self._emit("error", f"Goal not fully completed after {global_step} steps.")

        await self._emit("stats", "Final summary", {
            "cache": self.cache.stats(),
            "router": self.router.stats(),
            "total_steps": global_step,
        })
        return {"steps_taken": global_step, "plan": plan.to_dict()}

    async def interrupt(self, new_goal: Optional[str] = None):
        self.is_interrupted = True
        if new_goal:
            msg = f"Interrupted with new goal: {new_goal}"
            await self._emit("info", msg)
            await self.memory.log_message(self.session_id, "human", f"(INTERRUPT) {new_goal}")
            # Wait a moment for the loops to recognize the stop
            await asyncio.sleep(0.5)
            self.is_interrupted = False
            self._current_goal = new_goal
            # The next run_goal will fetch the history including the interruption
            await self.run_goal(new_goal)
        else:
            await self._emit("info", "Task stopped by user.")
            await self.memory.log_message(self.session_id, "human", "(STOP)")

    async def _extract_and_save_skill(self, goal, plan, steps, user_id):
        try:
            extractor = SkillExtractor()
            skill = await extractor.extract(goal, plan, steps)
            if skill:
                await self.skill_injector.store.save_skill(skill, user_id)
                await self._emit("info", f"New skill learned: '{skill['name']}'")
        except Exception as e:
            print(f"[Skill extraction] Error: {e}")

    async def _emit(self, event_type: str, message: str, data: dict = None):
        if self.websocket_send_fn:
            payload = {"type": event_type, "message": message}
            if data:
                payload["data"] = data
            try:
                await self.websocket_send_fn(payload)
            except Exception:
                pass

    async def _emit_screenshot(self, screenshot_b64: str):
        if self.websocket_send_fn:
            try:
                await self.websocket_send_fn({"type": "screenshot", "data": screenshot_b64})
            except Exception:
                pass

    async def cleanup(self):
        self.cache.invalidate_session()
        self.memory.clear_session_cache(self.session_id)
        if self._browser_launched:
            try:
                await self.browser_agent.close()
            except Exception:
                pass
