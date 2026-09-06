from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import hashlib
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .base import Claim, RecallHit, Store, StoreStats


def _hash_vector(text: str, dim: int = 768) -> np.ndarray:
    vec = np.zeros(dim, dtype=float)
    for token in text.lower().split():
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    n = np.linalg.norm(a) * np.linalg.norm(b)
    if n == 0:
        return 0.0
    return float(np.dot(a, b) / n)


class VectorStore(Store):
    name = "vector"

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim
        self._claims: dict[str, Claim] = {}
        self._vectors: dict[str, np.ndarray] = {}

    def _normalize(self, claim: Claim) -> Claim:
        c = dict(claim)
        c.setdefault("id", f"clm_{hash(c.get('text','')):x}")
        c.setdefault("support_set", [])
        c.setdefault("importance", 3)
        c.setdefault("status", "promoted")
        c.setdefault("turn_index", 0)
        c.setdefault("session", "default")
        c.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        c.setdefault("origin", "user_turn")
        c.setdefault("kind", "fact")
        c.setdefault("subject", "_")
        c.setdefault("polarity", "affirm")
        c.setdefault("source_class", "said")
        c.setdefault("tier", "semantic")
        c.setdefault("last_accessed", None)
        c.setdefault("activation", 1.0)
        c.setdefault("access_count", 0)
        c.setdefault("verbatim", False)
        return c

    def write(self, claim: Claim) -> None:
        c = self._normalize(dict(claim))
        text = c.get("text", "")
        self._claims[c["id"]] = c
        self._vectors[c["id"]] = _hash_vector(text, self._dim)

    def query(self, text: str, k: int = 5, *, session: str | None = None) -> list[RecallHit]:
        if not text:
            return []
        q = _hash_vector(text, self._dim)
        scores = []
        for cid, claim in self._claims.items():
            if claim.get("status") != "promoted":
                continue
            score = _cos(q, self._vectors.get(cid, np.zeros(self._dim)))
            # Naive vector matcher gives one-dimensional score.
            scores.append((score, claim))

        hits = []
        for score, claim in sorted(scores, key=lambda x: x[0], reverse=True)[:k]:
            hits.append(
                RecallHit(
                    claim=claim,
                    score=float(score),
                    store=self.name,
                    why="naive-cosine",
                )
            )
        return hits

    def refute(self, claim_id: str, *, by: str | None = None) -> None:
        # Deliberately naive (A-3): keep refuted claims for the fallback vector path.
        if claim_id in self._claims:
            self._claims[claim_id]["status"] = self._claims[claim_id].get("status", "promoted")

    def by_subject(self, subject: str) -> list[Claim]:
        return [c for c in self._claims.values() if c.get("subject") == subject]

    def stats(self) -> StoreStats:
        counts = defaultdict(int)
        for c in self._claims.values():
            counts[c.get("status", "held")] += 1
        return StoreStats(name=self.name, **{k: counts.get(k, 0) for k in ["promoted", "held", "refuted", "dormant"]})

    def touch(self, claim_ids: Iterable[str]) -> None:
        for cid in claim_ids:
            claim = self._claims.get(cid)
            if not claim:
                continue
            claim["access_count"] = int(claim.get("access_count", 0)) + 1
            claim["last_accessed"] = datetime.now(timezone.utc).isoformat()

    def link(self, src: str, dst: str, kind: str, weight_delta: float = 1.0) -> None:
        return None

    def export_markdown(self) -> dict[str, str]:
        return {
            "USER.md": "",
            "MEMORY.md": "",
        }
