from __future__ import annotations

from pathlib import Path

import pytest

from session2memory.auto import console
from session2memory.auto.store import AutoStore
from session2memory.auto.topics import memory_topic


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("Stock 素材必須核對商用授權與版權。", "rights"),
        ("混音完成後要聽檢音量，對齊字幕。", "audio"),
        ("封面縮到 320px 仍要看清楚標題。", "visual"),
        ("HKS embedding、別名與同義詞檢索需驗收。", "retrieval"),
        ("S2M 記憶 curate 後保留來源。", "memory"),
        ("搬移資料夾後檢查 venv 與 hook 路徑。", "environment"),
        ("審查 PR 後執行 regression 測試。", "review"),
        ("不要刪除原始資料，先做備份。", "data"),
        ("Paperclip 任務按工作流程發布。", "workflow"),
        ("橡皮鴨依照大小排列。", "general"),
        ("spring property", "general"),  # PR must be a word, not a substring.
        ("", "general"),
    ],
)
def test_topics_are_navigation_hints(text: str, topic: str) -> None:
    assert memory_topic(text)["id"] == topic


def test_topics_never_rewrite_memory_or_cross_project_boundaries(tmp_path: Path) -> None:
    store = AutoStore.open(tmp_path / "store.sqlite")
    try:
        ids = []
        for project in ("/work/one", "/work/two"):
            mid, _ = store.add_memory(
                project=project,
                scope="coding",
                kind="constraint",
                text="封面縮到 320px 檢查。",
                evidence=["codex:source"],
                run_id=None,
            )
            ids.append(mid)
        before = list(store._connection.iterdump())
        listed = console.memories(store, "/work/one")
        detail = console.memory(store, ids[0])
        assert [row["id"] for row in listed] == ids[:1]
        assert listed[0]["topic"] == {"id": "visual", "label": "視覺與版面"}
        assert detail is not None and detail["topic"] == listed[0]["topic"]
        assert detail["evidence"] == ["codex:source"]
        assert list(store._connection.iterdump()) == before
    finally:
        store.close()
