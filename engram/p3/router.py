from __future__ import annotations

import json
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_POLICY_PATH = Path(__file__).with_name("policy.json")


class Router:
    def __init__(self, policy_path: str | None = None) -> None:
        self._policy_path = policy_path or str(_POLICY_PATH)
        self._policy = self._load_policy(self._policy_path)

    @staticmethod
    def _load_policy(path: str) -> dict[str, str]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return raw.get("kind_to_tier", {})

    def route(self, claim: dict) -> str:
        kind = claim.get("kind")
        tier = self._policy.get(kind)
        if tier is None:
            LOGGER.warning("policy_fallback kind=%s", kind)
            return "semantic"
        return tier


_default_router = Router()


def route_claim(claim: dict) -> str:
    return _default_router.route(claim)
