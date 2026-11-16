"""Meaning-based search over distilled memory: local embeddings, optionally a reranker.

Keyword search only finds what shares words with the question. Here every active memory
and session summary also gets a vector, kept in the same database; a question is ranked by
meaning, keyword hits are added to the candidates, and a cross-encoder can re-read the best
of them to settle the order.

Memory and session summaries are ranked apart before that. There are some forty session
summaries to one memory and their vectors sit a little closer to any question, so in one
ranking the memory that answers it is rarely among the candidates; each kind brings its
own best, and the cross-encoder, which reads the texts, decides between them.

Everything runs on this machine through the optional ``fastembed`` package (ONNX models,
downloaded once): no text leaves it and a query costs no tokens. Without the package, or
before the first ``embed`` step has run, callers fall back to keyword search.

Two exceptions are opt-in. With ``SESSION2MEMORY_RERANK_API_URL`` set, the candidates of
a question (the question and the first characters of some twenty rows) are sent to that
rerank service instead of being read by the local cross-encoder, which is the slow part of
a search; a failing service leaves the local model. With ``SESSION2MEMORY_EMBED_API_URL``
set, vectors come from an embeddings service instead of the local model: the ``embed``
step then sends every searchable row there, and each search its question. Vectors of two
models cannot be compared, so there is no local model to fall back to: a failing service
leaves keyword search.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlsplit

from session2memory.auto.scope import Reader
from session2memory.auto.store import AutoStore, is_memory, search_tokens

EMBED_MODEL_ENV = "SESSION2MEMORY_EMBED_MODEL"
RERANK_MODEL_ENV = "SESSION2MEMORY_RERANK_MODEL"
MODEL_CACHE_ENV = "SESSION2MEMORY_MODEL_CACHE"
# A rerank service in place of the local cross-encoder (Cohere, Jina, Voyage and others
# share the request: ``{"model", "query", "documents"}``).
RERANK_API_URL_ENV = "SESSION2MEMORY_RERANK_API_URL"
RERANK_API_KEY_ENV = "SESSION2MEMORY_RERANK_API_KEY"
RERANK_API_MODEL_ENV = "SESSION2MEMORY_RERANK_API_MODEL"
RERANK_API_TIMEOUT_ENV = "SESSION2MEMORY_RERANK_API_TIMEOUT"
DEFAULT_RERANK_API_TIMEOUT = 5.0
# An embeddings service in place of the local model (the OpenAI request, which most
# services accept: ``{"model", "input"}``).
EMBED_API_URL_ENV = "SESSION2MEMORY_EMBED_API_URL"
EMBED_API_KEY_ENV = "SESSION2MEMORY_EMBED_API_KEY"
EMBED_API_MODEL_ENV = "SESSION2MEMORY_EMBED_API_MODEL"
EMBED_API_TIMEOUT_ENV = "SESSION2MEMORY_EMBED_API_TIMEOUT"
DEFAULT_EMBED_API_MODEL = "text-embedding-3-small"
DEFAULT_EMBED_API_TIMEOUT = 30.0
# The embed step sends every row; a service limits how fast. It waits and asks again this
# many times when told to slow down or when the service fails on its side.
EMBED_API_RETRIES = 6
EMBED_API_BATCH = 128
RETRIED_STATUS = (429, 500, 502, 503, 504)
DEFAULT_MODEL_CACHE = "~/.cache/session2memory/models"
DEFAULT_EMBED_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3-int8"
DOCUMENT_CHARS = 1000
RERANK_POOL = 20
RERANK_CHARS = 400
KEYWORD_SLOTS = 5
# Memory written after the last ``embed`` step has no vector. The few rows of it that share
# a word with the question are embedded for that question; embedding all of it would cost
# more than the rest of the search.
FRESH_MEMORIES = 8

Hit = tuple[str, str, str | None, str]


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


def model_cache() -> str:
    """Where downloaded models are kept. fastembed's own default is a temporary directory."""
    return os.path.expanduser(os.environ.get(MODEL_CACHE_ENV) or DEFAULT_MODEL_CACHE)


# Models trained with a marker in front of the text need it at both ends; fastembed does
# not add it. ``(document, question)`` by a fragment of the model name.
TASK_PREFIXES = {
    "e5": ("passage: ", "query: "),
    "nomic-embed": ("search_document: ", "search_query: "),
}


def task_prefixes(model: str) -> tuple[str, str]:
    lowered = model.lower()
    for fragment, prefixes in TASK_PREFIXES.items():
        if fragment in lowered:
            return prefixes
    return ("", "")


class FastEmbedEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding

        self.model = model
        self._model = TextEmbedding(model, cache_dir=model_cache())
        self._prefixes = task_prefixes(model)

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


