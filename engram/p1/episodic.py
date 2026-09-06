from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any


def append_episode(
    role: str,
    text: str,
    session: str,
    turn_index: int,
    *,
    thinking: str | dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Append one raw turn record before any tagging.

    Returned record has the fields requested by the prompt and no rewrites.
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    return {
        "id": f"ep_{uuid.uuid4().hex[:16]}",
        "session": session,
        "turn_index": int(turn_index),
        "role": role,
        "text": text,
        "thinking": thinking,
        "ts": ts,
    }
