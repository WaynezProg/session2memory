"""Antigravity CLI: reading its conversations and giving it memory at session start.

The transcript rows, the summaries table and the hook payload below have the shapes the
CLI (agy 1.5.6) was observed to write and send on 2026-11-13.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from test_auto_pipeline import LONG_AGO, NOW, Machine
from test_auto_pipeline import machine as machine  # noqa: F401 - pytest fixture
from test_session_start import Bench, claude_memory, hook_arguments
from test_session_start import bench as bench  # noqa: F401 - pytest fixture
from typer.testing import CliRunner

from session2memory.adapters.antigravity import (
    AntigravityAdapter,
    conversations_of,
    transcript_path,
)
from session2memory.auto.run import run_auto
from session2memory.auto.scan import ScanSources, scan
from session2memory.auto.scope import openclaw_roots
from session2memory.auto.store import AutoStore
from session2memory.cli import app
from session2memory.handoff import native_summary
from session2memory.handoff_find import find_antigravity
from session2memory.hook_install import update_settings
from session2memory.session_start import hook_output, previous_sessions, run_hook
from session2memory.source_readers import default_readers
from session2memory.state.store import StateStore

CONVERSATION = "0c4b6472-a747-4394-befa-d28233e3b3af"
REQUEST = "把每日匯出改成可增量重跑，先不要動排程"
REPLY = "已改成冪等寫入；排程沒有動。接下來要補重試測試。"
EXE = Path("/opt/s2m/bin/session2memory")


def rows(request: str = REQUEST, reply: str = REPLY) -> list[dict[str, object]]:
    wrapped = (
        f"<USER_REQUEST>\n{request}\n</USER_REQUEST>\n"
        "<ADDITIONAL_METADATA>\nThe current local time is 2026-11-13.\n</ADDITIONAL_METADATA>\n"
        "<USER_SETTINGS_CHANGE>\nmodel changed\n</USER_SETTINGS_CHANGE>"
    )
    return [
        {
            "step_index": "0",
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-11-13T04:00:00Z",
            "content": wrapped,
        },
        {
            "step_index": "1",
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-11-13T04:00:05Z",
            "content": "先看匯出程式。",
            "tool_calls": "[{'name': 'read_file'}]",
        },
        {
            "step_index": "2",
            "source": "MODEL",
            "type": "GENERIC",
            "status": "DONE",
            "created_at": "2026-11-13T04:00:06Z",
            "content": "Showing lines 1-25 of 33\n1: def export(): ...",
        },
        {
            "step_index": "3",
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-11-13T04:01:00Z",
            "content": reply,
        },
        {"step_index": "4", "source": "SYSTEM", "type": "ERROR_MESSAGE", "error": "quota"},
    ]


def conversation(
    root: Path,
    workspace: Path,
    identifier: str = CONVERSATION,
    *,
    title: str = "匯出增量化",
    modified: str = "2026-11-13 04:01:10.5+00:00",
    content: list[dict[str, object]] | None = None,
) -> Path:
    path = transcript_path(root, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in (content or rows())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    connection = sqlite3.connect(root / "conversation_summaries.db")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS conversation_summaries (conversation_id text PRIMARY KEY, "
        "title text, last_modified_time datetime, workspace_uris text)"
    )
    uri = "file://" + workspace.as_posix().replace(" ", "%20")
    connection.execute(
        "INSERT OR REPLACE INTO conversation_summaries VALUES (?, ?, ?, ?)",
        (identifier, title, modified, json.dumps([uri])),
    )
    connection.commit()
    connection.close()
    return path


def test_conversation_is_read_as_requests_and_replies(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    workspace = tmp_path / "my project"
    path = conversation(root, workspace)

    record = AntigravityAdapter(root).read_file(path)

    assert record.source_key == f"antigravity:{CONVERSATION}"
    assert record.cwd == workspace, "the folder is url-decoded from the index"
    assert [(m.role, m.locator, m.text) for m in record.messages] == [
        ("user", "step:0", REQUEST),
        ("assistant", "step:1", "先看匯出程式。"),
        ("assistant", "step:3", REPLY),
    ], "tool output, errors and the CLI's own metadata blocks are not conversation"
    assert record.started_at is not None and record.started_at.isoformat().startswith(
        "2026-11-13T04:00:00"
    )
    assert record.updated_at is not None and record.updated_at.isoformat().startswith(
        "2026-11-13T04:01:10"
    )
    assert (
        record.home_id == "antigravity@cli" and record.content_digest and record.record_count == 5
    )
    assert native_summary(record).title == "匯出增量化"


def test_a_conversation_missing_from_the_index_is_still_read(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    path = transcript_path(root, "orphan")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows()[0], ensure_ascii=False) + "\n", encoding="utf-8")

    record = AntigravityAdapter(root).read_file(path)

    assert record.cwd is None and [m.text for m in record.messages] == [REQUEST]
    assert native_summary(record).title is None


def test_registry_reader_and_date_import_find_the_conversation(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    path = conversation(root, tmp_path / "work")
    adapter = AntigravityAdapter(root)

    assert [r.session_id for r in adapter.iter_sessions("2026-11-13")] == [CONVERSATION]
    assert list(adapter.iter_sessions("2026-11-01")) == []
    reader = default_readers()["antigravity"]
    assert reader(path, CONVERSATION) is not None and reader(path, "another") is None


def test_conversations_are_found_by_workspace_newest_first(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    conversation(root, here, "old", modified="2026-11-10 00:00:00+00:00")
    conversation(root, here, "new", modified="2026-11-12 00:00:00+00:00")
    conversation(root, elsewhere, "other")

    assert conversations_of(root, here) == ["new", "old"]
    assert conversations_of(tmp_path / "missing", here) == []
    assert {f.record.session_id for f in find_antigravity(root, here, 5)} == {"new", "old"}
    previous = previous_sessions({"antigravity": root}, here, exclude=("antigravity", "new"))
    assert [c.source_key for c in previous] == ["antigravity:old"]


def test_the_nightly_scan_registers_antigravity_conversations(machine: Machine) -> None:
    root = machine.tmp / "antigravity-cli"
    path = conversation(root, machine.work)
    agent_path = conversation(root, machine.agent_work, "from-an-agent")
    stamp = NOW.timestamp() - 3600
    for one in (path, agent_path):
        os.utime(one, (stamp, stamp))
    machine.store_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(machine.db)
    try:
        store = AutoStore(state._connection)
        sources = ScanSources(
            antigravity_root=root, openclaw_roots=openclaw_roots(machine.state_dir)
        )
        report = scan(state.sources, store, sources, since=LONG_AGO)
        mine = store.get_session(f"antigravity:{CONVERSATION}")
        agents = store.get_session("antigravity:from-an-agent")
    finally:
        state.close()

    assert report.by_tool == {"antigravity": 2}
    assert mine is not None and mine.scope == "coding"
    assert REQUEST in (mine.digest or "") and "ADDITIONAL_METADATA" not in (mine.digest or "")
    assert agents is not None and agents.scope == "openclaw", "started inside an OpenClaw workspace"


def test_rescan_picks_up_conversations_older_than_the_last_scan(machine: Machine) -> None:
    """A store that already scanned everything never looks back; a new source needs it once."""
    root = machine.tmp / "antigravity-cli"
    path = conversation(root, machine.work)
    old = (NOW - timedelta(days=5)).timestamp()
    os.utime(path, (old, old))
    sources = ScanSources(antigravity_root=root)
    first = run_auto(
        machine.config(steps=("scan",), sources=ScanSources()), now=NOW - timedelta(days=1)
    )
    assert first["scan"]["sessions_new"] == 0  # type: ignore[index]

    usual = run_auto(machine.config(steps=("scan",), sources=sources), now=NOW)
    again = run_auto(machine.config(steps=("scan",), sources=sources, rescan=True), now=NOW)

    assert usual["scan"]["sessions_new"] == 0, "older than the watermark"  # type: ignore[index]
    assert again["scan"]["by_tool"] == {"antigravity": 1}  # type: ignore[index]


def test_a_named_conversation_is_found_beyond_the_newest_few(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    here = tmp_path / "here"
    for day in range(1, 9):
        conversation(root, here, f"c{day}", modified=f"2026-11-{day:02d} 00:00:00+00:00")

    found = [f.record.session_id for f in find_antigravity(root, here, 200)]

    assert sorted(found) == [f"c{day}" for day in range(1, 9)]
    assert len(list(find_antigravity(root, here, 3))) == 3


def test_a_closing_tag_typed_by_the_person_does_not_end_the_request(tmp_path: Path) -> None:
    root = tmp_path / "antigravity-cli"
    typed = "請修正 parser：輸入可能包含 </USER_REQUEST>，並保留後面的必要限制。"
    path = conversation(root, tmp_path / "w", content=rows(request=typed))

    record = AntigravityAdapter(root).read_file(path)

    assert record.messages[0].text == typed
    bare = [{**rows()[0], "content": "沒有包裝的輸入"}]
    plain = AntigravityAdapter(root).read_file(
        conversation(root, tmp_path / "w", "p", content=bare)
    )
    assert plain.messages[0].text == "沒有包裝的輸入"


def test_a_workspace_that_reaches_the_index_later_is_filled_in(machine: Machine) -> None:
    """Without a project a session is never curated; an unchanged transcript must not keep it so."""
    root = machine.tmp / "antigravity-cli"
    path = transcript_path(root, CONVERSATION)
    path.parent.mkdir(parents=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sources = ScanSources(antigravity_root=root, openclaw_roots=openclaw_roots(machine.state_dir))
    machine.store_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(machine.db)
    try:
        store = AutoStore(state._connection)
        scan(state.sources, store, sources, since=LONG_AGO)
        before = store.get_session(f"antigravity:{CONVERSATION}")
        text = path.read_text(encoding="utf-8")
        conversation(root, machine.work)  # the index catches up; the transcript is the same
        assert path.read_text(encoding="utf-8") == text
        report = scan(state.sources, store, sources, since=LONG_AGO)
        after = store.get_session(f"antigravity:{CONVERSATION}")
        pending = [row.source_key for row in store.pending_sessions()]
    finally:
        state.close()

    assert before is not None and before.project is None
    assert report.sessions_unchanged == 1
    assert after is not None and after.project == machine.work.as_posix()
    assert pending == [f"antigravity:{CONVERSATION}"]


# ---- hook ------------------------------------------------------------------------------


def test_hook_is_a_named_entry_in_antigravitys_hooks_file(tmp_path: Path) -> None:
    settings = tmp_path / "config" / "hooks.json"
    settings.parent.mkdir()
    theirs = {"enabled": False, "Stop": [{"type": "command", "command": "status.sh"}]}
    settings.write_text(json.dumps({"orca-status": theirs}), encoding="utf-8")

    plan = update_settings(settings, agent="antigravity", executable=EXE, apply=True, now=NOW)

    written = json.loads(settings.read_text(encoding="utf-8"))
    assert plan.action == "add" and plan.backup_path is not None
    assert written["orca-status"] == theirs
    assert written["session2memory"] == {
        "SessionStart": [
            {
                "type": "command",
                "command": (
                    f"'{EXE.as_posix()}' session-start-hook --agent antigravity "
                    "--event SessionStart --once"
                ),
                "timeout": 5,
            }
        ]
    }, "a flat list of handlers under a named hook, not Claude's matcher groups"
    again = update_settings(settings, agent="antigravity", executable=EXE, apply=True)
    assert again.action == "none"
    moved = update_settings(settings, agent="antigravity", executable=Path("/new/s2m"), apply=True)
    assert moved.action == "replace"
    removed = update_settings(
        settings, agent="antigravity", executable=EXE, remove=True, apply=True
    )
    assert removed.action == "remove"
    assert json.loads(settings.read_text(encoding="utf-8")) == {"orca-status": theirs}


def test_hook_refuses_a_file_that_is_not_an_object(tmp_path: Path) -> None:
    settings = tmp_path / "hooks.json"
    settings.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON object"):
        update_settings(settings, agent="antigravity", executable=EXE, apply=True)


def test_antigravity_payload_and_answer(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    payload = {
        "conversationId": "ae049bc3",
        "cwd": bench.cwd.as_posix(),
        "workspacePaths": [bench.cwd.as_posix()],
        "hookEventName": "SessionStart",
        "lastUserInput": "hello",
    }

    result = run_hook(bench.config("antigravity"), payload, {}, now=NOW)

    assert result.context is not None and "匯出格式" in result.context
    printed = json.loads(hook_output(result.context, style="antigravity"))
    assert printed == {"injectSteps": [{"systemMessage": {"systemMessage": result.context}}]}
    assert hook_output(None, style="antigravity") == "{}"
    only_paths = {"conversationId": "x", "workspacePaths": [bench.cwd.as_posix()]}
    assert run_hook(bench.config("antigravity"), only_paths, {}, now=NOW).context is not None


def test_cli_answers_antigravity_once_per_conversation(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    arguments = [
        *[("antigravity" if a == "claude" else a) for a in hook_arguments(bench)],
        "--once", "--seen-dir", (bench.tmp / "seen").as_posix(),
    ]  # fmt: skip
    payload = json.dumps({"conversationId": "c1", "cwd": bench.cwd.as_posix()})

    first = json.loads(CliRunner().invoke(app, arguments, input=payload).output)
    second = json.loads(CliRunner().invoke(app, arguments, input=payload).output)

    assert "匯出格式" in first["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert second == {}, "the same conversation opened again gets nothing"
