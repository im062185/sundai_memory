"""Consolidation DAG — lane A owns the graph; lanes B/C fill node bodies via nodes.py hooks.

expire → capture → encode → merge → adjudicate → promote → wire → reindex → census → evolve
Only `promote` writes to stores (tests/test_dag.py + test_isolation.py).
"""
from __future__ import annotations
import importlib, json, pathlib, time
import networkx as nx

ORDER = ["expire", "capture", "encode", "merge", "adjudicate", "promote", "wire", "reindex", "census", "evolve"]
G = nx.DiGraph()
G.add_edges_from(zip(ORDER, ORDER[1:]))
assert nx.is_directed_acyclic_graph(G), "consolidation DAG must be acyclic"


def _opt(mod: str, attr: str):
    try:
        return getattr(importlib.import_module(mod), attr)
    except Exception:
        return None


def run(store, messages: list, *, reason: str = "cli", out: pathlib.Path = pathlib.Path("out")) -> dict:
    """Runs every node exactly once in topological order. Returns {digest, counts}."""
    from engram.consolidate import nodes
    state = {"store": store, "messages": messages, "reason": reason, "out": pathlib.Path(out), "episodes": [], "candidates": [],
             "counts": {"expired": 0, "captured": 0, "encoded": 0, "merged": 0, "adjudicated": 0, "promoted": 0, "wired": 0, "reindexed": False, "generation": 0},
             "digest_lines": [], "visited": []}
    for node in nx.topological_sort(G):
        state["visited"].append(node)
        getattr(nodes, node)(state)
    digest = "\n".join(state["digest_lines"])
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": reason, "counts": state["counts"], "visited": state["visited"]}
    state["out"].mkdir(parents=True, exist_ok=True)
    with (state["out"] / "consolidation.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return {"digest": digest, "counts": state["counts"]}
