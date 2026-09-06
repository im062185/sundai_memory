"""The judge's refusals (bench/judge.py).

No network here and no real client: the point of these tests is that the
judge produces `null` + "not run" instead of a number whenever it has no
right to a number.
"""
import json

import pytest

from bench.judge import (
    JUDGE_MODEL,
    gold_text,
    judge_records,
    judgements_to_dict,
    unscorable_reason,
    user_prompt,
)
from bench.metrics import JUDGE_NOT_RUN


def record(**over):
    base = {
        "question_id": "gpt4_2655b836",
        "question_type": "multi-session",
        "abstention": False,
        "question": "How many cows did I buy from Peter?",
        "gold": "12",
        "arm": "on",
        "answer": "You bought 12 cows from Peter.",
        "answerer": "pi",
        "error": None,
    }
    base.update(over)
    return base


class FakeMessages:
    """Records requests; returns whatever verdicts it was primed with."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        correct = self.verdicts.pop(0)
        text = json.dumps({"correct": correct, "reason": "because"})
        block = type("Block", (), {"type": "text", "text": text})()
        return type("Response", (), {"content": [block]})()


class FakeClient:
    def __init__(self, verdicts):
        self.messages = FakeMessages(verdicts)


# --------------------------------------------------------------------------
# gold
# --------------------------------------------------------------------------

def test_int_gold_is_stringified():
    """32 of the 500 oracle instances have an int `answer` (bench/LONGMEMEVAL.md)."""
    assert gold_text(3) == "3"
    assert gold_text("three") == "three"
    assert gold_text(None) == ""


def test_int_gold_reaches_the_prompt_without_raising():
    text = user_prompt(record(gold=12))
    assert "Gold answer: 12" in text


# --------------------------------------------------------------------------
# the refusals
# --------------------------------------------------------------------------

def test_stub_answers_are_never_scored():
    reason = unscorable_reason(record(answerer="stub"))
    assert reason and "stub" in reason


def test_errored_and_empty_records_are_never_scored():
    assert unscorable_reason(record(error="pi: exit 1"))
    assert unscorable_reason(record(answer="   "))
    assert unscorable_reason(record()) is None


def test_a_stub_run_yields_accuracy_null_and_judge_not_run():
    client = FakeClient([True, True])
    judgements, judge, notes = judge_records([record(answerer="stub"), record(answerer="stub")],
                                             client=client)
    assert client.messages.requests == [], "the judge called the model on stub answers"
    payload = judgements_to_dict(judgements, judge, notes, arm="on", variant="longmemeval_oracle")
    assert payload["accuracy"] is None, "a stub run must not produce a number"
    assert payload["judge"] == JUDGE_NOT_RUN
    assert payload["n"] == 2, "n still reports the slice size"


def test_no_client_means_not_run_not_zero():
    judgements, judge, notes = judge_records([record(), record()], client=None)
    assert judge == JUDGE_NOT_RUN
    assert all(j.correct is None for j in judgements)
    payload = judgements_to_dict(judgements, judge, notes, arm="on", variant="v")
    assert payload["accuracy"] is None
    assert all(not n["scored"] for n in payload["notes"])


# --------------------------------------------------------------------------
# a judged run
# --------------------------------------------------------------------------

def test_a_judged_run_names_the_judge_and_keeps_categories():
    client = FakeClient([True, False, True])
    records = [
        record(question_id="a", question_type="multi-session"),
        record(question_id="b", question_type="knowledge-update"),
        record(question_id="c", question_type="multi-session"),
    ]
    judgements, judge, notes = judge_records(records, client=client)
    assert judge == JUDGE_MODEL
    payload = judgements_to_dict(judgements, judge, notes, arm="on", variant="v")
    assert payload["judge"] == JUDGE_MODEL
    assert payload["accuracy"] == pytest.approx(2 / 3)
    assert payload["per_category"]["multi-session"] == {"accuracy": 1.0, "n": 2}
    assert payload["per_category"]["knowledge-update"] == {"accuracy": 0.0, "n": 1}


def test_the_request_uses_the_documented_sonnet_5_shape():
    """claude-sonnet-5 rejects temperature/top_p/top_k and thinking.budget_tokens."""
    client = FakeClient([True])
    judge_records([record()], client=client)
    req = client.messages.requests[0]
    assert req["model"] == "claude-sonnet-5"
    assert not ({"temperature", "top_p", "top_k", "thinking"} & set(req))
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_a_partial_judge_run_is_visible_rather_than_averaged_as_wrong():
    """One stub among real answers: n=3, ratio over the 2 that were scored."""
    client = FakeClient([True, False])
    records = [record(question_id="a"), record(question_id="b"),
               record(question_id="c", answerer="stub")]
    judgements, judge, notes = judge_records(records, client=client)
    payload = judgements_to_dict(judgements, judge, notes, arm="on", variant="v")
    assert payload["n"] == 3
    assert payload["accuracy"] == pytest.approx(0.5)


def test_abstention_questions_tell_the_judge_to_expect_a_refusal():
    text = user_prompt(record(abstention=True, gold="The information provided is not enough."))
    assert "abstention" in text.lower()
    assert "declines" in text.lower()
