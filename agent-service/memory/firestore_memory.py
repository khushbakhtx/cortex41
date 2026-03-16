"""
Firestore-backed memory system for cortex41.
Stores workflows, session logs, and user preferences.
Uses Gemini text-embedding-004 for semantic similarity matching.
"""

import math
import json
from datetime import datetime, timezone
from typing import Optional

import google.generativeai as genai
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.config import (
    GOOGLE_CLOUD_PROJECT,
    FIRESTORE_COLLECTION_WORKFLOWS,
    FIRESTORE_COLLECTION_SESSIONS,
    GEMINI_API_KEY,
)

genai.configure(api_key=GEMINI_API_KEY)


class FirestoreMemory:
    def __init__(self):
        try:
            self.db = firestore.AsyncClient(project=GOOGLE_CLOUD_PROJECT)
            self.workflows_col = self.db.collection(FIRESTORE_COLLECTION_WORKFLOWS)
            self.sessions_col = self.db.collection(FIRESTORE_COLLECTION_SESSIONS)
            # New collection for human/AI conversational turns
            self.messages_col = self.db.collection("cortex41_messages")
        except Exception as e:
            print(f"[Memory] Firestore unavailable (run 'gcloud auth application-default login' to enable persistence): {e}")
            self.db = None
            self.workflows_col = None
            self.sessions_col = None
            self.messages_col = None
        # In-memory action cache per session (hot path, avoids Firestore round-trip)
        self._session_action_cache: dict[str, list[dict]] = {}

    async def save_workflow(
        self,
        goal: str,
        steps: list[dict],
        user_id: str,
        session_id: str,
    ) -> bool:
        """Save a completed workflow with its goal embedding."""
        if self.db is None:
            return False
        try:
            embedding = await self._get_embedding(goal)
            doc_ref = self.workflows_col.document()
            await doc_ref.set({
                "goal": goal,
                "goal_embedding": embedding,
                "steps": self._sanitize_steps(steps),
                "user_id": user_id,
                "session_id": session_id,
                "step_count": len(steps),
                "created_at": datetime.now(timezone.utc),
                "success": True,
            })
            return True
        except Exception as e:
            print(f"[Memory] Failed to save workflow: {e}")
            return False

    async def find_similar_workflow(
        self,
        goal: str,
        user_id: str,
        threshold: float = 0.85,
    ) -> Optional[dict]:
        """
        Find a previously saved workflow similar to the given goal.
        Uses cosine similarity between Gemini embeddings.
        Returns the most similar workflow above threshold, or None.
        """
        if self.db is None:
            return None
        try:
            query_embedding = await self._get_embedding(goal)

            docs = (
                self.workflows_col
                .where(filter=FieldFilter("user_id", "==", user_id))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(100)
            )

            best_match = None
            best_score = 0.0

            async for doc in docs.stream():
                data = doc.to_dict()
                stored_embedding = data.get("goal_embedding", [])
                if not stored_embedding:
                    continue
                score = _cosine_similarity(query_embedding, stored_embedding)
                if score > best_score:
                    best_score = score
                    best_match = data

            if best_match and best_score >= threshold:
                return {"workflow": best_match, "similarity": best_score}
            return None

        except Exception as e:
            print(f"[Memory] Failed to find workflow: {e}")
            return None

    async def log_session_event(self, session_id: str, event: dict):
        """Append an event to a session's log in Firestore."""
        if self.db is None:
            return
        try:
            doc_ref = self.sessions_col.document(session_id)
            await doc_ref.set({
                "events": firestore.ArrayUnion([{
                    **{k: v for k, v in event.items() if k not in ("screenshot_after", "raw_reasoning")},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }])
            }, merge=True)
        except Exception as e:
            print(f"[Memory] Failed to log session event: {e}")

    def push_action_to_cache(self, session_id: str, action: dict):
        """Push action to in-memory hot cache for this session."""
        if session_id not in self._session_action_cache:
            self._session_action_cache[session_id] = []
        cache = self._session_action_cache[session_id]
        cache.append(action)
        # Keep last 50 actions in memory
        if len(cache) > 50:
            self._session_action_cache[session_id] = cache[-50:]

    def get_recent_actions(self, session_id: str, limit: int = 5) -> list[dict]:
        """Return recent actions from in-memory cache (hot path)."""
        cache = self._session_action_cache.get(session_id, [])
        return cache[-limit:]

    def clear_session_cache(self, session_id: str):
        self._session_action_cache.pop(session_id, None)

    async def log_message(self, session_id: str, role: str, text: str, user_id: str = "default"):
        """Save a human or assistant message to the session's conversation history."""
        if self.db is None:
            return
        try:
            doc_ref = self.messages_col.document()
            await doc_ref.set({
                "session_id": session_id,
                "user_id": user_id,
                "role": role,
                "text": text,
                "created_at": datetime.now(timezone.utc),
            })
        except Exception as e:
            print(f"[Memory] Failed to log message: {e}")

    async def get_conversation_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """Fetch the last few conversational turns for this session."""
        if self.db is None:
            return []
        try:
            docs = (
                self.messages_col
                .where(filter=FieldFilter("session_id", "==", session_id))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )
            
            history = []
            async for doc in docs.stream():
                data = doc.to_dict()
                history.append({
                    "role": data.get("role"),
                    "text": data.get("text"),
                    "created_at": data.get("created_at")
                })
            
            # Sort chronologically for the Planner
            history.sort(key=lambda x: x["created_at"])
            return history
        except Exception as e:
            print(f"[Memory] Failed to get conversation history: {e}")
            return []

    async def _get_embedding(self, text: str) -> list[float]:
        """Get Gemini text embedding for semantic similarity."""
        try:
            result = genai.embed_content(
                model="models/gemini-embedding-001",
                content=text[:2000],
                task_type="SEMANTIC_SIMILARITY",
            )
            return result["embedding"]
        except Exception as e:
            print(f"[Memory] Embedding failed: {e}")
            return []

    def _sanitize_steps(self, steps: list[dict]) -> list[dict]:
        """
        Recursively remove large base64 screenshot data before storing to Firestore.
        Firestore has a 1MB limit per document.
        """
        keys_to_remove = {"screenshot_after", "screenshot_before", "click_annotated", "raw_reasoning"}
        
        def _clean(obj):
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items() if k not in keys_to_remove}
            elif isinstance(obj, list):
                return [_clean(i) for i in obj]
            return obj

        return [_clean(step) for step in steps]


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
