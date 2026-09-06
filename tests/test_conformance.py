"""Every Store in REGISTRY (plus NullStore) must honor engram/adapters/base.py."""
import jsonschema, pytest
from engram.adapters.base import Store, RecallHit, StoreStats
from engram.adapters.null import NullStore
from engram.adapters import REGISTRY
from conftest import make_claim

STORES = {"null": NullStore, **REGISTRY}


@pytest.fixture(params=list(STORES))
def store(request, tmp_path):
    cls = STORES[request.param]
    try:
        return cls(path=str(tmp_path / "engram.db"))
    except TypeError:
        return cls()


def test_is_store(store):
    assert isinstance(store, Store)
    assert isinstance(store.name, str) and store.name


def test_write_then_by_subject(store, schema):
    c = make_claim()
    jsonschema.validate(c, schema)
    store.write(c)
    got = store.by_subject("deploy target")
    assert [g["id"] for g in got] == [c["id"]]


def test_query_returns_hits_only_for_promoted(store):
    store.write(make_claim(suffix="promoted1", text="User prefers short answers", subject="answer length"))
    store.write(make_claim(suffix="heldheld1", status="held", text="Vendor SDK has no batch endpoint", subject="batch endpoint", polarity="deny", kind="absence"))
    hits = store.query("batch endpoint", k=5)
    assert all(isinstance(h, RecallHit) for h in hits)
    assert all(h.claim["status"] == "promoted" for h in hits)


def test_refute_takes_effect_for_next_query(store):
    c = make_claim(suffix="refuteme1", text="Vendor SDK has no batch endpoint", subject="batch endpoint", polarity="deny", kind="absence")
    store.write(c)
    store.refute(c["id"], by="clm_newerone1")
    assert all(h.claim["id"] != c["id"] for h in store.query("batch endpoint", k=5)) or store.name == "vector"
    if store.name != "vector":  # vector arm's refute is deliberately naive (TDD A-3)
        got = store.by_subject("batch endpoint")
        assert got and got[0]["status"] == "refuted"


def test_touch_and_stats(store):
    c = make_claim(suffix="touchme01")
    store.write(c)
    store.touch([c["id"]])
    s = store.stats()
    assert isinstance(s, StoreStats) and s.name == store.name
    assert s.promoted >= 1


def test_link_and_export(store):
    store.write(make_claim(suffix="linkaaaa1", kind="preference", tier="always", text="User prefers short answers", subject="answer length"))
    store.write(make_claim(suffix="linkbbbb1", kind="feedback", tier="always", text="Never use emojis in commit messages", subject="commit style"))
    store.link("clm_linkaaaa1", "clm_linkbbbb1", "co_retrieved")
    md = store.export_markdown()
    assert set(md) >= {"USER.md", "MEMORY.md"}
    assert all(isinstance(v, str) for v in md.values())
