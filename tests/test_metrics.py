"""Lane D — the five metrics (TDD §7: `test_metrics.py`).

Asserts, at minimum, the two things the TDD names:
  1. a returned refuted id lowers precision by exactly 1/k
  2. the scorecard order
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.metrics import (
    JUDGE_NOT_RUN,
    SCORECARD_ORDER,
    TOKENS_ESTIMATED,
    TOKENS_MEASURED,
    Judgement,
    RecallObservation,
    RememberObservation,
    accuracy,
    build_scorecard,
    load_queries,
    macro_average,
    p50,
    per_store_scorecards,
    precision,
    read_corrections,
    recall,
    speed,
    tokens,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1. the 1/k assertion (TDD §7)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("k", [2, 3, 4, 5, 8])
def test_refuted_id_lowers_precision_by_exactly_one_over_k(k):
    """Swap one correct id in a k-length result for a refuted id: precision
    must fall by exactly 1/k. This is the A-3 penalty."""
    gold = [f"clm_gold{i:04d}" for i in range(k)]
    clean = list(gold)

    refuted_id = "clm_deadbeef01"
    dirty = list(gold[:-1]) + [refuted_id]

    p_clean = precision(clean, gold=gold, refuted=[refuted_id])
    p_dirty = precision(dirty, gold=gold, refuted=[refuted_id])

    assert p_clean == pytest.approx(1.0)
    assert p_dirty == pytest.approx(p_clean - 1 / k)


def test_refuted_id_is_a_false_positive_even_when_it_is_in_gold():
    """A-3 exactly: the vector arm returns a claim that WAS true and is now
    refuted. Membership in gold must not rescue it."""
    gold = ["clm_aaaaaaaa", "clm_bbbbbbbb", "clm_cccccccc"]
    refuted = ["clm_cccccccc"]

    returned = ["clm_aaaaaaaa", "clm_bbbbbbbb", "clm_cccccccc"]
    k = len(returned)

    assert precision(returned, gold, refuted) == pytest.approx(2 / k)
    # and it is exactly 1/k below the same result with the refuted slot correct
    assert precision(returned, gold, refuted) == pytest.approx(
        precision(["clm_aaaaaaaa", "clm_bbbbbbbb", "clm_dddddddd"], gold + ["clm_dddddddd"], refuted) - 1 / k
    )


def test_recall_denominator_drops_refuted_gold():
    gold = ["clm_aaaaaaaa", "clm_bbbbbbbb", "clm_cccccccc"]
    refuted = ["clm_cccccccc"]
    # found both live gold claims -> perfect recall, despite missing the dead one
    assert recall(["clm_aaaaaaaa", "clm_bbbbbbbb"], gold, refuted) == pytest.approx(1.0)


def test_precision_and_recall_are_none_not_zero_when_undefined():
    """Undefined is not failure. An abstention query with no gold must not
    silently score 0.0 recall and drag the macro average down."""
    assert precision([], gold=["clm_aaaaaaaa"]) is None
    assert recall(["clm_aaaaaaaa"], gold=[]) is None
    assert recall(["clm_aaaaaaaa"], gold=["clm_bbbbbbbb"], refuted=["clm_bbbbbbbb"]) is None


def test_macro_average_skips_undefined_queries():
    assert macro_average([1.0, None, 0.5]) == pytest.approx(0.75)
    assert macro_average([None, None]) is None


def test_duplicate_returned_ids_do_not_inflate_hits():
    gold = ["clm_aaaaaaaa"]
    returned = ["clm_aaaaaaaa", "clm_aaaaaaaa", "clm_bbbbbbbb"]
    assert precision(returned, gold) == pytest.approx(1 / 3)
    assert recall(returned, gold) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 2. the scorecard order (CUJ S10)
# --------------------------------------------------------------------------

def test_scorecard_order_is_speed_accuracy_tokens_precision_recall():
    assert SCORECARD_ORDER == ("speed", "accuracy", "tokens", "precision", "recall")


def test_scorecard_as_ordered_follows_scorecard_order():
    card = build_scorecard("memory-on", [RecallObservation("q1", ["clm_aaaaaaaa"], ["clm_aaaaaaaa"], ms=10.0)])
    assert [name for name, _ in card.as_ordered()] == list(SCORECARD_ORDER)


def test_rendered_scorecard_line_keeps_the_order():
    """The renderer is the thing the builder sees; S10 is about that line."""
    render = pytest.importorskip("engram.report.render", reason="engram/report/ lands in lane D step 2")
    render_scorecard_line = render.render_scorecard_line

    card = build_scorecard(
        "memory-on",
        [RecallObservation("q1", ["clm_aaaaaaaa"], ["clm_aaaaaaaa"], ms=41.0, injected_tokens=312)],
        remembers=[RememberObservation("t1", ms=57.0)],
    )
    line = render_scorecard_line(card)
    positions = [line.index(name) for name in SCORECARD_ORDER]
    assert positions == sorted(positions), line
    assert line.count("·") == 4, line


# --------------------------------------------------------------------------
# speed / tokens / accuracy
# --------------------------------------------------------------------------

def test_speed_reports_recall_and_remember_separately():
    s = speed(
        [RecallObservation("q1", [], ms=10.0), RecallObservation("q2", [], ms=30.0)],
        [RememberObservation("t1", ms=50.0), RememberObservation("t2", ms=70.0), RememberObservation("t3", ms=90.0)],
    )
    assert s.recall_p50_ms == pytest.approx(20.0)
    assert s.recall_mean_ms == pytest.approx(20.0)
    assert s.remember_p50_ms == pytest.approx(70.0)
    assert s.n_recall == 2 and s.n_remember == 3


def test_p50_handles_even_and_odd_lengths():
    assert p50([3.0, 1.0, 2.0]) == pytest.approx(2.0)
    assert p50([4.0, 1.0, 2.0, 3.0]) == pytest.approx(2.5)
    assert p50([]) is None


def test_tokens_marks_the_arm_estimated_if_any_count_was_estimated():
    measured = RecallObservation("q1", [], injected_tokens=100, token_source=TOKENS_MEASURED)
    estimated = RecallObservation("q2", [], injected_tokens=200, token_source=TOKENS_ESTIMATED)
    assert tokens([measured]).estimated is False
    assert tokens([measured, estimated]).estimated is True
    assert tokens([measured, estimated]).per_turn_mean == pytest.approx(150.0)
    assert tokens([]).per_turn_mean is None


def test_accuracy_is_null_and_judge_named_not_run_when_no_judge():
    a = accuracy([])
    assert a.value is None
    assert a.judge == JUDGE_NOT_RUN

    unjudged = [Judgement("q1", correct=None, category="multi-session")]
    a = accuracy(unjudged)
    assert a.value is None
    assert a.judge == JUDGE_NOT_RUN
    assert a.per_category["multi-session"] == (None, 1)


def test_accuracy_names_the_judge_and_reports_per_category_n():
    js = [
        Judgement("q1", True, "knowledge-update", judge="claude-sonnet-5"),
        Judgement("q2", False, "knowledge-update", judge="claude-sonnet-5"),
        Judgement("q3", True, "temporal", judge="claude-sonnet-5"),
    ]
    a = accuracy(js)
    assert a.value == pytest.approx(2 / 3)
    assert a.judge == "claude-sonnet-5"
    assert a.per_category["knowledge-update"] == (pytest.approx(0.5), 2)
    assert a.per_category["temporal"] == (pytest.approx(1.0), 1)
    assert a.n == 3


# --------------------------------------------------------------------------
# per-store table
# --------------------------------------------------------------------------

def test_per_store_table_shows_the_vector_arm_losing_precision():
    """The component table has to be able to show A-3: same query, one arm
    returns the refuted claim and only that arm's precision drops."""
    obs = RecallObservation(
        query_id="q07",
        returned=["clm_aaaaaaaa", "clm_deadbeef"],
        gold=["clm_aaaaaaaa", "clm_deadbeef"],
        refuted=["clm_deadbeef"],
        by_store={"sqlite": ["clm_aaaaaaaa"], "vector": ["clm_aaaaaaaa", "clm_deadbeef"]},
        store_ms={"sqlite": 4.0, "vector": 9.0},
    )
    cards = per_store_scorecards([obs])
    assert cards["sqlite"].precision == pytest.approx(1.0)
    assert cards["vector"].precision == pytest.approx(0.5)
    # a store does not answer questions, so it never carries an accuracy number
    assert cards["vector"].accuracy.value is None
    assert cards["vector"].accuracy.judge == JUDGE_NOT_RUN


