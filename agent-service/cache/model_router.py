"""
Adaptive model router for cortex41.
Routes each vision step to Gemini Flash or Gemini Pro.
Target: ~70% Flash routing -> ~7x average cost reduction vs naive all-Pro.
"""

from backend.config import FLASH_MODEL, PRO_MODEL, FLASH_CONFIDENCE_FLOOR


SIMPLE_ACTION_TYPES = {
    "click", "scroll", "navigate", "key", "wait",
    "browser_navigate", "browser_click", "browser_type",
}

# Pages that require full Pro reasoning (forms, auth, payments)
COMPLEX_PAGE_SIGNALS = [
    "checkout", "payment", "confirm", "captcha",
    "login", "signin", "verify", "2fa", "auth",
    "form", "signup", "register",
]

# Goal keywords that imply complex reasoning needed
COMPLEX_GOAL_KEYWORDS = [
    "fill", "enter", "type", "select", "choose", "compare",
    "book", "purchase", "checkout", "register", "sign up",
]


class ModelRouter:
    def __init__(self):
        self._routing_log: list[dict] = []
        self._flash_count = 0
        self._pro_count = 0

    def select_model(
        self,
        prior_confidence: float,
        prior_action_type: str | None,
        page_url: str,
        page_title: str,
        step_number: int,
        goal: str,
    ) -> str:
        reason = ""

        if step_number <= 1:
            model, reason = PRO_MODEL, "first_step_always_pro"

        elif self._is_complex_page(page_url, page_title):
            model, reason = PRO_MODEL, "complex_page_signal"

        elif self._is_complex_goal(goal):
            model, reason = PRO_MODEL, "complex_goal_keywords"

        elif (
            prior_confidence >= FLASH_CONFIDENCE_FLOOR
            and prior_action_type in SIMPLE_ACTION_TYPES
        ):
            model = FLASH_MODEL
            reason = f"high_confidence_simple (conf={prior_confidence:.2f})"

        else:
            model = PRO_MODEL
            reason = f"default_pro (conf={prior_confidence:.2f})"

        self._routing_log.append({
            "step": step_number,
            "model": "flash" if model == FLASH_MODEL else "pro",
            "reason": reason,
            "prior_confidence": prior_confidence,
        })

        if model == FLASH_MODEL:
            self._flash_count += 1
        else:
            self._pro_count += 1

        return model

    def stats(self) -> dict:
        total = self._flash_count + self._pro_count
        flash_pct = (self._flash_count / total * 100) if total else 0
        pro_cost = self._pro_count * 0.030
        flash_cost = self._flash_count * 0.003
        naive_cost = total * 0.030
        savings = naive_cost - (pro_cost + flash_cost)
        return {
            "total_steps": total,
            "flash_steps": self._flash_count,
            "pro_steps": self._pro_count,
            "flash_pct": round(flash_pct, 1),
            "estimated_cost_usd": round(pro_cost + flash_cost, 4),
            "estimated_savings_vs_naive_usd": round(savings, 4),
            "routing_log": self._routing_log[-10:],
        }

    def _is_complex_page(self, url: str, title: str) -> bool:
        combined = (url + title).lower()
        return any(s in combined for s in COMPLEX_PAGE_SIGNALS)

    def _is_complex_goal(self, goal: str) -> bool:
        return any(k in goal.lower() for k in COMPLEX_GOAL_KEYWORDS)
