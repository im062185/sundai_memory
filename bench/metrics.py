"""The five metrics — speed · accuracy · tokens · precision · recall.

Definitions per docs/TDD-engram-v2.md §5.5, §6.7, §7 and docs/AMD-03 (Benchmarks).

NOTE ON PROVENANCE OF THESE DEFINITIONS: TDD §0 item 4 names
`docs/AMD-02-five-metrics.md` §3 as the normative source for the metric
definitions. That file is not in this repository. Everything below is built
from the definitions that ARE in the repo (TDD §5.3 `ms`/`tokens` fields,
§5.5, §6.7, §7's "a returned refuted id lowers precision by exactly 1/k",
CUJ §5 S10's ordering) plus the lane D prompt. Where AMD-02 would have been
the authority, the choice is spelled out in a comment rather than guessed at
silently. If AMD-02 lands, re-check: the p50-vs-mean choice for speed, and
whether recall's denominator excludes refuted gold.

Only pure measurement lives here. Rendering is engram/report/render.py.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# CUJ §5 S10: the report's first line is the five metrics in exactly this order.
# tests/test_metrics.py asserts this tuple and that the renderer follows it.
SCORECARD_ORDER: tuple[str, ...] = ("speed", "accuracy", "tokens", "precision", "recall")

# Where a token count came from. Labelled everywhere it is used so an estimate
# is never mistaken for a measurement.
#
# Read off engram/server.py, not assumed: the server's `recall` response
# carries `tokens`, but it computes that as `max(1, len(text) // 4)` — it is
# the server's own chars/4 estimate, not a tokenizer count. So a number that
# came from the server is TOKENS_SERVER (an estimate), and TOKENS_MEASURED is
# reserved for a real tokenizer/usage count from the model. Calling the
# server's field "measured" would overstate every token number we print.
TOKENS_MEASURED = "tokenizer"
TOKENS_SERVER = "estimate:chars/4 (engram recall)"
TOKENS_ESTIMATED = "estimate:chars/4"

JUDGE_NOT_RUN = "not run"


# --------------------------------------------------------------------------
# observations — what a bench run records
# --------------------------------------------------------------------------

@dataclass
class RecallObservation:
    """One `recall` op: what came back, what should have, and what it cost.

    gold      — claim ids that SHOULD be returned for this query.
    refuted   — claim ids that are refuted and must NEVER be returned. A
                refuted id counts as a false positive even when it appears in
                `gold`, because after refutation it is no longer a correct
                answer (A-3: the naive vector arm keeps returning one).
    """
    query_id: str
    returned: Sequence[str]
    gold: Sequence[str] = ()
    refuted: Sequence[str] = ()
    ms: float | None = None
    injected_tokens: int | None = None
    token_source: str = TOKENS_SERVER
    by_store: Mapping[str, Sequence[str]] = field(default_factory=dict)
    store_ms: Mapping[str, float] = field(default_factory=dict)


@dataclass
class RememberObservation:
    """One `remember` op. Speed only — quality of extraction is the gate's bench."""
    turn_id: str
    ms: float | None = None


@dataclass
class Judgement:
    """One judged answer. `correct` is None when no judge ran."""
    question_id: str
    correct: bool | None = None
    category: str | None = None
    judge: str = JUDGE_NOT_RUN


# --------------------------------------------------------------------------
# metric values — each carries enough to be rendered honestly
# --------------------------------------------------------------------------

@dataclass
class SpeedMetric:
    recall_p50_ms: float | None = None
    recall_mean_ms: float | None = None
    remember_p50_ms: float | None = None
    remember_mean_ms: float | None = None
    n_recall: int = 0
    n_remember: int = 0


@dataclass
class AccuracyMetric:
    """`value` is None when no judge ran. `judge` is named in every case."""
    value: float | None = None
    n: int = 0
    judge: str = JUDGE_NOT_RUN
    per_category: dict[str, tuple[float | None, int]] = field(default_factory=dict)


