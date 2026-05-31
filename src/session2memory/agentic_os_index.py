from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgenticOsMeta:
    session_id: str
    agent_id: str
    cwd: str
    stdout_log: str
    stderr_log: str
    audit_ids: tuple[int, ...]


class AgenticOsIndex:
    def __init__(self, *, db_path: Path, sessions: dict[Path, AgenticOsMeta]) -> None:
        self.db_path = db_path
        self._sessions = sessions

    @classmethod
    def open(cls, db_path: Path) -> AgenticOsIndex:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT id, agent_id, cwd, stdout_log, stderr_log
                FROM sessions
                """
            ).fetchall()
        finally:
            connection.close()

        sessions: dict[Path, AgenticOsMeta] = {}
        for row in rows:
            stdout = Path(str(row["stdout_log"])).expanduser()
            stderr = Path(str(row["stderr_log"])).expanduser()
            audit_ids = cls._audit_ids_for_session(db_path, str(row["id"]))
            meta = AgenticOsMeta(
                session_id=str(row["id"]),
                agent_id=str(row["agent_id"]),
                cwd=str(row["cwd"]),
                stdout_log=stdout.as_posix(),
                stderr_log=stderr.as_posix(),
                audit_ids=audit_ids,
            )
            sessions[stdout.resolve(strict=False)] = meta
            sessions[stderr.resolve(strict=False)] = meta
        return cls(db_path=db_path, sessions=sessions)

    @staticmethod
    def _audit_ids_for_session(db_path: Path, session_id: str) -> tuple[int, ...]:
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                """
                SELECT id FROM audit_events
                WHERE domain = 'session' AND entity_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        except sqlite3.Error:
            return ()
        finally:
            connection.close()
        return tuple(int(row[0]) for row in rows)

    def lookup_log_path(self, path: Path) -> AgenticOsMeta | None:
        resolved = path.expanduser().resolve(strict=False)
        return self._sessions.get(resolved)

    def registered_log_paths_for_date(self, date: str) -> set[Path]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT stdout_log, stderr_log, started_at, updated_at
                FROM sessions
                """
            ).fetchall()
        finally:
            connection.close()

        paths: set[Path] = set()
        for row in rows:
            if not _session_touches_date(
                date,
                str(row["started_at"] or ""),
                str(row["updated_at"] or ""),
            ):
                continue
            paths.add(Path(str(row["stdout_log"])).expanduser().resolve(strict=False))
            paths.add(Path(str(row["stderr_log"])).expanduser().resolve(strict=False))
        return paths

    def enrich_evidence_record(
        self,
        record: dict[str, Any],
        *,
        source_path: Path,
    ) -> dict[str, Any]:
        meta = self.lookup_log_path(source_path)
        if meta is None:
            return record
        enriched = dict(record)
        enriched["agentic_os_session_id"] = meta.session_id
        enriched["agentic_os_agent_id"] = meta.agent_id
        if meta.audit_ids:
            enriched["agentic_os_audit_ids"] = list(meta.audit_ids)
        return enriched


def _session_touches_date(date: str, started_at: str, updated_at: str) -> bool:
    return date in started_at or date in updated_at
