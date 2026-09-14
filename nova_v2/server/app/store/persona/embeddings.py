"""Embedding sources for Persona vector search.

Claude/Anthropic has no embeddings endpoint, so Persona vectors come from a
free, fully-local model: BAAI/bge-large-en-v1.5 run via fastembed (ONNX, no
PyTorch). `LocalEmbedder` is the real backend; `FakeEmbedder` is a
deterministic, dependency-free stand-in used by tests and by anyone building
against the seam before the model is downloaded.

Both expose the same shape and the SAME dimension (`EMBED_DIM`) so the pgvector
column and the fake never disagree.

Note the model is ~1.2 GB and is downloaded on first use, then cached (see
FASTEMBED_CACHE_PATH). Nothing imports fastembed until LocalEmbedder is
constructed, so a backend that never touches Persona never pays for it.
"""
from __future__ import annotations

import hashlib
import math
from typing import Literal, Protocol, runtime_checkable

# bge-large-en-v1.5 outputs 1024-dim vectors. If the model changes, keep the
# dimension in sync with vector(1024) in db/schema.sql.
EMBED_MODEL = "BAAI/bge-large-en-v1.5"
EMBED_DIM = 1024

# bge retrieval works best when the *query* (not the stored document) carries
# this instruction. We apply it ourselves so behaviour is identical regardless
# of fastembed internals.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

InputType = Literal["query", "document"]


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...


class LocalEmbedder:
    """Free local embeddings via fastembed (ONNX). Model is downloaded and
    cached on first use (~1.2 GB), then runs on CPU."""

    def __init__(self, model_name: str = EMBED_MODEL, dim: int = EMBED_DIM) -> None:
        from fastembed import TextEmbedding  # lazy: tests never need the dependency

        self._model = TextEmbedding(model_name=model_name)
        self.dim = dim

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []
        prepared = (
            [QUERY_INSTRUCTION + t for t in texts]
            if input_type == "query"
            else list(texts)
        )
        # fastembed yields L2-normalised numpy vectors; good for cosine search.
        return [vec.tolist() for vec in self._model.embed(prepared)]


class FakeEmbedder:
    """Deterministic hash-based vectors. Same text -> same vector, unit length.

    Not semantically meaningful, but stable and self-consistent, which is all
    the store logic and tests need. Similar strings do NOT map to similar
    vectors, so tests assert on exact/self matches, not fuzzy semantics.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec: list[float] = []
        i = 0
        while len(vec) < self.dim:
            h = hashlib.sha256(f"{text}:{i}".encode()).digest()
            for b in h:
                if len(vec) >= self.dim:
                    break
                vec.append((b / 255.0) * 2.0 - 1.0)  # map byte -> [-1, 1]
            i += 1
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
