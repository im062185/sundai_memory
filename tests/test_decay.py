from datetime import datetime, timedelta, timezone
from engram.adapters import REGISTRY
from conftest import make_claim

NOW = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)


def _store(tmp_path):
    return REGISTRY["sqlite"](path=str(tmp_path / "d.db"))


def test_untouched_fact_fades_and_goes_dormant(tmp_path):
    s = _store(tmp_path)
    s.write(make_claim(suffix="oldfact01", created_at=(NOW - timedelta(days=90)).isoformat(), importance=3))
    s.write(make_claim(suffix="newfact01", created_at=(NOW - timedelta(days=1)).isoformat(), importance=3, subject="x"))
    n = s.decay(now=NOW)
    old, new = s.by_subject("deploy target")[0], s.by_subject("x")[0]
    assert n == 1 and old["status"] == "dormant" and new["status"] == "promoted"
    assert old["activation"] < 0.1 < new["activation"] <= 1.0
    assert all(h.claim["id"] != old["id"] for h in s.query("deploy", k=5)), "dormant claims are not recalled"


def test_use_and_immunity_keep_memories_alive(tmp_path):
    s = _store(tmp_path)
    s.write(make_claim(suffix="usedfact1", created_at=(NOW - timedelta(days=90)).isoformat(), importance=3, subject="used"))
    s.touch(["clm_usedfact1"] * 20)
    s.write(make_claim(suffix="rulefact1", created_at=(NOW - timedelta(days=365)).isoformat(), kind="feedback", subject="rule"))
    assert s.decay(now=NOW) == 0
    assert s.by_subject("used")[0]["status"] == "promoted"
    assert s.by_subject("rule")[0]["activation"] >= 1.0


def test_dormant_revives_when_touched(tmp_path):
    s = _store(tmp_path)
    s.write(make_claim(suffix="reviveme1", created_at=(NOW - timedelta(days=120)).isoformat()))
    s.decay(now=NOW)
    assert s.by_subject("deploy target")[0]["status"] == "dormant"
    s.touch(["clm_reviveme1"] * 5)
    s.decay(now=NOW)
    assert s.by_subject("deploy target")[0]["status"] == "promoted"
