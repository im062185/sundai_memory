"""Node bodies. Each takes the shared `state` dict. Lane B/C functions are looked up lazily so
the DAG runs (as a no-op) before those lanes merge. Only `promote` may call store.write/refute."""
from __future__ import annotations
from engram.consolidate.dag import _opt


def _instant(value):
    """An ISO-8601 string as a tz-aware datetime, or None if it will not parse.

    Both suffixes are in the tree: "+00:00" from datetime.isoformat() and "Z"
    from the consolidation log. Naive stamps are read as UTC so a comparison
    never raises on mixed awareness.
    """
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def expire(state):
    """Recompute activation; mark dormant below threshold (never delete). Lane B may provide store.decay()."""
    decay = getattr(state["store"], "decay", None)
    if decay:
        import json
        kw = {}
        gens = sorted((state["out"] / "generations").glob("gen-*")) if (state["out"] / "generations").exists() else []
        if gens and (gens[-1] / "weights.json").exists():
            w = json.loads((gens[-1] / "weights.json").read_text())
            kw = {"decay_lambda": float(w.get("decay_lambda", 0.05)), "dormancy_threshold": float(w.get("dormancy_threshold", 0.1))}
        state["counts"]["expired"] = int(decay(**kw) or 0)


def capture(state):
    """Turn the incoming AgentMessages (compaction / session file) into episode records."""
    eps = []
    if not state["messages"]:
        # CLI / start / micro: consolidate the episodes appended since the last consolidation.
        #
        # Compare instants, never the ISO strings. The two writers disagree on
        # format — episodes.jsonl carries "…50.088779+00:00", consolidation.jsonl
        # carries "…50Z" — and '.' (46) sorts before 'Z' (90), so a string
        # compare calls an episode written *after* the marker older than it.
        # The cursor only moves forward, so those episodes were skipped forever.
        #
        # Known and deliberate: dag.py stamps the marker at whole-second
        # resolution, so an episode written later in the same second as the
        # previous run's marker is captured once more on the next run. That is
        # the safe direction — merge() and the gate dedupe a repeat, nothing
        # dedupes a memory that was never captured — but it means `captured`
        # can overcount by the size of one turn. Do not read it as exact.
        import json
        log, since = state["out"] / "episodes.jsonl", None
        cons = state["out"] / "consolidation.jsonl"
        if cons.exists():
            lines = cons.read_text().splitlines()
            if lines:
                since = _instant(json.loads(lines[-1]).get("ts"))
        if log.exists():
            for line in log.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = _instant(r.get("ts"))
                # An episode with no parsable ts is captured, not dropped: losing a
                # turn is worse than consolidating it twice (merge() dedupes).
                if r.get("text") and (since is None or ts is None or ts > since):
                    eps.append({"turn_index": r.get("turn_index", 0), "role": r.get("role"), "text": r["text"], "thinking": r.get("thinking")})
        state["episodes"] = eps
        state["counts"]["captured"] = len(eps)
        return
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
    if True:  # salience rules always run too (fast-track kinds); merge() dedupes against the encoder
        extract = _opt("engram.p1.rules", "extract_claims")
        route = _opt("engram.p3.router", "route_claim")
        if extract:
            for ep in state["episodes"]:
                if ep["role"] != "user":
                    continue
                for c in extract({"role": "user", "text": ep["text"]}, session="consolidate", turn_index=ep["turn_index"]) or []:
                    if route:
                        c["tier"] = route(c)
                    state["candidates"].append(c)
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
        _normalize(c, state)
        verdict, fired, _q = gate(c, state["store"])
        if verdict == "promoted":
            n += 1
            state["digest_lines"].append(f"- [{c.get('source_class', 'said')}] {c.get('text', '')}")
    state["counts"]["promoted"] = n


def _normalize(c: dict, state) -> None:
    """Encoder candidates carry no id/status/timestamps by contract; the DAG fills them before the gate."""
    import time, uuid
    c.setdefault("id", "clm_" + uuid.uuid4().hex[:12])
    c.setdefault("status", "held")
    c.setdefault("origin", "user_turn")
    c.setdefault("source_class", "said")
    c.setdefault("polarity", "affirm")
    c.setdefault("kind", "fact")
    c.setdefault("subject", (c.get("text") or "")[:40].lower())
    c.setdefault("session", "consolidate")
    c.setdefault("turn_index", 0)
    c.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if c.get("origin") == "assistant_thinking":
        c["source_class"] = "inferred"


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
