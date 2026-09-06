import networkx as nx, pathlib
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
