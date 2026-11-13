"""The JSON views the operator console reads, and its two write operations."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from test_auto_pipeline import NOW
from typer.testing import CliRunner

from session2memory.auto import console
from session2memory.auto.run import DB_NAME
from session2memory.auto.store import AutoStore
from session2memory.cli import app

ALPHA = "/work/alpha"
BETA = "/work/beta"


def build(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A store with two projects, three sessions, and memory in every state."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store = AutoStore.open(store_dir / DB_NAME)
    ids: dict[str, str] = {}
    try:
        for index, (key, project, tool) in enumerate(
            [
                ("codex:s1", ALPHA, "codex"),
                ("claude:s2", ALPHA, "claude"),
                ("cursor:s3", BETA, "cursor"),
            ]
        ):
            tool_name, _, session_id = key.partition(":")
            store.upsert_session(
                source_key=key,
                tool=tool,
                session_id=session_id,
                agent=tool_name,
                scope="coding",
                project=project,
                title=None,
                digest=f"U: 要求 {index}\nA: 回覆 {index}",
                version_no=1,
                last_activity=(NOW + timedelta(hours=index)).isoformat(timespec="seconds"),
                native_summary=None,
            )
        store.mark_curated("codex:s1", 1, title="匯出格式", summary="改用 Parquet")

        def remember(
            name: str, text: str, evidence: str, project: str = ALPHA, **more: object
        ) -> None:
            ids[name], _ = store.add_memory(
                project=project, scope="coding", kind="decision", text=text,
                evidence=[evidence], run_id="r1", now=NOW, **more,  # type: ignore[arg-type]
            )  # fmt: skip

        remember("old", "匯出格式用 CSV", "codex:s1")
        remember("new", "匯出格式改用 Parquet", "claude:s2", supersedes_id=ids["old"])
        store.set_memory_status(
            ids["old"], "superseded", reason="格式已改", run_id="r1", evidence=["claude:s2"]
        )
        remember("gone", "CI 用 Python 3.11", "codex:s1")
        store.set_memory_status(ids["gone"], "retired", reason="已升級", run_id="r1", evidence=[])
        remember("beta", "Beta 的部署要先跑 migration", "cursor:s3", project=BETA)
        store.start_run("r1")
        store.finish_run("r1", {"scan": {"sessions_new": 3}, "curate": {"llm_calls": 2}})
    finally:
        store.close()
    return store_dir, ids


def call(store_dir: Path, *arguments: str) -> tuple[int, dict[str, object]]:
    result = CliRunner().invoke(
        app, ["auto", "console", *arguments, "--store", store_dir.as_posix()]
    )
    return result.exit_code, json.loads(result.output)


def data(store_dir: Path, *arguments: str) -> object:
    code, payload = call(store_dir, *arguments)
    assert code == 0 and payload["ok"] is True, payload
    return payload["data"]


def test_overview_and_runs(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    overview = data(store_dir, "overview")
    assert overview["store"] == store_dir.as_posix()  # type: ignore[index]
    assert overview["counts"]["memories_active"] == 2  # type: ignore[index]
    assert overview["last_run"]["run_id"] == "r1"  # type: ignore[index]

    [run] = data(store_dir, "runs")  # type: ignore[misc]
    assert run["run_id"] == "r1" and run["finished_at"]
    assert run["report"]["curate"]["llm_calls"] == 2


def test_projects_are_listed_with_counts_most_recent_first(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    listed = data(store_dir, "projects")

    assert [p["project"] for p in listed] == [BETA, ALPHA]  # type: ignore[union-attr]
    alpha = listed[1]  # type: ignore[index]
    assert (alpha["name"], alpha["active"], alpha["superseded"], alpha["retired"]) == (
        "alpha", 1, 1, 1,
    )  # fmt: skip
    assert (alpha["sessions"], alpha["sessions_pending"]) == (2, 1), "one was curated"


def test_memories_by_status_and_one_memory_with_its_history(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    active = data(store_dir, "memories", "--project", ALPHA)
    everything = data(store_dir, "memories", "--project", ALPHA, "--status", "all")
    assert [m["id"] for m in active] == [ids["new"]]  # type: ignore[union-attr]
    assert {m["status"] for m in everything} == {"active", "superseded", "retired"}  # type: ignore[union-attr]

    old = data(store_dir, "memory", "--id", ids["old"])
    assert old["replaced_by"] == [ids["new"]] and old["restorable"] is False  # type: ignore[index]
    assert [e["op"] for e in old["events"]] == ["add", "superseded"]  # type: ignore[index]
    assert old["events"][1]["detail"]["reason"] == "格式已改"  # type: ignore[index]
    [evidence] = old["evidence_sessions"]  # type: ignore[index]
    assert (evidence["key"], evidence["title"]) == ("codex:s1", "匯出格式")
    new = data(store_dir, "memory", "--id", ids["new"])
    assert new["supersedes_id"] == ids["old"]  # type: ignore[index]


def test_sessions_can_be_filtered_and_opened(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    assert [s["key"] for s in data(store_dir, "sessions")] == [  # type: ignore[union-attr]
        "cursor:s3", "claude:s2", "codex:s1",
    ]  # fmt: skip
    only_cursor = data(store_dir, "sessions", "--tool", "cursor")
    assert [s["key"] for s in only_cursor] == ["cursor:s3"]  # type: ignore[union-attr]
    in_alpha = data(store_dir, "sessions", "--project", ALPHA, "--limit", "1", "--offset", "1")
    assert [s["key"] for s in in_alpha] == ["codex:s1"]  # type: ignore[union-attr]
    assert "digest" not in in_alpha[0], "lists stay small"  # type: ignore[index]

    opened = data(store_dir, "session", "--key", "codex:s1")
    assert opened["digest"].startswith("U: 要求 0") and opened["pending"] is False  # type: ignore[index]
    assert {m["id"] for m in opened["memories"]} == {ids["old"], ids["gone"]}  # type: ignore[index]


def test_retire_and_restore_by_hand_are_recorded_with_the_reason(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    retired = data(store_dir, "retire", "--id", ids["new"], "--reason", "寫錯了")
    assert retired["status"] == "retired" and retired["restorable"] is True  # type: ignore[index]
    assert retired["events"][-1]["detail"]["reason"] == "by hand: 寫錯了"  # type: ignore[index]

    restored = data(store_dir, "restore", "--id", ids["new"], "--reason", "其實是對的")
    assert restored["status"] == "active"  # type: ignore[index]
    assert [e["op"] for e in restored["events"]][-2:] == ["retired", "restore"]  # type: ignore[index]
    assert restored["last_confirmed_at"] >= retired["last_confirmed_at"]  # type: ignore[index, operator]


def test_what_cannot_be_done_is_refused_as_json(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    for arguments, message in [
        (("restore", "--id", ids["old"], "--reason", "x"), "only a retired or stale memory"),
        (("restore", "--id", ids["new"], "--reason", "x"), "only a retired or stale memory"),
        (("retire", "--id", ids["gone"], "--reason", "x"), "only an active memory"),
        (("retire", "--id", ids["new"]), "needs --reason"),
        (("retire", "--id", "m_missing", "--reason", "x"), "no memory m_missing"),
        (("memory", "--id", "m_missing"), "not found"),
        (("memories",), "needs --project"),
        (("memories", "--project", ALPHA, "--status", "odd"), "unknown status"),
        (("nonsense",), "unknown view"),
    ]:
        code, payload = call(store_dir, *arguments)
        assert code == 1 and payload["ok"] is False and message in str(payload["error"]), arguments

    code, payload = call(tmp_path / "nowhere", "overview")
    assert code == 1 and "no store" in str(payload["error"])


def test_reading_does_not_change_the_store(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)
    database = store_dir / DB_NAME
    before = database.read_bytes()

    for arguments in (("overview",), ("projects",), ("memory", "--id", ids["old"]), ("runs",)):
        data(store_dir, *arguments)

    assert database.read_bytes() == before
    store = AutoStore.open(database)
    try:
        assert console.memory(store, "m_missing") is None
    finally:
        store.close()
