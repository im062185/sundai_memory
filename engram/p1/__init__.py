"""Lane B: per-turn capture and salience tagging."""

from .episodic import append_episode
from .rules import extract_claims
from .think import extract_thinking
from .contradiction import opposing_claim_ids

__all__ = ["append_episode", "extract_claims", "extract_thinking", "opposing_claim_ids"]
