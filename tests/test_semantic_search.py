"""Meaning-based search with stand-in models: what is embedded, ranked and withheld."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from test_auto_pipeline import (
    ALPHA,
    NOW,
    FakeRunner,
    Machine,
    remember,
    seed,
)
from test_auto_pipeline import machine as machine  # noqa: F401 - pytest fixture
from test_auto_pipeline import store as store  # noqa: F401 - pytest fixture
from typer.testing import CliRunner

import session2memory.cli as cli_module
from session2memory.auto.run import run_auto
from session2memory.auto.semantic import embed_pending, semantic_search
from session2memory.auto.store import AutoStore
from session2memory.cli import app

# Words that mean the same thing share a dimension: what a real model learns.
CONCEPTS = (
    ("備援", "fallback", "後備"),
    ("額度", "quota", "用量上限"),
    ("部署", "deploy", "上線"),
    ("發票", "invoice"),
)


class ConceptEmbedder:
    model = "test/concepts"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float(any(word in text.lower() for word in group)) for group in CONCEPTS] + [0.01]
            for text in texts
        ]


class PreferWord:
    """A reranker that puts rows containing one word first."""

    def __init__(self, word: str) -> None:
        self.word = word
        self.seen: list[str] = []

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        self.seen = list(texts)
        return [1.0 if self.word in text else 0.0 for text in texts]


def refs(hits: list[tuple[str, str, str | None, str]] | None) -> list[str]:
    assert hits is not None
    return [ref for _, ref, _, _ in hits]


def test_a_question_sharing_no_word_with_the_memory_still_finds_it(store: AutoStore) -> None:
    quota = remember(store, "Antigravity 用量上限到了不回錯誤，後備模型不會啟動")
    remember(store, "上線前要先合併 main")
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)

    assert store.search("quota 用完 fallback 沒觸發") == [], "no shared word: keywords find nothing"
    hits = semantic_search(store, "quota 用完 fallback 沒觸發", embedder=embedder)

    assert refs(hits)[0] == quota


def test_only_changed_rows_are_embedded_again_and_gone_rows_lose_their_vector(
    store: AutoStore,
) -> None:
    first = remember(store, "部署前先跑測試")
    second = remember(store, "發票以明細為單位")
    session = seed(store, "s1", minute=1)
    store.mark_curated(session, 1, title="部署", summary="上線流程整理")
    embedder = ConceptEmbedder()

    report = embed_pending(store, embedder)
    assert (report.embedded, report.unchanged, report.removed) == (3, 0, 0)
    again = embed_pending(store, embedder)
    assert (again.embedded, again.unchanged) == (0, 3)

    store.set_memory_status(second, "retired", reason="old", run_id=None, evidence=[])
    store.mark_curated(session, 1, title="部署", summary="上線流程改成兩階段")
    changed = embed_pending(store, embedder)

    assert (changed.embedded, changed.unchanged, changed.removed) == (1, 1, 1)
    assert embedder.calls[-1] == ["部署｜上線流程改成兩階段"]
    assert set(store.vector_digests(embedder.model)) == {first, session}


def test_vectors_of_another_model_are_replaced(store: AutoStore) -> None:
    remember(store, "部署前先跑測試")
    old = ConceptEmbedder()
    old.model = "test/old"
    embed_pending(store, old)

    report = embed_pending(store, ConceptEmbedder())

    assert report.embedded == 1 and report.removed == 1
    assert store.vector_digests("test/old") == {}


def test_openclaw_reader_never_gets_a_coding_row_by_meaning(store: AutoStore) -> None:
    coding = remember(store, "備援模型在額度用完時要手動切換")
    agent = remember(store, "agent 的後備通道每日巡檢", scope="openclaw")
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)

    everyone = refs(semantic_search(store, "fallback", embedder=embedder))
    agents = refs(semantic_search(store, "fallback", embedder=embedder, reader="openclaw"))

    assert set(everyone) == {coding, agent}
    assert agents == [agent]


def test_project_filter_and_limit_apply(store: AutoStore, tmp_path: Path) -> None:
    other = (tmp_path / "other").as_posix()
    here = remember(store, "部署要先合併 main")
    remember(store, "deploy 需要 VPN", other)
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)

    assert refs(semantic_search(store, "上線", embedder=embedder, project=ALPHA)) == [here]
    assert len(refs(semantic_search(store, "上線", embedder=embedder, limit=1))) == 1


def test_without_vectors_the_caller_is_told_to_fall_back(store: AutoStore) -> None:
    remember(store, "部署前先跑測試")
    assert semantic_search(store, "上線", embedder=ConceptEmbedder()) is None
    assert semantic_search(store, "   ", embedder=ConceptEmbedder()) == []


def test_reranker_has_the_last_word_over_the_candidates(store: AutoStore) -> None:
    plain = remember(store, "部署 部署 部署 流程")
    exact = remember(store, "上線前必須確認 migration 已套用")
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)
    reranker = PreferWord("migration")

    hits = semantic_search(store, "部署", embedder=embedder, reranker=reranker)

    assert refs(hits) == [exact, plain]
    assert len(reranker.seen) == 2


def test_embed_step_runs_in_the_pipeline_and_is_skipped_without_the_package(
    machine: Machine,
) -> None:
    remember(machine.store(), "部署前先跑測試")
    runner = FakeRunner()

    skipped = run_auto(machine.config(steps=("embed",)), runner=runner, now=NOW)
    assert "fastembed" in str(skipped["embed"]["skipped"])  # type: ignore[index]

    embedder = ConceptEmbedder()
    done = run_auto(
        machine.config(steps=("embed",), embedder_factory=lambda: embedder), runner=runner, now=NOW
    )

    result = done["embed"]
    assert isinstance(result, dict)
    assert (result["model"], result["embedded"]) == ("test/concepts", 1)
    assert runner.prompts == []


def test_cli_search_uses_meaning_when_vectors_exist_and_keywords_otherwise(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = machine.store()
    target = remember(opened, "Antigravity 用量上限到了不回錯誤，後備模型不會啟動")
    arguments = ["auto", "search", "quota fallback", "--store", machine.store_dir.as_posix()]
    embedder = ConceptEmbedder()
    monkeypatch.setattr(cli_module, "embed_model_name", lambda: embedder.model)

    before = CliRunner().invoke(app, [*arguments, "--mode", "semantic"])
    assert "no match" in before.output and "unavailable" in before.output

    embed_pending(opened, embedder)
    monkeypatch.setattr(cli_module, "load_embedder", lambda: embedder)
    found = CliRunner().invoke(app, arguments)
    keyword = CliRunner().invoke(app, [*arguments, "--mode", "keyword"])
    bad = CliRunner().invoke(app, [*arguments, "--mode", "fuzzy"])

    assert target in found.output
    assert "no match" in keyword.output
    assert bad.exit_code != 0


def test_an_exact_name_the_vectors_miss_is_still_a_candidate(store: AutoStore) -> None:
    """Ids and ticket numbers mean nothing to an embedding; keywords put them in the pool."""
    for index in range(30):
        remember(store, f"部署注意事項第 {index} 條")
    ticket = remember(store, "VID-2362 的語音失敗依政策不重試")
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)

    hits = semantic_search(
        store,
        "部署時 VID-2362 要不要重試",
        embedder=embedder,
        reranker=PreferWord("VID-2362"),
        limit=3,
    )

    assert refs(hits)[0] == ticket


def test_a_row_found_both_ways_is_returned_once(store: AutoStore) -> None:
    """A keyword hit that is also a late vector candidate must not enter the pool twice."""
    both = [remember(store, f"部署 deploy 上線 檢查表 {index}") for index in range(4)]
    for index in range(20):
        remember(store, f"上線 流程 補充 {index}")
    late = remember(store, "部署 檢查表 例外：發票")
    embedder = ConceptEmbedder()
    embed_pending(store, embedder)

    for limit, pool in ((3, 20), (25, 20), (8, 6)):
        hits = refs(
            semantic_search(
                store,
                "部署 檢查表",
                embedder=embedder,
                reranker=PreferWord("例外"),
                limit=limit,
                pool=pool,
            )
        )
        assert len(hits) == len(set(hits)), (limit, pool)
        assert hits[0] == late and set(both) <= set(
            refs(semantic_search(store, "部署 檢查表", embedder=embedder, limit=30))
        )
