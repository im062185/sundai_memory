# 인코더의 모델 호출을 감싸고, 테스트에서는 녹화된 픽스처로 재생하는 클라이언트
"""Model access for the encoder.

Two implementations behind one `complete()` call:

- `AnthropicClient` — the real call, model from `ENGRAM_ENCODER_MODEL`
  (default `claude-haiku-4-5-20251001`), key from `ANTHROPIC_API_KEY`.
- `FakeClient` — replays `tests/fixtures/encode_*.json`. Selected by
  `ENGRAM_ENCODER_FAKE=1` so the test suite never touches the network.

The fake returns the *raw* recorded string, so the parsing and drop-invalid paths
in `encode.py` are exercised exactly as they are against a live model.
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Protocol

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class EncoderClient(Protocol):
    def complete(self, prompt: str, *, session: str | None = None) -> str: ...


class AnthropicClient:
    """Live call. Never used in tests; there is no network in the suite."""

    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("ENGRAM_ENCODER_MODEL", DEFAULT_MODEL)

    def complete(self, prompt: str, *, session: str | None = None) -> str:
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        reply = client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in reply.content if block.type == "text")


class FakeClient:
    """Replays recorded encoder responses from tests/fixtures/encode_*.json.

    A fixture is `{"name": ..., "match": {"session": ...}, "response": "<raw text>"}`.
    A fixture with `"match": {"default": true}` is used when no session matches.
    """

    name = "fake"

    def __init__(self, fixture_dir: pathlib.Path | None = None) -> None:
        self.fixture_dir = pathlib.Path(fixture_dir or FIXTURE_DIR)

    def _fixtures(self) -> list[dict]:
        out = []
        for path in sorted(self.fixture_dir.glob("encode_*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return out

    def complete(self, prompt: str, *, session: str | None = None) -> str:
        fixtures = self._fixtures()
        for fx in fixtures:
            if session and fx.get("match", {}).get("session") == session:
                return fx.get("response", "")
        for fx in fixtures:
            if fx.get("match", {}).get("default"):
                return fx.get("response", "")
        return ""


def get_client() -> EncoderClient:
    if os.environ.get("ENGRAM_ENCODER_FAKE") == "1":
        return FakeClient()
    return AnthropicClient()
