from __future__ import annotations

import json
from pathlib import Path
import tempfile

from engram.p1 import append_episode, extract_claims, extract_thinking
from engram.p2 import gate as p2_gate
from engram.p2.gate import promote
from engram.p3.router import route_claim
from engram.adapters.sqlite import SQLiteStore


def _load_messages(path: Path) -> list[dict]:
    messages: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        messages.append(json.loads(raw))
    return messages


def _explain_lines(claims: list[dict]) -> str:
    buckets = {
        "said": [],
        "inferred": [],
        "verified": [],
    }

    for claim in claims:
        key = claim.get("source_class")
        if key not in buckets:
            continue
        buckets[key].append(claim["text"])

    def fmt(lines: list[str]) -> str:
        if not lines:
            return "none"
        return "; ".join(lines)

    return "\n".join(
        [
            f"said: {fmt(buckets['said'])}",
            f"inferred: {fmt(buckets['inferred'])}",
            f"verified: {fmt(buckets['verified'])}",
        ]
    )


def test_cuj_session_flow_s2_s4_s5():
    root = Path(__file__).resolve().parent / "fixtures"
    session1 = _load_messages(root / "session1.jsonl")
    session2 = _load_messages(root / "session2.jsonl")
    messages = session1 + session2

    p2_gate.ABSENCE_QUESTION.clear()

    with tempfile.NamedTemporaryFile(suffix=".sqlite") as f:
        store = SQLiteStore(f.name)

        questions: list[dict] = []
        correction_event = None
        old_batch_ref = None

        for msg in messages:
            session = msg["session"]
            turn_index = int(msg["turn_index"])
            role = msg.get("role", "user")

            episode = append_episode(role=role, text=msg.get("text", ""), session=session, turn_index=turn_index, thinking=msg.get("thinking"))
            assert episode["session"] == session
            assert episode["turn_index"] == turn_index

            claimed = extract_claims(msg, session=session, turn_index=turn_index)
            claimed.extend(extract_thinking(msg))

            for claim in claimed:
                claim["tier"] = route_claim(claim)
                if claim["kind"] == "absence" and claim["subject"] == "vendor_sdk_batch_write":
                    old_batch_ref = claim["id"]

                if "shipped" in claim["text"].lower() and "batch" in claim["text"].lower():
                    before = {c["id"]: c["status"] for c in store.by_subject(claim["subject"])}
                    verdict, _fired, question = promote(claim, store)
                    after = {c["id"]: c["status"] for c in store.by_subject(claim["subject"])}
                    correction_event = {
                        "verdict": verdict,
                        "claim_id": claim["id"],
                        "subject": claim["subject"],
                        "old_status_before": before,
                        "old_status_after": after,
                    }
                    if question:
                        questions.append({"session": session, "turn_index": turn_index, "question": question, "claim_id": claim["id"]})
                    continue

                verdict, _fired, question = promote(claim, store)
                assert verdict in {"promoted", "held", "rejected"}
                if question:
                    questions.append({"session": session, "turn_index": turn_index, "question": question, "claim_id": claim["id"]})

        assert old_batch_ref is not None, "batch write absence claim was not emitted"
        assert correction_event is not None, "batch write correction claim was not seen"
        assert correction_event["verdict"] == "promoted"

        post_subject_records = store.by_subject("vendor_sdk_batch_write")
        old_records = [r for r in post_subject_records if r["id"] != correction_event["claim_id"]]
        assert old_records, "no prior batch-write claim to be corrected"
        assert any(r["status"] == "refuted" for r in old_records), "old batch-write claim was not refuted"
        assert any(r["status"] == "promoted" and r["id"] == correction_event["claim_id"] for r in post_subject_records)

        absence_questions = [q for q in questions if q["question"] and "source" in q["question"].lower()]
        assert len(absence_questions) == 1, f"expected one clarifying question, got {absence_questions}"

        recall_hits = [hit.claim for hit in store.query("memory layer for pi", k=20)]
        inferred_hits = [store._row_to_claim(row) for row in store._conn.execute("SELECT * FROM memories WHERE source_class = ? AND status = ?", ("inferred", "promoted"))]

        explain_text = _explain_lines(recall_hits + inferred_hits)
        assert "said" in explain_text
        assert "inferred" in explain_text
        assert "verified" in explain_text

        retrieval = store.query("vendor batch writes in pi", k=5)
        assert all(hit.claim["status"] == "promoted" for hit in retrieval)
        assert all(hit.claim["id"] != old_batch_ref for hit in store.query("batch write endpoint", k=10)), "refuted claim still surfaced"