@dataclass
class TokensMetric:
    per_turn_mean: float | None = None
    per_turn_p50: float | None = None
    n: int = 0
    source: str = TOKENS_ESTIMATED

    @property
    def estimated(self) -> bool:
        """Anything that is not a real tokenizer count is an estimate."""
        return self.source != TOKENS_MEASURED


@dataclass
class Scorecard:
    """The five metrics for one arm (memory-on, memory-off, or a single store)."""
    arm: str
    speed: SpeedMetric
    accuracy: AccuracyMetric
    tokens: TokensMetric
    precision: float | None
    recall: float | None
    n_queries: int = 0

    def as_ordered(self) -> list[tuple[str, Any]]:
        """The five fields in SCORECARD_ORDER. The renderer must not reorder."""
        return [(name, getattr(self, name)) for name in SCORECARD_ORDER]


# --------------------------------------------------------------------------
# precision / recall
# --------------------------------------------------------------------------

def effective_gold(gold: Iterable[str], refuted: Iterable[str]) -> set[str]:
    """Gold minus refuted. A refuted claim is never a correct answer."""
    return set(gold) - set(refuted)


def hits(returned: Iterable[str], gold: Iterable[str], refuted: Iterable[str] = ()) -> set[str]:
    """Returned ids that are correct: in gold, not refuted, deduped."""
    return set(returned) & effective_gold(gold, refuted)


def precision(returned: Sequence[str], gold: Iterable[str], refuted: Iterable[str] = ()) -> float | None:
    """|hits| / k, where k is the number of ids actually returned.

    k is len(returned), not the requested k, so precision is measured over
    what was really injected into the model's context.

    This is the definition that makes TDD §7 exact: swapping one correct id
    in a k-length result for a refuted id drops precision by exactly 1/k,
    because the refuted id cannot enter `hits` (see `effective_gold`).
    Returns None when nothing was returned — undefined, not zero.
    """
    k = len(returned)
    if k == 0:
        return None
    return len(hits(returned, gold, refuted)) / k


def recall(returned: Iterable[str], gold: Iterable[str], refuted: Iterable[str] = ()) -> float | None:
    """|hits| / |gold minus refuted|.

    The denominator drops refuted ids for the same reason precision does: once
    a claim is refuted it is not something recall should have found.
    Returns None when there is no gold to find — undefined, not zero.
    """
    denom = effective_gold(gold, refuted)
    if not denom:
        return None
    return len(hits(returned, gold, refuted)) / len(denom)


def macro_average(values: Iterable[float | None]) -> float | None:
    """Mean over the queries where the metric is defined. None if none are."""
    defined = [v for v in values if v is not None]
    if not defined:
        return None
    return sum(defined) / len(defined)


# --------------------------------------------------------------------------
# speed / tokens / accuracy
# --------------------------------------------------------------------------

