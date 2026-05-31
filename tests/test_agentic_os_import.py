import json
import sqlite3
from pathlib import Path

from session2memory.agentic_os_index import AgenticOsIndex
from session2memory.models import EvidencePointer, MemoryCandidate
from session2memory.workspace import WorkspaceIdentity
from session2memory.writer import write_output


def test_write_output_enriches_evidence_from_agentic_os(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text("line\n", encoding="utf-8")
    db = tmp_path / "agentic-os.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        f"""
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, cwd TEXT NOT NULL,
          argv_json TEXT NOT NULL, env_json TEXT NOT NULL DEFAULT '{{}}',
          status TEXT NOT NULL, artifact_dir TEXT NOT NULL,
          stdout_log TEXT NOT NULL, stderr_log TEXT NOT NULL,
          started_at TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          domain TEXT NOT NULL, entity_id TEXT NOT NULL,
          event_type TEXT NOT NULL, message TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{{}}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO sessions VALUES (
          'aos-1','codex','/tmp/repo','[]','{{}}','stopped',
          '/tmp/a','/tmp/err.log','{log_path.as_posix()}',
          '2026-05-22T10:00:00+00:00','2026-05-22T10:05:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    index = AgenticOsIndex.open(db)
    output = tmp_path / "session-memory"
    pointer = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=log_path,
        message_start=1,
        message_end=1,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )
    candidate = MemoryCandidate(
        kind="decision",
        text="Decision: test",
        workspace_id="repo-123",
        evidence=pointer,
        durable=True,
    )
    workspace = WorkspaceIdentity(
        workspace_id="repo-123",
        canonical_path=Path("/tmp/repo"),
        repo_root=Path("/tmp/repo"),
        opened_cwd=Path("/tmp/repo"),
        tool_workspace_id=None,
    )
    write_output(
        output_dir=output,
        date="2026-05-22",
        candidates=[candidate],
        workspaces={"repo-123": workspace},
        scanned_tools=["codex"],
        source_roots={"codex": Path("/tmp")},
        skipped=[],
        session_count=1,
        message_count=1,
        filtered_count=0,
        dry_run=False,
        agentic_os_index=index,
    )
    evidence = json.loads((output / "evidence" / "index.jsonl").read_text(encoding="utf-8"))
    assert evidence["agentic_os_session_id"] == "aos-1"
