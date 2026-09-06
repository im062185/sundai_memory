# 에피소드 창을 사후 관점으로 인코딩해 게이트가 심사할 후보 클레임을 만드는 모듈
"""Processing II, moved off the hot path (AMD-03 §1).

`encode()` runs inside the consolidation DAG, over episodes that were already
appended verbatim. Because it runs late it can see the human turn that *follows*
an assistant turn, which is the only place the reaction signal exists.

It never writes to a store. It returns candidates; `engram/p2` decides.

Two rules are enforced here rather than trusted to the model:

- `user_reaction` is read from the following human turn (the model's own guess is
  only a fallback for a window with no following human turn).
- Anything the gate would reject on shape is dropped and counted, never patched
  into validity (TDD §12.6).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from engram.encode.client import get_client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "encoder.md"
SCHEMA_PATH = ROOT / "schema" / "claim.schema.json"

# `entities` is required by the lane C spec but the frozen claim schema is
# additionalProperties:false and has no such property, and no store persists it.
# It rides along on the returned candidate and is stripped before validation.
SIDECAR_KEYS = ("entities", "source_turn_index")

# Wording that must survive intact: commands, paths, versions, quoted rules.
VERBATIM_RE = re.compile(
    r"`[^`]+`"
    r"|\b\d+\.\d+(?:\.\d+)?\b"
    r"|(?:^|\s)(?:/|\./|~/)[\w./-]+"
    r"|\b(?:git|npm|npx|pip|uv|python|pytest|cd|rm|curl)\s+[\w./-]"
    r"|[\"“][^\"”]{4,}[\"”]",
    re.IGNORECASE,
)

_REACTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("frustrated", re.compile(r"\b(again|still|stop|ugh|i already (said|told)|why do you keep)\b", re.I)),
    ("correcting", re.compile(r"\b(actually|correction|not quite|that'?s not|wrong|instead|no longer|shipped in|now supports)\b", re.I)),
    ("approving", re.compile(r"\b(thanks|thank you|perfect|great|exactly|correct|nice|yes[,.!]|got it)\b", re.I)),
)


@dataclass
class EncodeStats:
    returned: int = 0
    dropped_invalid_json: int = 0
    dropped_invalid_candidate: int = 0
    model_error: str | None = None
    reasons: list[str] = field(default_factory=list)


LAST_STATS = EncodeStats()


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _thinking_text(thinking: Any) -> str:
    """Render a thinking payload for the prompt. Handles block, inline and None."""
    if not thinking:
        return ""
    if isinstance(thinking, dict):
        return str(thinking.get("thinking", ""))
    text = str(thinking)
    inline = re.findall(r"<think>(.*?)</think>", text, re.S)
    return "\n".join(inline) if inline else text


def _normalize_episodes(episodes: list[dict]) -> list[dict]:
    """Accept both shapes: nodes.capture() output and p1/episodic.append_episode()."""
    out = []
    for i, ep in enumerate(episodes or []):
        if not isinstance(ep, dict):
            continue
        out.append(
            {
                "turn_index": ep.get("turn_index", i),
                "role": ep.get("role", ""),
                "text": ep.get("text", "") or "",
                "thinking": _thinking_text(ep.get("thinking")),
                "session": ep.get("session"),
            }
        )
    return out


def _render_prompt(episodes: list[dict], persona: str, related: list[dict]) -> str:
    lines = []
    for ep in episodes:
        lines.append(f"[turn {ep['turn_index']}] {ep['role']}: {ep['text']}")
        if ep["thinking"]:
            lines.append(f"[turn {ep['turn_index']}] {ep['role']} thinking: {ep['thinking']}")
    related_text = (
        "\n".join(f"- ({c.get('id', '?')}) {c.get('text', '')}" for c in (related or [])[:5])
        or "(none)"
    )
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{persona}", (persona or "(none)").strip())
        .replace("{related}", related_text)
        .replace("{episodes}", "\n".join(lines) or "(empty)")
    )


def _parse(raw: str, stats: EncodeStats) -> list[dict]:
    """Model output → list of raw candidate dicts. Invalid JSON is dropped, not repaired."""
    text = (raw or "").strip()
    if not text:
        stats.dropped_invalid_json += 1
        stats.reasons.append("empty response")
        return []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        stats.dropped_invalid_json += 1
        stats.reasons.append(f"invalid json: {exc.msg}")
        return []
    candidates = data.get("candidates") if isinstance(data, dict) else data
    if not isinstance(candidates, list):
        stats.dropped_invalid_json += 1
        stats.reasons.append("no candidates list")
        return []
    return candidates


def _reaction_after(episodes: list[dict], turn_index: Any) -> str | None:
    """The reaction is in the human turn that FOLLOWS this one. That is the whole point."""
    if turn_index is None:
        return None
    later = [e for e in episodes if e["turn_index"] > turn_index and e["role"] == "user"]
    if not later:
        return None
    text = later[0]["text"]
    for label, pattern in _REACTION_PATTERNS:
        if pattern.search(text):
            return label
    return "neutral"


def _is_quoted_from(text: str, episodes: list[dict]) -> bool:
    """True when the candidate reproduces a span of an episode exactly."""
    return bool(text) and any(text in ep["text"] for ep in episodes)


def _clean(candidate: dict, episodes: list[dict], schema: dict, stats: EncodeStats) -> dict | None:
    if not isinstance(candidate, dict):
        stats.dropped_invalid_candidate += 1
        stats.reasons.append("candidate is not an object")
        return None

    cand = dict(candidate)
    origin = cand.get("origin")
    source_class = cand.get("source_class")

    # G9 and G2 shape violations are dropped, never relabelled into validity.
    if origin == "assistant_thinking" and source_class != "inferred":
        stats.dropped_invalid_candidate += 1
        stats.reasons.append("G9: assistant_thinking must be inferred")
        return None
    if origin == "assistant_turn" and source_class == "inferred":
        stats.dropped_invalid_candidate += 1
        stats.reasons.append("G2: assistant_turn cannot be inferred")
        return None

    # Only a claim from an assistant turn has a reaction to read; a claim the human
    # stated outright was not reacted to by anyone.
    derived = None
    if origin in {"assistant_turn", "assistant_thinking"}:
        derived = _reaction_after(episodes, cand.get("source_turn_index"))
    if derived is not None:
        cand["user_reaction"] = derived
    elif cand.get("user_reaction") not in {"approving", "neutral", "correcting", "frustrated"}:
        cand["user_reaction"] = None

    # Verbatim is a flag on the text the model produced; the text is never rewritten
    # here. Substituting a "more exact" source sentence drags conversational framing
    # ("Oh, correction: ...") into what has to stay a declarative claim.
    text = str(cand.get("text", ""))
    if cand.get("verbatim") or VERBATIM_RE.search(text) or _is_quoted_from(text, episodes):
        cand["verbatim"] = True
    cand["text"] = text

    probe = {k: v for k, v in cand.items() if k not in SIDECAR_KEYS}
    probe.update({"id": "clm_probe000", "status": "held", "created_at": "1970-01-01T00:00:00Z"})
    try:
        jsonschema.validate(probe, schema)
    except jsonschema.ValidationError as exc:
        stats.dropped_invalid_candidate += 1
        stats.reasons.append(f"schema: {exc.message}")
        return None
    return cand


def encode(episodes: list[dict], persona: str = "", related: list[dict] | None = None) -> list[dict]:
    """Episodes + persona + related claims → candidate claims for the gate.

    Never writes to a store. Never repairs a malformed candidate.
    Per-call counts are left on `LAST_STATS`.
    """
    global LAST_STATS
    stats = EncodeStats()
    LAST_STATS = stats

    eps = _normalize_episodes(episodes)
    if not eps:
        return []

    session = next((e["session"] for e in eps if e.get("session")), None)
    # No key, no network, a provider error: consolidation still has to finish, so the
    # encode node contributes nothing and says why (TDD §2.4).
    try:
        raw = get_client().complete(_render_prompt(eps, persona, related or []), session=session)
    except Exception as exc:  # noqa: BLE001 - any provider failure is non-fatal here
        stats.model_error = f"{type(exc).__name__}: {exc}"
        stats.reasons.append(stats.model_error)
        return []

    schema = _schema()
    out = []
    for candidate in _parse(raw, stats):
        cleaned = _clean(candidate, eps, schema, stats)
        if cleaned is not None:
            out.append(cleaned)
    stats.returned = len(out)
    return out
