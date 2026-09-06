"""Text rendering for the performance report. Pure formatting, no measurement.

Order is fixed by CUJ §5 S10 / S8 and the lane D brief:

  1. five-metric scorecard, memory-on   (speed · accuracy · tokens · precision · recall)
  2. the same five, memory-off
  3. per-store table, same five columns
  4. three labelled tiers: Outcome / Component / Provenance
  5. LongMemEval per-category row, with n and the judge named or "judge: not run"
  6. final line: corrections per session, per generation

Rules this module enforces:
  - a missing number renders as "—", never as 0.00
  - accuracy always carries the judge's name or "judge: not run"
  - component numbers are labelled "the pipe works" and never called benefit
  - an estimated token count renders "(est)"; a provisional gold set renders
    "[provisional]" — the builder can always see what a number is worth
"""
from __future__ import annotations

from typing import Sequence

from bench.metrics import (
    JUDGE_NOT_RUN,
    SCORECARD_ORDER,
    AccuracyMetric,
    CorrectionsLine,
    Scorecard,
    SpeedMetric,
    TokensMetric,
)

MISSING = "—"
COMPONENT_LABEL = "the pipe works"
COMPONENT_CAVEAT = "these numbers say the plumbing is sound; they are not a claim of benefit"


# --------------------------------------------------------------------------
# scalar formatting
# --------------------------------------------------------------------------

def fmt_ratio(value: float | None) -> str:
    return MISSING if value is None else f"{value:.2f}"


def fmt_ms(value: float | None) -> str:
    return MISSING if value is None else f"{value:.0f}ms"


def fmt_speed(speed: SpeedMetric) -> str:
    """ms per recall and ms per remember — both, because they are different ops.

    p50 is the headline (one cold query should not move the number a builder
    reads); the mean is available in the JSON.
    """
    parts = []
    if speed.recall_p50_ms is not None:
        parts.append(f"{fmt_ms(speed.recall_p50_ms)} recall")
    if speed.remember_p50_ms is not None:
        parts.append(f"{fmt_ms(speed.remember_p50_ms)} remember")
    # A store row has no remember half — omit it rather than print a dash.
    return " / ".join(parts) if parts else MISSING


def fmt_accuracy(accuracy: AccuracyMetric) -> str:
    """Never a bare number. The judge is part of the value."""
    value = MISSING if accuracy.value is None else f"{accuracy.value:.2f}"
    judge = accuracy.judge or JUDGE_NOT_RUN
    n = f", n={accuracy.n}" if accuracy.n else ""
    return f"{value} (judge: {judge}{n})"


def fmt_tokens(tokens: TokensMetric) -> str:
    if tokens.per_turn_mean is None:
        return MISSING
    suffix = " (est)" if tokens.estimated else ""
    return f"{tokens.per_turn_mean:.0f}/turn{suffix}"


# --------------------------------------------------------------------------
# 1 + 2. the scorecard lines
# --------------------------------------------------------------------------

def render_scorecard_line(card: Scorecard, provisional: bool = False) -> str:
    """`<arm> | speed … · accuracy … · tokens … · precision … · recall …`

    The five fields appear in SCORECARD_ORDER, separated by " · ", each
    prefixed by its metric name. tests/test_metrics.py asserts that shape.
    """
    fields = {
        "speed": fmt_speed(card.speed),
        "accuracy": fmt_accuracy(card.accuracy),
        "tokens": fmt_tokens(card.tokens),
        "precision": fmt_ratio(card.precision),
        "recall": fmt_ratio(card.recall),
    }
    body = " · ".join(f"{name} {fields[name]}" for name in SCORECARD_ORDER)
    line = f"{card.arm} | {body}"
    if provisional:
        line += "  [provisional]"
    return line


# --------------------------------------------------------------------------
# 3. the per-store table
# --------------------------------------------------------------------------

