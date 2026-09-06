from __future__ import annotations

from typing import Iterable

USER_CHAR_LIMIT = 1400
MEMORY_CHAR_LIMIT = 2200


def _format_lines(title: str, claims: Iterable[dict], *, include_kind: bool) -> str:
    lines = [f"## {title}"]
    for claim in claims:
        source_class = claim.get("source_class", "said")
        text = claim.get("text", "")
        if include_kind:
            lines.append(f"- [{source_class}] {text}")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines)


def render_user_md(profile_preference_claims: list[dict], *, cap: int = USER_CHAR_LIMIT) -> str:
    text = _format_lines("USER", profile_preference_claims, include_kind=True)
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


def render_memory_md(feedback_procedure_claims: list[dict], *, cap: int = MEMORY_CHAR_LIMIT) -> str:
    text = _format_lines("MEMORY", feedback_procedure_claims, include_kind=False)
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"