# --------------------------------------------------------------------------
# corrections per session, per generation (AMD-03 §4)
# --------------------------------------------------------------------------

def test_read_corrections_returns_empty_when_no_generations(tmp_path):
    line = read_corrections(tmp_path / "generations")
    assert line.present is False
    assert line.per_generation == []


def test_read_corrections_reads_gen_dirs_in_order_and_skips_unreadable(tmp_path):
    root = tmp_path / "generations"
    (root / "gen-001").mkdir(parents=True)
    (root / "gen-002").mkdir(parents=True)
    (root / "gen-003").mkdir(parents=True)
    (root / "gen-001" / "fitness.json").write_text(
        json.dumps({"corrections_per_session": {"memory_on": 0.5, "memory_off": 2.0}})
    )
    (root / "gen-002" / "fitness.json").write_text("{ not json")
    (root / "gen-003" / "fitness.json").write_text(json.dumps({"corrections_per_session_memory_on": 0.25}))

    line = read_corrections(root)
    assert [e["generation"] for e in line.per_generation] == ["gen-001", "gen-003"]
    assert line.per_generation[0]["memory_off"] == pytest.approx(2.0)
    assert line.per_generation[1]["memory_on"] == pytest.approx(0.25)


# --------------------------------------------------------------------------
# the query set
# --------------------------------------------------------------------------

