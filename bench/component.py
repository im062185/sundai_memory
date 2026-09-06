"""The component bench (TDD §5.5 step 10, §8 CI): the bench queries against
every store in the registry, one row per store, same five columns.

These are COMPONENT numbers. CUJ §5 S8: they are labelled "the pipe works"
and are never presented as benefit. This module measures; engram/report/
owns the label.

What it is for, beyond a table: **A-3**. The naive numpy vector arm keeps
returning claims that have been refuted. It does NOT show up in the query
table on lane B's corpus — the absence claim there is only ever *held*, so no
store returns it before or after the correction. It shows up in `a3_probe()`,
which refutes a *promoted* claim through the product's own `refute` op and asks
again. Per TDD §9: never fix the vector arm before this snapshot records it.

  python -m bench.component                # every store, every scorable query
  python -m bench.component --smoke --n 5  # what CI runs
  python -m bench.component --store sqlite
  python -m bench.component --dump         # print what the seed actually wrote

HOW GOLD IS RESOLVED. Claim ids are random per run — engram/p1/rules.py:40 is
`f"clm_{uuid.uuid4().hex[:12]}"` — so `bench/gold_map.json` cannot hold literal
ids. It holds TEXT PATTERNS. Each store is seeded by replaying lane B's CUJ
fixtures through `remember`, the claims the gate writes are observed at write
time (see SeedLog), and the patterns are matched against those texts to produce
this run's ids. Gold is therefore resolved per store, against what that store
actually holds.

WHAT IS NOT SCORED. Most of the 25 queries name fact keys that lane B's two
fixture sessions never state (fact.judge, fact.benchmark, rule.only_p2_writes,
…). A query whose gold did not resolve is SKIPPED, not scored 0.00 — scoring it
would measure the corpus, not the retriever. The skipped count is printed and
written into out/results.json so the table's `n` is never mistaken for 25.
A query that declares no gold at all (q24, the abstention probe) is kept: an
empty gold set is the right answer there, not a missing one.

Offline. No model, no network, no key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.metrics import (  # noqa: E402
    TOKENS_SERVER,
    Query,
    RecallObservation,
    build_scorecard,
    load_queries,

)
from engram.report.scorecard import (  # noqa: E402
    DEFAULT_RESULTS,
    load_results,
    scorecard_to_dict,
    write_results,
)

GOLD_MAP = REPO / "bench" / "gold_map.json"
FIXTURES = REPO / "tests" / "fixtures"
SEED_SESSIONS = ("session1.jsonl", "session2.jsonl")
DEFAULT_K = 8


# --------------------------------------------------------------------------
# seeding — replay the CUJ fixtures so the stores have something to retrieve
# --------------------------------------------------------------------------

class SeedLog:
    """Everything the gate wrote while seeding, captured at write time.

    The bench never calls store.write() itself (tests/test_isolation.py: only
    p2 may). It wraps the bound method to *observe* what p2 wrote, because the
    Store contract has no "list every claim" op and the contract is frozen.
    """

    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []
        self.refuted: set[str] = set()

    def attach(self, store) -> None:
        write, refute = store.write, store.refute

        def observed_write(claim):
            self.claims.append(dict(claim))
            return write(claim)

        def observed_refute(claim_id, **kw):
            self.refuted.add(claim_id)
            return refute(claim_id, **kw)

        store.write = observed_write      # type: ignore[method-assign]
        store.refute = observed_refute    # type: ignore[method-assign]

    def latest(self) -> dict[str, dict[str, Any]]:
        """id -> claim, last write wins (the gate rewrites a claim it updates)."""
        return {c["id"]: c for c in self.claims}


def seed_turns(paths: Sequence[Path] = ()) -> list[tuple[str, dict[str, Any]]]:
    """(session, turn) pairs from lane B's fixtures, in order."""
    turns: list[tuple[str, dict[str, Any]]] = []
    for path in paths or [FIXTURES / n for n in SEED_SESSIONS]:
        if not path.is_file():
            continue
        session = path.stem
        for line in path.read_text().splitlines():
            if line.strip():
                turns.append((session, json.loads(line)))
    return turns


def seed(engram, turns: Sequence[tuple[str, dict[str, Any]]]) -> None:
    """Replay the journey through `remember` — the same op the extension calls."""
    for session, turn in turns:
        engram.handle({
            "op": "remember",
            "session": session,
            "turn_index": int(turn.get("turn_index", -1)),
            "turn": {"role": turn.get("role", "user"), "text": turn.get("text", "")},
        })


