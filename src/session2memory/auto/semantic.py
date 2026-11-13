"""Meaning-based search over distilled memory: local embeddings, optionally a reranker.

Keyword search only finds what shares words with the question. Here every active memory
and session summary also gets a vector, kept in the same database; a question is ranked by
meaning, keyword hits are added to the candidates, and a cross-encoder can re-read the best
of them to settle the order.

Everything runs on this machine through the optional ``fastembed`` package (ONNX models,
downloaded once): no text leaves it and a query costs no tokens. Without the package, or
before the first ``embed`` step has run, callers fall back to keyword search.
"""

from __future__ import annotations

import math
import os
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from session2memory.auto.scope import Reader
from session2memory.auto.store import AutoStore

EMBED_MODEL_ENV = "SESSION2MEMORY_EMBED_MODEL"
RERANK_MODEL_ENV = "SESSION2MEMORY_RERANK_MODEL"
MODEL_CACHE_ENV = "SESSION2MEMORY_MODEL_CACHE"
DEFAULT_MODEL_CACHE = "~/.cache/session2memory/models"
DEFAULT_EMBED_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3-int8"
DOCUMENT_CHARS = 1000
RERANK_POOL = 20
RERANK_CHARS = 400
KEYWORD_SLOTS = 5

Hit = tuple[str, str, str | None, str]


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


def model_cache() -> str:
    """Where downloaded models are kept. fastembed's own default is a temporary directory."""
    return os.path.expanduser(os.environ.get(MODEL_CACHE_ENV) or DEFAULT_MODEL_CACHE)


class FastEmbedEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding

        self.model = model
        self._model = TextEmbedding(model, cache_dir=model_cache())
        # The e5 family was trained with these markers and needs them at both ends.
        self._prefixes = ("passage: ", "query: ") if "e5" in model.lower() else ("", "")

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        prefix = self._prefixes[1 if query else 0]
        prepared = [prefix + text for text in texts]
        # Some models encode a question differently from a passage; fastembed exposes
        # that through these two entry points and falls back to plain embedding.
        vectors = (
            self._model.query_embed(prepared) if query else self._model.passage_embed(prepared)
        )
        return [[float(x) for x in vector] for vector in vectors]


class FastEmbedReranker:
    def __init__(self, model: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model, cache_dir=model_cache())

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(x) for x in self._model.rerank(query, list(texts))]


def embed_model_name(env: dict[str, str] | os._Environ[str] | None = None) -> str:
    return (env if env is not None else os.environ).get(EMBED_MODEL_ENV) or DEFAULT_EMBED_MODEL


def load_embedder(model: str | None = None) -> Embedder | None:
    """The local embedding model, or ``None`` when ``fastembed`` is not installed."""
    try:
        return FastEmbedEmbedder(model or embed_model_name())
    except ImportError:
        return None


def load_reranker(model: str | None = None) -> Reranker | None:
    try:
        return FastEmbedReranker(model or os.environ.get(RERANK_MODEL_ENV) or DEFAULT_RERANK_MODEL)
    except ImportError:
        return None


