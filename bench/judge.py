"""Judge LongMemEval answers against gold (TDD §6.8, step 12; AMD-03 §4).

The rule this file exists to enforce: **every accuracy number carries the
judge's name, or the word "not run".** There is no third state and no
silent zero.

  ANTHROPIC_API_KEY set   -> grade with claude-sonnet-5, judge = "claude-sonnet-5"
  else OPENAI_API_KEY set -> grade with gpt-4.1,         judge = "openai/gpt-4.1"
  neither                 -> accuracy null, judge = "not run"
  (Judge switched to allow OpenAI on 2026-09-06 by lane A with the team's ok: one key for
  answerer and judge. The judge is a different, larger model than the answerer, and it is
  named on every accuracy line, which is the rule this file exists to enforce.)

This is the only file in the repo that touches the network (CLAUDE.md).
Nothing here runs from the chat loop or from the test suite; the tests
exercise the pure parts (gold stringification, the refusals) with a fake
client and never construct a real one.

Records it refuses to score, each producing correct=None rather than False:

  answerer == "stub"   the driver's offline echo. Scoring it would manufacture
                       a benchmark number out of a string we wrote ourselves.
  record.error         the arm failed for that question; no answer exists.
  empty answer         nothing to grade.

Usage:
  python -m bench.judge out/bench/longmemeval_on.json
  python -m bench.judge out/bench/longmemeval_on.json --out out/bench
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.metrics import JUDGE_NOT_RUN, Judgement, accuracy  # noqa: E402

OPENAI_JUDGE_MODEL = "openai/gpt-4.1"
JUDGE_MODEL = "claude-sonnet-5" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else (
    OPENAI_JUDGE_MODEL if os.environ.get("OPENAI_API_KEY", "").strip() else "claude-sonnet-5")
MAX_TOKENS = 1024
STUB_ANSWERER = "stub"

# claude-sonnet-5 rejects non-default temperature/top_p/top_k with a 400, and
# rejects thinking.budget_tokens. The request below is deliberately the
# smallest documented shape: model, max_tokens, system, messages, and the
# structured-output block. Verified against the claude-api skill on
# 2026-09-06; not executed on this machine (no key here), so _judge_call
# degrades to plain text if the structured form is rejected.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["correct", "reason"],
    "additionalProperties": False,
}

SYSTEM = """You grade one answer from a memory-augmented coding assistant against a gold answer from the LongMemEval dataset.

Grade for substance, not wording. The answer is correct if it states the same fact as the gold answer, even with different phrasing, extra context, or a different unit spelling. It is incorrect if it states a different fact, hedges so much that no fact is asserted, or answers a different question.

Some questions are abstention questions: the gold answer says the information is not available (e.g. "The information provided is not enough..."). For those, an answer is correct only if it also declines — says it does not know, or that the history does not contain the fact. An answer that guesses a plausible fact is incorrect.

Be strict about numbers, dates and names: a wrong digit is wrong."""


# --------------------------------------------------------------------------
# what we can and cannot score
# --------------------------------------------------------------------------

def have_key() -> bool:
    """True when ANTHROPIC_API_KEY is set and non-empty.

    Note for whoever runs this later: an unset variable does not always mean
    no credentials (an `ant auth login` profile resolves too). Lane D's brief
    is explicit — gate on the variable — so that is what this does, and it
    reports "not run" rather than trying and half-succeeding.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip())


def gold_text(gold: Any) -> str:
    """Gold answers are str for 468 of 500 instances and **int** for 32.

    bench/LONGMEMEVAL.md records this; the judge stringifies rather than
    letting an int reach the message body and raise.
    """
    if gold is None:
        return ""
    if isinstance(gold, (list, tuple)):
        return " / ".join(gold_text(g) for g in gold)
    return str(gold)


def unscorable_reason(record: dict) -> str | None:
    """None when the record can be graded; otherwise why it cannot."""
    if record.get("answerer") == STUB_ANSWERER:
        return "answerer=stub — an offline echo, not a model answer"
    if record.get("error"):
        return f"run error: {record['error']}"
    if not str(record.get("answer") or "").strip():
        return "empty answer"
    return None


def user_prompt(record: dict) -> str:
    abstention = bool(record.get("abstention"))
    parts = [
        f"Question: {record.get('question', '')}",
        f"Gold answer: {gold_text(record.get('gold'))}",
        f"Assistant answer: {record.get('answer', '')}",
    ]
    if abstention:
        parts.append(
            "This is an abstention question: the gold answer declines. "
            "Mark correct only if the assistant also declines."
        )
    parts.append('Reply with the verdict object: correct (boolean) and a one-sentence reason.')
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

def make_client():
    """Anthropic client when ANTHROPIC_API_KEY is set, else an OpenAI client. Raises if neither."""
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        import anthropic  # imported here so the module loads without the SDK
        return anthropic.Anthropic()
    import openai
    return openai.OpenAI()


def _is_openai(client: Any) -> bool:
    return type(client).__module__.startswith("openai")


