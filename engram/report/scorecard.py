"""Assembles the performance report from whatever evidence exists on disk.

Degrades honestly: with no `out/results.json` and no `out/generations/`, the
report still renders — every number as "—" and every judge as "not run".
The demo must never crash because a bench has not been run yet, and it must
never fill a gap with a plausible number.

Input contract: `out/results.json`, per TDD §5.5 — `scorecard` block first,
then `component`, `outcome`, `longmemeval`, `provenance`. bench/ writes it;
this reads it. engram/server.py's `report` op calls `report_text()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from bench.metrics import (
    JUDGE_NOT_RUN,
    TOKENS_ESTIMATED,
    TOKENS_MEASURED,
    AccuracyMetric,
    CorrectionsLine,
    Scorecard,
    SpeedMetric,
    TokensMetric,
    read_corrections,
)
from engram.report.render import MISSING, render_report

DEFAULT_RESULTS = "out/results.json"
DEFAULT_GENERATIONS = "out/generations"

# The six LongMemEval categories are NOT hard-coded here — bench/longmemeval.py
# reads them off the release and records them in bench/LONGMEMEVAL.md and in
# the slice file. This is only the display order when the slice supplies one.
PROVENANCE_CLASSES = ("said", "inferred", "verified", "refuted")


@dataclass
class LongMemEvalRow:
    n: int = 0
    accuracy: float | None = None
    judge: str = JUDGE_NOT_RUN
    per_category: dict[str, tuple[float | None, int]] = field(default_factory=dict)
    category_order: list[str] = field(default_factory=list)
    categories_expected: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ProvenanceCensus:
    counts: dict[str, int] = field(default_factory=dict)
    since: str | None = None

    @property
    def present(self) -> bool:
        return bool(self.counts)


@dataclass
class Report:
    memory_on: Scorecard | None = None
    memory_off: Scorecard | None = None
    stores: dict[str, Scorecard] = field(default_factory=dict)
    longmemeval: LongMemEvalRow = field(default_factory=LongMemEvalRow)
    provenance: ProvenanceCensus = field(default_factory=ProvenanceCensus)
    corrections: CorrectionsLine = field(default_factory=CorrectionsLine)
    pass_k: dict[str, Any] = field(default_factory=dict)
    provisional: bool = False
    notes: list[str] = field(default_factory=list)

    # -- tier bodies -------------------------------------------------------

    def outcome_lines(self) -> list[str]:
        """Outcome tier: does memory make the agent better at the work."""
        out: list[str] = []
        judge = self.longmemeval.judge or JUDGE_NOT_RUN
        acc = MISSING if self.longmemeval.accuracy is None else f"{self.longmemeval.accuracy:.2f}"
        out.append(
            f"LongMemEval slice: accuracy {acc}, n={self.longmemeval.n}, judge: {judge} "
            f"(per-category row below)"
        )
        if self.memory_on is not None and self.memory_off is not None:
            on, off = self.memory_on.accuracy.value, self.memory_off.accuracy.value
            if on is not None and off is not None:
                out.append(f"memory-on {on:.2f} vs memory-off {off:.2f} (judge: {self.memory_on.accuracy.judge})")
            else:
                out.append(
                    f"memory-on vs memory-off accuracy: {MISSING} "
                    f"(judge: {self.memory_on.accuracy.judge or JUDGE_NOT_RUN})"
                )
        status = self.pass_k.get("status", "not run")
        reason = self.pass_k.get("reason", "step 13 cut before the build started (AMD-03 §6)")
        out.append(f"pass^k on the project scenarios: {status} — {reason}")
        return out

    def component_lines(self) -> list[str]:
        if not self.stores:
            return ["no store results recorded"]
        out = [f"{len(self.stores)} store(s) measured: {', '.join(self.stores)} — see the table above"]
        losers = [
            name for name, card in self.stores.items()
            if card.precision is not None and card.precision < 1.0
        ]
        if losers:
            out.append(
                f"precision below 1.00 in: {', '.join(losers)} — a store returning a refuted claim "
                f"loses exactly 1/k per query it does it on (A-3)"
            )
        return out

    def provenance_lines(self) -> list[str]:
        if not self.provenance.present:
            return ["no census recorded (run `engram consolidate`, then report again)"]
        since = f" since {self.provenance.since}" if self.provenance.since else ""
        counts = " · ".join(
            f"{name} {self.provenance.counts.get(name, 0)}" for name in PROVENANCE_CLASSES
        )
        extra = {k: v for k, v in self.provenance.counts.items() if k not in PROVENANCE_CLASSES}
        line = f"{counts}{since}"
        if extra:
            line += " · " + " · ".join(f"{k} {v}" for k, v in sorted(extra.items()))
        return [line]

    def text(self) -> str:
        return render_report(self)


# --------------------------------------------------------------------------
# (de)serialising a Scorecard
# --------------------------------------------------------------------------

def scorecard_to_dict(card: Scorecard) -> dict[str, Any]:
    return {
        "arm": card.arm,
        "n_queries": card.n_queries,
        # order matters in the file too — readers should not have to know S10
        "speed": {
            "recall_p50_ms": card.speed.recall_p50_ms,
            "recall_mean_ms": card.speed.recall_mean_ms,
            "remember_p50_ms": card.speed.remember_p50_ms,
            "remember_mean_ms": card.speed.remember_mean_ms,
            "n_recall": card.speed.n_recall,
            "n_remember": card.speed.n_remember,
        },
        "accuracy": {
            "value": card.accuracy.value,
            "n": card.accuracy.n,
            "judge": card.accuracy.judge,
            "per_category": {k: {"accuracy": v[0], "n": v[1]} for k, v in card.accuracy.per_category.items()},
        },
        "tokens": {
            "per_turn_mean": card.tokens.per_turn_mean,
            "per_turn_p50": card.tokens.per_turn_p50,
            "n": card.tokens.n,
            "source": card.tokens.source,
        },
        "precision": card.precision,
        "recall": card.recall,
    }


def scorecard_from_dict(data: Mapping[str, Any]) -> Scorecard:
    speed = data.get("speed") or {}
    acc = data.get("accuracy") or {}
    tok = data.get("tokens") or {}
    per_category = {
        k: (v.get("accuracy"), int(v.get("n", 0)))
        for k, v in (acc.get("per_category") or {}).items()
    }
    return Scorecard(
        arm=data.get("arm", "?"),
        speed=SpeedMetric(
            recall_p50_ms=speed.get("recall_p50_ms"),
            recall_mean_ms=speed.get("recall_mean_ms"),
            remember_p50_ms=speed.get("remember_p50_ms"),
            remember_mean_ms=speed.get("remember_mean_ms"),
            n_recall=int(speed.get("n_recall", 0)),
            n_remember=int(speed.get("n_remember", 0)),
        ),
        accuracy=AccuracyMetric(
            value=acc.get("value"),
            n=int(acc.get("n", 0)),
            judge=acc.get("judge") or JUDGE_NOT_RUN,
            per_category=per_category,
        ),
        tokens=TokensMetric(
            per_turn_mean=tok.get("per_turn_mean"),
            per_turn_p50=tok.get("per_turn_p50"),
            n=int(tok.get("n", 0)),
            source=tok.get("source") or TOKENS_MEASURED,
        ),
        precision=data.get("precision"),
        recall=data.get("recall"),
        n_queries=int(data.get("n_queries", 0)),
    )


def empty_scorecard(arm: str) -> Scorecard:
    return Scorecard(
        arm=arm,
        speed=SpeedMetric(),
        accuracy=AccuracyMetric(),
        tokens=TokensMetric(),
        precision=None,
        recall=None,
    )


# --------------------------------------------------------------------------
# building the report
# --------------------------------------------------------------------------

def build_report(
    results: Mapping[str, Any] | None = None,
    generations_dir: str | Path = DEFAULT_GENERATIONS,
) -> Report:
    """Assemble a Report from a results mapping (may be None or partial)."""
    results = results or {}
    report = Report()

    scorecards = results.get("scorecard") or {}
    if scorecards.get("memory_on"):
        report.memory_on = scorecard_from_dict(scorecards["memory_on"])
    if scorecards.get("memory_off"):
        report.memory_off = scorecard_from_dict(scorecards["memory_off"])

    component = results.get("component") or {}
    for name, card in (component.get("stores") or {}).items():
        report.stores[name] = scorecard_from_dict(card)

    lme = results.get("longmemeval") or {}
    per_category = {
        k: (v.get("accuracy"), int(v.get("n", 0)))
        for k, v in (lme.get("per_category") or {}).items()
    }
    report.longmemeval = LongMemEvalRow(
        n=int(lme.get("n", 0)),
        accuracy=lme.get("accuracy"),
        judge=lme.get("judge") or JUDGE_NOT_RUN,
        per_category=per_category,
        category_order=list(lme.get("category_order") or []),
        categories_expected=list(lme.get("categories_expected") or []),
        note=lme.get("note", ""),
    )

    prov = results.get("provenance") or {}
    counts = {k: int(v) for k, v in prov.items() if isinstance(v, int)}
    report.provenance = ProvenanceCensus(counts=counts, since=prov.get("since"))

    report.pass_k = dict(results.get("outcome", {}).get("pass_k", {}))
    report.corrections = read_corrections(generations_dir)
    report.provisional = bool(results.get("provisional", False))
    report.notes = list(results.get("notes", []))
    return report


def load_results(path: str | Path = DEFAULT_RESULTS) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_results(data: Mapping[str, Any], path: str | Path = DEFAULT_RESULTS) -> Path:
    """Write out/results.json with TDD §5.5's block order preserved."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    order = ["generated_at", "provisional", "scorecard", "component", "outcome", "longmemeval", "provenance", "notes"]
    ordered = {k: data[k] for k in order if k in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    p.write_text(json.dumps(ordered, indent=2) + "\n")
    return p


def report_text(
    results_path: str | Path = DEFAULT_RESULTS,
    generations_dir: str | Path = DEFAULT_GENERATIONS,
) -> str:
    """What `engram report` / the `report` op prints. Never raises on missing data."""
    results = load_results(results_path)
    report = build_report(results, generations_dir)
    if results is None:
        report.notes.append(
            f"no bench results at {results_path} — run `python -m bench.component` "
            f"or `python -m bench.longmemeval` first; every number above is unmeasured, not zero"
        )
    return report.text()
