import json, networkx as nx, pathlib, pytest
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


def _episode(n):
    return json.dumps({"ts": "2026-09-06T23:00:50.0000%02d+00:00" % n, "role": "user", "text": "turn %d" % n})


def test_capture_cursor_does_not_recapture(tmp_path):
    """Two consolidations, no new turns in between: the second captures nothing.

    The cursor is a line count over an append-only log, so this holds no matter
    how close together the runs are. The timestamp cursor it replaced failed
    here whenever a turn landed in the same whole second as the marker.
    """
    (tmp_path / "episodes.jsonl").write_text("\n".join(_episode(i) for i in range(3)) + "\n")
    assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 3
    assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 0
    with (tmp_path / "episodes.jsonl").open("a") as f:
        f.write(_episode(3) + "\n")
    assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 1


def test_capture_cursor_survives_a_turn_appended_mid_run(tmp_path):
    """A turn appended while consolidation is running belongs to the NEXT run.

    encode() can sit on a model call for seconds, and pi keeps appending turns
    the whole time. The old cursor was wall-clock stamped after the DAG
    finished, so every one of those turns fell into the gap and was never seen.
    """
    (tmp_path / "episodes.jsonl").write_text(_episode(0) + "\n")
    import engram.consolidate.nodes as nodes
    real_encode = nodes.encode

    def slow_encode(state):
        with (tmp_path / "episodes.jsonl").open("a") as f:   # pi, mid-consolidation
            f.write(_episode(1) + "\n")
        return real_encode(state)

    nodes.encode = slow_encode
    try:
        assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 1
    finally:
        nodes.encode = real_encode
    assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 1, \
        "the turn written during the run was lost"


def test_capture_cursor_not_advanced_when_a_node_raises(tmp_path):
    """A crash mid-DAG must re-capture the batch, never drop it."""
    (tmp_path / "episodes.jsonl").write_text(_episode(0) + "\n")
    import engram.consolidate.nodes as nodes
    real_promote = nodes.promote

    def boom(state):
        raise RuntimeError("store went away")

    nodes.promote = boom
    try:
        with pytest.raises(RuntimeError):
            dag.run(NullStore(), [], reason="cli", out=tmp_path)
    finally:
        nodes.promote = real_promote
    assert dag.run(NullStore(), [], reason="cli", out=tmp_path)["counts"]["captured"] == 1
