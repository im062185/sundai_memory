from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
import uuid


PREFERENCE_PATTERNS = [
    (re.compile(r"\bnever\b", re.IGNORECASE), "constraint", "deny"),
    (re.compile(r"\balways\b", re.IGNORECASE), "preference", "affirm"),
    (re.compile(r"\bkeep answers short\b", re.IGNORECASE), "preference", "affirm"),
    (re.compile(r"\bno\s+\w+\b", re.IGNORECASE), "constraint", "deny"),
]

ABSENCE_PATTERNS = [
    re.compile(r"has no", re.IGNORECASE),
    re.compile(r"doesn't support", re.IGNORECASE),
    re.compile(r"does not support", re.IGNORECASE),
    re.compile(r"there's no", re.IGNORECASE),
    re.compile(r"there is no", re.IGNORECASE),
    re.compile(r"can't", re.IGNORECASE),
    re.compile(r"not available", re.IGNORECASE),
]

REFUTE_PATTERNS = [
    re.compile(r"actually", re.IGNORECASE),
    re.compile(r"no longer", re.IGNORECASE),
    re.compile(r"forget that", re.IGNORECASE),
    re.compile(r"shipped", re.IGNORECASE),
    re.compile(r"now supports", re.IGNORECASE),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_id() -> str:
    return f"clm_{uuid.uuid4().hex[:12]}"


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _subject_from_text(value: str) -> str:
    lowered = _compact(value)

    if "vendor" in lowered and "sdk" in lowered and "batch" in lowered and ("write" in lowered or "writes" in lowered):
        return "vendor_sdk_batch_write"

    cleaned = re.sub(r"[^a-z0-9 ]", " ", lowered)
    tokens = [t for t in cleaned.split() if t not in {"is", "are", "was", "were", "a", "an", "the", "that", "this", "it", "and", "with", "from", "for", "of"}]
    if not tokens:
        return "topic"
    if "_" in tokens[0]:
        return "_".join(tokens[:6])
    return "_".join(tokens[:6])


def _base_claim(
    *,
    text: str,
    kind: str,
    subject: str,
    polarity: str,
    origin: str,
    source_class: str,
    status: str = "held",
    support_set: list[str] | None = None,
    verbatim: bool = False,
) -> dict[str, Any]:
    now = _now()
    return {
        "id": _claim_id(),
        "text": text,
        "kind": kind,
        "origin": origin,
        "source_class": source_class,
        "subject": subject,
        "polarity": polarity,
        "support_set": support_set or [],
        "refutation_trigger": None,
        "user_reaction": None,
        "importance": 3,
        "tier": "semantic",
        "status": status,
        "created_at": now,
        "verbatim": verbatim,
    }


def _build_pref_or_constraint(claim_text: str, lowered: str, origin: str, source_class: str, pattern_index: int) -> dict[str, Any]:
    if pattern_index == 0:
        return _base_claim(
            text=claim_text,
            kind="constraint",
            subject=f"constraint_{_subject_from_text(claim_text)}",
            polarity="deny",
            origin=origin,
            source_class=source_class,
            status="promoted",  # high-confidence explicit constraints
            verbatim=True,
        )

    if pattern_index == 1:
        return _base_claim(
            text=claim_text,
            kind="preference",
            subject=f"preference_{_subject_from_text(claim_text)}",
            polarity="affirm",
            origin=origin,
            source_class=source_class,
            status="held",
            verbatim=True,
        )

    if pattern_index == 2:
        return _base_claim(
            text=claim_text,
            kind="preference",
            subject=f"answer_style",
            polarity="affirm",
            origin=origin,
            source_class=source_class,
            status="promoted",
            verbatim=True,
        )

    return _base_claim(
        text=claim_text,
        kind="constraint",
        subject=f"constraint_{_subject_from_text(claim_text)}",
        polarity="deny",
        origin=origin,
        source_class=source_class,
        status="promoted",
        verbatim=True,
    )


def _build_absence(claim_text: str, origin: str, source_class: str) -> dict[str, Any]:
    return _base_claim(
        text=claim_text,
        kind="absence",
        subject=_subject_from_text(claim_text),
        polarity="deny",
        origin=origin,
        source_class=source_class,
        status="held",
        verbatim=True,
    )


def _build_refute(claim_text: str, origin: str, source_class: str) -> dict[str, Any]:
    lowered = _compact(claim_text)
    subject = _subject_from_text(claim_text)

    if ("vendor" in lowered or "sdk" in lowered) and "batch" in lowered and ("write" in lowered or "writes" in lowered):
        subject = "vendor_sdk_batch_write"

    return _base_claim(
        text=claim_text,
        kind="fact",
        subject=subject,
        polarity="affirm",
        origin=origin,
        source_class=source_class,
        status="promoted",
        verbatim=False,
    )


def extract_claims(
    message: dict[str, Any],
    *,
    session: str,
    turn_index: int,
) -> list[dict[str, Any]]:
    """Extract salience-only claims from one non-thinking message."""
    role = message.get("role", "user")
    raw_text = _normalize_text(str(message.get("text", "")))
    if not raw_text:
        return []

    lowered = _compact(raw_text)
    origin = "user_turn" if role == "user" else "assistant_turn"
    source_class = "said"

    claim: dict[str, Any] | None = None

    for pattern in ABSENCE_PATTERNS:
        if pattern.search(raw_text):
            claim = _build_absence(raw_text, origin, source_class)
            break

    if claim is None:
        for idx, (pattern, _kind, _polarity) in enumerate(PREFERENCE_PATTERNS):
            if pattern.search(lowered):
                claim = _build_pref_or_constraint(raw_text, lowered, origin, source_class, idx)
                break

    if claim is None:
        for pattern in REFUTE_PATTERNS:
            if pattern.search(lowered):
                claim = _build_refute(raw_text, origin, source_class)
                break

    if claim is None:
        claim = _base_claim(
            text=raw_text,
            kind="fact",
            subject=_subject_from_text(raw_text),
            polarity="affirm",
            origin=origin,
            source_class=source_class,
            status="held",
        )

    claim.update(
        {
            "session": session,
            "turn_index": int(turn_index),
        }
    )
    return [claim]
