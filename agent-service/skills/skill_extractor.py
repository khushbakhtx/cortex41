"""
Post-task skill extractor for cortex41.
After a successful task, reflects on what non-obvious knowledge was gained
and writes a structured skill for future use.
Uses Gemini Pro — slow offline step, not in the hot path.
"""

import json
import re
from typing import Optional

import google.generativeai as genai

from backend.config import GEMINI_API_KEY, PRO_MODEL
from backend.agent.planner import TaskPlan

genai.configure(api_key=GEMINI_API_KEY)


EXTRACTOR_SYSTEM_PROMPT = """
You are the self-improvement module of cortex41, a UI navigation agent.

After a task completes, your job is to extract reusable, generalizable knowledge
from what happened — specifically knowledge that would make future similar tasks
faster, more reliable, or require fewer retries.

ONLY extract a skill if there is genuinely non-obvious knowledge. Do NOT write a skill for:
- Simple tasks that any reasonable agent would handle correctly first try
- Generic knowledge (e.g., "click buttons", "type in fields")
- One-off tasks unlikely to repeat

DO write a skill for:
- Site-specific UI quirks (date pickers, multi-step modals, shadow DOM elements)
- Sequences that must happen in a specific order to work
- Elements that look clickable but aren't, or hidden elements that need scrolling
- Error recovery patterns that worked
- CAPTCHA bypass strategies (e.g., wait longer, try alternative flow)

OUTPUT: Respond ONLY with valid JSON or the exact string "NO_SKILL" if nothing useful was learned.

{
  "name": "short_snake_case_identifier",
  "tags": ["site name", "task type", "ui element"],
  "applies_to_urls": ["partial URL patterns this applies to"],
  "content": "2-5 sentence description of the non-obvious knowledge. Be specific about sequences, element labels, and gotchas.",
  "gotchas": ["List of things that go wrong if you don't follow this skill"],
  "success_rate": 0.9
}
"""


class SkillExtractor:
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name=PRO_MODEL,
            system_instruction=EXTRACTOR_SYSTEM_PROMPT,
        )

    async def extract(
        self,
        goal: str,
        plan: TaskPlan,
        completed_steps: list[dict],
    ) -> Optional[dict]:
        """
        Reflect on a completed task and extract a reusable skill.
        Returns skill dict or None.
        """
        step_summary = self._summarize_steps(completed_steps)
        retried_sub_tasks = [st for st in plan.sub_tasks if st.attempts > 1]
        retry_info = ""
        if retried_sub_tasks:
            retry_info = "\nSUB-TASKS THAT REQUIRED RETRIES:\n" + "\n".join(
                f"  - '{st.description}' failed {st.attempts - 1} time(s) before succeeding"
                for st in retried_sub_tasks
            )

        prompt = f"""COMPLETED GOAL: {goal}
TOTAL STEPS TAKEN: {len(completed_steps)}
PLAN COMPLEXITY: {plan.estimated_complexity}
WAS THIS A RE-PLAN TASK: {plan.is_replan}

STEP SUMMARY:
{step_summary}
{retry_info}

RISKS FLAGGED BEFORE THE TASK: {', '.join(plan.risks) or 'none'}

Based on this, what non-obvious, reusable knowledge was gained?
Respond with the skill JSON or "NO_SKILL".
"""
        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=1024,
            ),
        )

        text = response.text.strip()

        if "NO_SKILL" in text[:20]:
            return None

        clean = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        try:
            skill = json.loads(clean)
            if "name" in skill and "content" in skill:
                return skill
        except json.JSONDecodeError:
            pass

        return None

    def _summarize_steps(self, steps: list[dict]) -> str:
        lines = []
        for s in steps:
            action_type = s.get("type", "?")
            narration = s.get("narration", "")
            confidence = s.get("confidence", 0)
            result_ok = s.get("result", {}).get("success", True)
            status = "OK" if result_ok else "FAIL"
            lines.append(
                f"  [{status}] Step {s.get('step', '?')}: [{action_type}] {narration} (conf={confidence:.2f})"
            )
        return "\n".join(lines)
