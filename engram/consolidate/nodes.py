"""Node bodies. Each takes the shared `state` dict. Lane B/C functions are looked up lazily so
the DAG runs (as a no-op) before those lanes merge. Only `promote` may call store.write/refute."""
from __future__ import annotations
from engram.consolidate.dag import _opt


def expire(state):
    """Recompute activation; mark dormant below threshold (never delete). Lane B may provide store.decay()."""
    decay = getattr(state["store"], "decay", None)
    if decay:
        state["counts"]["expired"] = int(decay() or 0)


def capture(state):
    """Turn the incoming AgentMessages (compaction / session file) into episode records."""
    eps = []
    for i, m in enumerate(state["messages"]):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        text, thinking = "", None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            th = [p.get("thinking", "") for p in content if isinstance(p, dict) and p.get("type") == "thinking"]
            thinking = "\n".join(th) or None
        if role in ("user", "assistant") and text:
            eps.append({"turn_index": i, "role": role, "text": text, "thinking": thinking})
    state["episodes"] = eps
    state["counts"]["captured"] = len(eps)


def encode(state):
    """Hindsight LLM pass (lane C): episodes + persona + related claims → candidates. Never writes."""
    fn = _opt("engram.encode.encode", "encode")
    if fn and state["episodes"]:
        persona = ""
        p = state["out"].parent / "persona.md"
        if p.exists():
            persona = p.read_text()
        state["candidates"] = list(fn(state["episodes"], persona, []) or [])
    state["counts"]["encoded"] = len(state["candidates"])


def merge(state):
    """Dedupe near-identical candidates (exact text today; lane C may refine)."""
    seen, kept = set(), []
    for c in state["candidates"]:
        key = (c.get("subject"), c.get("text", "").strip().lower())
        if key not in seen:
            seen.add(key)
            kept.append(c)
    state["counts"]["merged"] = len(state["candidates"]) - len(kept)
    state["candidates"] = kept


def adjudicate(state):
    """Same subject, opposite polarity → mark the older as superseded (lane B contradiction rule)."""
    fn = _opt("engram.p1.contradiction", "adjudicate")
    if fn:
        state["counts"]["adjudicated"] = int(fn(state["candidates"], state["store"]) or 0)


def promote(state):
    """The DAG's single writer: every candidate goes through the gate (lane B p2)."""
    gate = _opt("engram.p2.gate", "promote")
    n = 0
    for c in state["candidates"]:
        if gate is None:
            break
        verdict, fired, _q = gate(c, state["store"])
        if verdict == "promoted":
            n += 1
            state["digest_lines"].append(f"- [{c.get('source_class', 'said')}] {c.get('text', '')}")
    state["counts"]["promoted"] = n


def wire(state):
    """Fire together, wire together: co-retrieved claims from retrieval_log get links."""
    import json
    log = state["out"] / "retrieval_log.jsonl"
    n = 0
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ids = rec.get("injected") or []
            for a in ids:
                for b in ids:
                    if a < b:
                        state["store"].link(a, b, "co_retrieved", 1.0)
                        n += 1
    state["counts"]["wired"] = n


def reindex(state):
    fn = getattr(state["store"], "reindex", None)
    if fn:
        fn()
        state["counts"]["reindexed"] = True


def census(state):
    s = state["store"].stats()
    state["digest_lines"].insert(0, f"Memory census: promoted {s.promoted}, held {s.held}, refuted {s.refuted}, dormant {s.dormant}.")


def evolve(state):
    """One generation per consolidation (lane C)."""
    fn = _opt("engram.evolve", "evolve")
    if fn:
        rep = fn(state["store"], None, state["out"] / "generations")
        state["counts"]["generation"] = int(getattr(rep, "generation", 0) or (rep or {}).get("generation", 0) if rep else 0)
