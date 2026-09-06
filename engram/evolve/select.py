# 후보들의 적합도를 비교해 최선을 고르고, 나아진 것이 없으면 부모를 지키는 모듈
"""Selection with revert-on-regression.

The hard rule from AMD-03 §4: **a candidate that lowers recall is never selected**,
whatever it does to the other three numbers. Tokens, corrections and latency only
break ties among candidates that held recall. When nothing improves, the parent
survives unchanged — a generation is allowed to be a no-op.
"""
from __future__ import annotations

import json
import pathlib

from engram.evolve.genome import Genome, diff, save

EPS = 1e-9

# `median_ms` is reported in fitness.json but is deliberately NOT a selection
# criterion. A replayed recall takes ~0.1 ms, so run-to-run jitter is larger than
# any real effect: selecting on it makes a generation "win" on noise and turns
# "the parent survives when nothing improves" into a coin flip.
def is_better(candidate: dict[str, float], parent: dict[str, float]) -> bool:
    """Strictly better on recall, or equal recall and better on a metric that is safe to chase."""
    if candidate["recall_at_k"] < parent["recall_at_k"] - EPS:
        return False                       # never trade recall away
    if candidate["recall_at_k"] > parent["recall_at_k"] + EPS:
        return True

    # Recall tied. Fewer corrections is always a real win.
    if candidate["corrections"] < parent["corrections"] - EPS:
        return True
    if candidate["corrections"] > parent["corrections"] + EPS:
        return False

    # Fewer tokens is a win only while retrieval is actually finding something.
    # With recall tied at zero, "inject less" minimises tokens perfectly, so a
    # tokens tie-break would ratchet k and the budget down every generation and
    # starve recall for good. Refuse to call that an improvement.
    if parent["recall_at_k"] <= EPS:
        return False
    if candidate["tokens"] < parent["tokens"] - EPS:
        return True
    return False


def choose(candidates: list, fitnesses: list[dict[str, float]], parent_fitness: dict[str, float]):
    """Return (index or None, fitness). None means the parent survives."""
    best_index, best_fitness = None, parent_fitness
    for i, fitness in enumerate(fitnesses):
        if is_better(fitness, best_fitness):
            best_index, best_fitness = i, fitness
    return best_index, best_fitness


def write_generation(
    directory: pathlib.Path,
    genome: Genome,
    fitness: dict[str, float],
    parent_fitness: dict[str, float],
    parent: Genome,
    selected: str,
) -> str:
    """gen-NNN/{weights.json, encoder.lessons.md, persona.md, tiers.json, fitness.json, DIFF.md}."""
    directory.mkdir(parents=True, exist_ok=True)
    save(genome, directory)

    (directory / "fitness.json").write_text(
        json.dumps(
            {
                "selected": selected,
                "fitness": fitness,
                "parent_fitness": parent_fitness,
                "recall_at_k": fitness["recall_at_k"],
                "tokens": fitness["tokens"],
                "corrections": fitness["corrections"],
                "median_ms": fitness["median_ms"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    body = diff(parent, genome)
    delta = (
        f"| recall@k | {parent_fitness['recall_at_k']:.3f} | {fitness['recall_at_k']:.3f} |\n"
        f"| tokens | {parent_fitness['tokens']:.1f} | {fitness['tokens']:.1f} |\n"
        f"| corrections | {parent_fitness['corrections']:.0f} | {fitness['corrections']:.0f} |\n"
        f"| median ms | {parent_fitness['median_ms']:.2f} | {fitness['median_ms']:.2f} |\n"
    )
    text = (
        f"# {directory.name}\n\nSelected: **{selected}**\n\n"
        f"## Change\n\n{body}\n\n"
        f"## Fitness\n\n| metric | parent | this generation |\n|---|---|---|\n{delta}\n"
        "Recall is never traded away: a candidate that lowered it is not selectable.\n"
    )
    (directory / "DIFF.md").write_text(text, encoding="utf-8")
    return body
