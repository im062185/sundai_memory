from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone


_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def extract_thinking(message: dict) -> list[dict[str, str | int | bool | None]]:
    """Extract assistant thinking claims from structured blocks or inline <think> tags."""
    if not message:
        return []

    role = message.get("role", "assistant")
    if role != "assistant":
        return []

    thinking_texts: list[str] = []
    if isinstance(message.get("thinking"), dict):
        thinking = message["thinking"]
        if isinstance(thinking, dict) and thinking.get("type") == "thinking" and thinking.get("thinking"):
            thinking_texts.append(str(thinking["thinking"]))
    elif isinstance(message.get("thinking"), str):
        thinking_texts.append(message["thinking"])

    if not thinking_texts:
        content = message.get("text") or ""
        thinking_texts.extend(_THINK_TAG_RE.findall(content))

    claims = []
    for text in thinking_texts:
        txt = text.strip()
        if not txt:
            continue
        claims.append(
            {
                "id": f"clm_{uuid.uuid4().hex[:12]}",
                "text": txt,
                "kind": "decision",
                "origin": "assistant_thinking",
                "source_class": "inferred",
                "subject": "assistant_reasoning",
                "polarity": "affirm",
                "support_set": [],
                "refutation_trigger": None,
                "user_reaction": None,
                "importance": 2,
                "tier": "semantic",
                "status": "held",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "verbatim": False,
                "session": message.get("session", "session1"),
                "turn_index": int(message.get("turn_index", 0)),
            }
        )
    return claims
