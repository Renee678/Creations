"""Text embedders. Both return L2-normalised float32 vectors so cosine == dot product."""

import hashlib
import logging
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z]+")
_STOP = {"a", "an", "and", "the", "with", "in", "of", "for", "to", "on", "at", "is", "by", "or", "from"}


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class HashEmbedder:
    """Offline bag-of-words embedder (hashing trick over unigrams and bigrams).

    No model download, fully deterministic: used in tests, CI and as the fallback
    when the semantic model is unavailable. It only captures lexical overlap.
    """

    name = "hash"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        v = np.zeros(self.dim, dtype=np.float32)
        for f in features:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalise(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class BgeEmbedder:
    """BAAI/bge-small-en-v1.5 via fastembed (ONNX, CPU). Same model as the H&M dataset vectors."""

    name = "bge"
    dim = 384
    model_name = "BAAI/bge-small-en-v1.5"

    def __init__(self):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(self.model_name)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.array(list(self._model.embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self._model.query_embed(text)), dtype=np.float32))[0]


def make_embedder(kind: str) -> Embedder:
    if kind == "bge":
        try:
            return BgeEmbedder()
        except Exception:  # model download blocked / fastembed missing: degrade, don't crash
            logging.getLogger(__name__).exception("BGE embedder unavailable; falling back to the hash embedder")
            return HashEmbedder()
    if kind == "hash":
        return HashEmbedder()
    raise ValueError(f"unknown embedder {kind!r}")
