"""Store adapters."""
from .base import Claim, Store, RecallHit
from .sqlite import SQLiteStore
from .vector import VectorStore


REGISTRY: dict[str, type[Store]] = {
    "sqlite": SQLiteStore,
    "vector": VectorStore,
}

__all__ = ["REGISTRY", "Claim", "Store", "RecallHit", "SQLiteStore", "VectorStore"]
