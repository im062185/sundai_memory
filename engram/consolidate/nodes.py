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
        # The cursor is a LINE COUNT, not a timestamp. episodes.jsonl is strictly
        # append-only (server.py `_append`), so "how many lines have been consumed"
        # is exact and needs no clock. Timestamps were tried and were wrong twice
        # over: the two writers disagree on format ("…50.088779+00:00" from
        # datetime.isoformat vs "…50Z" from time.strftime) and '.' (46) sorts
        # before 'Z' (90), so a string compare called an episode written within
        # the same second as the marker *older* than it and the cursor — which
        # only moves forward — skipped it forever. Parsing the stamps fixes that
        # but not the rest: the marker is written after the whole DAG has run, so
        # any turn appended while encode() was waiting on the model fell into the
        # gap, and whole-second truncation re-captured a turn on the next run.
        # A line count has none of these failure modes.
        #
        # dag.py advances the cursor only after every node has run, so a crash
        # mid-consolidation re-captures rather than loses the batch.
        import json
        log = state["out"] / "episodes.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        cursor = state["out"] / "capture_cursor.json"
        consumed, seeded = 0, cursor.exists()
        if seeded:
            try:
                consumed = int(json.loads(cursor.read_text()).get("lines", 0))
            except (ValueError, TypeError, json.JSONDecodeError):
                consumed = 0
        if consumed > len(lines):
            consumed = 0  # the log was rotated or `out/` rebuilt under us; re-read it
        pending = lines[consumed:]
        if not seeded:
            # First run against an out/ that predates the cursor: fall back to the
            # old marker so an existing store does not re-consolidate its history.
            # After this run the line count takes over and the fallback is dead.
            since = None
            cons = state["out"] / "consolidation.jsonl"
            if cons.exists():
                prior = cons.read_text().splitlines()
                if prior:
                    since = _instant(json.loads(prior[-1]).get("ts"))
            if since is not None:
                keep = []
                for line in pending:
                    try:
                        ts = _instant(json.loads(line).get("ts"))
                    except Exception:
                        ts = None
                    # An episode with no parsable ts is captured, not dropped: losing
                    # a turn is worse than consolidating it twice (merge() dedupes).
                    if ts is None or ts > since:
                        keep.append(line)
                pending = keep
        for line in pending:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("text"):
                eps.append({"turn_index": r.get("turn_index", 0), "role": r.get("role"), "text": r["text"], "thinking": r.get("thinking")})
        # Only the lines actually read are claimed. Anything appended from here on
        # is the next run's work, which is what closes the mid-run race.
        state["cursor_lines"] = len(lines)
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
