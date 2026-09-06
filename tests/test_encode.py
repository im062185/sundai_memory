# 사후 관점 인코더가 픽스처만으로 결정적으로 동작하는지 검증하는 테스트
"""Lane C step 1. Fixture-driven, no network: every case runs under
ENGRAM_ENCODER_FAKE=1 against tests/fixtures/encode_*.json.
"""
import ast
import inspect
import json
import pathlib

import pytest

from engram.encode.encode import encode

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ENCODE_PKG = ROOT / "engram" / "encode"


@pytest.fixture(autouse=True)
def fake_encoder(monkeypatch):
    """No network anywhere in this file."""
    monkeypatch.setenv("ENGRAM_ENCODER_FAKE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def load_session(n: int) -> list[dict]:
    path = FIXTURES / f"session{n}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stats():
    """LAST_STATS is rebound per call, so read it off the module, not the import."""
    import engram.encode.encode as mod

    return mod.LAST_STATS


# --- verbatim ---------------------------------------------------------------

def test_verbatim_text_is_preserved_exactly():
    """A candidate quoted from an episode keeps the exact string, byte for byte."""
    episodes = load_session(1)
    out = encode(episodes, persona="Builder.")
    by_text = {c["text"]: c for c in out}

    quoted = "I never want TypeScript in the organ codebase."
    assert quoted in by_text, "the standing rule should survive encoding"
    assert by_text[quoted]["verbatim"] is True
    # and it really is a span of the source turn, not a paraphrase
    assert any(quoted in ep["text"] for ep in episodes)


def test_text_quoted_from_an_episode_is_flagged_verbatim_even_if_model_said_false():
    """encode_session1 records this candidate with verbatim:false; it appears in the turn."""
    raw = json.loads((FIXTURES / "encode_session1.json").read_text(encoding="utf-8"))
    recorded = json.loads(raw["response"])["candidates"]
    absence = next(c for c in recorded if c["text"].startswith("The vendor SDK has no"))
    assert absence["verbatim"] is False, "fixture must record the model getting this wrong"

    out = encode(load_session(1), persona="Builder.")
    got = next(c for c in out if c["text"].startswith("The vendor SDK has no"))
    assert got["verbatim"] is True


def test_verbatim_never_rewrites_the_claim_text():
    """A version number flags verbatim, but the declarative text is left alone."""
    out = encode(load_session(2), persona="Builder.")
    got = next(c for c in out if "3.4" in c["text"])
    assert got["verbatim"] is True
    assert got["text"] == "The SDK shipped batch writes in 3.4."
    assert "correction" not in got["text"].lower(), "must not absorb conversational framing"


# --- hindsight: the reaction comes from the NEXT human turn ------------------

def test_user_reaction_is_read_from_the_following_human_turn():
    """The whole point of running late: the model guessed 'approving', the human corrected."""
    raw = json.loads((FIXTURES / "encode_session2.json").read_text(encoding="utf-8"))
    recorded = json.loads(raw["response"])["candidates"]
    assistant_candidate = next(c for c in recorded if c["origin"] == "assistant_turn")
    assert assistant_candidate["user_reaction"] == "approving", "fixture records the model's guess"

    out = encode(load_session(2), persona="Builder.")
    got = next(c for c in out if c["origin"] == "assistant_turn")
    assert got["user_reaction"] == "correcting"


def test_reaction_falls_back_to_neutral_when_the_next_human_turn_is_plain():
    out = encode(load_session(1), persona="Builder.")
    thinking = next(c for c in out if c["origin"] == "assistant_thinking")
    assert thinking["user_reaction"] == "neutral"


def test_user_stated_claims_carry_no_reaction():
    """Nobody reacted to what the human said themselves."""
    out = encode(load_session(1), persona="Builder.")
    for c in out:
        if c["origin"] == "user_turn":
            assert c["user_reaction"] is None


# --- invalid model output is dropped and counted, never repaired -------------

def test_invalid_candidates_are_dropped_and_counted():
    """Five malformed entries, one survivor. Nothing is patched into validity."""
    out = encode([{"turn_index": 0, "role": "user", "text": "anything", "session": "no-such-session"}])
    assert [c["text"] for c in out] == ["A valid survivor claim."]

    s = stats()
    assert s.returned == 1
    assert s.dropped_invalid_candidate == 5
    joined = " | ".join(s.reasons)
    assert "G9" in joined and "G2" in joined
    assert "not an object" in joined


def test_invalid_json_drops_the_whole_response():
    out = encode([{"turn_index": 0, "role": "user", "text": "anything", "session": "badjson"}])
    assert out == []
    s = stats()
    assert s.dropped_invalid_json == 1
    assert s.returned == 0


def test_missing_key_is_not_fatal(monkeypatch):
    """No fake, no key: consolidation must still finish (TDD 2.4)."""
    monkeypatch.delenv("ENGRAM_ENCODER_FAKE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = encode(load_session(1), persona="Builder.")
    assert out == []
    assert stats().model_error is not None


def test_empty_window_returns_nothing():
    assert encode([], persona="Builder.") == []


# --- encode never touches a store -------------------------------------------

def test_encode_takes_no_store_and_never_writes():
    """Structural: the gate decides. encode() has no store to write to."""
    params = list(inspect.signature(encode).parameters)
    assert params == ["episodes", "persona", "related"]

    for path in ENCODE_PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            assert not any("adapters" in n for n in names), f"{path} imports adapters"
            assert not any(n.startswith("engram.p1") or n.startswith("engram.p2") for n in names), \
                f"{path} imports the salience/gate lanes"

        src = path.read_text(encoding="utf-8")
        assert ".write(" not in src and ".refute(" not in src, f"{path} touches a store"


def test_dag_lookup_path_resolves():
    """dag._opt('engram.encode.encode', 'encode') is how this module is reached."""
    import importlib

    fn = getattr(importlib.import_module("engram.encode.encode"), "encode")
    assert callable(fn)
    assert fn is encode
