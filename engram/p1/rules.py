from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_id() -> str:
    return f"clm_{uuid.uuid4().hex[:12]}"


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _subject_from_text(text: str) -> str:
    txt = text.lower().replace("the ", "").replace("a ", "")
    txt = re.sub(r"[^a-z0-9_ ]", " ", txt)
    words = [w for w in txt.split() if w not in {"is", "are", "was", "were", "a", "an", "the"}]
    return "_".join(words[:6]) if words else "unknown"


PREFERENCE_PATTERNS = [
    (re.compile(r"\bnever\b"), "constraint", "deny", "preference"),
    (re.compile(r"\balways\b"), "preference", "affirm", "preference"),
    (re.compile(r"\bkeep answers short\b"), "preference", "affirm", "answer_style"),
    (re.compile(r"\bno\s+[^\s]+\b"), "constraint", "deny", "constraint"),
]

ABSENCE_PATTERNS = [
    re.compile(r"has no"),
    re.compile(r"doesn't support"),
    re.compile(r"there's no"),
    re.compile(r"can't"),
    re.compile(r"not available"),
]

REFUTE_PATTERNS = [
    re.compile(r"actually"),
    re.compile(r"no longer"),
    re.compile(r"forget that"),
    re.compile(r"shipped in"),
    re.compile(r"now supports"),
]


def _polarity_from_refute(text: str) -> str:
    if "no longer" in text or "forget that" in text:
        return "deny"
    if "shipped in" in text or "now supports" in text:
        return "affirm"
    return "affirm"


def _refute_subject(text: str) -> str:
    if "shipped in" in text:
        return _subject_from_text(text.split("shipped in", 1)[0])
    if "now supports" in text:
        return _subject_from_text(text.split("now supports", 1)[0])
    if "no longer" in text:
        return _subject_from_text(text.split("no longer", 1)[0])
    if "forget that" in text:
        return _subject_from_text(text.split("forget that", 1)[0])
    return _subject_from_text(text)


def _absent_subject(text: str) -> str:
    for pat in ABSENCE_PATTERNS:
        if pat.search(text):
            break
    # Use everything after the matched phrase or the whole text as fallback.
    text = text.replace("There", "")
    text = text.replace("there is", "")
    text = text.replace("there's", "")
    return _subject_from_text(text)


def _base_claim(
    *,
    text: str,
    kind: str,
    subject: str,
    polarity: str,
    origin: str,
    source_class: str,
    status: str = "held",
) -> dict[str, Any]:
    return {
        "id": _claim_id(),
        "text": text,
        "kind": kind,
        "origin": origin,
        "source_class": source_class,
        "subject": subject or "_",
        "polarity": polarity,
        "support_set": [],
        "refutation_trigger": None,
        "user_reaction": None,
        "importance": 3,
        "tier": "semantic",
        "status": status,
        "created_at": _now(),
        "verbatim": "fact" not in kind,
    }


def extract_claims(
    message: dict[str, Any],
    *,
    session: str,
    turn_index: int,
) -> list[dict[str, Any]]:
    """Run only salience tagging over user/assistant text.

    No adapters are imported in this module.
    """
    role = message.get("role", "user")
    text = _normalize_text(message.get("text", ""))
    if not text:
        return []

    lowered = _clean(text)
    origin = "user_turn" if role == "user" else "assistant_turn"
    source_class = "said"

    claims: list[dict[str, Any]] = []

    for pat, kind, polarity, subject_hint in PREFERENCE_PATTERNS:
        if pat.search(lowered):
            subject = f"{subject_hint}_{_subject_from_text(text)}"
            claim = _base_claim(
                text=text,
                kind=kind,
                subject=subject,
                polarity=polarity,
                origin=origin,
                source_class=source_class,
                status="promoted" if kind == "preference" else "held",
            )
            claim.update(
                {
                    "session": session,
                    "turn_index": turn_index,
                    "verbatim": True,
                    "status": "promoted" if kind in {"preference", "constraint"} else "held",
                }
            )
            claim["source_class"] = source_class
            claims.append(claim)
            return claims

    for pat in ABSENCE_PATTERNS:
        if pat.search(lowered):
            subject = _absent_subject(lowered)
            claim = _base_claim(
                text=text,
                kind="absence",
                subject=subject,
                polarity="deny",
                origin=origin,
                source_class=source_class,
                status="held",
            )
            claim["session"] = session
            claim["turn_index"] = turn_index
            claims.append(claim)
            return claims

    if any(p.search(lowered) for p in REFUTE_PATTERNS):
        polarity = _polarity_from_refute(lowered)
        subject = _refute_subject(lowered)
        kind = "fact"
        claim = _base_claim(
            text=text,
            kind=kind,
            subject=subject,
            polarity=polarity,
            origin=origin,
            source_class=source_class,
            status="promoted",
        )
        claim["session"] = session
        claim["turn_index"] = turn_index
        claim["refutation_trigger"] = None
        claims.append(claim)
        return claims

    # Generic fact extraction fallback if no explicit pattern matched.
    if origin in {"user_turn", "assistant_turn"}:
        claim = _base_claim(
            text=text,
            kind="fact",
            subject=_subject_from_text(text),
            polarity="affirm",
            origin=origin,
            source_class=source_class,
            status="held",
        )
        claim["session"] = session
        claim["turn_index"] = turn_index
        claims.append(claim)
    return claims
