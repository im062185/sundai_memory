from __future__ import annotations

from typing import Any

from .rules import ALLOWED_KINDS, ALLOWED_ORIGINS, ALLOWED_SOURCE_CLASSES, has_opposite

ABSENCE_QUESTION: dict[str, bool] = {}


def promote(claim: dict[str, Any], store) -> tuple[str, list[str], str | None]:
    """Return (verdict, fired_rules, question)."""
    claim = dict(claim)
    fired: list[str] = []
    session = claim.get("session", "default")
    origin = claim.get("origin")
    source_class = claim.get("source_class")
    subject = (claim.get("subject") or "").strip()
    text = (claim.get("text") or "").strip()
    kind = claim.get("kind")

    # G1
    if not text:
        return "rejected", ["G1"], None

    # G2: assistant turn cannot claim inferred in this lane's design.
    if origin == "assistant_turn" and source_class == "inferred":
        return "rejected", ["G2"], None

    # G9
    if origin == "assistant_thinking" and source_class != "inferred":
        return "rejected", ["G9"], None

    # G4-like shape checks
    if origin not in ALLOWED_ORIGINS:
        return "rejected", ["G4"], None
    if source_class not in ALLOWED_SOURCE_CLASSES:
        return "rejected", ["G6"], None
    if kind not in ALLOWED_KINDS:
        return "rejected", ["G5"], None
    if claim.get("polarity") not in {"affirm", "deny"}:
        return "rejected", ["G7"], None
    if not subject:
        return "rejected", ["G1"], None

    # G8: duplicate same-subject same-polarity is rejected for non-absence claims.
    if kind != "absence":
        for prior in store.by_subject(subject):
            if prior.get("status") == "refuted":
                continue
            if prior.get("polarity") == claim.get("polarity") and prior.get("status") in {"promoted", "held", "dormant"}:
                return "rejected", ["G8"], None

    # G3 and G4: absence claims from user say-so are held; one clarifying question per session.
    if kind == "absence" and origin == "user_turn" and source_class == "said":
        claim["status"] = "held"
        store.write(claim)
        if not ABSENCE_QUESTION.get(session):
            ABSENCE_QUESTION[session] = True
            return "held", ["G3"], "Can you share a source for this absence claim?"
        return "held", ["G3", "G4"], None

    # Contradiction rule: opposite-polarity same subject refutes old claims.
    to_refute = [
        prior["id"]
        for prior in store.by_subject(subject)
        if has_opposite(prior, claim)
    ]
    if to_refute:
        for old_id in to_refute:
            store.refute(old_id, by=claim.get("id"))
        fired.append("G8")

    claim["status"] = "promoted"
    store.write(claim)
    return "promoted", fired, None
