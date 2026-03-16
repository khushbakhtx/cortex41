"""
Parses Gemini's structured <action> JSON from vision reasoning responses.
"""

import re
import json


def parse_action_from_response(response_text: str) -> dict:
    """
    Extract and validate the <action> JSON block from Gemini's response.
    Returns a normalized action dict. Falls back to 'wait' on parse failure.
    """
    pattern = r"<action>\s*(.*?)\s*</action>"
    match = re.search(pattern, response_text, re.DOTALL)

    if not match:
        return {
            "type": "wait",
            "duration_ms": 1000,
            "narration": "Thinking...",
            "confidence": 0.0,
            "parse_error": "No <action> block found in response",
        }

    json_str = match.group(1).strip()
    # Strip markdown code fences if present
    json_str = re.sub(r"^```(?:json)?\s*|\s*```$", "", json_str, flags=re.MULTILINE).strip()

    try:
        action = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "type": "wait",
            "duration_ms": 1000,
            "narration": "Parse error, waiting...",
            "confidence": 0.0,
            "parse_error": str(e),
        }

    # Normalize defaults
    action.setdefault("confidence", 0.5)
    action.setdefault("narration", f"Executing {action.get('type', 'unknown')} action")
    action.setdefault("goal_progress", "unknown")

    action_type = action.get("type", "wait")

    # Clamp coordinates — use browser viewport bounds for browser actions
    BROWSER_ACTION_TYPES = {"browser_navigate", "browser_click", "browser_type"}
    if action_type in BROWSER_ACTION_TYPES:
        # Browser viewport: 1280x800
        if "x" in action:
            action["x"] = max(0, min(1280, int(action["x"])))
        if "y" in action:
            action["y"] = max(0, min(800, int(action["y"])))
    else:
        # Desktop: support large screens
        if "x" in action:
            action["x"] = max(0, min(3840, int(action["x"])))
        if "y" in action:
            action["y"] = max(0, min(2160, int(action["y"])))

    return action


def extract_reasoning(response_text: str) -> str:
    """Extract <reasoning> block from Gemini response."""
    match = re.search(r"<reasoning>(.*?)</reasoning>", response_text, re.DOTALL)
    return match.group(1).strip() if match else ""
