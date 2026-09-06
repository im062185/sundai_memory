# 세대 루프가 회귀를 되돌리고 재현율을 절대 팔지 않는지 검증하는 테스트
"""Lane C step 3. No network, no live model: replay is pure store reads."""
import json
import pathlib

import pytest

from engram.adapters.base import RecallHit, StoreStats
from engram.adapters.sqlite import SQLiteStore
from engram.evolve import evolve
from engram.evolve.genome import WEIGHT_KEYS, Genome, load, save, validate
from engram.evolve.run import GenerationReport
from engram.evolve.select import is_better
from engram.evolve.signals import load_signals

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

FITNESS_KEYS = {"recall_at_k", "tokens", "corrections", "median_ms"}


class FakeStore:
    """Deterministic ranking, so selection can be asserted without FTS in the way."""

    name = "fake"

    def __init__(self, n: int = 10) -> None:
        self.claims = {
            f"c{i}": {"id": f"c{i}", "text": f"claim number {i}", "activation": 1.0,
                      "kind": "fact", "origin": "user_turn"}
            for i in range(n)
        }

    def query(self, text, k=5, *, session=None):
        return [RecallHit(claim=c, score=1.0 - i * 0.05, store=self.name)
                for i, c in enumerate(self.claims.values())][:k]

    def stats(self):
        return StoreStats(name=self.name)

    def touch(self, claim_ids):
        pass

    def by_subject(self, subject):
        return []

    def link(self, src, dst, kind, weight_delta=1.0):
        pass

    def export_markdown(self):
        return {}


