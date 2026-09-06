"""Pluggable semantic scorer for episodic recall (P0-2).

Interface: fit(texts) then score(query) -> np.ndarray of cosine similarities.
Reference implementation is LSA (TF-IDF + TruncatedSVD) — fully offline.
Measured on LoCoMo held-out split: LSA blending gains ~0 over lexical
(0.767 vs 0.771) because per-conversation LSA has too little training signal.
It ships defaulted OFF; the interface exists so a pretrained sentence-encoder
(the real fix, needs model download) drops in without touching core.
"""
from __future__ import annotations


class LSAScorer:
    def __init__(self, dims: int = 120):
        self.dims = dims
        self._ready = False

    def fit(self, texts: list[str]):
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        self.np = np
        self.vec = TfidfVectorizer(sublinear_tf=True, stop_words="english")
        X = self.vec.fit_transform(texts)
        d = max(2, min(self.dims, X.shape[1] - 1, X.shape[0] - 1))
        self.svd = TruncatedSVD(n_components=d, random_state=0)
        E = self.svd.fit_transform(X)
        self.E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
        self._ready = True

    def score(self, query: str):
        if not self._ready:
            return None
        qv = self.svd.transform(self.vec.transform([query]))
        qv = qv / (self.np.linalg.norm(qv) + 1e-9)
        return self.E @ qv.ravel()
