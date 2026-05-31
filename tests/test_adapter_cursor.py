import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from session2memory.adapters.cursor import CursorAdapter, CursorCliAdapter


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _write_cursor_db(path: Path, *, created_at: datetime) -> None:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    meta = {
        "agentId": "cursor-session-1",
        "name": "Cursor GUI Import",
        "createdAt": _millis(created_at),
    }
    connection.execute("insert into meta values (?, ?)", ("0", json.dumps(meta).encode().hex()))
    connection.execute(
        "insert into blobs values (?, ?)",
        (
            "system",
            json.dumps({"role": "system", "content": "internal prompt"}).encode(),
        ),
    )
    connection.execute(
        "insert into blobs values (?, ?)",
        (
            "user",
            json.dumps(
                {
                    "role": "user",
                    "content": (
                        "<user_info>\nWorkspace Path: /tmp/repo\n</user_info>\n"
                        "<user_query>\n決定：Cursor GUI 也要抓。\n</user_query>"
                    ),
                },
                ensure_ascii=False,
            ).encode(),
        ),
    )
    connection.execute(
        "insert into blobs values (?, ?)",
        (
            "assistant",
            json.dumps(
                {
                    "role": "assistant",
                    "content": [
                        {"type": "redacted-reasoning", "data": "hidden"},
                        {"type": "text", "text": "驗證：Cursor store.db 可讀。"},
                    ],
                },
                ensure_ascii=False,
            ).encode(),
        ),
    )
    connection.commit()
    connection.close()


def test_cursor_adapter_reads_store_db_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "cursor" / "workspace" / "cursor-session-1" / "store.db"
    _write_cursor_db(db_path, created_at=datetime(2026, 5, 22, 1, 0, tzinfo=UTC))

    records = list(CursorAdapter(tmp_path / "cursor").iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "cursor"
    assert records[0].session_id == "cursor-session-1"
    assert records[0].cwd == Path("/tmp/repo")
    assert [message.role for message in records[0].messages] == ["user", "assistant"]
    assert records[0].messages[0].text == "決定：Cursor GUI 也要抓。"
    assert records[0].messages[0].raw_pointer.message_start == 2
    assert records[0].messages[1].text == "驗證：Cursor store.db 可讀。"


def test_cursor_adapter_includes_store_db_modified_on_requested_date(tmp_path: Path) -> None:
    db_path = tmp_path / "cursor" / "workspace" / "cursor-session-1" / "store.db"
    _write_cursor_db(db_path, created_at=datetime(2026, 5, 22, 1, 0, tzinfo=UTC))
    modified_at = datetime(2026, 5, 26, 9, 0).timestamp()
    os.utime(db_path, (modified_at, modified_at))

    records = list(CursorAdapter(tmp_path / "cursor").iter_sessions("2026-05-26"))

    assert [record.session_id for record in records] == ["cursor-session-1"]


def test_cursor_cli_adapter_reads_agent_transcripts(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    session_path = (
        root
        / "Users-waynetu-bootstrap"
        / "agent-transcripts"
        / "cursor-cli-1"
        / "cursor-cli-1.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "<user_query>\n坑：Cursor CLI session 要保留。\n"
                                        "</user_query>"
                                    ),
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "message": {
                            "content": [
                                {"type": "redacted-reasoning", "data": "hidden"},
                                {"type": "text", "text": "已記錄 Cursor CLI transcript。"},
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    modified_at = datetime(2026, 5, 22, 9, 0).timestamp()
    os.utime(session_path, (modified_at, modified_at))

    records = list(CursorCliAdapter(root).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "cursor-cli"
    assert records[0].session_id == "cursor-cli-1"
    assert [message.role for message in records[0].messages] == ["user", "assistant"]
    assert records[0].messages[0].text == "坑：Cursor CLI session 要保留。"
    assert records[0].messages[1].text == "已記錄 Cursor CLI transcript。"
