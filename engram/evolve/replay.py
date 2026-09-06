# 살아있는 모델 없이 기록된 질의를 재생해 게놈 후보의 적합도를 재는 모듈
"""Replay: score a candidate genome against what actually happened.

No live model, no agent, seconds not minutes. For every recall query the server
logged, run it again against the store with the candidate's `k` and blend, and
measure four numbers:

    recall_at_k    fraction of previously-used claim ids the genome still finds   (up)
    tokens         mean tokens injected per query                                 (down)
    corrections    refuted claims + clarifying questions, from signals            (down)
    median_ms      median wall time of a replayed recall                          (down)

A limitation worth stating plainly: `SQLiteStore.query()` computes its own
`fts_score * activation + link_boost` and takes no blend argument, so the blend
cannot be pushed into the store. Replay over-fetches and re-ranks on the public
`RecallHit` fields instead, which changes the ordering the blend controls but
not the store's own candidate set.
"""
from __future__ import annotations

import statistics
import time
from typing import Any

from engram.server import _fts_query, _tokens

# Over-fetch this many times k before re-ranking, so the blend has room to reorder.
POOL_FACTOR = 4
POOL_MIN = 20


def _rerank(hits: list[Any], blend: float, k: int, token_budget: int) -> tuple[list[str], int]:
    """Blend the store's score with activation, then cut by k and the token budget."""
    if not hits:
        return [], 0
    scores = [float(getattr(h, "score", 0.0)) for h in hits]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0

    ranked = []
    for hit, raw in zip(hits, scores):
        claim = getattr(hit, "claim", {}) or {}
        activation = float(claim.get("activation", 1.0) or 1.0)
        blended = blend * ((raw - lo) / span) + (1.0 - blend) * min(activation, 1.0)
        ranked.append((blended, claim))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    ids, used = [], 0
    for _score, claim in ranked[:k]:
        cost = _tokens(claim.get("text", "") or "")
        if used + cost > token_budget:
            break
        used += cost
        ids.append(claim.get("id"))
    return [i for i in ids if i], used


def replay(store, genome, signals) -> dict[str, float]:
    """Rerun every logged query under this genome. Returns the fitness dict."""
    weights = genome.weights
    k = int(weights.get("k", 5))
    blend = float(weights.get("fts_activation_blend", 0.6))
    budget = int(weights.get("recall_token_budget", 400))
    pool = max(k * POOL_FACTOR, POOL_MIN)

    hit_counts, miss_counts, token_counts, timings = 0, 0, [], []

    for record in signals.queries:
        text = record.get("query", "") or ""
        if not text:
            continue
        query = _fts_query(text) if getattr(store, "name", "") == "sqlite" else text
        if not query:
            continue

        t0 = time.perf_counter()
        hits = store.query(query, k=pool)
        ids, tokens = _rerank(list(hits), blend, k, budget)
        timings.append((time.perf_counter() - t0) * 1000.0)
        token_counts.append(tokens)

        # Gold = the claims this turn's reply actually used, per the feedback op.
        gold = {i for i in record.get("injected", []) if i in signals.used}
        if not gold:
            continue
        found = gold & set(ids)
        hit_counts += len(found)
        miss_counts += len(gold) - len(found)

    total_gold = hit_counts + miss_counts
    return {
        "recall_at_k": (hit_counts / total_gold) if total_gold else 0.0,
        "tokens": statistics.fmean(token_counts) if token_counts else 0.0,
        "corrections": float(signals.corrections),
        "median_ms": statistics.median(timings) if timings else 0.0,
    }
