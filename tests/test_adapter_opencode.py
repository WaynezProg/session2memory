import json
import sqlite3
from pathlib import Path

from session2memory.adapters.opencode import OpenCodeAdapter


def create_opencode_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (
            id text PRIMARY KEY,
            project_id text NOT NULL,
            directory text NOT NULL,
            title text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            workspace_id text
        );
        CREATE TABLE message (
            id text PRIMARY KEY,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        CREATE TABLE part (
            id text PRIMARY KEY,
            message_id text NOT NULL,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        """
    )
    connection.execute(
        "insert into session values (?, ?, ?, ?, ?, ?, ?)",
        ("ses1", "proj1", "/tmp/repo", "OpenCode Import", 1779412154000, 1779412160000, "ws1"),
    )
    connection.execute(
        "insert into message values (?, ?, ?, ?, ?)",
        (
            "msg1",
            "ses1",
            1779412155000,
            1779412155000,
            json.dumps({"role": "user", "time": {"created": 1779412155000}}),
        ),
    )
    connection.execute(
        "insert into part values (?, ?, ?, ?, ?, ?)",
        (
            "part1",
            "msg1",
            "ses1",
            1779412155000,
            1779412155000,
            json.dumps({"type": "text", "text": "驗證：uv run pytest -q passed。"}),
        ),
    )
    connection.commit()
    connection.close()


def test_opencode_adapter_reads_sqlite_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    create_opencode_db(db_path)

    records = list(OpenCodeAdapter(db_path).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "opencode"
    assert records[0].session_id == "ses1"
    assert records[0].cwd == Path("/tmp/repo")
    assert records[0].tool_workspace_id == "ws1"
    assert records[0].messages[0].text == "驗證：uv run pytest -q passed。"
    assert records[0].messages[0].raw_pointer.message_start == 1
