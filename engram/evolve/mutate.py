# 부모 게놈에서 한 군데만 바꾼 후보 셋을 만드는 변이 모듈
"""Three candidates, exactly one change each.

One change per candidate is the whole discipline: when a generation regresses you
know precisely what to revert, and the DIFF.md is one line. A candidate that
changes two things teaches nothing.

    1. a numeric weight, +/- 20 %
    2. one lesson line appended, derived from the session's signals
    3. one tier move, derived from which kinds earned their always-on slot

Gate rules and safety rules are not reachable from here: `mutate` only ever
touches `weights`, `lessons` and `tiers`, and `genome.validate()` rejects any key
outside `WEIGHT_KEYS`.
"""
from __future__ import annotations

from dataclasses import dataclass

from engram.evolve import lessons as lessons_mod
from engram.evolve.genome import NUMERIC_KEYS, Genome, validate

INT_KEYS = {"k", "recall_token_budget"}
# Blend is a ratio; nudging it out of [0, 1] would be meaningless, not bold.
BOUNDS = {"fts_activation_blend": (0.05, 0.95), "promotion_threshold": (0.05, 0.95),
          "dormancy_threshold": (0.01, 0.9), "decay_lambda": (0.001, 1.0),
          "k": (1, 50), "recall_token_budget": (50, 4000)}


@dataclass
class Candidate:
    genome: Genome
    change: str


def _nudge(parent: Genome, generation: int, shrink: bool) -> Candidate:
    key = NUMERIC_KEYS[generation % len(NUMERIC_KEYS)]
    before = parent.weights.get(key, 0)
    factor = 0.8 if shrink else 1.2
    after = before * factor
    if key in INT_KEYS:
        after = max(1, int(round(after)))
        if after == before:                      # 20 % of a small int can round to nothing
            after = before + (-1 if shrink else 1)
    else:
        after = round(after, 4)
    low, high = BOUNDS.get(key, (None, None))
    if low is not None:
        after = max(low, min(high, after))

    child = parent.copy()
    child.weights[key] = after
    return Candidate(child, f"weights.{key}: {before} -> {after}")


def _lesson(parent: Genome, store, signals) -> Candidate:
    child = parent.copy()
    lines = lessons_mod.lesson_lines(store, signals)
    if not lines:
        return Candidate(child, "lessons: nothing new to record")
    # One change means one line, not the whole tally.
    unrecorded = [ln for ln in lines if ln not in parent.lessons]
    if not unrecorded:
        return Candidate(child, "lessons: already recorded")
    child.lessons = lessons_mod.append_lessons(parent.lessons, unrecorded[:1])
    return Candidate(child, f"lessons += {unrecorded[0]}")


def _tier_move(parent: Genome, store, signals) -> Candidate:
    """Demote the always-on kind whose claims were injected but never used."""
    child = parent.copy()
    tally = lessons_mod.tally(store, signals)
    waste = {}
    for (kind, _origin), counts in tally.items():
        waste[kind] = waste.get(kind, 0) + counts["retrieved"] - counts["used"]

    for kind in sorted(waste, key=lambda k: (-waste[k], k)):
        if waste[kind] <= 0:
            break
        if child.tiers.get(kind) == "always":
            child.tiers[kind] = "semantic"
            return Candidate(child, f"tiers.{kind}: always -> semantic (injected but unused)")

    # Nothing wasteful: promote the most-used semantic kind into the always tier.
    earned = {}
    for (kind, _origin), counts in tally.items():
        earned[kind] = earned.get(kind, 0) + counts["used"]
    for kind in sorted(earned, key=lambda k: (-earned[k], k)):
        if earned[kind] <= 0:
            break
        if child.tiers.get(kind) == "semantic":
            child.tiers[kind] = "always"
            return Candidate(child, f"tiers.{kind}: semantic -> always (used every time)")
    return Candidate(child, "tiers: no move earned")


def mutate(parent: Genome, store, signals, generation: int) -> list[Candidate]:
    """Three candidates, one change each. Never touches gate or safety rules."""
    shrink = len(signals.ignored) > len(signals.used)
    candidates = [
        _nudge(parent, generation, shrink),
        _lesson(parent, store, signals),
        _tier_move(parent, store, signals),
    ]
    for cand in candidates:
        validate(cand.genome)
    return candidates
