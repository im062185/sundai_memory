from __future__ import annotations

import os
import tempfile
import pytest

from engram.adapters import REGISTRY


def _sample_claim(i: int, subject: str = "subject"):
    return {
        "id": f"clm_{i:012x}",
        "text": f"claim text {i}",
        "kind": "fact",
        "origin": "user_turn",
        "source_class": "said",
        "subject": subject,
        "polarity": "affirm",
        "status": "promoted",
        "created_at": "2026-09-06T00:00:00Z",
    }


def _build_store(name: str):
    store_cls = REGISTRY[name]
    if name == "sqlite":
        path = tempfile.NamedTemporaryFile(delete=True, suffix=".sqlite")
        path.close()
        return store_cls(path.name)
    return store_cls()


@pytest.mark.parametrize("name", [n for n in REGISTRY if n != "null"])
def test_store_roundtrip(name):
    store = _build_store(name)
    claim = _sample_claim(1)
    store.write(claim)
    hits = store.query("claim text")
    assert any(hit.claim["id"] == claim["id"] for hit in hits)
    assert hits[0].store == name


def test_store_refute_keeps_not_deleted():
    for name in REGISTRY:
        if name == "vector":
            continue
        store = _build_store(name)
        claim = _sample_claim(2, subject="refute")
        store.write(claim)
        store.refute(claim["id"], by="x")
        hits = store.query("claim text")
        assert all(hit.claim["id"] != claim["id"] for hit in hits)
        by_subject = store.by_subject("refute")
        assert any(c["id"] == claim["id"] and c.get("status") == "refuted" for c in by_subject)


@pytest.mark.xfail(reason="A-3")
def test_vector_refute_keeps_returning():
    store = REGISTRY["vector"]()
    claim = _sample_claim(3, subject="vector")
    store.write(claim)
    store.refute(claim["id"], by="x")
    hits = store.query("claim text")
    assert any(hit.claim["id"] == claim["id"] for hit in hits)


def test_sqlite_export_limits_chars():
    store = _build_store("sqlite")
    for idx in range(20):
        kind = "preference" if idx % 2 == 0 else "feedback"
        subject = "profile" if kind == "preference" else "feedback"
        store.write(
            {
                "id": f"clm_exp_{idx}",
                "text": f"value {idx} " + ("x" * 200),
                "kind": kind,
                "origin": "user_turn",
                "source_class": "said",
                "subject": subject,
                "polarity": "affirm",
                "status": "promoted",
                "created_at": "2026-09-06T00:00:00Z",
            }
        )
    out = store.export_markdown()
    assert len(out["USER.md"]) <= 1400
    assert len(out["MEMORY.md"]) <= 2200


def test_sqlite_link_and_touch():
    store = _build_store("sqlite")
    a = _sample_claim(10, subject="a")
    b = _sample_claim(11, subject="b")
    store.write(a)
    store.write(b)
    store.link(a["id"], b["id"], "co_retrieved", 2.0)
    store.touch([a["id"], a["id"], b["id"]])
    assert store._row_to_claim(store._conn.execute("SELECT * FROM memories WHERE id=?", (a["id"],)).fetchone())["access_count"] >= 2
    assert store._conn.execute("SELECT 1 FROM links WHERE src=? AND kind=?", (a["id"], "co_retrieved")).fetchone()
