import sqlite3
from pathlib import Path

from session2memory.agentic_os_index import AgenticOsIndex


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, cwd TEXT NOT NULL,
          argv_json TEXT NOT NULL, env_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL, artifact_dir TEXT NOT NULL,
          stdout_log TEXT NOT NULL, stderr_log TEXT NOT NULL,
          started_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          domain TEXT NOT NULL, entity_id TEXT NOT NULL,
          event_type TEXT NOT NULL, message TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO sessions VALUES (
          'aos-1','codex','/tmp/repo','[]','{}','stopped',
          '/tmp/a','/tmp/out.log','/tmp/err.log',
          '2026-05-22T10:00:00+00:00','2026-05-22T10:05:00+00:00'
        );
        INSERT INTO audit_events (domain, entity_id, event_type, message)
        VALUES ('session','aos-1','started','ok');
        """
    )
    conn.commit()
    conn.close()


def test_lookup_by_log_path(tmp_path: Path) -> None:
    db = tmp_path / "agentic-os.db"
    _make_db(db)
    index = AgenticOsIndex.open(db)
    meta = index.lookup_log_path(Path("/tmp/out.log"))
    assert meta is not None
    assert meta.session_id == "aos-1"
    assert meta.agent_id == "codex"
    assert meta.audit_ids == (1,)


def test_enrich_evidence_record(tmp_path: Path) -> None:
    db = tmp_path / "agentic-os.db"
    _make_db(db)
    index = AgenticOsIndex.open(db)
    record = index.enrich_evidence_record(
        {"source_path": "/tmp/out.log"},
        source_path=Path("/tmp/out.log"),
    )
    assert record["agentic_os_session_id"] == "aos-1"
    assert record["agentic_os_audit_ids"] == [1]
