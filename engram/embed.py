"""Optional embedding rerank for recall (lane A, 2026-09-06 evening).

Off unless ENGRAM_EMBED=1 and OPENAI_API_KEY is set: tests, CI and the default chat loop never
touch the network. Embeddings are cached by text hash in <out>/embed_cache.sqlite so a claim is
embedded once. Model: ENGRAM_EMBED_MODEL (default text-embedding-3-small).
"""
from __future__ import annotations
import hashlib, json, math, os, pathlib, sqlite3
from typing import Iterable

MODEL = os.environ.get("ENGRAM_EMBED_MODEL", "text-embedding-3-small")


def enabled() -> bool:
    return os.environ.get("ENGRAM_EMBED") == "1" and bool(os.environ.get("OPENAI_API_KEY", "").strip())


class Embedder:
    def __init__(self, out: pathlib.Path):
        out.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(out / "embed_cache.sqlite"))
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (h TEXT PRIMARY KEY, model TEXT, v TEXT)")
        self._client = None

    @staticmethod
    def _h(text: str) -> str:
        return hashlib.sha1(text.strip().lower().encode()).hexdigest()

    def embed(self, texts: Iterable[str]) -> list[list[float] | None]:
        texts = list(texts)
        out: list[list[float] | None] = [None] * len(texts)
        missing = []
        for i, t in enumerate(texts):
            row = self._db.execute("SELECT v FROM cache WHERE h = ? AND model = ?", (self._h(t), MODEL)).fetchone()
            if row:
                out[i] = json.loads(row[0])
            elif t.strip():
                missing.append(i)
        if missing:
            try:
                if self._client is None:
                    import openai
                    self._client = openai.OpenAI()
                resp = self._client.embeddings.create(model=MODEL, input=[texts[i][:2000] for i in missing])
                for i, d in zip(missing, resp.data):
                    out[i] = d.embedding
                    self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (self._h(texts[i]), MODEL, json.dumps(d.embedding)))
                self._db.commit()
            except Exception:
                pass  # degrade to keyword ranking
        return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b)); na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
