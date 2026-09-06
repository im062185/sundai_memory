"""Store adapters. REGISTRY is filled by lane B; lane A owns base.py and null.py."""
from .base import Claim, Store, RecallHit  # noqa: F401

REGISTRY: dict[str, type] = {}
