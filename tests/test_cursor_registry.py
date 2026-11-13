"""Cursor sessions in the source registry, the nightly scan and handoff."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from test_adapter_cursor import _write_cursor_db
from test_auto_pipeline import LONG_AGO, NOW, Machine
from test_auto_pipeline import machine as machine  # noqa: F401 - pytest fixture
from test_session_start import NOW as HOOK_NOW
from test_session_start import Bench, claude_memory, hook_arguments
from test_session_start import bench as bench  # noqa: F401 - pytest fixture
from typer.testing import CliRunner

from session2memory.adapters.cursor import CursorAdapter, CursorCliAdapter
from session2memory.auto.scan import ScanSources, scan
from session2memory.auto.store import AutoStore
from session2memory.cli import app
from session2memory.handoff import native_summary
from session2memory.handoff_find import find_cursor, find_cursor_cli
from session2memory.session_start import hook_output, run_hook
from session2memory.source_readers import default_readers
from session2memory.state.store import StateStore

SESSION = "cursor-session-1"


def store_db(root: Path, workspace: Path, name: str = SESSION) -> Path:
    path = root / "hash" / name / "store.db"
    _write_cursor_db(path, created_at=LONG_AGO)
    connection = sqlite3.connect(path)
    meta = {"agentId": name, "name": "Cursor GUI Import", "createdAt": 0}
    connection.execute("update meta set value=?", (json.dumps(meta).encode().hex(),))
    connection.execute(
        "update blobs set data=? where id='user'",
        (
            json.dumps(
                {
                    "role": "user",
                    "content": (
                        f"<user_info>\nWorkspace Path: {workspace.as_posix()}\n</user_info>\n"
                        "<user_query>\n請把 Cursor 的對話也納入每晚的整理流程。\n</user_query>"
                    ),
                },
                ensure_ascii=False,
            ).encode(),
        ),
    )
    connection.commit()
    connection.close()
    return path


def transcript(root: Path, name: str = "cli-1") -> Path:
    path = root / "project" / "agent-transcripts" / name / f"{name}.jsonl"
    path.parent.mkdir(parents=True)
    lines = [
        {"role": "user", "message": {"content": "<user_query>\n整理 Cursor CLI。\n</user_query>"}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "已整理完成。"}]}},
    ]
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n")
    return path


def test_store_is_identified_by_content_and_blob_ids(tmp_path: Path) -> None:
    path = store_db(tmp_path / "chats", tmp_path / "work")
    adapter = CursorAdapter(tmp_path / "chats")

    record = adapter.read_file(path)
    assert record.source_key == f"cursor:{SESSION}"
    assert [m.locator for m in record.messages] == ["blob:user", "blob:assistant"]
    assert record.record_count == 3 and record.content_digest

    connection = sqlite3.connect(path)
    connection.execute(
        "insert into blobs values ('more', ?)",
        (json.dumps({"role": "user", "content": "再補一句"}).encode(),),
    )
    connection.commit()
    connection.close()
    assert adapter.read_file(path).content_digest != record.content_digest


def test_store_is_not_written_while_reading(tmp_path: Path) -> None:
    path = store_db(tmp_path / "chats", tmp_path / "work")
    os.chmod(path, 0o444)
    assert CursorAdapter(tmp_path / "chats").read_file(path).messages


def test_registry_readers_reopen_cursor_sessions(tmp_path: Path) -> None:
    db = store_db(tmp_path / "chats", tmp_path / "work")
    jsonl = transcript(tmp_path / "projects")
    readers = default_readers()

    assert readers["cursor"](db, SESSION) is not None and readers["cursor"](db, "x") is None
    found = readers["cursor-cli"](jsonl, "cli-1")
    assert found is not None and [m.locator for m in found.messages] == ["msg:0", "msg:1"]
    assert readers["cursor-cli"](jsonl, "x") is None


def test_the_nightly_scan_registers_cursor_sessions(machine: Machine) -> None:
    chats, projects = machine.tmp / "chats", machine.tmp / "projects"
    db = store_db(chats, machine.work)
    jsonl = transcript(projects)
    stamp = NOW.timestamp() - 3600
    for one in (db, jsonl):
        os.utime(one, (stamp, stamp))
    machine.store_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(machine.db)
    try:
        store = AutoStore(state._connection)
        sources = ScanSources(cursor_root=chats, cursor_cli_root=projects)
        report = scan(state.sources, store, sources, since=LONG_AGO)
        again = scan(state.sources, store, sources, since=LONG_AGO)
        chat = store.get_session(f"cursor:{SESSION}")
        cli = store.get_session("cursor-cli:cli-1")
    finally:
        state.close()

    assert report.by_tool == {"cursor": 1, "cursor-cli": 1}
    assert again.sessions_unchanged == 2 and again.by_tool == {}
    assert chat is not None and chat.scope == "coding"
    assert chat.project == machine.work.as_posix()
    assert "納入每晚的整理流程" in (chat.digest or "") and "user_info" not in (chat.digest or "")
    assert chat.last_activity is not None and chat.last_activity.startswith(
        NOW.date().isoformat()[:8]
    ), "the store's own time, not the creation time"
    assert cli is not None and "整理 Cursor CLI" in (cli.digest or "")


def test_a_conversation_in_both_cursor_places_is_one_session(machine: Machine) -> None:
    chats, projects = machine.tmp / "chats", machine.tmp / "projects"
    db = store_db(chats, machine.work)
    same = transcript(projects, SESSION)
    stale = store_db(chats, machine.work, "older")
    transcript(projects, "older")
    transcript(projects, "cli-only")
    stamp = NOW.timestamp() - 3600
    for one in (db, same):
        os.utime(one, (stamp, stamp))
    # This store last changed before the scan window; its transcript changed inside it.
    old = LONG_AGO.timestamp() - 86400
    os.utime(stale, (old, old))
    machine.store_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(machine.db)
    try:
        store = AutoStore(state._connection)
        sources = ScanSources(cursor_root=chats, cursor_cli_root=projects)
        report = scan(state.sources, store, sources, since=LONG_AGO)
        keys = {
            key: store.get_session(key) is not None
            for key in (
                f"cursor:{SESSION}",
                f"cursor-cli:{SESSION}",
                "cursor-cli:older",
                "cursor-cli:cli-only",
            )
        }
    finally:
        state.close()

    assert report.by_tool == {"cursor": 1, "cursor-cli": 1}
    assert keys == {
        f"cursor:{SESSION}": True,
        f"cursor-cli:{SESSION}": False,
        "cursor-cli:older": False,
        "cursor-cli:cli-only": True,
    }


def test_a_store_written_through_its_log_is_still_seen(machine: Machine) -> None:
    chats = machine.tmp / "chats"
    db = store_db(chats, machine.work)
    old = LONG_AGO.timestamp() - 86400
    os.utime(db, (old, old))
    wal = db.with_name("store.db-wal")
    machine.store_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(machine.db)
    try:
        store = AutoStore(state._connection)
        sources = ScanSources(cursor_root=chats)
        before = scan(state.sources, store, sources, since=LONG_AGO)
        wal.write_bytes(b"")
        after = scan(state.sources, store, sources, since=LONG_AGO)
    finally:
        state.close()
        wal.unlink()

    assert before.sessions_new == 0 and after.sessions_new == 1


def test_handoff_finds_cursor_sessions_of_a_workspace(tmp_path: Path) -> None:
    chats, here = tmp_path / "chats", tmp_path / "here"
    store_db(chats, here, "mine")
    store_db(chats, tmp_path / "elsewhere", "other")

    found = list(find_cursor(chats, here, 5))
    assert [f.record.session_id for f in found] == ["mine"]
    assert native_summary(found[0].record).title == "Cursor GUI Import"
    assert list(find_cursor_cli(tmp_path / "projects", here, 5)) == []
    assert (
        CursorCliAdapter(tmp_path / "projects")
        .read_file(transcript(tmp_path / "projects"))
        .content_digest
    )


# ---- memory hook -----------------------------------------------------------------------


def test_hook_is_a_handler_in_cursors_versioned_hooks_file(tmp_path: Path) -> None:
    from datetime import datetime

    from session2memory.hook_install import update_settings

    exe = Path("/opt/s2m/bin/session2memory")
    settings = tmp_path / ".cursor" / "hooks.json"
    settings.parent.mkdir()
    theirs = {
        "version": 1,
        "hooks": {
            "sessionStart": [{"command": "./hooks/session-init.sh"}],
            "stop": [{"command": "./hooks/audit.sh", "loop_limit": 10}],
        },
    }
    settings.write_text(json.dumps(theirs), encoding="utf-8")

    plan = update_settings(
        settings, agent="cursor", executable=exe, apply=True, now=datetime(2026, 11, 13)
    )

    written = json.loads(settings.read_text(encoding="utf-8"))
    assert plan.action == "add" and plan.backup_path is not None
    assert written["version"] == 1 and written["hooks"]["stop"] == theirs["hooks"]["stop"]
    assert written["hooks"]["sessionStart"] == [
        {"command": "./hooks/session-init.sh"},
        {"command": f"'{exe.as_posix()}' session-start-hook --agent cursor", "timeout": 5},
    ], "a flat list of handlers, without Claude's matcher groups or a type"
    assert update_settings(settings, agent="cursor", executable=exe, apply=True).action == "none"
    moved = update_settings(
        settings, agent="cursor", executable=Path("/new/session2memory"), apply=True
    )
    assert moved.action == "replace"
    removed = update_settings(settings, agent="cursor", executable=exe, remove=True, apply=True)
    assert removed.action == "remove"
    assert json.loads(settings.read_text(encoding="utf-8")) == theirs


def test_hook_creates_cursors_file_and_leaves_no_empty_event(tmp_path: Path) -> None:
    from session2memory.hook_install import update_settings

    exe = Path("/opt/s2m/bin/session2memory")
    settings = tmp_path / "hooks.json"
    assert update_settings(settings, agent="cursor", executable=exe).action == "add"
    assert not settings.exists(), "nothing is written without apply"
    update_settings(settings, agent="cursor", executable=exe, apply=True)
    assert json.loads(settings.read_text(encoding="utf-8"))["version"] == 1
    update_settings(settings, agent="cursor", executable=exe, remove=True, apply=True)
    assert json.loads(settings.read_text(encoding="utf-8")) == {"version": 1, "hooks": {}}


def test_cursor_payload_and_answer(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    payload = {
        "session_id": "c0ffee",
        "conversation_id": "c0ffee",
        "hook_event_name": "sessionStart",
        "workspace_roots": [bench.cwd.as_posix()],
        "is_background_agent": False,
        "composer_mode": "agent",
    }

    result = run_hook(bench.config("cursor"), payload, {}, now=HOOK_NOW)

    assert result.context is not None and "匯出格式" in result.context
    assert json.loads(hook_output(result.context, "sessionStart", style="cursor")) == {
        "additional_context": result.context
    }
    assert hook_output(None, style="cursor") == "{}"


def test_cli_answers_cursor_in_its_own_shape(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    arguments = [("cursor" if a == "claude" else a) for a in hook_arguments(bench)]
    payload = json.dumps({"conversation_id": "c1", "workspace_roots": [bench.cwd.as_posix()]})

    answer = json.loads(CliRunner().invoke(app, arguments, input=payload).output)

    assert "匯出格式" in answer["additional_context"]