def p50(values: Sequence[float]) -> float | None:
    """Median. Reported alongside the mean because one cold query skews a mean
    over 25 queries badly, and the scorecard shows a single number."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2


def mean(values: Sequence[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def speed(
    recalls: Sequence[RecallObservation],
    remembers: Sequence[RememberObservation] = (),
) -> SpeedMetric:
    """ms per recall and ms per remember."""
    r_ms = [o.ms for o in recalls if o.ms is not None]
    w_ms = [o.ms for o in remembers if o.ms is not None]
    return SpeedMetric(
        recall_p50_ms=p50(r_ms),
        recall_mean_ms=mean(r_ms),
        remember_p50_ms=p50(w_ms),
        remember_mean_ms=mean(w_ms),
        n_recall=len(r_ms),
        n_remember=len(w_ms),
    )


def estimate_tokens(text: str) -> int:
    """Crude chars/4 estimate. ONLY for arms where the server did not report a
    token count. Anything derived from it is tagged TOKENS_ESTIMATED and the
    renderer prints '(est)'. Never present an estimate as a measurement."""
    return math.ceil(len(text) / 4)


def tokens(recalls: Sequence[RecallObservation]) -> TokensMetric:
    """Tokens injected per turn."""
    counted = [o for o in recalls if o.injected_tokens is not None]
    if not counted:
        return TokensMetric(n=0)
    sources = {o.token_source for o in counted}
    # One source: say which. Mixed: degrade to "estimated" — the weaker claim wins.
    source = sources.pop() if len(sources) == 1 else TOKENS_ESTIMATED
    vals = [float(o.injected_tokens) for o in counted]
    return TokensMetric(
        per_turn_mean=mean(vals),
        per_turn_p50=p50(vals),
        n=len(vals),
        source=source,
    )


def accuracy(judgements: Sequence[Judgement], judge: str | None = None) -> AccuracyMetric:
    """Judged accuracy. None (never 0.0) when no judge ran.

    A judgement with correct=None is counted in `n` for the category but
    excluded from the ratio, so a partial judge run is visible rather than
    silently averaged as wrong.
    """
    if not judgements:
        return AccuracyMetric(value=None, n=0, judge=judge or JUDGE_NOT_RUN)

    names = {j.judge for j in judgements if j.judge and j.judge != JUDGE_NOT_RUN}
    resolved = judge or (sorted(names)[0] if len(names) == 1 else None)
    if resolved is None:
        resolved = "+".join(sorted(names)) if names else JUDGE_NOT_RUN

    def ratio(items: Sequence[Judgement]) -> float | None:
        scored = [j for j in items if j.correct is not None]
        if not scored:
            return None
        return sum(1 for j in scored if j.correct) / len(scored)

    per_category: dict[str, tuple[float | None, int]] = {}
    for j in judgements:
        per_category.setdefault(j.category or "uncategorised", (None, 0))
    for cat in per_category:
        items = [j for j in judgements if (j.category or "uncategorised") == cat]
        per_category[cat] = (ratio(items), len(items))

    return AccuracyMetric(
        value=ratio(judgements),
        n=len(judgements),
        judge=resolved,
        per_category=per_category,
    )


# --------------------------------------------------------------------------
# assembling a scorecard
# --------------------------------------------------------------------------

def build_scorecard(
    arm: str,
    recalls: Sequence[RecallObservation],
    remembers: Sequence[RememberObservation] = (),
    judgements: Sequence[Judgement] = (),
    judge: str | None = None,
) -> Scorecard:
    """The five metrics for one arm, macro-averaged over queries."""
    return Scorecard(
        arm=arm,
        speed=speed(recalls, remembers),
        accuracy=accuracy(judgements, judge),
        tokens=tokens(recalls),
        precision=macro_average(precision(o.returned, o.gold, o.refuted) for o in recalls),
        recall=macro_average(recall(o.returned, o.gold, o.refuted) for o in recalls),
        n_queries=len(recalls),
    )


def per_store_scorecards(
    recalls: Sequence[RecallObservation],
    remembers: Sequence[RememberObservation] = (),
) -> dict[str, Scorecard]:
    """The same five columns for each store that contributed hits.

    Per CUJ §5 S8 these are COMPONENT numbers — "the pipe works" — never
    presented as benefit. render.py owns that label; this only measures.
    Accuracy is always null per store: a store does not answer questions.
    """
    names: list[str] = []
    for o in recalls:
        for name in o.by_store:
            if name not in names:
                names.append(name)

    cards: dict[str, Scorecard] = {}
    for name in names:
        sliced = [
            RecallObservation(
                query_id=o.query_id,
                returned=list(o.by_store.get(name, ())),
                gold=o.gold,
                refuted=o.refuted,
                ms=o.store_ms.get(name),
                injected_tokens=None,
                token_source=o.token_source,
            )
            for o in recalls
            if name in o.by_store
        ]
        cards[name] = build_scorecard(name, sliced, remembers=())
    return cards


# --------------------------------------------------------------------------
# corrections per session (AMD-03 §4 — the new scorecard line)
# --------------------------------------------------------------------------

@dataclass
class CorrectionsLine:
    """corrections per session, memory-on vs memory-off, per generation."""
    per_generation: list[dict[str, Any]] = field(default_factory=list)
    source: str | None = None

    @property
    def present(self) -> bool:
        return bool(self.per_generation)


def read_corrections(generations_dir: str | Path = "out/generations") -> CorrectionsLine:
    """Read corrections-per-session from out/generations/gen-*/fitness.json.

    Lane C owns the layout of fitness.json. This reads defensively: any
    generation directory without a readable fitness.json, or without a
    corrections figure in it, is skipped rather than guessed at. Absent input
    means the line renders as 'not measured', never as a number.
    """
    root = Path(generations_dir)
    line = CorrectionsLine(source=str(root))
    if not root.is_dir():
        return line

    for gen_dir in sorted(root.glob("gen-*")):
        fitness = gen_dir / "fitness.json"
        if not fitness.is_file():
            continue
        try:
            data = json.loads(fitness.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        entry: dict[str, Any] = {"generation": gen_dir.name}
        found = False
        # lane C's actual layout (2026-09-06): top-level "corrections" and fitness.corrections,
        # measured on the memory-on replay only; memory-off is not produced by evolve.
        fit = data.get("fitness") if isinstance(data.get("fitness"), dict) else {}
        for cand in (data.get("corrections"), fit.get("corrections")):
            if isinstance(cand, (int, float)) and "memory_on" not in entry:
                entry["memory_on"] = float(cand)
                found = True
        for key, out_key in (
            ("corrections_per_session", "memory_on"),
            ("corrections_per_session_memory_on", "memory_on"),
            ("corrections_per_session_memory_off", "memory_off"),
        ):
            value = data.get(key)
            if isinstance(value, (int, float)):
                entry[out_key] = float(value)
                found = True
            elif isinstance(value, dict):
                for sub, sub_key in (("memory_on", "memory_on"), ("memory_off", "memory_off")):
                    if isinstance(value.get(sub), (int, float)):
                        entry[sub_key] = float(value[sub])
                        found = True
        if found:
            line.per_generation.append(entry)
    return line


# --------------------------------------------------------------------------
# query set
# --------------------------------------------------------------------------

@dataclass
class Query:
    id: str
    text: str
    gold: list[str] = field(default_factory=list)
    gold_facts: list[str] = field(default_factory=list)
    refuted: list[str] = field(default_factory=list)
    refuted_facts: list[str] = field(default_factory=list)
    category: str = ""
    note: str = ""


@dataclass
class QuerySet:
    queries: list[Query]
    provisional: bool
    reason: str = ""
    unresolved: list[str] = field(default_factory=list)


def load_queries(
    path: str | Path = "bench/queries.json",
    gold_map: Mapping[str, Sequence[str]] | None = None,
) -> QuerySet:
    """Load bench/queries.json, resolving fact keys to claim ids if a map is given.

    Until lane B's tests/fixtures land there are no claim ids to point at, so
    each query carries `gold_facts` (stable fact keys drawn from the CUJ §3
    journey) instead. `gold_map` maps fact key -> claim ids. Any fact key with
    no mapping is listed in `unresolved`; the set stays `provisional` until
    every key resolves, and the renderer says so.
    """
    data = json.loads(Path(path).read_text())
    unresolved: list[str] = []
    queries: list[Query] = []

    for raw in data.get("queries", []):
        gold = list(raw.get("gold", []))
        facts = list(raw.get("gold_facts", []))
        refuted = list(raw.get("refuted", []))
        refuted_facts = list(raw.get("refuted_facts", []))
        if gold_map is not None:
            for keys, target in ((facts, gold), (refuted_facts, refuted)):
                for key in keys:
                    mapped = gold_map.get(key)
                    if mapped:
                        target.extend(mapped)
                    else:
                        unresolved.append(key)
        queries.append(
            Query(
                id=raw["id"],
                text=raw["text"],
                gold=list(dict.fromkeys(gold)),
                gold_facts=facts,
                refuted=list(dict.fromkeys(refuted)),
                refuted_facts=refuted_facts,
                category=raw.get("category", ""),
                note=raw.get("note", ""),
            )
        )

    declared = bool(data.get("provisional", False))
    provisional = declared or gold_map is None or bool(unresolved)
    return QuerySet(
        queries=queries,
        provisional=provisional,
        reason=data.get("provisional_reason", ""),
        unresolved=sorted(set(unresolved)),
    )
