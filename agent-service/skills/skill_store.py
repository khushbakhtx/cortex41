"""
Firestore-backed skill store for cortex41.
Skills are site-specific or task-type knowledge extracted after successful tasks.
Retrieved by semantic embedding similarity before planning begins.
"""

import math
from datetime import datetime, timezone

import google.generativeai as genai
from google.cloud import firestore

from backend.config import (
    GEMINI_API_KEY,
    GOOGLE_CLOUD_PROJECT,
    SKILLS_COLLECTION,
    SKILL_RETRIEVAL_THRESHOLD,
    SKILL_MIN_SUCCESS_RATE,
)

genai.configure(api_key=GEMINI_API_KEY)


class SkillStore:
    def __init__(self):
        try:
            self.db = firestore.AsyncClient(project=GOOGLE_CLOUD_PROJECT)
            self.col = self.db.collection(SKILLS_COLLECTION)
        except Exception as e:
            print(f"[Skills] Firestore unavailable, skill persistence disabled: {e}")
            self.db = None
            self.col = None

    async def save_skill(self, skill: dict, user_id: str) -> str:
        """
        Persist a skill to Firestore with its embedding.
        Returns the new document ID.
        If a very similar skill already exists (sim >= 0.95), merge/update instead.
        """
        if self.db is None:
            return ""
        embed_text = skill["content"] + " " + " ".join(skill.get("tags", []))
        embedding = await self._embed(embed_text)

        # Check for near-duplicate skill
        existing = await self._find_near_duplicate(embedding, user_id)
        if existing:
            doc_ref = self.col.document(existing["doc_id"])
            await doc_ref.update({
                "content": skill["content"],
                "gotchas": skill.get("gotchas", []),
                "times_used": firestore.Increment(1),
                "last_updated_at": datetime.now(timezone.utc),
                "embedding": embedding,
            })
            return existing["doc_id"]

        # New skill
        doc_ref = self.col.document()
        await doc_ref.set({
            "name": skill.get("name", "unnamed_skill"),
            "tags": skill.get("tags", []),
            "applies_to_urls": skill.get("applies_to_urls", []),
            "content": skill["content"],
            "gotchas": skill.get("gotchas", []),
            "embedding": embedding,
            "user_id": user_id,
            "success_rate": skill.get("success_rate", 0.9),
            "times_used": 0,
            "created_at": datetime.now(timezone.utc),
            "last_used_at": None,
            "enabled": True,
        })
        return doc_ref.id

    async def get_relevant_skills(
        self,
        goal: str,
        user_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """Retrieve the top-k most relevant skills for this goal."""
        if self.db is None:
            return []
        query_embedding = await self._embed(goal)

        docs = (
            self.col
            .where(filter=firestore.FieldFilter("user_id", "==", user_id))
            .where(filter=firestore.FieldFilter("enabled", "==", True))
        )

        results = []
        try:
            async for doc in docs.stream():
                data = doc.to_dict()
                if data.get("success_rate", 1.0) < SKILL_MIN_SUCCESS_RATE:
                    continue
                stored_emb = data.get("embedding", [])
                if not stored_emb:
                    continue
                score = _cosine_sim(query_embedding, stored_emb)
                if score >= SKILL_RETRIEVAL_THRESHOLD:
                    results.append({**data, "similarity": score, "doc_id": doc.id})
        except Exception as e:
            print(f"[Skills] Firestore query failed, disabling: {e}")
            self.db = None
            return []

        results.sort(key=lambda x: x["similarity"], reverse=True)
        top = results[:top_k]

        # Update last_used_at for retrieved skills (fire-and-forget)
        for skill in top:
            try:
                self.col.document(skill["doc_id"]).update({
                    "last_used_at": datetime.now(timezone.utc),
                    "times_used": firestore.Increment(1),
                })
            except Exception:
                pass

        return top

    async def list_all_skills(self, user_id: str) -> list[dict]:
        """Return all skills for a user (for skill library UI)."""
        if self.db is None:
            return []
        docs = (
            self.col
            .where(filter=firestore.FieldFilter("user_id", "==", user_id))
            .order_by("times_used", direction=firestore.Query.DESCENDING)
        )
        skills = []
        try:
            async for doc in docs.stream():
                data = doc.to_dict()
                data["doc_id"] = doc.id
                data.pop("embedding", None)
                skills.append(data)
        except Exception as e:
            print(f"[Skills] Firestore list failed: {e}")
            self.db = None
        return skills

    async def disable_skill(self, doc_id: str):
        """Mark a skill as disabled (user feedback it's wrong)."""
        if self.db is None:
            return
        await self.col.document(doc_id).update({"enabled": False})

    async def _find_near_duplicate(
        self, embedding: list[float], user_id: str, threshold: float = 0.95
    ) -> dict | None:
        if self.db is None:
            return None
        docs = self.col.where(filter=firestore.FieldFilter("user_id", "==", user_id))
        try:
            async for doc in docs.stream():
                data = doc.to_dict()
                stored_emb = data.get("embedding", [])
                if stored_emb and _cosine_sim(embedding, stored_emb) >= threshold:
                    return {**data, "doc_id": doc.id}
        except Exception as e:
            print(f"[Skills] Firestore near-dup check failed: {e}")
            self.db = None
        return None

    async def _embed(self, text: str) -> list[float]:
        try:
            result = genai.embed_content(
                model="models/gemini-embedding-001",
                content=text[:2000],
                task_type="SEMANTIC_SIMILARITY",
            )
            return result["embedding"]
        except Exception as e:
            print(f"[Skills] Embedding failed: {e}")
            return []


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0
