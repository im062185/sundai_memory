"""LoCoMo pressure test (retrieval-level, offline, no LLM).

Metric: answer-in-context recall — a QA counts as a hit if >=50% of the gold
answer's content tokens appear in the context block the memory layer supplies.
This upper-bounds what any answerer model could extract from that context, so
it isolates the MEMORY layer from answerer quality. Also reports mean context
tokens per query (the cost axis Zep/full-history lose on).

Conditions (same conversations, same probes):
  full_history : entire conversation in context (recall upper bound, max cost)
  keyword_rag  : top-10 raw turns by keyword overlap (append-only baseline)
  engram_facts : ONLY the regex fact layer (expected to fail -> quantifies the
                 extraction bottleneck we flagged in README)
  engram       : episodic layer (ACT-R ranked, k=10) + fact block

Categories: 1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop
(category 5 = adversarial/unanswerable, excluded as is standard).
Usage: python eval/locomo_bench.py path/to/locomo10.json
"""
import json, re, sys, time
sys.path.insert(0, __file__.rsplit("/", 2)[0])
from engram.core import Engram

from datetime import datetime

def parse_dt(s):
    return datetime.strptime(s, "%I:%M %p on %d %B, %Y").timestamp()

def toks(s):
    return [w for w in re.findall(r"\w+", str(s).lower()) if len(w) > 2]

def hit(answer, context):
    a = toks(answer)
    if not a:
        return str(answer).lower() in context.lower()
    ctoks = set(toks(context))
    return sum(t in ctoks for t in a) / len(a) >= 0.5

def contexts_for(sample):
    """Ingest one conversation; return dict of condition -> context_fn(question)."""
    conv = sample["conversation"]
    sessions = sorted(int(k.split("_")[1]) for k in conv
                      if re.fullmatch(r"session_\d+", k))
    mem = Engram()
    all_turns = []
    for i in sessions:
        date = conv.get(f"session_{i}_date_time")
        now = parse_dt(date) if date else time.time()
        stamp = f"({date}) " if date else ""
        for turn in conv[f"session_{i}"]:
            text = stamp + turn.get("text", "")
            all_turns.append(f"[{turn['speaker']}] {text}")
            mem.observe_episode(text, turn["speaker"], now)
        mem.sleep(now=now + 3600)
    now = time.time()
    full = "\n".join(all_turns)

    def kw_rag(q, k=10):
        qt = set(toks(q))
        scored = sorted(all_turns, key=lambda t: -len(qt & set(toks(t))))
        return "\n".join(scored[:k])

    return {
        "full_history": lambda q: full,
        "keyword_rag": kw_rag,
        "engram_facts": lambda q: mem.context_block(q, now=now),
        "engram": lambda q: "\n".join(mem.recall_episodes(q, now=now))
                            + "\n" + mem.context_block(q, now=now),
    }

def main(path):
    data = json.load(open(path))
    print("== TUNE split (convs 0-4, used for calibration) ==")
    run_split(data[:5])
    print("== TEST split (convs 5-9, held out) ==")
    run_split(data[5:])
    print("== FULL (all 10, for comparison with prior runs) ==")
    run_split(data)

def run_split(data):
    conds = ["full_history", "keyword_rag", "engram_facts", "engram"]
    stats = {c: {"hit": 0, "n": 0, "tok": 0,
                 "by_cat": {i: [0, 0] for i in (1, 2, 3, 4)}} for c in conds}
    for sample in data:
        ctxs = contexts_for(sample)
        for qa in sample["qa"]:
            cat = qa.get("category")
            if cat == 5:
                continue
            q, gold = qa["question"], qa["answer"]
            for c in conds:
                ctx = ctxs[c](q)
                h = hit(gold, ctx)
                st = stats[c]
                st["hit"] += h; st["n"] += 1; st["tok"] += len(ctx.split())
                st["by_cat"][cat][0] += h; st["by_cat"][cat][1] += 1
    print(f"{'condition':<14}{'recall':>8}{'tokens/q':>10}   per-category (1 multihop / 2 temporal / 3 open / 4 singlehop)")
    for c in conds:
        s = stats[c]
        cats = "  ".join(f"{k}:{v[0]/v[1]:.2f}" for k, v in s["by_cat"].items())
        print(f"{c:<14}{s['hit']/s['n']:>8.3f}{s['tok']//s['n']:>10}   {cats}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/home/claude/locomo/data/locomo10.json")
