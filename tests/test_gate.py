from __future__ import annotations

import json

from engram.p2 import gate as p2_gate
from engram.p2.gate import promote


class InMemoryStore:
    def __init__(self) -> None:
        self.claims: list[dict] = []
        self.refute_calls: list[str] = []

    def write(self, claim) -> None:
        self.claims.append(dict(claim))

    def by_subject(self, subject):
        return [c for c in self.claims if c.get("subject") == subject]

    def refute(self, claim_id, by=None) -> None:
        self.refute_calls.append(claim_id)
        for c in self.claims:
            if c.get("id") == claim_id:
                c["status"] = "refuted"

    def query(self, *_args, **_kwargs):
        return []

    def touch(self, *_args, **_kwargs):
        return None

    def link(self, *_args, **_kwargs):
        return None

    def stats(self):
        return {}

    def export_markdown(self):
        return {}


def test_gate_cases_trigger_expected_rules():
    data = json.loads(open("tests/fixtures/gate_cases.json", encoding="utf-8").read())["cases"]
    p2_gate.ABSENCE_QUESTION.clear()
    store = InMemoryStore()

    for item in data:
        claim = item["claim"]
        verdict, fired, question = promote(claim, store)
        expected = item["expected"]
        assert verdict == expected["verdict"]
        assert fired == expected["fired"]
        if "question" in expected:
            assert question == expected.get("question")

        if second := item.get("second_pass"):
            second_expected = item["second_expected"]
            second_verdict, second_fired, second_question = promote(second, store)
            assert second_verdict == second_expected["verdict"]
            assert second_fired == second_expected["fired"]
            if second_expected.get("refute_older"):
                assert store.refute_calls
            if second_expected.get("question") is not None:
                assert second_question == second_expected.get("question")
