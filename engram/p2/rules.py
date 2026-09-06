from __future__ import annotations

ALLOWED_KINDS = {
    "profile",
    "preference",
    "constraint",
    "fact",
    "absence",
    "decision",
    "capability",
    "feedback",
    "procedure",
    "episode",
}

ALLOWED_ORIGINS = {
    "user_turn",
    "assistant_turn",
    "assistant_thinking",
    "tool_result",
    "trajectory",
}

ALLOWED_SOURCE_CLASSES = {"said", "inferred", "verified", "refuted"}


def has_opposite(existing: dict, candidate: dict) -> bool:
    return (
        existing.get("subject") == candidate.get("subject")
        and existing.get("polarity") != candidate.get("polarity")
        and existing.get("status") != "refuted"
    )
