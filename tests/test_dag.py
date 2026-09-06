import json, networkx as nx, pathlib
from engram.consolidate import dag
from engram.adapters.null import NullStore


def test_acyclic_and_order():
    assert nx.is_directed_acyclic_graph(dag.G)
    assert list(nx.topological_sort(dag.G)) == dag.ORDER


def test_each_node_once_and_counts_schema(tmp_path):
    msgs = [{"role": "user", "content": "Deploy target is Vercel"}, {"role": "assistant", "content": [{"type": "text", "text": "Noted."}, {"type": "thinking", "thinking": "user prefers vercel"}]}]
    res = dag.run(NullStore(), msgs, reason="manual", out=tmp_path)
    assert set(res["counts"]) == {"expired", "captured", "encoded", "merged", "adjudicated", "promoted", "wired", "reindexed", "generation"}
    assert res["counts"]["captured"] == 2
    assert res["digest"].startswith("Memory census")
    assert (tmp_path / "consolidation.jsonl").exists()


def test_only_promote_writes():
    src = pathlib.Path(dag.__file__).with_name("nodes.py").read_text()
    body = {}
    cur = None
    for line in src.splitlines():
        if line.startswith("def "):
            cur = line[4:line.index("(")]
            body[cur] = []
        elif cur:
            body[cur].append(line)
    for name, lines in body.items():
        text = "\n".join(lines)
        if name != "promote":
            assert ".write(" not in text and ".refute(" not in text, name


def test_capture_cursor_compares_instants_not_strings(tmp_path):
    """An episode written after the last consolidation must be captured.

    Regression: the cursor compared raw ISO strings. episodes.jsonl writes
    "…50.088779+00:00" and consolidation.jsonl writes "…50Z"; '.' (46) sorts
    before 'Z' (90), so an episode written *within the same second* as the
    marker compared as older than it, and — because the cursor only moves
    forward — was never consolidated. The DAG ran clean and reported
    captured: 0 forever.

    The stamps below must share a second. Straddle it and the string compare
    is accidentally right, which is why this went unnoticed: the session_start
    marker and the turn that follows it land in the same second constantly.
    """
    (tmp_path / "consolidation.jsonl").write_text(
        json.dumps({"ts": "2026-09-06T23:00:50Z", "reason": "start", "counts": {}, "visited": []}) + "\n")
    (tmp_path / "episodes.jsonl").write_text("\n".join([
        json.dumps({"ts": "2026-09-06T23:00:49.900000+00:00", "role": "user", "text": "before the marker"}),
        json.dumps({"ts": "2026-09-06T23:00:50.088779+00:00", "role": "user", "text": "after the marker"}),
    ]) + "\n")
    res = dag.run(NullStore(), [], reason="cli", out=tmp_path)
    assert res["counts"]["captured"] == 1, "the episode after the marker was skipped"
