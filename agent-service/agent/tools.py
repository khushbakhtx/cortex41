"""
Tool definitions for cortex41.
Plain async functions — registered as callable tools for the agent loop.
All AI calls go through the GenAI SDK (google-generativeai).
"""

from typing import Optional

from backend.browser.browser_controller import Cortex41BrowserController
from backend.browser.action_executor import Cortex41ActionExecutor
from backend.vision.gemini_vision import GeminiVisionEngine
from backend.memory.firestore_memory import FirestoreMemory


def create_tools(
    browser: Cortex41BrowserController,
    executor: Cortex41ActionExecutor,
    vision: GeminiVisionEngine,
    memory: FirestoreMemory,
    session_id: str,
) -> dict:
    """
    Factory: creates all tools bound to a specific session's resources.
    Returns a dict of tool_name -> async callable.
    """

    async def take_screenshot_and_reason(goal: str, step_number: int) -> dict:
        """
        Capture current screen state and use Gemini Vision to decide next action.
        Returns the action dict with narration and confidence.
        """
        screenshot = await browser.get_screenshot_base64()
        page_url = await browser.get_page_url()
        page_title = await browser.get_page_title()
        previous_actions = memory.get_recent_actions(session_id, limit=5)

        action = await vision.reason_and_act(
            screenshot_base64=screenshot,
            goal=goal,
            step_number=step_number,
            previous_actions=previous_actions,
            page_url=page_url,
            page_title=page_title,
        )
        return action

    async def execute_action(action: dict) -> dict:
        """Execute a browser action. Returns success status and post-action screenshot."""
        result = await executor.execute(action)
        return result

    async def navigate_to_url(url: str) -> dict:
        """Navigate browser to a specific URL."""
        result = await executor.execute({"type": "navigate", "url": url})
        return result

    async def recall_workflow(goal_description: str, user_id: str = "default") -> Optional[dict]:
        """
        Search Firestore for a previously learned workflow matching this goal.
        Returns workflow steps if found, None otherwise.
        """
        return await memory.find_similar_workflow(goal_description, user_id)

    async def save_workflow(goal: str, steps: list[dict], user_id: str = "default") -> bool:
        """Save a completed workflow to Firestore for future recall."""
        return await memory.save_workflow(goal, steps, user_id, session_id)

    async def emit_narration(message: str, action_type: str = "info") -> bool:
        """Placeholder — wired to WebSocket in cortex41_agent.py."""
        return True

    return {
        "take_screenshot_and_reason": take_screenshot_and_reason,
        "execute_action": execute_action,
        "navigate_to_url": navigate_to_url,
        "recall_workflow": recall_workflow,
        "save_workflow": save_workflow,
        "emit_narration": emit_narration,
    }
