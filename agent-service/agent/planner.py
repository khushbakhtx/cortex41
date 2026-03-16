"""
Hierarchical goal planner for cortex41.
Runs ONCE per goal (or on re-plan) to produce an ordered list of sub-tasks.
The executor loop in cortex41_agent.py then fulfils each sub-task one step at a time.
Uses Gemini Pro exclusively — planning requires full reasoning capacity.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

from backend.config import GEMINI_API_KEY, PRO_MODEL

genai.configure(api_key=GEMINI_API_KEY)


PLANNER_SYSTEM_PROMPT = """
You are the strategic planning module of cortex41, a universal UI navigation agent.

Your job: given a user's goal and the current screen state, produce a precise, ordered
list of sub-tasks that — when executed step by step — will accomplish the goal.

RULES:
1. Each sub-task must be a single, atomic, verifiable objective.
   GOOD: "Click the search field and type 'Paris CDG'"
   BAD:  "Search for flights" (too vague) or "Search and book" (two things)
2. For ANY web task: NEVER create a separate "open browser" sub-task.
   Instead, start directly with the URL: "Open https://youtube.com in Chrome"
   The executor will use open_url which handles browser + URL in one step.
3. Include navigation sub-tasks explicitly: "Navigate to google.com/flights"
4. Include verification sub-tasks where critical: "Verify the date shows June 1"
5. Maximum 15 sub-tasks per plan. If a goal needs more, it's too broad — flag it.
6. Each sub-task must have a clear success_criteria: what does the screen look like when done?
7. If injected skills are provided, use them to make sub-tasks more precise.
8. **SPEED**: Group multiple actions into one sub-task (e.g., "Click Search and type 'Nvidia'"). Avoid separate "Verify" sub-tasks for trivial actions like opening a URL.

SKILL INJECTION: If you are given skills, they contain site-specific knowledge from
prior successful runs. Trust them. Incorporate their specific advice into your sub-tasks.

OUTPUT FORMAT — respond ONLY with valid JSON, no preamble, no markdown fences:
{
  "goal_understood": "one sentence restatement of the goal",
  "estimated_complexity": "simple|medium|complex",
  "sub_tasks": [
    {
      "id": 1,
      "description": "Navigate to google.com/travel/flights",
      "success_criteria": "Page title contains 'Flights' and search form is visible",
      "estimated_steps": 2,
      "requires_skill": null
    }
  ],
  "risks": ["CAPTCHA possible on booking step", "Date picker may require two clicks"],
  "fallback_url": "https://www.kayak.com/flights"
}
"""


@dataclass
class SubTask:
    id: int
    description: str
    success_criteria: str
    estimated_steps: int = 3
    requires_skill: Optional[str] = None
    status: str = "pending"   # pending | in_progress | done | failed
    attempts: int = 0
    completed_steps: list = field(default_factory=list)


@dataclass
class TaskPlan:
    goal: str
    goal_understood: str
    estimated_complexity: str
    sub_tasks: list[SubTask]
    risks: list[str]
    fallback_url: Optional[str]
    current_index: int = 0
    is_replan: bool = False

    @property
    def current_sub_task(self) -> Optional[SubTask]:
        if self.current_index < len(self.sub_tasks):
            return self.sub_tasks[self.current_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_index >= len(self.sub_tasks)

    def advance(self):
        if self.current_sub_task:
            self.current_sub_task.status = "done"
        self.current_index += 1

    def mark_failed(self):
        if self.current_sub_task:
            self.current_sub_task.status = "failed"
            self.current_sub_task.attempts += 1

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "goal_understood": self.goal_understood,
            "complexity": self.estimated_complexity,
            "sub_tasks": [
                {
                    "id": st.id,
                    "description": st.description,
                    "success_criteria": st.success_criteria,
                    "status": st.status,
                    "attempts": st.attempts,
                    "estimated_steps": st.estimated_steps,
                }
                for st in self.sub_tasks
            ],
            "risks": self.risks,
            "current_index": self.current_index,
            "is_replan": self.is_replan,
        }


class Planner:
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name=PRO_MODEL,
            system_instruction=PLANNER_SYSTEM_PROMPT,
        )

    async def create_plan(
        self,
        goal: str,
        screenshot_b64: str,
        page_url: str,
        page_title: str,
        injected_skills: list[str] | None = None,
        history: list[dict] | None = None,
    ) -> TaskPlan:
        """Generate a full task plan for the given goal. Called once at task start."""
        skills_block = ""
        if injected_skills:
            skills_block = "\n\nINJECTED SKILLS (site-specific knowledge from prior runs):\n"
            for i, skill in enumerate(injected_skills, 1):
                skills_block += f"{i}. {skill}\n"

        prompt = f"""GOAL: {goal}

