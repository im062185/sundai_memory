"""FROZEN CONTRACT (step 03). Change only by agreement of all four lanes.

Store interface per TDD v2 §5.2 + AMD-03 §2. A Claim is a dict validated against
schema/claim.schema.json. Only engram/p2 (the gate) may call write() / refute();
tests/test_isolation.py enforces this.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

Claim = dict[str, Any]


@dataclass
class RecallHit:
    claim: Claim
    score: float
    store: str
    why: str = ""            # e.g. "fts:0.71 act:0.40 link:+0.1"


@dataclass
class StoreStats:
    name: str
    promoted: int = 0
    held: int = 0
    refuted: int = 0
    dormant: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Store(Protocol):
    name: str

    def write(self, claim: Claim) -> None: ...
    def query(self, text: str, k: int = 5, *, session: str | None = None) -> list[RecallHit]: ...
    def refute(self, claim_id: str, *, by: str | None = None) -> None:
        """Mark refuted; set valid_to; never delete. Must take effect for the next query()."""
    def by_subject(self, subject: str) -> list[Claim]: ...
    def stats(self) -> StoreStats: ...
    def touch(self, claim_ids: Iterable[str]) -> None:
        """Retrieval bookkeeping: access_count += 1, last_accessed = now."""
    def link(self, src: str, dst: str, kind: str, weight_delta: float = 1.0) -> None:
        """Graph edge upsert. kinds: co_retrieved | same_subject | supersedes."""
    def export_markdown(self) -> dict[str, str]:
        """{'USER.md': ..., 'MEMORY.md': ...} rendered from tier == 'always'."""