def write_logs(out: pathlib.Path, injected, used, *, refuted=0, questions=0, turns=4):
    recall = [{"ts": "t", "session": "s1", "turn_index": i, "query": "claim number",
               "injected": injected, "stores": ["fake"]} for i in range(turns)]
    feedback = [{"ts": "t", "session": "s1", "turn_index": i, "kind": "feedback",
                 "injected": injected, "used": used} for i in range(turns)]
    (out / "retrieval_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recall + feedback) + "\n", encoding="utf-8")

    trace = [{"ts": "t", "op": "remember", "session": "s1", "turn_index": i, "role": "user",
              "episode": f"ep_{i}", "tagged": [], "gate": {},
              "refuted": ["c0"] if i < refuted else [],
              "question": "source?" if i < questions else None} for i in range(turns)]
    (out / "trace.jsonl").write_text(
        "\n".join(json.dumps(r) for r in trace) + "\n", encoding="utf-8")


@pytest.fixture
def out_dir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


# --- three generations end to end, on the real store ------------------------

def test_three_generations_on_the_sqlite_store(out_dir):
    store = SQLiteStore(path=str(out_dir / "engram.db"))
    claims = [c for c in json.loads((FIXTURES / "claims.json").read_text(encoding="utf-8"))
              if c.get("status") == "promoted"][:12]
    for claim in claims:
        store.write(claim)

    ids = [c["id"] for c in claims]
    recall = [{"ts": "t", "session": "s1", "turn_index": i, "query": c["text"],
               "injected": ids[:4], "stores": ["sqlite"]} for i, c in enumerate(claims[:5])]
    feedback = [{"ts": "t", "session": "s1", "turn_index": i, "kind": "feedback",
                 "injected": ids[:4], "used": ids[:2]} for i in range(5)]
    (out_dir / "retrieval_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recall + feedback) + "\n", encoding="utf-8")

    gens = out_dir / "generations"
    reports = [evolve(store, None, gens) for _ in range(3)]

    assert [r.generation for r in reports] == [1, 2, 3]
    for n in (1, 2, 3):
        d = gens / f"gen-{n:03d}"
        assert d.is_dir(), f"gen-{n:03d} missing"
        for name in ("weights.json", "encoder.lessons.md", "persona.md", "tiers.json",
                     "fitness.json", "DIFF.md"):
            assert (d / name).exists(), f"gen-{n:03d}/{name} missing"

    fitness = json.loads((gens / "gen-001" / "fitness.json").read_text(encoding="utf-8"))
    assert FITNESS_KEYS <= set(fitness)
    for key in FITNESS_KEYS:
        assert isinstance(fitness[key], (int, float))
    assert isinstance(reports[0], GenerationReport)


def test_gen_000_is_seeded_from_defaults(out_dir):
    gens = out_dir / "generations"
    write_logs(out_dir, [f"c{i}" for i in range(4)], ["c0"])
    evolve(FakeStore(), None, gens)
    assert (gens / "gen-000" / "weights.json").exists()
    assert json.loads((gens / "gen-000" / "weights.json").read_text())["k"] == 5


# --- the two hard rules -----------------------------------------------------

def test_a_candidate_that_lowers_recall_is_never_selected():
    parent = {"recall_at_k": 0.6, "tokens": 100.0, "corrections": 4.0, "median_ms": 5.0}
    # better on every other axis, worse on recall
    worse = {"recall_at_k": 0.5, "tokens": 1.0, "corrections": 0.0, "median_ms": 0.1}
    assert is_better(worse, parent) is False

    better = {"recall_at_k": 0.7, "tokens": 999.0, "corrections": 99.0, "median_ms": 99.0}
    assert is_better(better, parent) is True


def test_parent_is_kept_when_nothing_improves(out_dir, monkeypatch):
    """Every candidate replays identically to the parent, so no generation may claim a win."""
    import engram.evolve.run as run_mod

    flat = {"recall_at_k": 0.5, "tokens": 50.0, "corrections": 2.0, "median_ms": 1.0}
    monkeypatch.setattr(run_mod, "replay", lambda store, genome, signals: dict(flat))

    write_logs(out_dir, [f"c{i}" for i in range(4)], ["c0"])
    gens = out_dir / "generations"

    report = evolve(FakeStore(), None, gens)
    assert report.selected.startswith("parent")

    parent_weights = json.loads((gens / "gen-000" / "weights.json").read_text())
    child_weights = json.loads((gens / "gen-001" / "weights.json").read_text())
    assert child_weights == parent_weights


# --- regressions for two selection bugs found by running the loop -----------

def test_tokens_never_win_while_recall_is_zero():
    """Otherwise 'inject nothing' minimises tokens and ratchets k down forever."""
    parent = {"recall_at_k": 0.0, "tokens": 100.0, "corrections": 2.0, "median_ms": 1.0}
    starved = {"recall_at_k": 0.0, "tokens": 1.0, "corrections": 2.0, "median_ms": 1.0}
    assert is_better(starved, parent) is False

    # once retrieval works, the same trade is a genuine win
    working = {"recall_at_k": 0.5, "tokens": 100.0, "corrections": 2.0, "median_ms": 1.0}
    cheaper = {"recall_at_k": 0.5, "tokens": 40.0, "corrections": 2.0, "median_ms": 1.0}
    assert is_better(cheaper, working) is True


def test_latency_jitter_never_decides_a_generation():
    """median_ms is reported but not selectable; ~0.1 ms replays are pure noise."""
    parent = {"recall_at_k": 0.5, "tokens": 50.0, "corrections": 2.0, "median_ms": 0.120}
    faster = {"recall_at_k": 0.5, "tokens": 50.0, "corrections": 2.0, "median_ms": 0.001}
    assert is_better(faster, parent) is False


def test_k_does_not_ratchet_down_across_generations(out_dir):
    """End-to-end version of the ratchet bug: gold unreachable, so recall stays 0."""
    write_logs(out_dir, [f"c{i}" for i in range(10)], ["c8", "c9"])
    gens = out_dir / "generations"
    store = FakeStore()
    for _ in range(3):
        evolve(store, None, gens)

    ks = [json.loads((gens / f"gen-{n:03d}" / "weights.json").read_text())["k"] for n in range(4)]
    assert ks == [5, 5, 5, 5], f"k drifted with zero recall: {ks}"


# --- the genome is not the gate ---------------------------------------------

def test_weights_never_contain_gate_or_safety_rules(out_dir):
    write_logs(out_dir, [f"c{i}" for i in range(4)], ["c0"])
    gens = out_dir / "generations"
    for _ in range(3):
        evolve(FakeStore(), None, gens)

    banned = {"gate", "rules", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
              "safety", "gate_rules"}
    for n in range(4):
        weights = json.loads((gens / f"gen-{n:03d}" / "weights.json").read_text())
        assert set(weights) <= set(WEIGHT_KEYS), f"gen-{n:03d} weights grew a key"
        assert not (set(weights) & banned)
        blob = json.dumps(weights).lower()
        for token in ("g1", "g9", "gate", "verdict", "promote("):
            assert token not in blob


def test_validate_rejects_a_smuggled_gate_rule():
    g = Genome()
    g.weights["gate_rules"] = {"G3": "off"}
    with pytest.raises(ValueError, match="not the gate"):
        validate(g)


def test_tiers_are_seeded_from_the_router_policy(out_dir):
    policy = json.loads((ROOT / "engram" / "p3" / "policy.json").read_text(encoding="utf-8"))
    assert Genome().tiers == policy["kind_to_tier"], "genome and router must not drift"


# --- signals ----------------------------------------------------------------

def test_signals_read_both_logs(out_dir):
    write_logs(out_dir, ["c0", "c1", "c2"], ["c0"], refuted=2, questions=1)
    sig = load_signals(out_dir)
    assert len(sig.queries) == 4
    assert sig.used == {"c0"}
    assert sig.ignored == {"c1", "c2"}
    assert sig.refuted == 2
    assert sig.questions == 1
    assert sig.corrections == 3
    assert sig.sessions == {"s1"}


def test_signals_survive_a_truncated_log_line(out_dir):
    (out_dir / "retrieval_log.jsonl").write_text(
        json.dumps({"ts": "t", "session": "s1", "turn_index": 0, "query": "q",
                    "injected": ["c0"], "stores": ["fake"]}) + "\n{\"half\": \n",
        encoding="utf-8")
    sig = load_signals(out_dir)
    assert len(sig.queries) == 1


def test_missing_logs_are_not_an_error(out_dir):
    sig = load_signals(out_dir)
    assert sig.queries == [] and sig.corrections == 0


def test_genome_round_trips(tmp_path):
    g = Genome()
    g.weights["k"] = 9
    save(g, tmp_path / "gen-007")
    back = load(tmp_path / "gen-007")
    assert back.weights["k"] == 9
    assert back.tiers == g.tiers