CURRENT STATE:
- URL: {page_url}
- Page title: {page_title}
- Screenshot attached
{skills_block}
- CONVERSATION HISTORY:
{chr(10).join([f"  {h['role'].upper()}: {h['text']}" for h in (history or [])]) or "  (None)"}

Generate the task plan now."""

        image_part = {"mime_type": "image/png", "data": screenshot_b64}

        response = await self.model.generate_content_async(
            [prompt, image_part],
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                max_output_tokens=2048,
            ),
        )

        return self._parse_plan(goal, response.text)

    async def replan(
        self,
        goal: str,
        original_plan: TaskPlan,
        stuck_sub_task: SubTask,
        screenshot_b64: str,
        page_url: str,
        completed_so_far: list[dict],
        history: list[dict] | None = None,
    ) -> TaskPlan:
        """Re-plan from current stuck state when a sub-task fails MAX_SUBTASK_ATTEMPTS times."""
        completed_descriptions = [
            f"  - {st.description}"
            for st in original_plan.sub_tasks
            if st.status == "done"
        ]

        prompt = f"""ORIGINAL GOAL: {goal}

SITUATION: Stuck on sub-task {stuck_sub_task.id}: "{stuck_sub_task.description}"
The sub-task has failed {stuck_sub_task.attempts} times.

COMPLETED SUB-TASKS SO FAR:
{chr(10).join(completed_descriptions) or "  (none)"}

CURRENT STATE:
- URL: {page_url}
- Screenshot attached

- CONVERSATION HISTORY (Current Session):
{chr(10).join([f"  {h['role'].upper()}: {h['text']}" for h in (history or [])]) or "  (None)"}

Please generate a REVISED plan starting from the current screen state.
Remaining goal: still need to complete "{goal}" — adapt the approach."""

        image_part = {"mime_type": "image/png", "data": screenshot_b64}

        response = await self.model.generate_content_async(
            [prompt, image_part],
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )

        plan = self._parse_plan(goal, response.text)
        plan.is_replan = True
        return plan

    def _parse_plan(self, goal: str, response_text: str) -> TaskPlan:
        """Parse Gemini's JSON plan response into a TaskPlan object."""
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", response_text).strip()

        data = None
        # First attempt: parse cleaned text directly
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            pass

        # Second attempt: find the outermost JSON object in the response
        if data is None:
            match = re.search(r'\{.*\}', clean, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        if data is None:
            # Fallback: single sub-task plan if JSON parsing fails
            print(f"[planner] WARNING: plan parse failed. Raw response:\n{response_text[:500]}")
            return TaskPlan(
                goal=goal,
                goal_understood=goal,
                estimated_complexity="simple",
                sub_tasks=[
                    SubTask(
                        id=1,
                        description=f"Complete goal: {goal}",
                        success_criteria="Goal appears accomplished on screen",
                        estimated_steps=10,
                    )
                ],
                risks=["Plan parsing failed — using fallback single-task plan"],
                fallback_url=None,
            )

        sub_tasks = [
            SubTask(
                id=st["id"],
                description=st["description"],
                success_criteria=st.get("success_criteria", ""),
                estimated_steps=st.get("estimated_steps", 3),
                requires_skill=st.get("requires_skill"),
            )
            for st in data.get("sub_tasks", [])
        ]

        return TaskPlan(
            goal=goal,
            goal_understood=data.get("goal_understood", goal),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            sub_tasks=sub_tasks,
            risks=data.get("risks", []),
            fallback_url=data.get("fallback_url"),
        )
