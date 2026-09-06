import json, pathlib, sys, pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Lane D: tests/ has no __init__.py, so pytest's prepend import mode puts
# tests/ on sys.path, not the repo root — `import bench.metrics` then fails.
# Harmless for every other lane; move it to a root conftest or a pytest.ini
# `pythonpath` if you prefer, integrator.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_claim(**over):
    base = {
        "id": "clm_" + (over.pop("suffix", None) or "abcdefgh"),
        "text": "Deploy target is Vercel",
        "kind": "fact",
        "origin": "user_turn",
        "source_class": "said",
        "subject": "deploy target",
        "polarity": "affirm",
        "status": "promoted",
        "tier": "semantic",
        "created_at": "2026-09-06T17:00:00Z",
        "session": "s1",
        "turn_index": 1,
    }
    base.update(over)
    return base


@pytest.fixture
def schema():
    return json.loads((ROOT / "schema" / "claim.schema.json").read_text())


@pytest.fixture
def claim():
    return make_claim()
