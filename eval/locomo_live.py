"""End-to-end LoCoMo evaluation with a live answerer and LLM judge.

This produces a real LoCoMo score (the kind comparable in spirit to published
end-to-end numbers), unlike eval/locomo_bench.py which is retrieval-level.

Protocol:
  1. Ingest each conversation session-by-session through Engram (backdated
     timestamps via observe_episode(now=session_time)).
  2. For each QA (categories 1-4), retrieve context and have the answerer
     model produce a short answer.
  3. Score two ways: token-F1 against gold (deterministic, the original
     paper's metric) and LLM-judge correct/incorrect (the protocol most
     memory papers now report). Judge model and prompt are printed into the
     results file for reproducibility.

Operational features:
  - Checkpointing: every result appends to results/<run>.jsonl immediately;
    reruns skip already-answered question ids, so crashes cost nothing.
  - Cost tracking: input/output tokens accumulated from API usage fields.
  - --dry-run: exercises the full pipeline offline with a mock answerer/judge
    (no API key needed) so the harness can be validated before spending.
  - --limit N and --conditions to scope pilot runs.

Usage:
  export ANTHROPIC_API_KEY=sk-...
  python eval/locomo_live.py --data locomo/data/locomo10.json \
      --conditions engram keyword_rag --limit 50        # ~$0.50 pilot
  python eval/locomo_live.py --data ... --conditions engram keyword_rag  # full
  python eval/locomo_live.py --dry-run                  # offline validation
"""
from __future__ import annotations
import argparse, collections, json, os, re, sys, time, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engram.core import Engram

ANSWER_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL = "claude-haiku-4-5-20251001"   # freeze one judge for ALL conditions

ANSWER_PROMPT = """You answer questions about a two-person conversation using
only the excerpts below. Be concise: answer in a short phrase, with no
explanation. If the excerpts don't contain the answer, reply exactly:
No information available.

Excerpts:
{context}

Question: {question}
Answer:"""

JUDGE_PROMPT = """You are grading an answer to a question about a conversation.
Question: {question}
Gold answer: {gold}
Model answer: {prediction}
Reply with exactly one word: CORRECT if the model answer is semantically
equivalent to the gold answer (paraphrase, different date format, or extra
detail is fine), otherwise WRONG."""


def api(model: str, prompt: str, max_tokens: int = 200) -> tuple[str, int, int]:
    key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            u = out.get("usage", {})
            return (out["content"][0]["text"].strip(),
                    u.get("input_tokens", 0), u.get("output_tokens", 0))
        except Exception as e:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def mock_api(model, prompt, max_tokens=200):
    if "grading an answer" in prompt:            # mock judge: substring check
        gold = re.search(r"Gold answer: (.*)", prompt).group(1).lower()
        pred = re.search(r"Model answer: (.*)", prompt).group(1).lower()
        return ("CORRECT" if gold and gold in pred else "WRONG", 0, 0)
    q = re.search(r"Question: (.*)", prompt).group(1)   # mock answerer
    ctx = prompt.split("Excerpts:")[1].split("Question:")[0]
    qt = set(w for w in re.findall(r"\w+", q.lower()) if len(w) > 2)
    best, sc = "No information available.", 0
    for line in ctx.splitlines():
        o = len(qt & set(re.findall(r"\w+", line.lower())))
        if o > sc:
            best, sc = line.strip(), o
    return (best, 0, 0)


def f1(gold: str, pred: str) -> float:
    g = re.findall(r"\w+", str(gold).lower())
    p = re.findall(r"\w+", str(pred).lower())
    if not g or not p:
        return 0.0
    common = collections.Counter(g) & collections.Counter(p)
    n = sum(common.values())
    if n == 0:
        return 0.0
    prec, rec = n / len(p), n / len(g)
    return 2 * prec * rec / (prec + rec)


def parse_dt(s):
    return datetime.strptime(s, "%I:%M %p on %d %B, %Y").timestamp()


