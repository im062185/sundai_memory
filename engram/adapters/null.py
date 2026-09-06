"""Null store: accepts everything, returns nothing. Conformance baseline."""
from __future__ import annotations
from .base import Claim, RecallHit, StoreStats


class NullStore:
    name = "null"

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}

    def write(self, claim: Claim) -> None:
        self._claims[claim["id"]] = dict(claim)

    def query(self, text: str, k: int = 5, *, session=None) -> list[RecallHit]:
        return []

    def refute(self, claim_id: str, *, by=None) -> None:
        if claim_id in self._claims:
            self._claims[claim_id]["status"] = "refuted"

    def by_subject(self, subject: str) -> list[Claim]:
        return [c for c in self._claims.values() if c.get("subject") == subject]

    def stats(self) -> StoreStats:
        s = StoreStats(name=self.name)
        for c in self._claims.values():
            st = c.get("status", "held")
            if hasattr(s, st):
                setattr(s, st, getattr(s, st) + 1)
        return s

    def touch(self, claim_ids) -> None:
        for cid in claim_ids:
            if cid in self._claims:
                self._claims[cid]["access_count"] = self._claims[cid].get("access_count", 0) + 1

    def link(self, src, dst, kind, weight_delta=1.0) -> None:
        pass

    def export_markdown(self) -> dict[str, str]:
        return {"USER.md": "", "MEMORY.md": ""}