def _judge_openai(client: Any, record: dict, model: str) -> tuple[bool | None, str]:
    mid = model.split("/", 1)[1] if model.startswith("openai/") else model
    try:
        r = client.chat.completions.create(
            model=mid, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": SYSTEM + "\nReply with JSON: {\"correct\": true|false, \"reason\": \"...\"}"},
                      {"role": "user", "content": user_prompt(record)}],
            response_format={"type": "json_object"},
        )
        return _parse_verdict((r.choices[0].message.content or "").strip())
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _text_of(response: Any) -> str:
    """Concatenate the text blocks. Thinking blocks have no .text — skip them."""
    out = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            out.append(getattr(block, "text", ""))
    return "\n".join(out).strip()


def _parse_verdict(text: str) -> tuple[bool | None, str]:
    """Read the verdict. Structured output gives JSON; the fallback gives prose."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and isinstance(obj.get("correct"), bool):
            return obj["correct"], str(obj.get("reason", ""))
    except (ValueError, TypeError):
        pass
    low = text.lower()
    if "incorrect" in low or '"correct": false' in low or "correct: false" in low:
        return False, text[:200]
    if "correct" in low:
        return True, text[:200]
    return None, f"unparsable verdict: {text[:200]}"


def judge_one(client: Any, record: dict, model: str = JUDGE_MODEL) -> tuple[bool | None, str]:
    """Grade one record. Returns (correct, reason); correct is None on failure."""
    if _is_openai(client):
        return _judge_openai(client, record, model)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user_prompt(record)}],
    }
    try:
        response = client.messages.create(
            **request,
            output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        )
    except Exception as e:  # structured output rejected, or a transport error
        if _is_bad_request(e):
            try:
                response = client.messages.create(**request)
            except Exception as e2:
                return None, f"{type(e2).__name__}: {e2}"
        else:
            return None, f"{type(e).__name__}: {e}"
    return _parse_verdict(_text_of(response))


def _is_bad_request(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    return status == 400 or type(e).__name__ == "BadRequestError"


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def judge_records(
    records: Sequence[dict],
    client: Any = None,
    model: str = JUDGE_MODEL,
) -> tuple[list[Judgement], str, list[dict]]:
    """Grade every scorable record.

    Returns (judgements, judge_name, notes). With no client and no key the
    judge name is "not run" and every judgement carries correct=None — the
    categories and n still come through, so the report can print the slice
    size next to an honest blank.
    """
    if client is None and have_key():
        client = make_client()
    judge_name = model if client is not None else JUDGE_NOT_RUN

    judgements: list[Judgement] = []
    notes: list[dict] = []
    for record in records:
        qid = record.get("question_id", "?")
        category = record.get("question_type")
        skip = unscorable_reason(record)
        if skip or client is None:
            reason = skip or "ANTHROPIC_API_KEY not set — judge not run"
            judgements.append(Judgement(question_id=qid, correct=None, category=category,
                                        judge=JUDGE_NOT_RUN))
            notes.append({"question_id": qid, "scored": False, "reason": reason})
            continue
        correct, reason = judge_one(client, record, model=model)
        judgements.append(Judgement(question_id=qid, correct=correct, category=category,
                                    judge=model if correct is not None else JUDGE_NOT_RUN))
        notes.append({"question_id": qid, "scored": correct is not None,
                      "correct": correct, "reason": reason})

    # A judge that scored nothing did not run, whatever client we held.
    if not any(j.correct is not None for j in judgements):
        judge_name = JUDGE_NOT_RUN
    return judgements, judge_name, notes


def judgements_to_dict(
    judgements: Sequence[Judgement],
    judge_name: str,
    notes: Sequence[dict],
    arm: str,
    variant: str,
) -> dict:
    """The on-disk shape the report reads. accuracy is null, never 0, when unjudged."""
    metric = accuracy(list(judgements), judge=judge_name if judge_name != JUDGE_NOT_RUN else None)
    return {
        "variant": variant,
        "arm": arm,
        "judge": metric.judge,
        "n": metric.n,
        "accuracy": metric.value,
        "per_category": {cat: {"accuracy": val, "n": n}
                         for cat, (val, n) in sorted(metric.per_category.items())},
        "judgements": [{"question_id": j.question_id, "correct": j.correct,
                        "category": j.category, "judge": j.judge} for j in judgements],
        "notes": list(notes),
    }


def judge_run_file(run_path: Path, out_dir: Path, model: str = JUDGE_MODEL) -> Path:
    run = json.loads(run_path.read_text())
    records = run.get("records", [])
    judgements, judge_name, notes = judge_records(records, model=model)
    payload = judgements_to_dict(judgements, judge_name, notes,
                                 arm=run.get("arm", "?"), variant=run.get("variant", "?"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"judgements_{run.get('arm', 'run')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="a run file written by bench.longmemeval --run")
    ap.add_argument("--out", default="out/bench")
    ap.add_argument("--model", default=JUDGE_MODEL)
    args = ap.parse_args(argv)

    run_path = Path(args.run)
    if not run_path.is_file():
        print(f"no such run file: {run_path}", file=sys.stderr)
        return 1
    path = judge_run_file(run_path, Path(args.out), model=args.model)
    payload = json.loads(path.read_text())
    value = payload["accuracy"]
    shown = "—" if value is None else f"{value:.2f}"
    print(f"{path}: accuracy {shown} (judge: {payload['judge']}, n={payload['n']})")
    if payload["judge"] == JUDGE_NOT_RUN:
        print("no judge key set (ANTHROPIC_API_KEY or OPENAI_API_KEY), or every record was unscorable — "
              "accuracy is null by design, not zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
