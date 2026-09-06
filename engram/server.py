"""Engram stdio server — implements docs/PROTOCOL.md. Lane A owns this file.

Stores come from engram.adapters.REGISTRY (lane B). Until a real store lands, NullStore.
Salience tagging (p1), gate (p2), router (p3), encode/evolve (lane C) are imported lazily
so the server runs with whatever lanes have merged.
"""
from __future__ import annotations
import json, os, sys, time, uuid, importlib, pathlib
from typing import Any

OUT = pathlib.Path(os.environ.get("ENGRAM_OUT", "out"))
RECALL_TOKENS = int(os.environ.get("ENGRAM_RECALL_TOKENS", "400"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _try(mod: str, attr: str):
    try:
        return getattr(importlib.import_module(mod), attr)
    except Exception:
        return None


def _append(path: pathlib.Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


class Engram:
    def __init__(self, store=None, out: pathlib.Path = OUT):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        if store is None:
            from engram.adapters import REGISTRY
            from engram.adapters.null import NullStore
            name = os.environ.get("ENGRAM_STORE", "sqlite" if "sqlite" in REGISTRY else "null")
            cls = REGISTRY.get(name)
            if cls is None:
                store = NullStore()
            else:
                try:
                    store = cls(path=str(self.out / "engram.db"))
                except TypeError:
                    store = cls()
        self.store = store
        self.questions_asked: set[str] = set()
        self.injected: dict[tuple[str, int], list[str]] = {}

    # ---- ops -------------------------------------------------------------
    def recall(self, req: dict) -> dict:
        text, k, session = req.get("text", ""), int(req.get("k", 5)), req.get("session", "default")
        q = _fts_query(text) if self.store.name == "sqlite" else text
        hits = self.store.query(q, k=k, session=None) if q else []  # memory is cross-session by design
        claims, used = [], 0
        for h in hits:
            t = _tokens(h.claim["text"])
            if used + t > RECALL_TOKENS:
                break
            used += t
            claims.append({"id": h.claim["id"], "text": h.claim["text"], "source_class": h.claim.get("source_class"),
                           "kind": h.claim.get("kind"), "store": h.store, "score": round(h.score, 3)})
        # always-on tier (USER.md / MEMORY.md) rides in every prompt, ahead of retrieved claims
        always = {}
        try:
            md = self.store.export_markdown()
            always = {k: v for k, v in md.items() if v and "\n- " in v}
            used += sum(_tokens(v) for v in always.values())
        except Exception:
            always = {}
        self.store.touch([c["id"] for c in claims])
        ti = int(req.get("turn_index", -1))
        self.injected[(session, ti)] = [c["id"] for c in claims]
        _append(self.out / "retrieval_log.jsonl", {"ts": _now(), "session": session, "turn_index": ti, "query": text,
                                                   "injected": [c["id"] for c in claims], "stores": sorted({c["store"] for c in claims})})
        return {"claims": claims, "always": always, "tokens": used}

    def remember(self, req: dict) -> dict:
        turn, session, ti = req.get("turn", {}), req.get("session", "default"), int(req.get("turn_index", -1))
        role, text, thinking = turn.get("role", "user"), turn.get("text", "") or "", turn.get("thinking")
        # 1. capture first: the verbatim episode, before any judgment
        append_episode = _try("engram.p1.episodic", "append_episode")
        ep = append_episode(role=role, text=text, session=session, turn_index=ti, thinking=thinking) if append_episode else \
            {"id": "ep_" + uuid.uuid4().hex[:12], "session": session, "turn_index": ti, "role": role, "text": text, "thinking": thinking, "ts": _now()}
        _append(self.out / "episodes.jsonl", ep)
        add_ep = getattr(self.store, "add_episode", None)
        if add_ep:
            add_ep(ep)
        # 2. salience tagging (lane B p1) → router (p3) → gate (p2, the only writer)
        tagged, gate, refuted, question = [], {}, [], None
        extract = _try("engram.p1.rules", "extract_claims")
        extract_thinking = _try("engram.p1.think", "extract_thinking") or _try("engram.p1", "extract_thinking")
        promote = _try("engram.p2.gate", "promote")
        route = _try("engram.p3.router", "route_claim")
        opposing = _try("engram.p1.contradiction", "opposing_claim_ids")
        cands = []
        # Capture-first: only the HUMAN's turns are salience-tagged. Assistant text stays episodic
        # (its thinking may yield `inferred` claims below). One claim per sentence, not per turn.
        if extract and text and role == "user":
            for sent in _sentences(text):
                cands += extract({"role": role, "text": sent}, session=session, turn_index=ti) or []
        if extract_thinking and thinking and role == "assistant":
            for c in extract_thinking({"role": role, "text": text, "thinking": thinking}) or []:
                c.setdefault("session", session); c.setdefault("turn_index", ti)
                cands.append(c)
        for cand in cands:
            if promote is None:
                break
            if route:
                cand["tier"] = route(cand)
            opp = opposing(new_claim=cand, existing_claims=self.store.by_subject(cand.get("subject", ""))) if opposing else []
            verdict, fired, q = promote(cand, self.store)
            gate[cand.get("id", f"cand_{len(gate)}")] = {"verdict": verdict, "fired": fired}
            if verdict == "promoted":
                tagged.append(cand["id"])
                refuted += opp
            if q and not question:
                question = q
        rec = {"ts": _now(), "op": "remember", "session": session, "turn_index": ti, "role": role, "episode": ep["id"],
               "tagged": tagged, "gate": gate, "refuted": refuted, "question": question}
        _append(self.out / "trace.jsonl", rec)
        return {"episode_id": ep["id"], "tagged": tagged, "gate": gate, "refuted": refuted, "question": question}

    def feedback(self, req: dict) -> dict:
        session, ti, reply = req.get("session", "default"), int(req.get("turn_index", -1)), req.get("reply", "") or ""
        injected = req.get("injected") or self.injected.get((session, ti), [])
        used = []
        low = reply.lower()
        # cheap usage signal: claim text overlap with reply (lane C refines in evolve/signals.py)
        for cid in injected:
            text = self._text_of(cid)
            if text and (text.lower()[:40] in low or any(w in low for w in _keywords(text))):
                used.append(cid)
        _append(self.out / "retrieval_log.jsonl", {"ts": _now(), "session": session, "turn_index": ti, "used": used, "injected": injected, "kind": "feedback"})
        return {"used": used}

    def consolidate(self, req: dict) -> dict:
        run = _try("engram.consolidate.dag", "run")
        if run is None:
            return {"digest": "", "counts": {"expired": 0, "captured": 0, "encoded": 0, "merged": 0, "adjudicated": 0,
                                             "promoted": 0, "wired": 0, "reindexed": False, "generation": 0}}
        return run(self.store, req.get("messages") or [], reason=req.get("reason", "cli"), out=self.out)

    def explain(self, req: dict) -> dict:
        s = self.store.stats()
        lines = [f"Store: {s.name}. Promoted {s.promoted}, held {s.held}, refuted {s.refuted}, dormant {s.dormant}."]
        md = self.store.export_markdown()
        if md.get("USER.md"):
            lines += ["", "About you:", md["USER.md"]]
        if md.get("MEMORY.md"):
            lines += ["", "Standing notes:", md["MEMORY.md"]]
        return {"text": "\n".join(lines)}

    def report(self, req: dict) -> dict:
        render = _try("engram.report.render", "render")
        if render:
            return {"text": render(self.store, self.out)}
        s = self.store.stats()
        return {"text": f"speed — · accuracy — · tokens — · precision — · recall —\n(report lane not merged) store={s.name} promoted={s.promoted}"}

    def refute(self, req: dict) -> dict:
        self.store.refute(req["claim_id"], by=req.get("by"))
        return {}

    # ---- helpers ---------------------------------------------------------
    def _text_of(self, cid: str) -> str | None:
        getter = getattr(self.store, "get", None)
        if getter:
            c = getter(cid)
            return c["text"] if c else None
        conn = getattr(self.store, "_conn", None)
        if conn is not None:
            row = conn.execute("SELECT text FROM memories WHERE id = ?", (cid,)).fetchone()
            return row[0] if row else None
        return None

    def handle(self, req: dict) -> dict:
        t0 = time.perf_counter()
        op = req.get("op")
        fn = getattr(self, op, None) if op in {"recall", "remember", "feedback", "consolidate", "explain", "report", "refute"} else None
        try:
            if fn is None:
                raise ValueError(f"unknown op {op!r}")
            res = {"ok": True, **fn(req)}
        except Exception as e:  # never crash the loop
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return res


_STOP = set("a an the and or but if then so of to in on at for from by with about as is are was were be been being do does did have has had it its this that these those i you we they he she me my our your their what which who whom when where why how can could would should will shall may might must not no yes oh ok okay please".split())


def _fts_query(text: str) -> str:
    """Sentence → FTS5 OR-query of quoted keywords (FTS5 ANDs bare terms and chokes on ?/')."""
    import re
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w); out.append(f'"{w}"')
    return " OR ".join(out[:12])


def _sentences(text: str) -> list[str]:
    import re
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return parts or [text]


def _keywords(text: str) -> list[str]:
    return [w for w in text.lower().replace(".", " ").split() if len(w) > 5][:3]


def serve(engram: Engram | None = None) -> None:
    eng = engram or Engram()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps({"ok": False, "error": f"bad json: {e}", "ms": 0}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps(eng.handle(req)) + "\n")
        sys.stdout.flush()
