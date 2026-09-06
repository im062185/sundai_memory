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
        hits = self.store.query(text, k=k, session=session)
        claims, used = [], 0
        for h in hits:
            t = _tokens(h.claim["text"])
            if used + t > RECALL_TOKENS:
                break
            used += t
            claims.append({"id": h.claim["id"], "text": h.claim["text"], "source_class": h.claim.get("source_class"),
                           "kind": h.claim.get("kind"), "store": h.store, "score": round(h.score, 3)})
        self.store.touch([c["id"] for c in claims])
        ti = int(req.get("turn_index", -1))
        self.injected[(session, ti)] = [c["id"] for c in claims]
        _append(self.out / "retrieval_log.jsonl", {"ts": _now(), "session": session, "turn_index": ti, "query": text,
                                                   "injected": [c["id"] for c in claims], "stores": sorted({c["store"] for c in claims})})
        return {"claims": claims, "tokens": used}

    def remember(self, req: dict) -> dict:
        turn, session, ti = req.get("turn", {}), req.get("session", "default"), int(req.get("turn_index", -1))
        episode_id = "ep_" + uuid.uuid4().hex[:12]
        episodic = _try("engram.p1.episodic", "append")
        if episodic:
            episodic(self.store, {"id": episode_id, "session": session, "turn_index": ti, "role": turn.get("role"),
                                  "text": turn.get("text", ""), "thinking": turn.get("thinking"), "ts": _now()})
        else:
            _append(self.out / "episodes.jsonl", {"id": episode_id, "session": session, "turn_index": ti, **turn, "ts": _now()})
        tagged, gate, refuted, question = [], {}, [], None
        tag = _try("engram.p1.rules", "tag")
        promote = _try("engram.p2.gate", "promote")
        if tag and turn.get("role") == "user":
            for cand in tag(turn.get("text", ""), session=session, turn_index=ti):
                if promote:
                    verdict, fired, q = promote(cand, self.store)
                    gate[cand["id"]] = {"verdict": verdict, "fired": fired}
                    if verdict == "promoted":
                        tagged.append(cand["id"])
                    if q and session not in self.questions_asked:
                        question, _ = q, self.questions_asked.add(session)
                    if verdict == "promoted":
                        refuted += [r for r in cand.get("refutes", [])]
        rec = {"ts": _now(), "op": "remember", "session": session, "turn_index": ti, "episode": episode_id,
               "tagged": tagged, "gate": gate, "refuted": refuted, "question": question}
        _append(self.out / "trace.jsonl", rec)
        return {"episode_id": episode_id, "tagged": tagged, "gate": gate, "refuted": refuted, "question": question}

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
