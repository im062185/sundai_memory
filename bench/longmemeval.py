"""LongMemEval slice + driver (TDD §6.8, step 12).

The release layout is recorded in bench/LONGMEMEVAL.md — read from the
dataset README and then verified against the downloaded file. Nothing here
assumes a variant name or a field name; everything is keyed to what that
file says.

Three things this module does:

  --build-slice   pick a deterministic >=20-question slice covering all six
                  question types, write tests/fixtures/longmemeval_slice.json
  --run           per question: fresh Engram -> ingest the history sessions
                  through `remember` (encoder off) -> `recall(question)` ->
                  answer with the recalled claims injected as the SAME
                  "Memory (provenance-tagged)" message the pi extension
                  sends -> record answer, tokens, latency
  --run --arm off the memory-off arm: no recall, history truncated to the
                  model's context

The answerer is pluggable so the slice, the layout and the judge could be
finished before a chat model existed on this machine:

  --answerer pi     `pi -p --mode json` (needs a working models.json)
  --answerer stub   deterministic offline echo; every record it produces is
                    tagged answerer="stub" and the judge refuses to score it

Usage:
  python -m bench.longmemeval --build-slice
  python -m bench.longmemeval --run --answerer stub --arm on
  python -m bench.longmemeval --run --answerer pi --model lmstudio/<id>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.metrics import TOKENS_SERVER  # noqa: E402

RELEASE = REPO / "bench" / "data" / "longmemeval_oracle.json"
SLICE = REPO / "tests" / "fixtures" / "longmemeval_slice.json"
VARIANT = "longmemeval_oracle"

# Read off the release, not assumed. See bench/LONGMEMEVAL.md.
QUESTION_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "knowledge-update",
    "temporal-reasoning",
)
ABSTENTION_SUFFIX = "_abs"

NON_ABS_PER_TYPE = 3
ABS_PER_TYPE = 1

# Byte-for-byte the string pi-extension/src/index.ts builds (memoryMessage).
MEMORY_HEADER = "Memory (provenance-tagged):"
SOURCE_CLASSES = ("said", "verified", "inferred")


# --------------------------------------------------------------------------
# slice
# --------------------------------------------------------------------------

def is_abstention(instance: dict) -> bool:
    return str(instance.get("question_id", "")).endswith(ABSTENTION_SUFFIX)


def load_release(path: Path = RELEASE) -> list[dict]:
    if not path.is_file():
        raise SystemExit(
            f"missing {path}\n"
            f"download it first (see bench/LONGMEMEVAL.md):\n"
            f"  mkdir -p {path.parent}\n"
            f"  curl -L -o {path} https://huggingface.co/datasets/"
            f"xiaowu0162/longmemeval-cleaned/resolve/main/{path.name}"
        )
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a JSON list of instances, got {type(data).__name__}")
    return data


def build_slice(release: Sequence[dict]) -> dict[str, Any]:
    """Deterministic selection — see bench/LONGMEMEVAL.md for the rule.

    Sorted by question_id, NOT by length: sorting by turn count would quietly
    select the easy instances.
    """
    seen_types = sorted({str(x.get("question_type")) for x in release})
    unexpected = [t for t in seen_types if t not in QUESTION_TYPES]
    if unexpected:
        raise SystemExit(
            f"release contains question_type(s) not recorded in bench/LONGMEMEVAL.md: "
            f"{unexpected}. Re-read the release before changing anything."
        )

    picked: list[dict] = []
    for qtype in QUESTION_TYPES:
        of_type = sorted(
            (x for x in release if x.get("question_type") == qtype),
            key=lambda x: str(x["question_id"]),
        )
        plain = [x for x in of_type if not is_abstention(x)][:NON_ABS_PER_TYPE]
        abstain = [x for x in of_type if is_abstention(x)][:ABS_PER_TYPE]
        if len(plain) < NON_ABS_PER_TYPE:
            raise SystemExit(f"only {len(plain)} non-abstention instances of type {qtype}")
        picked.extend(plain + abstain)

    covered = {x["question_type"] for x in picked}
    assert covered == set(QUESTION_TYPES), covered

    return {
        "variant": VARIANT,
        "source": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned",
        "layout_evidence": "bench/LONGMEMEVAL.md (read from the README, verified against the file)",
        "selection_rule": (
            f"group by question_type; sort by question_id; take the first "
            f"{NON_ABS_PER_TYPE} non-abstention of each of the six types, plus the first "
            f"{ABS_PER_TYPE} abstention (question_id ending '{ABSTENTION_SUFFIX}') of each type "
            f"that has any. Deterministic; rerunning reproduces this file."
        ),
        "n": len(picked),
        "categories": list(QUESTION_TYPES),
        "n_abstention": sum(1 for x in picked if is_abstention(x)),
        "questions": picked,
    }


def write_slice(data: dict[str, Any], path: Path = SLICE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def load_slice(path: Path = SLICE) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------------
# the memory message — identical to the extension's
# --------------------------------------------------------------------------

def memory_message(claims: Sequence[dict]) -> str:
    """Mirror of memoryMessage() in pi-extension/src/index.ts.

    If that function changes, this must change with it, or the bench stops
    measuring what the demo does. tests assert the two agree in shape.
    """
    def tag(source_class: str | None) -> str:
        return source_class if source_class in SOURCE_CLASSES else "said"

    lines = [f"- [{tag(c.get('source_class'))}] {c.get('text', '')}" for c in claims]
    return MEMORY_HEADER + "\n" + "\n".join(lines)


# --------------------------------------------------------------------------
# answerers
# --------------------------------------------------------------------------

@dataclass
class Answer:
    text: str
    prompt_chars: int
    latency_ms: float
    tokens: dict[str, Any] = field(default_factory=dict)
    answerer: str = "stub"
    error: str | None = None


class StubAnswerer:
    """Offline placeholder. Produces a deterministic non-answer.

    Everything it returns carries answerer="stub"; bench/judge.py refuses to
    score stub answers, so a stub run can never become an accuracy number.
    """
    name = "stub"

    def __call__(self, prompt: str, *, memory: str | None) -> Answer:
        t0 = time.perf_counter()
        head = (memory or "").splitlines()[1:2]
        text = "[stub answerer — no model was called] " + (head[0].strip() if head else "no memory injected")
        return Answer(
            text=text,
            prompt_chars=len(prompt) + len(memory or ""),
            latency_ms=(time.perf_counter() - t0) * 1000,
            answerer=self.name,
        )


class PiAnswerer:
    """`pi -p --mode json`, per pi-extension/HOOKS.md ("Headless / JSON mode").

    The memory message is passed as its own leading message so the model sees
    exactly what the extension injects in an interactive session.
    """
    name = "pi"

    def __init__(self, model: str | None = None, timeout: int = 180, binary: str = "pi"):
        self.model = model
        self.timeout = timeout
        self.binary = binary

    def __call__(self, prompt: str, *, memory: str | None) -> Answer:
        full = f"{memory}\n\n{prompt}" if memory else prompt
        cmd = [self.binary, "-p", "--mode", "json"]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(full)

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError:
            return Answer("", len(full), 0.0, answerer=self.name, error=f"{self.binary} not on PATH")
        except subprocess.TimeoutExpired:
            return Answer("", len(full), self.timeout * 1000.0, answerer=self.name, error="timeout")
        ms = (time.perf_counter() - t0) * 1000

        text, tokens = _parse_pi_json(proc.stdout)
        error = None
        if not text:
            error = (proc.stderr or proc.stdout or "no output")[-400:]
        return Answer(text=text, prompt_chars=len(full), latency_ms=ms, tokens=tokens,
                      answerer=self.name, error=error)


def _parse_pi_json(stdout: str) -> tuple[str, dict[str, Any]]:
    """Pull the reply text and usage out of pi's JSON event stream.

    HOOKS.md records the stream as JsonAgentSessionEvent lines filtered with
    `select(.type=="message_end")`. Field names inside an event are not
    recorded there, so this reads defensively: it collects assistant text
    from whatever shape it finds and never invents a token count. If usage is
    absent, tokens stays empty and the metric is marked estimated upstream.
    """
    chunks: list[str] = []
    tokens: dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        usage = event.get("usage") or (event.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            tokens.update({k: v for k, v in usage.items() if isinstance(v, (int, float))})

        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
        elif isinstance(event.get("text"), str) and event.get("type") in {"message_end", "text", "assistant"}:
            chunks.append(event["text"])

    return "\n".join(c for c in chunks if c).strip(), tokens


def make_answerer(kind: str, model: str | None):
    if kind == "stub":
        return StubAnswerer()
    if kind == "pi":
        return PiAnswerer(model=model)
    raise SystemExit(f"unknown answerer {kind!r} (stub|pi)")


# --------------------------------------------------------------------------
# engram side
# --------------------------------------------------------------------------

def fresh_engram(out_dir: Path):
    """A fresh in-process Engram per question — no leakage between questions.

    Uses engram.server.Engram directly rather than the stdio loop: same code
    path, one process, and the bench can read `ms` off each response exactly
    as the protocol defines it.

    On "encoder disabled" (TDD §6.8 step 2): the TDD names `ENGRAM_ASSIST=0`,
    but engram/server.py does not read it — its knobs are ENGRAM_OUT,
    ENGRAM_STORE and ENGRAM_RECALL_TOKENS. The structural guarantee is
    stronger than the flag anyway: LLM encoding lives in the consolidation
    DAG, `remember` never invokes the DAG, and this driver never calls
    `consolidate` during ingest. The variable is still set, in case lane C's
    encoder reads it, and the discrepancy is written down rather than
    silently resolved either way.
    """
    from engram.server import Engram

    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ENGRAM_ASSIST"] = "0"  # TDD §6.8; not read by server.py today
    return Engram(out=out_dir)


def ingest(engram, instance: dict, session_prefix: str = "lme") -> list[float]:
    """Push every history turn through `remember`. Returns per-call ms."""
    timings: list[float] = []
    sessions = instance.get("haystack_sessions") or []
    session_ids = instance.get("haystack_session_ids") or []
    for s_idx, session in enumerate(sessions):
        session_id = session_ids[s_idx] if s_idx < len(session_ids) else f"{session_prefix}{s_idx}"
        for t_idx, turn in enumerate(session):
            res = engram.handle({
                "op": "remember",
                "session": session_id,
                "turn_index": t_idx,
                "turn": {"role": turn.get("role"), "text": turn.get("content", ""), "thinking": None},
            })
            if res.get("ms") is not None:
                timings.append(float(res["ms"]))
    return timings


def evidence_claim_ids(engram, instance: dict) -> set[str]:
    """Not inferrable from the release: `has_answer` marks evidence TURNS, and
    the mapping turn -> claim id only exists once the gate has run. Left to
    the caller; returns an empty set so precision/recall against evidence is
    reported as unmeasured rather than guessed."""
    return set()


def truncate_history(instance: dict, char_budget: int) -> str:
    """Memory-off arm: the raw history, most recent first, cut to the context.

    Kept as characters, not tokens: no tokenizer for an arbitrary local model
    is available offline, and a fake token count is worse than an honest
    character budget. The budget is recorded in the results.
    """
    parts: list[str] = []
    sessions = instance.get("haystack_sessions") or []
    dates = instance.get("haystack_dates") or []
    for s_idx in range(len(sessions) - 1, -1, -1):
        date = dates[s_idx] if s_idx < len(dates) else ""
        block = [f"[session {s_idx + 1}{(' — ' + date) if date else ''}]"]
        for turn in sessions[s_idx]:
            block.append(f"{turn.get('role')}: {turn.get('content', '')}")
        text = "\n".join(block)
        if sum(len(p) for p in parts) + len(text) > char_budget:
            break
        parts.insert(0, text)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

@dataclass
class Record:
    question_id: str
    question_type: str
    abstention: bool
    question: str
    gold: Any
    arm: str
    answer: str
    answerer: str
    recalled: list[dict] = field(default_factory=list)
    injected_tokens: int | None = None
    token_source: str = TOKENS_SERVER
    recall_ms: float | None = None
    remember_ms_mean: float | None = None
    answer_ms: float | None = None
    model_tokens: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def run_question(
    instance: dict,
    answerer,
    arm: str,
    out_root: Path,
    k: int = 5,
    char_budget: int = 6000,
) -> Record:
    question = instance.get("question", "")
    record = Record(
        question_id=str(instance.get("question_id")),
        question_type=str(instance.get("question_type")),
        abstention=is_abstention(instance),
        question=question,
        gold=instance.get("answer"),
        arm=arm,
        answer="",
        answerer=getattr(answerer, "name", "?"),
    )

    if arm == "on":
        out_dir = out_root / "stores" / record.question_id
        engram = fresh_engram(out_dir)
        remember_ms = ingest(engram, instance)
        record.remember_ms_mean = sum(remember_ms) / len(remember_ms) if remember_ms else None

        res = engram.handle({"op": "recall", "text": question, "k": k, "session": record.question_id})
        if not res.get("ok"):
            record.error = res.get("error")
            return record
        record.recalled = res.get("claims", [])
        record.injected_tokens = res.get("tokens")
        record.recall_ms = res.get("ms")
        memory = memory_message(record.recalled) if record.recalled else None
        prompt = question
    elif arm == "off":
        memory = None
        history = truncate_history(instance, char_budget)
        prompt = f"{history}\n\n{question}" if history else question
        record.injected_tokens = 0
    else:
        raise SystemExit(f"unknown arm {arm!r} (on|off)")

    answer = answerer(prompt, memory=memory)
    record.answer = answer.text
    record.answer_ms = answer.latency_ms
    record.model_tokens = answer.tokens
    record.error = record.error or answer.error
    return record


def run(
    slice_data: dict[str, Any],
    answerer,
    arm: str,
    out_root: Path,
    limit: int | None = None,
    k: int = 5,
) -> list[Record]:
    questions = slice_data["questions"][:limit] if limit else slice_data["questions"]
    records: list[Record] = []
    for i, instance in enumerate(questions, 1):
        rec = run_question(instance, answerer, arm, out_root, k=k)
        records.append(rec)
        state = rec.error or ("no recall (memory-off)" if arm == "off" else f"{len(rec.recalled)} claims")
        print(f"  [{i}/{len(questions)}] {rec.question_id} ({rec.question_type}) — {state}", file=sys.stderr)
    return records


def write_run(records: Sequence[Record], arm: str, answerer_name: str, out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / f"longmemeval_{arm}.json"
    path.write_text(json.dumps({
        "variant": VARIANT,
        "arm": arm,
        "answerer": answerer_name,
        "n": len(records),
        "records": [r.to_dict() for r in records],
    }, indent=2, ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-slice", action="store_true", help="rebuild tests/fixtures/longmemeval_slice.json")
    ap.add_argument("--run", action="store_true", help="run the slice")
    ap.add_argument("--arm", choices=("on", "off"), default="on")
    ap.add_argument("--answerer", choices=("stub", "pi"), default="stub")
    ap.add_argument("--model", default=os.environ.get("ENGRAM_CHAT_MODEL"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default="out/bench")
    args = ap.parse_args(argv)

    if args.build_slice:
        data = build_slice(load_release())
        path = write_slice(data)
        by_type: dict[str, int] = {}
        for q in data["questions"]:
            by_type[q["question_type"]] = by_type.get(q["question_type"], 0) + 1
        print(f"wrote {path} — n={data['n']}, abstention={data['n_abstention']}")
        for qtype in QUESTION_TYPES:
            print(f"  {qtype}: {by_type.get(qtype, 0)}")

    if args.run:
        slice_data = load_slice()
        answerer = make_answerer(args.answerer, args.model)
        out_root = Path(args.out)
        n = min(args.limit, slice_data["n"]) if args.limit else slice_data["n"]
        of = "" if n == slice_data["n"] else f" of {slice_data['n']}"
        print(f"running {n}{of} questions, arm={args.arm}, answerer={answerer.name}", file=sys.stderr)
        records = run(slice_data, answerer, args.arm, out_root, limit=args.limit, k=args.k)
        path = write_run(records, args.arm, answerer.name, out_root)
        print(f"wrote {path}")
        if answerer.name == "stub":
            print("answerer=stub — these answers are placeholders; the judge will refuse to score them.")

    if not args.build_slice and not args.run:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
