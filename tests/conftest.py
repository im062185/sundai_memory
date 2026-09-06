import json, pathlib, pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


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
