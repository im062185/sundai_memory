"""Fold judged LongMemEval runs into out/results.json so `engram report` shows the row.

Usage: python -m bench.fold_results [--out out]
Reads out/bench/judgements_{on,off}.json (written by bench.judge) and out/bench/longmemeval_{on,off}.json,
merges them into out/results.json (created by bench.component when present) under the TDD §5.5
`longmemeval` block. Memory-off accuracy and the token contrast go into `notes`. Never invents numbers.
"""
from __future__ import annotations
import argparse, json, statistics, sys, time
from pathlib import Path
from engram.report.scorecard import load_results, write_results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="out"); a = ap.parse_args(argv)
    out = Path(a.out); bench = out / "bench"
    on = bench / "judgements_on.json"
    if not on.is_file():
        print("no judged memory-on run; nothing to fold", file=sys.stderr); return 1
    jon = json.loads(on.read_text())
    results = load_results(out / "results.json") or {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provisional": True}
    results["longmemeval"] = {"variant": jon.get("variant"), "n": jon.get("n", 0), "accuracy": jon.get("accuracy"),
                              "judge": jon.get("judge"), "per_category": jon.get("per_category", {}), "arm": "on"}
    notes = [n for n in (results.get("notes") or []) if not str(n).startswith("LongMemEval memory-")]  # replace, never duplicate
    off = bench / "judgements_off.json"
    if off.is_file():
        joff = json.loads(off.read_text())
        notes.append(f"LongMemEval memory-off arm: accuracy {joff.get('accuracy'):.2f} (judge: {joff.get('judge')}, n={joff.get('n')}); "
                     f"per-category {json.dumps({k: v.get('accuracy') for k, v in joff.get('per_category', {}).items()})}")
    run_on = bench / "longmemeval_on.json"
    if run_on.is_file():
        recs = json.loads(run_on.read_text()).get("records", [])
        toks = [r.get("injected_tokens") or 0 for r in recs]
        if toks:
            notes.append(f"LongMemEval memory-on injected a median {statistics.median(toks):.0f} tokens of memory per question "
                         f"(estimate chars/4); memory-off received the raw history truncated to a 6000-character budget (~1500 tokens)")
    results["notes"] = notes
    p = write_results(results, out / "results.json"); print(f"folded into {p}"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
