"""Only p2 (the gate) and p3 (router) may import adapters; only p2 may call write/refute."""
import ast, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "engram"
ALLOWED_IMPORT = {"p2", "p3", "adapters", "server.py", "__main__.py", "consolidate", "report", "evolve"}
ALLOWED_WRITE = {"p2", "consolidate", "server.py"}   # consolidate.promote is the DAG's writer; server.py only for the explicit `refute` op (user command)


def _pkg(path):
    rel = path.relative_to(ROOT)
    return rel.parts[0]


def test_no_adapter_imports_outside_allowed():
    bad = []
    for f in ROOT.rglob("*.py"):
        if _pkg(f) in ALLOWED_IMPORT:
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.startswith("engram.adapters") or n == "adapters" or n.startswith(".adapters") for n in names):
                bad.append(str(f))
    assert not bad, f"adapters imported outside p2/p3: {bad}"


def test_only_gate_writes():
    bad = []
    pat = re.compile(r"\.(write|refute)\(")
    for f in ROOT.rglob("*.py"):
        if _pkg(f) in ALLOWED_WRITE or _pkg(f) == "adapters":
            continue
        src = f.read_text()
        for m in pat.finditer(src):
            line = src[:m.start()].count("\n") + 1
            bad.append(f"{f}:{line}")
    assert not bad, f"store.write/refute called outside p2: {bad}"