def test_queries_json_has_25_multi_gold_queries_and_is_marked_provisional():
    qs = load_queries(REPO / "bench" / "queries.json")
    assert len(qs.queries) == 25
    assert qs.provisional is True
    assert qs.reason

    ids = [q.id for q in qs.queries]
    assert len(set(ids)) == 25

    # "multi-gold" per TDD step 10: most queries need more than one gold fact.
    multi = [q for q in qs.queries if len(q.gold_facts) >= 2]
    assert len(multi) >= 20, f"only {len(multi)} multi-gold queries"

    # every fact key used is declared in the file's fact_keys block
    declared = set(json.loads((REPO / "bench" / "queries.json").read_text())["fact_keys"])
    for q in qs.queries:
        for key in list(q.gold_facts) + list(q.refuted_facts):
            assert key in declared, f"{q.id} uses undeclared fact key {key}"


def test_queries_cover_the_refutation_and_abstention_beats():
    qs = load_queries(REPO / "bench" / "queries.json")
    assert any(q.refuted_facts for q in qs.queries), "no query exercises A-3"
    assert any(not q.gold_facts for q in qs.queries), "no abstention query"
    categories = {q.category for q in qs.queries}
    assert {"preference", "knowledge-update", "abstention", "provenance"} <= categories


def test_query_set_stays_provisional_until_every_fact_key_resolves():
    qs = load_queries(REPO / "bench" / "queries.json", gold_map={"fact.project": ["clm_aaaaaaaa"]})
    assert qs.provisional is True
    assert "fact.model" in qs.unresolved
    resolved = next(q for q in qs.queries if "fact.project" in q.gold_facts)
    assert "clm_aaaaaaaa" in resolved.gold
