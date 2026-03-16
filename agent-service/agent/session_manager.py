"""
Multi-session manager for cortex41.
Tracks active agent runners per session ID.
Uses a simple dict (replaces google.adk.sessions.InMemorySessionService
with equivalent functionality that doesn't depend on ADK's internal API).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.cortex41_agent import Cortex41AgentRunner


class SessionManager:
    """
    In-memory session registry.
    Tracks active Cortex41AgentRunner instances keyed by session_id.
    """

    def __init__(self):
        self._sessions: dict[str, "Cortex41AgentRunner"] = {}

    def register(self, session_id: str, runner: "Cortex41AgentRunner"):
        self._sessions[session_id] = runner

    def get(self, session_id: str) -> "Cortex41AgentRunner | None":
        return self._sessions.get(session_id)

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)

    def active_count(self) -> int:
        return len(self._sessions)

    def all_session_ids(self) -> list[str]:
        return list(self._sessions.keys())


# Module-level singleton
_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _manager
