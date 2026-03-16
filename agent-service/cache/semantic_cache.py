"""
Semantic cache for cortex41.
Two-tier cache:
  Tier 1: In-memory perceptual hash (pHash) — zero-latency, session-scoped
  Tier 2: Firestore embedding similarity — cross-session, persistent

Prevents redundant Gemini Vision calls for visually identical or near-identical states.
"""

import io
import base64
import math
import time

from PIL import Image
import imagehash
import google.generativeai as genai
from google.cloud import firestore

from backend.config import (
    GEMINI_API_KEY,
    GOOGLE_CLOUD_PROJECT,
    FIRESTORE_COLLECTION_SCREENSHOT_CACHE,
    CACHE_PHASH_THRESHOLD,
    CACHE_EMBEDDING_THRESHOLD,
    CACHE_TTL_SECONDS,
)

genai.configure(api_key=GEMINI_API_KEY)


class SemanticCache:
    def __init__(self, session_id: str):
        self.session_id = session_id
        try:
            self.db = firestore.AsyncClient(project=GOOGLE_CLOUD_PROJECT)
            self.cache_col = self.db.collection(FIRESTORE_COLLECTION_SCREENSHOT_CACHE)
        except Exception as e:
            print(f"[Cache] Firestore unavailable, tier-2 cache disabled: {e}")
            self.db = None
            self.cache_col = None

        # Tier 1: In-memory pHash store — { phash_str: {action, timestamp, hits} }
        self._memory_cache: dict[str, dict] = {}

        # Stats
        self._total_lookups = 0
        self._memory_hits = 0
        self._firestore_hits = 0
        self._misses = 0
        self._tokens_saved = 0

    async def lookup(
        self,
        screenshot_b64: str,
        goal: str,
        page_url: str,
    ) -> tuple[dict | None, str]:
        """
        Look up whether we have a cached action for this visual state.

        Returns:
            (action_dict, cache_tier) where cache_tier is:
              "memory_hit"     -> exact pHash match in session memory
              "firestore_hit"  -> embedding similarity match in Firestore
              "miss"           -> no cache hit, must call Gemini
        """
        self._total_lookups += 1
        img = _b64_to_image(screenshot_b64)
        current_phash = imagehash.phash(img)

        # --- Tier 1: pHash in-memory lookup ---
        for stored_phash_str, entry in self._memory_cache.items():
            stored_phash = imagehash.hex_to_hash(stored_phash_str)
            hamming = current_phash - stored_phash

            if hamming <= CACHE_PHASH_THRESHOLD:
                if time.time() - entry["timestamp"] < CACHE_TTL_SECONDS:
                    self._memory_hits += 1
                    self._tokens_saved += 2000
                    entry["hits"] = entry.get("hits", 0) + 1
                    return entry["action"], "memory_hit"

        # --- Tier 2: Firestore embedding similarity ---
        if self.db is None:
            self._misses += 1
            return None, "miss"

        context_text = f"Goal: {goal} | URL: {page_url} | Screenshot hash: {str(current_phash)}"
        query_embedding = await self._get_embedding(context_text)

        firestore_result = await self._query_firestore_cache(query_embedding)
        if firestore_result:
            self._firestore_hits += 1
            self._tokens_saved += 2000
            # Promote to memory cache
            self._memory_cache[str(current_phash)] = {
                "action": firestore_result,
                "timestamp": time.time(),
                "hits": 1,
            }
            return firestore_result, "firestore_hit"

        self._misses += 1
        return None, "miss"

    async def store(
        self,
        screenshot_b64: str,
        goal: str,
        page_url: str,
        action: dict,
        result_success: bool,
    ):
        """Store a successful action result in both cache tiers."""
        if not result_success:
            return

        img = _b64_to_image(screenshot_b64)
        current_phash = imagehash.phash(img)
        phash_str = str(current_phash)

        # Tier 1: memory
        self._memory_cache[phash_str] = {
            "action": action,
            "timestamp": time.time(),
            "hits": 0,
        }

        # Evict oldest entries if cache grows large
        if len(self._memory_cache) > 500:
            oldest = sorted(self._memory_cache.items(), key=lambda x: x[1]["timestamp"])
            for k, _ in oldest[:50]:
                del self._memory_cache[k]

        # Tier 2: Firestore
        if self.db is None:
            return

        context_text = f"Goal: {goal} | URL: {page_url} | Screenshot hash: {phash_str}"
        embedding = await self._get_embedding(context_text)

        try:
            doc_ref = self.cache_col.document()
            await doc_ref.set({
                "phash": phash_str,
                "embedding": embedding,
                "action": action,
                "goal": goal,
                "page_url": page_url,
                "session_id": self.session_id,
                "created_at": firestore.SERVER_TIMESTAMP,
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "hit_count": 0,
            })
        except Exception as e:
            print(f"[Cache] Store error: {e}")

    def stats(self) -> dict:
        hit_rate = (
            (self._memory_hits + self._firestore_hits) / self._total_lookups
            if self._total_lookups > 0
            else 0.0
        )
        return {
            "total_lookups": self._total_lookups,
            "memory_hits": self._memory_hits,
            "firestore_hits": self._firestore_hits,
            "misses": self._misses,
            "hit_rate_pct": round(hit_rate * 100, 1),
            "estimated_tokens_saved": self._tokens_saved,
            "estimated_cost_saved_usd": round(self._tokens_saved * 0.000015, 4),
        }

    def invalidate_session(self):
        self._memory_cache.clear()

    async def _query_firestore_cache(self, query_embedding: list[float]) -> dict | None:
        try:
            now = time.time()
            docs = (
                self.cache_col
                .where(filter=firestore.FieldFilter("expires_at", ">", now))
                .limit(200)
            )

            best_action = None
            best_score = 0.0

            async for doc in docs.stream():
                data = doc.to_dict()
                stored_embedding = data.get("embedding", [])
                if not stored_embedding:
                    continue
                score = _cosine_similarity(query_embedding, stored_embedding)
                if score > best_score:
                    best_score = score
                    best_action = data.get("action")

            if best_action and best_score >= CACHE_EMBEDDING_THRESHOLD:
                return best_action
            return None
        except Exception as e:
            print(f"[Cache] Firestore query error: {e}")
            return None

    async def _get_embedding(self, text: str) -> list[float]:
        try:
            result = genai.embed_content(
                model="models/gemini-embedding-001",
                content=text[:2000],
                task_type="SEMANTIC_SIMILARITY",
            )
            return result["embedding"]
        except Exception as e:
            print(f"[Cache] Embedding failed: {e}")
            return []


def _b64_to_image(b64_str: str) -> Image.Image:
    img_bytes = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0
