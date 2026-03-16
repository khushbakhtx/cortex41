"""
Retrieves relevant skills from SkillStore and formats them
for injection into the Planner system prompt.
"""

from backend.skills.skill_store import SkillStore


class SkillInjector:
    def __init__(self):
        self.store = SkillStore()

    async def get_relevant_skills(self, goal: str, user_id: str) -> list[dict]:
        """Retrieve top-3 skills relevant to this goal."""
        return await self.store.get_relevant_skills(goal, user_id, top_k=3)

    def format_for_prompt(self, skills: list[dict]) -> str:
        """
        Format retrieved skills as a human-readable block for prompt injection.
        This string is appended to the Planner system prompt.
        """
        if not skills:
            return ""

        lines = ["\n=== INJECTED SKILLS (knowledge from prior successful runs) ===\n"]
        for i, skill in enumerate(skills, 1):
            times_used = skill.get("times_used", 0)
            success_rate = skill.get("success_rate", 0.9)
            lines.append(
                f"SKILL {i}: {skill['name']} "
                f"(used {times_used}x, success rate {success_rate:.0%})"
            )
            if skill.get("applies_to_urls"):
                lines.append(f"  Applies to: {', '.join(skill['applies_to_urls'])}")
            lines.append(f"  Knowledge: {skill['content']}")
            if skill.get("gotchas"):
                lines.append(f"  Gotchas: {'; '.join(skill['gotchas'])}")
            lines.append("")

        lines.append("=== END SKILLS ===\n")
        return "\n".join(lines)
