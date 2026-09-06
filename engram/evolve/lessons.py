# 어떤 종류의 기억이 실제로 쓰였는지 집계해 인코더 교훈으로 남기는 모듈
"""Lesson lines: `kind X from origin Y: stored N, retrieved M, used U`.

The encoder reads these back in its prompt, so it gets pickier about the slots
whose claims were stored and then never used, and more generous about the slots
that keep earning their place.
"""
from __future__ import annotations

from collections import defaultdict


def _claim_of(store, claim_id: str) -> dict | None:
    """Read one claim without assuming more of the Store protocol than exists."""
    getter = getattr(store, "get", None)
    if getter:
        try:
            return getter(claim_id)
        except Exception:
            return None
    conn = getattr(store, "_conn", None)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT kind, origin FROM memories WHERE id = ?", (claim_id,)
            ).fetchone()
        except Exception:
            return None
        if row:
            return {"kind": row["kind"], "origin": row["origin"]}
    return None


def tally(store, signals) -> dict[tuple[str, str], dict[str, int]]:
    """Per (kind, origin): how many distinct claims stored, how often retrieved and used."""
    rows: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"stored": 0, "retrieved": 0, "used": 0}
    )
    seen: set[str] = set()

    retrieved_counts: dict[str, int] = defaultdict(int)
    for record in signals.queries:
        for cid in record.get("injected", []):
            retrieved_counts[cid] += 1

    for cid in set(retrieved_counts) | signals.used:
        claim = _claim_of(store, cid)
        if not claim:
            continue
        slot = (claim.get("kind") or "unknown", claim.get("origin") or "unknown")
        if cid not in seen:
            seen.add(cid)
            rows[slot]["stored"] += 1
        rows[slot]["retrieved"] += retrieved_counts.get(cid, 0)
        if cid in signals.used:
            rows[slot]["used"] += 1
    return dict(rows)


def lesson_lines(store, signals) -> list[str]:
    lines = []
    for (kind, origin), counts in sorted(tally(store, signals).items()):
        lines.append(
            f"kind {kind} from origin {origin}: "
            f"stored {counts['stored']}, retrieved {counts['retrieved']}, used {counts['used']}"
        )
    return lines


def append_lessons(lessons: str, new_lines: list[str]) -> str:
    """Append only lines that are not already recorded."""
    existing = set(lessons.splitlines())
    fresh = [ln for ln in new_lines if ln not in existing]
    if not fresh:
        return lessons
    body = lessons.rstrip("\n")
    return body + "\n\n" + "\n".join(fresh) + "\n"