def build_conditions(sample):
    conv = sample["conversation"]
    nums = sorted(int(k.split("_")[1]) for k in conv
                  if re.fullmatch(r"session_\d+", k))
    mem = Engram()
    all_turns = []
    for i in nums:
        date = conv.get(f"session_{i}_date_time")
        now = parse_dt(date) if date else time.time()
        stamp = f"({date}) " if date else ""
        for t in conv[f"session_{i}"]:
            text = stamp + t.get("text", "")
            all_turns.append(f"[{t['speaker']}] {text}")
            mem.observe_episode(text, t["speaker"], now)
        mem.sleep(now=now + 3600)

    def toks(s):
        return set(w for w in re.findall(r"\w+", s.lower()) if len(w) > 2)

    def kw_rag(q, k=20):    # budget-matched baseline (~ same tokens as engram)
        qt = toks(q)
        ranked = sorted(all_turns, key=lambda t: -len(qt & toks(t)))
        return "\n".join(ranked[:k])

    return {
        "engram": lambda q: "\n".join(mem.recall_episodes(q))
                            + "\n" + mem.context_block(q),
        "keyword_rag": kw_rag,
        "full_history": lambda q: "\n".join(all_turns),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/home/claude/locomo/data/locomo10.json")
    ap.add_argument("--conditions", nargs="+",
                    default=["engram", "keyword_rag"])
    ap.add_argument("--limit", type=int, default=None,
                    help="max questions per condition (pilot runs)")
    ap.add_argument("--answer-model", default=ANSWER_MODEL)
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    ap.add_argument("--dry-run", action="store_true",
                    help="offline mock answerer/judge; validates the pipeline")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    call = mock_api if args.dry_run else api
    if not args.dry_run and "ANTHROPIC_API_KEY" not in os.environ:
        sys.exit("Set ANTHROPIC_API_KEY or use --dry-run.")

    os.makedirs(args.out, exist_ok=True)
    tag = "dryrun" if args.dry_run else args.answer_model.replace(".", "-")
    path = os.path.join(args.out, f"locomo_live_{tag}.jsonl")
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            done = {(r["condition"], r["qid"]) for r in map(json.loads, fh)}
        print(f"resuming: {len(done)} results already in {path}")
    meta = {"answer_model": args.answer_model, "judge_model": args.judge_model,
            "answer_prompt": ANSWER_PROMPT, "judge_prompt": JUDGE_PROMPT,
            "date": datetime.now().isoformat()}
    with open(os.path.join(args.out, f"locomo_live_{tag}.meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    data = json.load(open(args.data))
    tok_in = tok_out = 0
    fh = open(path, "a")
    for si, sample in enumerate(data):
        ctxs = build_conditions(sample)
        answered = 0
        for qi, qa in enumerate(sample["qa"]):
            if qa.get("category") == 5:
                continue
            qid = f"{sample.get('sample_id', si)}:{qi}"
            for cond in args.conditions:
                if (cond, qid) in done:
                    continue
                if args.limit and answered >= args.limit:
                    break
                ctx = ctxs[cond](qa["question"])
                pred, i1, o1 = call(args.answer_model, ANSWER_PROMPT.format(
                    context=ctx, question=qa["question"]))
                verdict, i2, o2 = call(args.judge_model, JUDGE_PROMPT.format(
                    question=qa["question"], gold=qa["answer"], prediction=pred),
                    max_tokens=5)
                tok_in += i1 + i2; tok_out += o1 + o2
                rec = {"condition": cond, "qid": qid,
                       "category": qa.get("category"),
                       "question": qa["question"], "gold": str(qa["answer"]),
                       "prediction": pred,
                       "judge": verdict.strip().upper().startswith("CORRECT"),
                       "f1": round(f1(qa["answer"], pred), 4)}
                fh.write(json.dumps(rec) + "\n"); fh.flush()
            answered += 1
        print(f"conversation {si+1}/{len(data)} done "
              f"(tokens: {tok_in} in / {tok_out} out)")
    fh.close()

    # ---- report ----
    rows = [json.loads(l) for l in open(path)]
    print(f"\n{'condition':<14}{'judge-acc':>10}{'mean F1':>9}{'n':>7}"
          "   per-category judge-acc")
    for cond in sorted({r['condition'] for r in rows}):
        rs = [r for r in rows if r["condition"] == cond]
        by = collections.defaultdict(list)
        for r in rs:
            by[r["category"]].append(r["judge"])
        cats = "  ".join(f"{c}:{sum(v)/len(v):.2f}" for c, v in sorted(by.items()))
        print(f"{cond:<14}{sum(r['judge'] for r in rs)/len(rs):>10.3f}"
              f"{sum(r['f1'] for r in rs)/len(rs):>9.3f}{len(rs):>7}   {cats}")
    if not args.dry_run:
        cost = tok_in / 1e6 * 1.0 + tok_out / 1e6 * 5.0   # Haiku 4.5 rates
        print(f"\nthis run's new API usage: {tok_in} in / {tok_out} out "
              f"(~${cost:.2f} at Haiku 4.5 rates)")


if __name__ == "__main__":
    main()