def _service_url(url: str, variable: str) -> str:
    """``url`` when it may carry a key and private text: https, or http to this machine."""
    target = urlsplit(url)
    local = target.hostname in ("localhost", "127.0.0.1", "::1")
    if target.scheme != "https" and not (target.scheme == "http" and local):
        raise ValueError(f"{variable} must be an https URL (http only for localhost)")
    return url


def _post_json(url: str, key: str, body: dict[str, object], timeout: float) -> object:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # the scheme was checked by _service_url
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


class ApiEmbedder:
    """An embeddings service over HTTP (the OpenAI request): texts go out, vectors come back.

    ``model`` is what the vectors are stored under, so vectors of the local model and of a
    service are never compared with each other.
    """

    def __init__(self, url: str, *, key: str = "", model: str, timeout: float) -> None:
        self._url = _service_url(url, EMBED_API_URL_ENV)
        self._key = key
        self._timeout = timeout
        self.model = model
        # Rows per request of the embed step; a request is cheap, a round trip is not.
        self.batch_size = EMBED_API_BATCH
        # How often a refused request is repeated. A search asks once (it has keyword
        # search to fall back to); the embed step raises this for its run.
        self.retries = 0
        self._sleep: Callable[[float], None] = time.sleep

    def _ask(self, texts: Sequence[str]) -> object:
        body: dict[str, object] = {"model": self.model, "input": list(texts)}
        for attempt in range(self.retries + 1):
            try:
                return _post_json(self._url, self._key, body, self._timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRIED_STATUS or attempt == self.retries:
                    raise
                asked = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(asked) if asked else 2.0**attempt
                except ValueError:
                    wait = 2.0**attempt
                self._sleep(min(wait, 60.0))
        raise AssertionError("unreachable")

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        payload = self._ask(texts)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("embeddings service reply has no data list")
        vectors: dict[int, list[float]] = {}
        for item in data:
            vectors[int(item["index"])] = [float(x) for x in item["embedding"]]
        if set(vectors) != set(range(len(texts))):
            raise ValueError("embeddings service did not embed every text")
        return [vectors[index] for index in range(len(texts))]


def _embed_api_url(env: dict[str, str] | os._Environ[str]) -> str:
    return (env.get(EMBED_API_URL_ENV) or "").strip()


def embed_model_name(env: dict[str, str] | os._Environ[str] | None = None) -> str:
    """The model vectors are stored under: the service's when one is configured."""
    source = env if env is not None else os.environ
    if _embed_api_url(source):
        return (source.get(EMBED_API_MODEL_ENV) or "").strip() or DEFAULT_EMBED_API_MODEL
    return source.get(EMBED_MODEL_ENV) or DEFAULT_EMBED_MODEL


def load_embedder(model: str | None = None) -> Embedder | None:
    """The configured embeddings service, else the local model (``None`` without ``fastembed``)."""
    url = _embed_api_url(os.environ)
    if url:
        return ApiEmbedder(
            url,
            key=(os.environ.get(EMBED_API_KEY_ENV) or "").strip(),
            model=model or embed_model_name(),
            timeout=float(os.environ.get(EMBED_API_TIMEOUT_ENV) or DEFAULT_EMBED_API_TIMEOUT),
        )
    try:
        return FastEmbedEmbedder(model or embed_model_name())
    except ImportError:
        return None


class ApiReranker:
    """A rerank service over HTTP: the question and the candidate texts go out, scores come back.

    The request is the one Cohere, Jina and Voyage accept; the reply lists
    ``{"index", "relevance_score"}`` under ``results`` (or ``data``).
    """

    def __init__(self, url: str, *, key: str = "", model: str = "", timeout: float) -> None:
        self._url = _service_url(url, RERANK_API_URL_ENV)
        self._key = key
        self._model = model
        self._timeout = timeout

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        body: dict[str, object] = {"query": query, "documents": list(texts)}
        if self._model:
            body["model"] = self._model
        payload = _post_json(self._url, self._key, body, self._timeout)
        results = payload.get("results", payload.get("data")) if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("rerank service reply has no result list")
        scores: dict[int, float] = {}
        for item in results:
            scores[int(item["index"])] = float(item["relevance_score"])
        if set(scores) != set(range(len(texts))):
            raise ValueError("rerank service did not score every candidate")
        return [scores[index] for index in range(len(texts))]


class FallbackReranker:
    """Asks ``first``; when that fails, loads ``second`` and lets it read the candidates.

    ``failure`` then says why, for the caller to show. The second one is loaded only when
    needed: not loading the local model is what the service is used for.
    """

    def __init__(self, first: Reranker, second: Callable[[], Reranker | None]) -> None:
        self._first = first
        self._second = second
        self.failure: str | None = None

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        try:
            return self._first.score(query, texts)
        except Exception as exc:  # noqa: BLE001 - any failure of the service leaves the local model
            self.failure = f"{type(exc).__name__}: {exc}"
            local = self._second()
            if local is None:
                raise
            return local.score(query, texts)


def load_local_reranker(model: str | None = None) -> Reranker | None:
    try:
        return FastEmbedReranker(model or os.environ.get(RERANK_MODEL_ENV) or DEFAULT_RERANK_MODEL)
    except ImportError:
        return None


def load_reranker(model: str | None = None) -> Reranker | None:
    """The configured rerank service with the local model behind it, else the local model."""
    url = (os.environ.get(RERANK_API_URL_ENV) or "").strip()
    if not url:
        return load_local_reranker(model)
    service = ApiReranker(
        url,
        key=(os.environ.get(RERANK_API_KEY_ENV) or "").strip(),
        model=(os.environ.get(RERANK_API_MODEL_ENV) or "").strip(),
        timeout=float(os.environ.get(RERANK_API_TIMEOUT_ENV) or DEFAULT_RERANK_API_TIMEOUT),
    )
    return FallbackReranker(service, lambda: load_local_reranker(model))


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
    # A service takes more rows per request than the local model, and is asked again when
    # it says to slow down: this run has nothing to fall back to.
    batch = int(getattr(embedder, "batch_size", batch))
    if hasattr(embedder, "retries"):
        embedder.retries = EMBED_API_RETRIES
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


def _without_repeats(refs: Sequence[str], rows: dict[str, Hit]) -> list[str]:
    """``refs`` without session summaries whose text an earlier one already has.

    One session recorded by two tools (the agent's own log and the harness's) is two rows
    with one text; the second would only take a place from something else.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for ref in refs:
        kind, _, _, text = rows[ref]
        if not is_memory(kind):
            if text in seen:
                continue
            seen.add(text)
        kept.append(ref)
    return kept


def _alternating(refs: Sequence[str], rows: dict[str, Hit]) -> list[str]:
    """Memory and session summaries in turn, each kind in the order it came in."""
    memories = [ref for ref in refs if is_memory(rows[ref][0])]
    sessions = [ref for ref in refs if not is_memory(rows[ref][0])]
    mixed: list[str] = []
    for index in range(max(len(memories), len(sessions))):
        mixed += memories[index : index + 1] + sessions[index : index + 1]
    return mixed


def _fresh_memories(query: str, unembedded: Sequence[Hit]) -> list[str]:
    """Refs of the memories without a vector that share most words with the question."""
    tokens = search_tokens(query)
    shared: list[tuple[int, str]] = []
    for kind, ref, _, text in unembedded:
        if not is_memory(kind):
            continue
        lowered = text.lower()
        count = sum(1 for token in tokens if token in lowered)
        if count:
            shared.append((count, ref))
    shared.sort(key=lambda item: -item[0])
    return [ref for _, ref in shared[:FRESH_MEMORIES]]


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
    # Memory newer than the last embed step (a reconcile run on its own rewrites it) has
    # no vector in the store. The rows sharing most words with the question get one here.
    fresh = _fresh_memories(query, [hit for hit in rows.values() if hit[1] not in vectors])
    if fresh:
        made = embedder.embed([_document(rows[ref][3]) for ref in fresh])
        similar.update(
            _similarities(
                wanted,
                {
                    ref: _normalised(vector).tobytes()
                    for ref, vector in zip(fresh, made, strict=True)
                },
            )
        )
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
    # The other places are shared by the two kinds, each filling them with its own best:
    # half for memory, half for session summaries, and what one kind cannot fill goes to
    # the other.
    others = [ref for ref in _without_repeats(by_meaning, rows) if ref not in chosen]
    memories = [ref for ref in others if is_memory(rows[ref][0])]
    sessions = [ref for ref in others if not is_memory(rows[ref][0])]
    room = max(0, size - len(chosen))
    for_memory = min(len(memories), max((room + 1) // 2, room - len(sessions)))
    picked = {*chosen, *memories[:for_memory], *sessions[: room - for_memory]}
    head = [ref for ref in by_meaning if ref in picked]
    head += [ref for ref in chosen if ref not in similar]
    if reranker is None:
        # Nothing will compare them with the rest: rows the vectors cannot rank at all go
        # first, since shared words are the only evidence there is for them. The two kinds
        # then take turns: their similarities are not on one scale (a long summary sits
        # closer to any question than a one-line memory).
        unranked = [ref for ref in head if ref not in similar]
        head = [*unranked, *_alternating([ref for ref in head if ref in similar], rows)]
    if reranker is not None and head:
        scores = reranker.score(query, [rows[ref][3][:RERANK_CHARS] for ref in head])
        ranked = sorted(zip(head, scores, strict=True), key=lambda pair: -pair[1])
        head = [ref for ref, _ in ranked]
    listed = set(head)
    ordered = [*head, *(ref for ref in by_meaning if ref not in listed)]
    return [rows[ref] for ref in _without_repeats(ordered, rows)[:limit]]


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