def render_store_table(cards: dict[str, Scorecard], provisional: bool = False) -> list[str]:
    """Same five columns, one row per store. Component tier — not benefit."""
    header = [f"Per-store ({COMPONENT_LABEL}) — same five columns" + ("  [provisional]" if provisional else "")]
    if not cards:
        return header + ["  (no store results — run bench/component or merge lane B's stores)"]

    columns = ["store", *SCORECARD_ORDER]
    rows: list[list[str]] = []
    for name, card in cards.items():
        rows.append([
            name,
            fmt_speed(card.speed),
            fmt_accuracy(card.accuracy),
            fmt_tokens(card.tokens),
            fmt_ratio(card.precision),
            fmt_ratio(card.recall),
        ])

    widths = [max(len(columns[i]), *(len(r[i]) for r in rows)) for i in range(len(columns))]
    out = list(header)
    out.append("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)).rstrip())
    out.append("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    return out


# --------------------------------------------------------------------------
# 5. the LongMemEval per-category row
# --------------------------------------------------------------------------

def render_longmemeval(row) -> list[str]:
    """Per-category accuracy, n per category, and the judge named — always."""
    judge = row.judge or JUDGE_NOT_RUN
    overall = MISSING if row.accuracy is None else f"{row.accuracy:.2f}"
    head = f"LongMemEval slice — accuracy {overall} · n={row.n} · judge: {judge}"
    lines = [head]
    if row.note:
        lines.append(f"  {row.note}")
    if not row.per_category:
        lines.append("  (no per-category result — slice not run)")
        return lines

    cells = []
    for category in row.category_order or sorted(row.per_category):
        acc, n = row.per_category.get(category, (None, 0))
        acc_text = MISSING if acc is None else f"{acc:.2f}"
        cells.append(f"{category} {acc_text} (n={n})")
    lines.append("  " + " · ".join(cells))
    missing = [c for c in (row.categories_expected or []) if c not in row.per_category]
    if missing:
        lines.append(f"  categories not covered: {', '.join(missing)}")
    return lines


# --------------------------------------------------------------------------
# 6. corrections per session, per generation
# --------------------------------------------------------------------------

def render_corrections(line: CorrectionsLine) -> str:
    """AMD-03 §4 — the number no existing system reports."""
    if not line.present:
        where = line.source or "out/generations"
        return f"corrections per session, per generation: not measured (no fitness.json under {where})"
    parts = []
    for entry in line.per_generation:
        on = entry.get("memory_on")
        off = entry.get("memory_off")
        on_text = MISSING if on is None else f"{on:.2f}"
        off_text = MISSING if off is None else f"{off:.2f}"
        parts.append(f"{entry['generation']} on {on_text} / off {off_text}")
    return "corrections per session, per generation: " + "; ".join(parts)


# --------------------------------------------------------------------------
# whole report
# --------------------------------------------------------------------------

def render_report(report) -> str:
    """The full report text in the fixed order. `report` is a scorecard.Report."""
    lines: list[str] = []

    # 1 + 2 — the scorecard, memory-on then memory-off
    for arm in (report.memory_on, report.memory_off):
        if arm is None:
            continue
        lines.append(render_scorecard_line(arm, provisional=report.provisional))
    if report.memory_on is None and report.memory_off is None:
        lines.append(f"memory-on | no run recorded — {MISSING}")

    # 3 — the per-store table
    lines.append("")
    lines.extend(render_store_table(report.stores, provisional=report.provisional))

    # 4 — the three labelled tiers
    lines.append("")
    lines.append("Outcome — does memory make the agent better at the work")
    for note in report.outcome_lines():
        lines.append(f"  {note}")

    lines.append("")
    lines.append(f"Component — {COMPONENT_LABEL}: {COMPONENT_CAVEAT}")
    for note in report.component_lines():
        lines.append(f"  {note}")

    lines.append("")
    lines.append("Provenance — what is remembered and how it was come by")
    for note in report.provenance_lines():
        lines.append(f"  {note}")

    # 5 — the LongMemEval per-category row
    lines.append("")
    lines.extend(render_longmemeval(report.longmemeval))

    # 6 — corrections per session, per generation
    lines.append("")
    lines.append(render_corrections(report.corrections))

    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append(f"note: {note}")

    return "\n".join(lines)


def render_lines(report) -> Sequence[str]:
    return render_report(report).split("\n")