def _normalised(vector: Sequence[float]) -> array[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return array("f", (x / norm for x in vector))


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def _document(text: str) -> str:
    return text[:DOCUMENT_CHARS]


@dataclass
class EmbedReport:
    model: str = ""
    embedded: int = 0
    unchanged: int = 0
    removed: int = 0

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def embed_pending(store: AutoStore, embedder: Embedder, *, batch: int = 32) -> EmbedReport:
    """Give every searchable row a current vector; drop vectors of rows that are gone.

    Only rows whose text changed since their vector was made are embedded again, so a
    nightly run embeds what that night's curation produced.
    """
    report = EmbedReport(model=embedder.model)
    rows = {ref: _document(text) for _, ref, _, text in store.search_rows(reader="coding")}
    known = store.vector_digests(embedder.model)
    todo = [(ref, text) for ref, text in rows.items() if known.get(ref) != _digest(text)]
    report.unchanged = len(rows) - len(todo)
    for start in range(0, len(todo), batch):
        chunk = todo[start : start + batch]
        vectors = embedder.embed([text for _, text in chunk])
        store.put_vectors(
            embedder.model,
            [
                (ref, _digest(text), _normalised(vector).tobytes())
                for (ref, text), vector in zip(chunk, vectors, strict=True)
            ],
        )
        report.embedded += len(chunk)
    report.removed = store.drop_vectors(embedder.model, keep=set(rows))
    return report


def _similarities(query: array[float], vectors: dict[str, bytes]) -> dict[str, float]:
    try:
        import numpy

        refs = list(vectors)
        matrix = numpy.frombuffer(b"".join(vectors[ref] for ref in refs), dtype=numpy.float32)
        scores = matrix.reshape(len(refs), -1) @ numpy.frombuffer(query.tobytes(), numpy.float32)
        return dict(zip(refs, (float(score) for score in scores), strict=True))
    except ImportError:
        result: dict[str, float] = {}
        for ref, blob in vectors.items():
            vector = array("f")
            vector.frombytes(blob)
            result[ref] = sum(a * b for a, b in zip(query, vector, strict=True))
        return result


def semantic_search(
    store: AutoStore,
    query: str,
    *,
    embedder: Embedder,
    reranker: Reranker | None = None,
    project: str | None = None,
    reader: Reader = "coding",
    limit: int = 8,
    pool: int = RERANK_POOL,
) -> list[Hit] | None:
    """Rows ranked by meaning, with keyword hits among the candidates; ``None`` without vectors.

    ``reader`` and ``project`` narrow the rows before anything is compared, exactly as in
    keyword search: a vector of a row the reader may not see is never looked at.
    """
    if not query.strip():
        return []
    rows = {
        ref: (kind, ref, row_project, text)
        for kind, ref, row_project, text in store.search_rows(project=project, reader=reader)
    }
    vectors = store.vectors(embedder.model, set(rows))
    if not vectors:
        return None
    wanted = _normalised(embedder.embed([query], query=True)[0])
    similar = _similarities(wanted, vectors)
    size = max(pool, limit)
    by_meaning = sorted(similar, key=lambda ref: -similar[ref])
    by_words = [
        ref
        for _, ref, _, _ in store.search(query, project=project, reader=reader, limit=KEYWORD_SLOTS)
    ]
    # Meaning leads. Measured on real memory, merging the two rankings as equals pushed
    # the right row down, so keyword hits only make sure an exact name or id that the
    # vectors missed is among the candidates the reranker reads.
    # Every selected keyword hit stays; only other candidates give up their place. A row
    # written after the last embed step has no vector yet and counts by its words alone.
    chosen = [ref for ref in by_words if ref in rows]
    room = size - len(chosen)
    head = []
    for ref in by_meaning:
        if ref in chosen:
            head.append(ref)
        elif room > 0:
            head.append(ref)
            room -= 1
    head += [ref for ref in chosen if ref not in similar]
    if reranker is None:
        # Nothing will compare them with the rest: rows the vectors cannot rank at all go
        # first, since shared words are the only evidence there is for them.
        unranked = [ref for ref in head if ref not in similar]
        head = [*unranked, *(ref for ref in head if ref in similar)]
    if reranker is not None and head:
        scores = reranker.score(query, [rows[ref][3][:RERANK_CHARS] for ref in head])
        ranked = sorted(zip(head, scores, strict=True), key=lambda pair: -pair[1])
        head = [ref for ref, _ in ranked]
    ordered = [*head, *(ref for ref in by_meaning if ref not in set(head))]
    return [rows[ref] for ref in ordered[:limit]]


__all__ = [
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_RERANK_MODEL",
    "EmbedReport",
    "Embedder",
    "Reranker",
    "embed_pending",
    "load_embedder",
    "load_reranker",
    "semantic_search",
]
