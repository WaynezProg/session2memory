from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from session2memory.models import ExtractionSource, MemoryCandidate, MemoryKind, WorkspaceIdentity
from session2memory.review_normalize import normalize_review_text
from session2memory.state.ids import (
    candidate_id_for,
    evidence_id_for,
    memory_entry_id_for,
    review_id_for,
)
from session2memory.state.migrate import migrate_legacy_output


@dataclass(frozen=True)
class StoredCandidate:
    candidate: MemoryCandidate
    candidate_id: str
    evidence_id: str
    review_id: str
    review_status: str
    review_note: str


class StateStore:
    def __init__(self, connection: sqlite3.Connection, *, output_dir: Path | None = None) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._output_dir = output_dir

    @classmethod
    def open(
        cls,
        db_path: Path,
        *,
        output_dir: Path | None = None,
    ) -> StateStore:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        store = cls(connection, output_dir=output_dir)
        store._apply_schema()
        if output_dir is not None:
            migrate_legacy_output(store, output_dir)
        return store

    def close(self) -> None:
        self._connection.close()

    def upsert_workspace(self, workspace: WorkspaceIdentity) -> None:
        self._connection.execute(
            """
            INSERT INTO workspaces (
                workspace_id, canonical_path, repo_root, opened_cwd, tool_workspace_id
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                canonical_path=excluded.canonical_path,
                repo_root=excluded.repo_root,
                opened_cwd=excluded.opened_cwd,
                tool_workspace_id=excluded.tool_workspace_id
            """,
            (
                workspace.workspace_id,
                workspace.canonical_path.as_posix(),
                workspace.repo_root.as_posix() if workspace.repo_root else None,
                workspace.opened_cwd.as_posix() if workspace.opened_cwd else None,
                workspace.tool_workspace_id,
            ),
        )
        self._connection.commit()

    def upsert_source_file(
        self,
        *,
        tool: str,
        path: str,
        digest: str,
        mtime_ns: int,
    ) -> bool:
        """Return True when digest changed (needs re-import)."""
        row = self.get_source_file(tool=tool, path=path)
        now = datetime.now(UTC).isoformat()
        if row is None:
            self._connection.execute(
                """
                INSERT INTO source_files (tool, path, digest, mtime_ns, last_imported_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tool, path, digest, mtime_ns, now),
            )
            self._connection.commit()
            return True
        if row["digest"] == digest and int(row["mtime_ns"]) == mtime_ns:
            return False
        self._connection.execute(
            """
            UPDATE source_files
            SET digest=?, mtime_ns=?, last_imported_at=?
            WHERE tool=? AND path=?
            """,
            (digest, mtime_ns, now, tool, path),
        )
        self._connection.commit()
        return True

    def get_source_file(self, *, tool: str, path: str) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            "SELECT * FROM source_files WHERE tool=? AND path=?",
            (tool, path),
        )
        row = cursor.fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def upsert_candidate(
        self,
        *,
        import_date: str,
        candidate: MemoryCandidate,
    ) -> StoredCandidate:
        normalized = normalize_review_text(candidate.text)
        evidence_id = evidence_id_for(
            tool=candidate.evidence.tool,
            session_id=candidate.evidence.session_id,
            start=candidate.evidence.message_start,
            end=candidate.evidence.message_end,
            digest=candidate.evidence.digest,
        )
        candidate_id = candidate_id_for(
            workspace_id=candidate.workspace_id,
            kind=candidate.kind,
            text_normalized=normalized,
            message_digest=candidate.evidence.digest,
        )
        review_id = review_id_for(candidate_id=candidate_id)
        existing = self._connection.execute(
            "SELECT review_status, review_note FROM candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        review_status = "pending"
        review_note = ""
        if isinstance(existing, sqlite3.Row):
            review_status = str(existing["review_status"])
            review_note = str(existing["review_note"])
        self._connection.execute(
            """
            INSERT INTO candidates (
                candidate_id, import_date, workspace_id, kind, text, text_normalized,
                extraction, confidence, evidence_quote, durable, evidence_id, review_id,
                review_status, review_note, tool, session_id, source_path,
                message_start, message_end, message_digest, workspace_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                import_date=excluded.import_date,
                text=excluded.text,
                text_normalized=excluded.text_normalized,
                extraction=excluded.extraction,
                confidence=excluded.confidence,
                evidence_quote=excluded.evidence_quote,
                durable=excluded.durable
            """,
            (
                candidate_id,
                import_date,
                candidate.workspace_id,
                candidate.kind,
                candidate.text,
                normalized,
                candidate.extraction,
                candidate.confidence,
                candidate.evidence_quote,
                int(candidate.durable),
                evidence_id,
                review_id,
                review_status,
                review_note,
                candidate.evidence.tool,
                candidate.evidence.session_id,
                candidate.evidence.source_path.as_posix(),
                candidate.evidence.message_start,
                candidate.evidence.message_end,
                candidate.evidence.digest,
                candidate.evidence.workspace_path.as_posix()
                if candidate.evidence.workspace_path
                else None,
            ),
        )
        self._connection.commit()
        return StoredCandidate(
            candidate=candidate,
            candidate_id=candidate_id,
            evidence_id=evidence_id,
            review_id=review_id,
            review_status=review_status,
            review_note=review_note,
        )

    def list_candidates_for_date(self, import_date: str) -> list[StoredCandidate]:
        rows = self._connection.execute(
            "SELECT * FROM candidates WHERE import_date=? ORDER BY evidence_id",
            (import_date,),
        ).fetchall()
        return [self._row_to_stored(row) for row in rows if isinstance(row, sqlite3.Row)]

    def update_review_status(
        self,
        *,
        review_id: str,
        status: str,
        note: str | None = None,
    ) -> None:
        if note is None:
            self._connection.execute(
                "UPDATE candidates SET review_status=? WHERE review_id=?",
                (status, review_id),
            )
        else:
            self._connection.execute(
                "UPDATE candidates SET review_status=?, review_note=? WHERE review_id=?",
                (status, note, review_id),
            )
        self._connection.commit()

    def get_candidate_by_review_id(self, review_id: str) -> StoredCandidate | None:
        row = self._connection.execute(
            "SELECT * FROM candidates WHERE review_id=?",
            (review_id,),
        ).fetchone()
        if not isinstance(row, sqlite3.Row):
            return None
        return self._row_to_stored(row)

    def insert_memory_entry(
        self,
        *,
        workspace_id: str,
        candidate_id: str | None,
        kind: str,
        text: str,
        evidence_id: str,
        review_ref: str,
        tool: str = "unknown",
        session_id: str = "unknown",
        message_start: int | None = None,
        message_end: int | None = None,
        supersedes_id: str | None = None,
    ) -> str:
        memory_entry_id = memory_entry_id_for(
            workspace_id=workspace_id,
            evidence_id=evidence_id,
            review_ref=review_ref,
        )
        promoted_at = datetime.now(UTC).isoformat()
        self._connection.execute(
            """
            INSERT INTO memory_entries (
                memory_entry_id, workspace_id, candidate_id, kind, text,
                evidence_id, review_ref, tool, session_id, message_start, message_end,
                status, supersedes_id, promoted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(memory_entry_id) DO NOTHING
            """,
            (
                memory_entry_id,
                workspace_id,
                candidate_id,
                kind,
                text,
                evidence_id,
                review_ref,
                tool,
                session_id,
                message_start,
                message_end,
                supersedes_id,
                promoted_at,
            ),
        )
        self._connection.commit()
        return memory_entry_id

    def list_active_memory_entries(self, *, workspace_id: str) -> list[sqlite3.Row]:
        rows = self._connection.execute(
            """
            SELECT * FROM memory_entries
            WHERE workspace_id=? AND status='active'
            ORDER BY promoted_at
            """,
            (workspace_id,),
        ).fetchall()
        return [row for row in rows if isinstance(row, sqlite3.Row)]

    def set_memory_status(self, *, memory_entry_id: str, status: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE memory_entries SET status=? WHERE memory_entry_id=?",
            (status, memory_entry_id),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def get_memory_entry(self, *, memory_entry_id: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            "SELECT * FROM memory_entries WHERE memory_entry_id=?",
            (memory_entry_id,),
        ).fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def set_memory_supersedes(self, *, memory_entry_id: str, supersedes_id: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE memory_entries SET supersedes_id=? WHERE memory_entry_id=?",
            (supersedes_id, memory_entry_id),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def list_sync_targets(self, *, workspace_id: str | None = None) -> list[sqlite3.Row]:
        if workspace_id is None:
            rows = self._connection.execute(
                "SELECT * FROM sync_targets ORDER BY workspace_id, target, dest_path"
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT * FROM sync_targets
                WHERE workspace_id=?
                ORDER BY target, dest_path
                """,
                (workspace_id,),
            ).fetchall()
        return [row for row in rows if isinstance(row, sqlite3.Row)]

    def get_sync_hash(self, *, workspace_id: str, target: str, dest_path: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT content_hash FROM sync_targets
            WHERE workspace_id=? AND target=? AND dest_path=?
            """,
            (workspace_id, target, dest_path),
        ).fetchone()
        if not isinstance(row, sqlite3.Row):
            return None
        return str(row["content_hash"])

    def record_sync_hash(
        self,
        *,
        workspace_id: str,
        target: str,
        dest_path: str,
        content_hash: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            """
            INSERT INTO sync_targets (workspace_id, target, dest_path, content_hash, last_synced_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, target, dest_path) DO UPDATE SET
                content_hash=excluded.content_hash,
                last_synced_at=excluded.last_synced_at
            """,
            (workspace_id, target, dest_path, content_hash, now),
        )
        self._connection.commit()

    def workspaces(self) -> dict[str, WorkspaceIdentity]:
        rows = self._connection.execute("SELECT * FROM workspaces").fetchall()
        result: dict[str, WorkspaceIdentity] = {}
        for row in rows:
            if not isinstance(row, sqlite3.Row):
                continue
            result[str(row["workspace_id"])] = WorkspaceIdentity(
                workspace_id=str(row["workspace_id"]),
                canonical_path=Path(str(row["canonical_path"])),
                repo_root=Path(str(row["repo_root"])) if row["repo_root"] else None,
                opened_cwd=Path(str(row["opened_cwd"])) if row["opened_cwd"] else None,
                tool_workspace_id=(
                    str(row["tool_workspace_id"]) if row["tool_workspace_id"] else None
                ),
            )
        return result

    def export_output(
        self,
        *,
        output_dir: Path,
        import_date: str,
        scanned_tools: list[str],
        source_roots: dict[str, Path],
        skipped: list[str],
        session_count: int,
        message_count: int,
        filtered_count: int,
        agentic_os_index: Any | None = None,
    ) -> None:
        from session2memory.writer import write_output

        stored = self.list_candidates_for_date(import_date)
        workspaces = self.workspaces()
        write_output(
            output_dir=output_dir,
            date=import_date,
            stored_candidates=stored,
            workspaces=workspaces,
            scanned_tools=scanned_tools,
            source_roots=source_roots,
            skipped=skipped,
            session_count=session_count,
            message_count=message_count,
            filtered_count=filtered_count,
            dry_run=False,
            agentic_os_index=agentic_os_index,
        )

    def export_memory_entries(self, *, output_dir: Path) -> list[str]:
        rows = self._connection.execute(
            """
            SELECT * FROM memory_entries
            WHERE status='active'
            ORDER BY workspace_id, promoted_at, memory_entry_id
            """
        ).fetchall()
        active_rows = [row for row in rows if isinstance(row, sqlite3.Row)]
        workspace_rows = self._connection.execute(
            "SELECT DISTINCT workspace_id FROM memory_entries ORDER BY workspace_id"
        ).fetchall()
        managed_workspace_ids = [
            str(row["workspace_id"]) for row in workspace_rows if isinstance(row, sqlite3.Row)
        ]
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in active_rows:
            grouped.setdefault(str(row["workspace_id"]), []).append(row)

        memories_dir = output_dir / "memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        for workspace_id in managed_workspace_ids:
            memory_path = memories_dir / f"{workspace_id}.md"
            rows_for_workspace = grouped.get(workspace_id, [])
            if not rows_for_workspace:
                memory_path.unlink(missing_ok=True)
                continue
            memory_path.write_text(
                _render_memory_markdown(workspace_id=workspace_id, rows=rows_for_workspace),
                encoding="utf-8",
            )
        self._update_manifest_memory_files(output_dir=output_dir, active_count=len(active_rows))
        return managed_workspace_ids

    def _update_manifest_memory_files(self, *, output_dir: Path, active_count: int) -> None:
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.is_file():
            return
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return
        output_files = loaded.get("output_files", [])
        if not isinstance(output_files, list):
            output_files = []
        memory_files = sorted(
            path.relative_to(output_dir).as_posix()
            for path in (output_dir / "memories").glob("*.md")
            if path.is_file()
        )
        non_memory = [str(item) for item in output_files if not str(item).startswith("memories/")]
        loaded["output_files"] = sorted({*non_memory, *memory_files})
        counts = loaded.get("counts")
        if isinstance(counts, dict):
            counts["durable_memories"] = active_count
        manifest_path.write_text(
            json.dumps(loaded, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def is_empty(self) -> bool:
        row = self._connection.execute("SELECT COUNT(*) AS c FROM candidates").fetchone()
        if not isinstance(row, sqlite3.Row):
            return True
        return int(row["c"]) == 0

    def _apply_schema(self) -> None:
        schema = resources.files("session2memory.state").joinpath("schema.sql").read_text(
            encoding="utf-8"
        )
        self._connection.executescript(schema)
        self._drop_legacy_evidence_unique_index(schema)
        self._ensure_memory_entry_columns()
        self._connection.commit()

    def _drop_legacy_evidence_unique_index(self, schema: str) -> None:
        """Rebuild candidates if an old DB still has UNIQUE(evidence_id).

        evidence_id is a pointer to a message range and is legitimately shared
        across multiple candidates (e.g. a marker and an LLM candidate from the
        same message), so the column must not be unique.
        """
        index_rows = self._connection.execute("PRAGMA index_list(candidates)").fetchall()
        legacy_index = None
        for row in index_rows:
            if not int(row["unique"]):
                continue
            info = self._connection.execute(
                f'PRAGMA index_info("{row["name"]}")'
            ).fetchall()
            if [str(col["name"]) for col in info] == ["evidence_id"]:
                legacy_index = str(row["name"])
                break
        if legacy_index is None:
            return
        columns = [
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(candidates)").fetchall()
        ]
        column_list = ", ".join(columns)
        self._connection.execute("ALTER TABLE candidates RENAME TO _candidates_legacy")
        self._connection.executescript(schema)
        self._connection.execute(
            f"INSERT INTO candidates ({column_list}) "
            f"SELECT {column_list} FROM _candidates_legacy"
        )
        self._connection.execute("DROP TABLE _candidates_legacy")

    def _ensure_memory_entry_columns(self) -> None:
        rows = self._connection.execute("PRAGMA table_info(memory_entries)").fetchall()
        existing = {str(row["name"]) for row in rows if isinstance(row, sqlite3.Row)}
        columns = {
            "tool": "TEXT",
            "session_id": "TEXT",
            "message_start": "INTEGER",
            "message_end": "INTEGER",
        }
        for name, column_type in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE memory_entries ADD COLUMN {name} {column_type}"
                )

    def _row_to_stored(self, row: sqlite3.Row) -> StoredCandidate:
        from session2memory.models import EvidencePointer

        evidence = EvidencePointer(
            tool=str(row["tool"]),
            session_id=str(row["session_id"]),
            source_path=Path(str(row["source_path"])),
            message_start=int(row["message_start"]),
            message_end=int(row["message_end"]),
            workspace_path=Path(str(row["workspace_path"])) if row["workspace_path"] else None,
            digest=str(row["message_digest"]),
        )
        kind_raw = str(row["kind"])
        extraction_raw = str(row["extraction"])
        kind: MemoryKind = "daily"
        if kind_raw in {
            "decision",
            "completed",
            "pitfall",
            "constraint",
            "verification",
            "daily",
        }:
            kind = kind_raw  # type: ignore[assignment]
        extraction: ExtractionSource = "marker"
        if extraction_raw in {"marker", "llm"}:
            extraction = extraction_raw  # type: ignore[assignment]
        candidate = MemoryCandidate(
            kind=kind,
            text=str(row["text"]),
            workspace_id=str(row["workspace_id"]),
            evidence=evidence,
            durable=bool(row["durable"]),
            extraction=extraction,
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            evidence_quote=str(row["evidence_quote"]) if row["evidence_quote"] else None,
        )
        return StoredCandidate(
            candidate=candidate,
            candidate_id=str(row["candidate_id"]),
            evidence_id=str(row["evidence_id"]),
            review_id=str(row["review_id"]),
            review_status=str(row["review_status"]),
            review_note=str(row["review_note"]),
        )


def _render_memory_markdown(*, workspace_id: str, rows: list[sqlite3.Row]) -> str:
    lines = [
        "---",
        "hks_type: workspace_memory",
        "source_domain: coding_session",
        f"workspace_id: {workspace_id}",
        "generator: session2memory",
        "schema_version: 1",
        "---",
        f"# {workspace_id}",
        "",
    ]
    lines.extend(_memory_entry_line(row) for row in rows)
    return "\n".join(lines).rstrip() + "\n"


def _memory_entry_line(row: sqlite3.Row) -> str:
    metadata = {
        "workspace_id": str(row["workspace_id"]),
        "memory_kind": str(row["kind"]),
        "tool": str(row["tool"] or "unknown"),
        "session_id": str(row["session_id"] or "unknown"),
        "evidence_id": str(row["evidence_id"]),
        "lines": _line_range(row),
        "review": str(row["review_ref"]),
        "memory_id": str(row["memory_entry_id"]),
    }
    supersedes = row["supersedes_id"]
    if supersedes:
        metadata["supersedes"] = str(supersedes)
    meta = " ".join(f"{key}={value}" for key, value in metadata.items())
    return f"- [{row['kind']}] {row['text']} {{{meta}}}"


def _line_range(row: sqlite3.Row) -> str:
    start = row["message_start"]
    end = row["message_end"]
    if start is None:
        return "unknown-unknown"
    if end is None:
        end = start
    return f"{start}-{end}"
