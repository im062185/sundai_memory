# 세션 로그에서 진화에 필요한 신호(사용 여부, 정정 횟수)를 뽑아내는 모듈
"""What the last session told us, read from the two logs the server writes.

Record shapes, exactly as `engram/server.py` writes them today:

  out/retrieval_log.jsonl
    recall    {"ts","session","turn_index","query","injected":[ids],"stores":[...]}
    feedback  {"ts","session","turn_index","used":[ids],"injected":[ids],"kind":"feedback"}
  out/trace.jsonl
    remember  {"ts","op":"remember","session","turn_index","role","episode",
               "tagged":[ids],"gate":{id:{"verdict","fired"}},"refuted":[ids],"question"}

`corrections` is the proxy the scorecard reports: refuted claims plus clarifying
questions. It is the number no other memory system publishes, so it is worth
being explicit that it is a proxy and not a human label.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Signals:
    queries: list[dict] = field(default_factory=list)      # one per recall record
    used: set[str] = field(default_factory=set)            # claim ids the reply actually used
    ignored: set[str] = field(default_factory=set)         # injected but unused
    refuted: int = 0
    questions: int = 0
    sessions: set[str] = field(default_factory=set)
    stored_by_slot: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def corrections(self) -> int:
        return self.refuted + self.questions

    @property
    def corrections_per_session(self) -> float:
        return self.corrections / max(1, len(self.sessions))


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue          # a half-written line must not kill a generation
        if isinstance(rec, dict):
            out.append(rec)
    return out


def load_signals(out_dir: pathlib.Path) -> Signals:
    """Read out/retrieval_log.jsonl and out/trace.jsonl. Missing logs are not an error."""
    out_dir = pathlib.Path(out_dir)
    sig = Signals()

    injected_all: set[str] = set()
    for rec in _read_jsonl(out_dir / "retrieval_log.jsonl"):
        session = rec.get("session", "default")
        sig.sessions.add(session)
        injected = [i for i in (rec.get("injected") or []) if isinstance(i, str)]
        injected_all.update(injected)
        if rec.get("kind") == "feedback":
            sig.used.update(i for i in (rec.get("used") or []) if isinstance(i, str))
        else:
            sig.queries.append(
                {
                    "session": session,
                    "turn_index": rec.get("turn_index", -1),
                    "query": rec.get("query", "") or "",
                    "injected": injected,
                }
            )
    sig.ignored = injected_all - sig.used

    stored: dict[tuple[str, str], int] = defaultdict(int)
    for rec in _read_jsonl(out_dir / "trace.jsonl"):
        if rec.get("op") != "remember":
            continue
        sig.sessions.add(rec.get("session", "default"))
        sig.refuted += len(rec.get("refuted") or [])
        if rec.get("question"):
            sig.questions += 1
        for _cid, verdict in (rec.get("gate") or {}).items():
            if isinstance(verdict, dict) and verdict.get("verdict") == "promoted":
                stored[(rec.get("role", "user"), "promoted")] += 1
    sig.stored_by_slot = dict(stored)
    return sig