# --------------------------------------------------------------------------
# gold — fact key -> text patterns -> this run's claim ids
# --------------------------------------------------------------------------

def load_patterns(path: Path = GOLD_MAP) -> dict[str, list[str]] | None:
    """fact key -> substrings that identify the claim carrying that fact.

    Absent, no query is scorable, precision and recall come back None and the
    report prints a dash. That is the correct answer to "how good is retrieval"
    when nothing has said what the right answer is.
    """
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return {k: [p.lower() for p in v] for k, v in data.get("facts", {}).items()}


def resolve_gold(patterns: dict[str, list[str]] | None,
                 claims: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Match each fact key's patterns against the seeded claim texts."""
    if not patterns:
        return {}
    resolved: dict[str, list[str]] = {}
    for key, pats in patterns.items():
        ids = [cid for cid, c in claims.items()
               if any(p in (c.get("text") or "").lower() for p in pats)]
        if ids:
            resolved[key] = ids
    return resolved


def scorable(queries: Sequence[Query], resolved: dict[str, list[str]]) -> tuple[list[Query], list[str]]:
    """Keep a query only if every fact key it declares resolved to a claim.

    A query with an unresolved key would be scored against a gold set that is
    missing an answer nobody ever stated — precision 0.00 for returning the
    right thing. Skipping is the honest handling; the count is reported.
    """
    keep, skipped = [], []
    for q in queries:
        missing = [k for k in list(q.gold_facts) + list(q.refuted_facts) if k not in resolved]
        (skipped.append(q.id) if missing else keep.append(q))
    return keep, skipped


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

def observe(engram, query: Query, k: int) -> RecallObservation:
    """One `recall`, recorded per store so the table can be built."""
    t0 = time.perf_counter()
    res = engram.handle({"op": "recall", "text": query.text, "k": k,
                         "session": "bench", "turn_index": 0})
    elapsed = float(res.get("ms", (time.perf_counter() - t0) * 1000))
    claims = res.get("claims", []) if res.get("ok") else []

    by_store: dict[str, list[str]] = {}
    for claim in claims:
        by_store.setdefault(claim.get("store") or "unknown", []).append(claim["id"])

    return RecallObservation(
        query_id=query.id,
        returned=[c["id"] for c in claims],
        gold=query.gold,
        refuted=query.refuted,
        ms=elapsed,
        injected_tokens=res.get("tokens"),
        # The server's `tokens` is len(text)//4 — its own estimate, not a
        # tokenizer count. Labelled honestly; see bench/metrics.py.
        token_source=TOKENS_SERVER,
        by_store=by_store,
        store_ms={},
    )


def run_store(name: str, queries_path: Path, patterns, out_root: Path, k: int,
              limit: int | None) -> dict[str, Any]:
    """Seed this store, resolve gold against what it holds, then query it."""
    from engram.server import Engram

    previous = os.environ.get("ENGRAM_STORE")
    os.environ["ENGRAM_STORE"] = name
    try:
        engram = Engram(out=out_root / name)
        log = SeedLog()
        log.attach(engram.store)
        seed(engram, seed_turns())

        written = log.latest()
        resolved = resolve_gold(patterns, written)
        query_set = load_queries(queries_path, gold_map=resolved or None)
        queries, skipped = scorable(query_set.queries, resolved)
        if limit:
            queries = queries[:limit]

        recalls = [observe(engram, q, k) for q in queries]
        return {
            "recalls": recalls,
            "queries": queries,
            "skipped": skipped,
            "seeded": len(written),
            "refuted": sorted(log.refuted),
            "resolved_keys": sorted(resolved),
            "unresolved_keys": query_set.unresolved,
            "provisional": query_set.provisional,
            "reason": query_set.reason,
            "written": written,
        }
    finally:
        if previous is None:
            os.environ.pop("ENGRAM_STORE", None)
        else:
            os.environ["ENGRAM_STORE"] = previous


def run(queries_path: Path, stores: Sequence[str], patterns, out_root: Path,
        k: int, limit: int | None) -> dict[str, Any]:
    all_recalls: list[RecallObservation] = []
    per_store: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    n_scored = 0

    # The sqlite store persists under out/. Seeding it twice is not a no-op:
    # the gate dedups, so the second run observes almost no writes, the gold
    # patterns resolve against almost nothing, and the row silently reports a
    # different number. The vector arm is in memory and is unaffected — which
    # makes the discrepancy between the two rows even harder to read. Say it.
    if out_root.exists():
        print(f"  warning: {out_root} already exists — a persistent store (sqlite) is "
              f"being reseeded, and the gate will dedup most of the seed. These numbers "
              f"are not comparable to a clean run; `rm -rf {out_root}` first.",
              file=sys.stderr)

    for name in stores:
        result = run_store(name, queries_path, patterns, out_root, k, limit)
        recalls = result["recalls"]
        all_recalls.extend(recalls)
        n_scored = max(n_scored, len(recalls))
        # build_scorecard over the whole observation, NOT per_store_scorecards:
        # that helper deliberately drops ms and tokens because in a merged
        # multi-store recall neither can be attributed to one store. Here each
        # run has exactly one store loaded, so the recall's latency and token
        # count are that store's. A store that returned nothing still gets a
        # row, so the reader sees it was asked and came back empty.
        card = build_scorecard(name, recalls)
        card.arm = name
        per_store[name] = scorecard_to_dict(card)
        detail[name] = {
            "seeded_claims": result["seeded"],
            "refuted_at_seed": len(result["refuted"]),
            "queries_scored": len(recalls),
            "queries_skipped": result["skipped"],
        }
        hits = sum(len(o.returned) for o in recalls)
        refuted_returned = sum(len(set(o.returned) & set(o.refuted)) for o in recalls)
        print(f"  {name}: seeded {result['seeded']} claims "
              f"({len(result['refuted'])} refuted) · {len(recalls)} queries scored, "
              f"{len(result['skipped'])} skipped · {hits} claims returned "
              f"({refuted_returned} refuted) · precision {_fmt(card.precision)}, "
              f"recall {_fmt(card.recall)}", file=sys.stderr)
        if refuted_returned:
            print(f"      A-3: {name} returned a refuted claim {refuted_returned} time(s); "
                  f"each costs precision exactly 1/k on its query.", file=sys.stderr)

    merged = build_scorecard("merged", all_recalls) if all_recalls else None

    # The table above cannot show A-3 on this corpus — see a3_probe's docstring.
    a3 = [a3_probe(name, out_root, k) for name in stores]
    for probe in a3:
        if probe.get("ran"):
            print(f"  A-3 {probe['store']}: refuted a promoted claim → "
                  f"returned again: {probe['returned_after_refute']}, "
                  f"stats.refuted={probe['stats_refuted_after']} "
                  f"({probe['verdict']})", file=sys.stderr)

    return {
        "stores": per_store,
        "merged": scorecard_to_dict(merged) if merged else None,
        "n_queries": n_scored,
        "k": k,
        "detail": detail,
        "a3": a3,
    }


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------
# A-3 — the probe the query table cannot make
# --------------------------------------------------------------------------

A3_TARGET = "shipped batch writes in 3.4"
A3_QUERY = "Can the vendor SDK write records in a batch?"


def a3_probe(name: str, out_root: Path, k: int = DEFAULT_K) -> dict[str, Any]:
    """Refute a PROMOTED claim through the server's `refute` op, then ask again.

    Why this exists as a separate probe: on lane B's CUJ corpus the absence
    claim is only ever *held* — the gate never promotes it — so no store
    returns it before or after the correction, and the 25-query table has no
    way to show A-3. The bug is real all the same: engram/adapters/vector.py's
    refute() writes back the status it found instead of "refuted", so a claim
    that WAS promoted stays promoted and keeps coming back. This probe puts a
    promoted claim through the same user-facing op the demo uses
    (`python -m engram refute <id>`) and records what each store does next.

    Per TDD §9: this snapshot exists so the vector arm's refutation is recorded
    before anyone fixes it. Do not fix it first.
    """
    from engram.server import Engram

    previous = os.environ.get("ENGRAM_STORE")
    os.environ["ENGRAM_STORE"] = name
    try:
        engram = Engram(out=out_root / f"a3-{name}")
        log = SeedLog()
        log.attach(engram.store)
        seed(engram, seed_turns())

        target = next((cid for cid, c in log.latest().items()
                       if A3_TARGET in (c.get("text") or "").lower()), None)
        if target is None:
            return {"store": name, "ran": False,
                    "why": f"no seeded claim matching {A3_TARGET!r}"}

        before = engram.handle({"op": "recall", "text": A3_QUERY, "k": k})
        returned_before = target in [c["id"] for c in before.get("claims", [])]
        engram.handle({"op": "refute", "claim_id": target, "by": "bench.a3"})
        after = engram.handle({"op": "recall", "text": A3_QUERY, "k": k})
        returned_after = target in [c["id"] for c in after.get("claims", [])]
        stats = engram.store.stats()

        return {
            "store": name,
            "ran": True,
            "claim": target,
            "text": log.latest()[target].get("text"),
            "returned_before_refute": returned_before,
            "returned_after_refute": returned_after,
            "stats_refuted_after": stats.refuted,
            # The finding, stated once, in the data rather than only in prose.
            "verdict": ("A-3 reproduces: still returned after refute"
                        if returned_after else
                        "refutation took effect for the next query"),
        }
    finally:
        if previous is None:
            os.environ.pop("ENGRAM_STORE", None)
        else:
            os.environ["ENGRAM_STORE"] = previous


def dump(store: str, patterns) -> int:
    """Print what a seed run actually wrote, so gold_map.json can be authored
    against real claim texts rather than remembered ones."""
    result = run_store(store, REPO / "bench" / "queries.json", patterns,
                       Path("out/component-dump"), DEFAULT_K, None)
    resolved = resolve_gold(patterns, result["written"])
    by_id = {cid: [k for k, ids in resolved.items() if cid in ids] for cid in result["written"]}
    print(f"seed → {store}: {result['seeded']} claims written, "
          f"{len(result['refuted'])} refuted")
    for cid, claim in result["written"].items():
        mark = "REFUTED" if cid in result["refuted"] else claim.get("status", "?")
        keys = ",".join(by_id[cid]) or "—"
        print(f"  {mark:9} {claim.get('kind',''):11} {keys:34} {claim.get('text','')[:90]}")
    print(f"\nresolved keys   : {', '.join(result['resolved_keys']) or '(none)'}")
    print(f"unresolved keys : {', '.join(result['unresolved_keys']) or '(none)'}")
    print(f"queries skipped : {', '.join(result['skipped']) or '(none)'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="a short run for CI")
    ap.add_argument("--n", type=int, default=None, help="use only the first n scorable queries")
    ap.add_argument("--store", action="append", default=None, help="limit to one store (repeatable)")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--out", default="out/component")
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--dump", action="store_true", help="print the seeded claims and exit")
    args = ap.parse_args(argv)

    from engram.adapters import REGISTRY

    available = dict(REGISTRY)
    patterns = load_patterns()
    queries_path = REPO / "bench" / "queries.json"

    if args.dump:
        return dump((args.store or ["sqlite"])[0], patterns)

    stores = args.store or [s for s in sorted(available) if s != "null"]
    unknown = [s for s in stores if s not in available]
    if unknown:
        print(f"unknown store(s) {unknown}; registry has {sorted(available) or '(empty)'}",
              file=sys.stderr)
        return 1

    limit = args.n if args.n is not None else (5 if args.smoke else None)
    print(f"component bench: {len(stores)} store(s) {stores or '(none registered)'}"
          f"{f', first {limit} scorable queries' if limit else ''}", file=sys.stderr)
    if not stores:
        # Lane B's adapters not merged yet. Not a failure: the bench has
        # nothing to measure, and says so instead of writing zeros.
        print("no stores registered — nothing measured. The report will print "
              "a dash for every store column, not a zero.", file=sys.stderr)
    if patterns is None:
        print("bench/gold_map.json missing — no query is scorable; precision and "
              "recall stay '—', not 0.00.", file=sys.stderr)

    component = run(queries_path, stores, patterns, Path(args.out), args.k, limit)

    # The set stays provisional while any fact key is unresolved: the table is
    # a real measurement of a subset, not of the 25 queries.
    total = len(load_queries(queries_path).queries)
    component["n_declared"] = total
    if component["n_queries"] < total:
        component["provisional"] = True
        component["provisional_reason"] = (
            f"{component['n_queries']} of {total} queries scored: the rest name facts "
            f"lane B's fixture sessions never state, so their gold does not resolve. "
            f"Skipped, not scored 0.00.")

    results = load_results(args.results) or {}
    results["component"] = component
    results["provisional"] = bool(results.get("provisional")) or bool(component.get("provisional"))
    path = write_results(results, args.results)
    print(f"wrote {path}")
    if component.get("provisional_reason"):
        print(component["provisional_reason"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
