# 통합(consolidation) 한 번을 한 세대로 삼아 게놈을 진화시키는 진입점
"""One consolidation = one generation.

    parent = latest gen-NNN (gen-000 seeded from defaults if there is no history)
    mutate -> three candidates, one change each
    replay -> each candidate scored against the logged queries, no live model
    select -> best kept, parent kept when nothing improves, never trading recall
    write  -> gen-NNN/{genome files, fitness.json, DIFF.md}

Named `run.py`, not `evolve.py`: the DAG reaches this as
`_opt("engram.evolve", "evolve")`, a package attribute, and a submodule called
`evolve` would be shadowed by that export.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from engram.evolve import select as select_mod
from engram.evolve.genome import Genome, ensure_seed, generations, load
from engram.evolve.mutate import mutate
from engram.evolve.replay import replay
from engram.evolve.signals import Signals, load_signals


@dataclass
class GenerationReport:
    generation: int = 0
    selected: str = "parent"
    fitness: dict = field(default_factory=dict)
    parent_fitness: dict = field(default_factory=dict)
    diff: str = ""


def _next_index(generations_dir: pathlib.Path) -> int:
    existing = generations(generations_dir)
    if not existing:
        return 1
    last = existing[-1].name.rsplit("-", 1)[-1]
    try:
        return int(last) + 1
    except ValueError:
        return len(existing)


def evolve(store, signals: Signals | None = None, generations_dir="out/generations") -> GenerationReport:
    """Run one generation. Called by the DAG as evolve(store, None, out/"generations")."""
    generations_dir = pathlib.Path(generations_dir)
    parent_dir = ensure_seed(generations_dir)
    parent: Genome = load(parent_dir)

    if signals is None:
        # The server writes retrieval_log.jsonl and trace.jsonl beside generations/.
        signals = load_signals(generations_dir.parent)

    index = _next_index(generations_dir)
    parent_fitness = replay(store, parent, signals)

    candidates = mutate(parent, store, signals, index)
    fitnesses = [replay(store, cand.genome, signals) for cand in candidates]

    best_index, best_fitness = select_mod.choose(candidates, fitnesses, parent_fitness)
    if best_index is None:
        chosen, selected = parent.copy(), "parent (nothing improved)"
    else:
        chosen, selected = candidates[best_index].genome, candidates[best_index].change

    target = generations_dir / f"gen-{index:03d}"
    body = select_mod.write_generation(target, chosen, best_fitness, parent_fitness, parent, selected)

    return GenerationReport(
        generation=index,
        selected=selected,
        fitness=best_fitness,
        parent_fitness=parent_fitness,
        diff=body,
    )
