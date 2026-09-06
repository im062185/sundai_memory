# 진화 패키지: DAG가 engram.evolve.evolve 로 호출하는 공개 진입점
"""Lane C, the outer loop. The inner loop stores memories; this changes the storing.

The DAG reaches it as `dag._opt("engram.evolve", "evolve")` — a package
attribute — so `evolve` is exported here and the driver module is named `run.py`
rather than `evolve.py`, which this export would otherwise shadow.
"""
from engram.evolve.genome import Genome
from engram.evolve.run import GenerationReport, evolve
from engram.evolve.signals import Signals, load_signals

__all__ = ["evolve", "GenerationReport", "Genome", "Signals", "load_signals"]
