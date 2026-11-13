"""JSON views of the store for an operator console (the macOS app in ``apps/mac``).

Every function returns plain data that serialises to JSON, so the console holds no SQL and
no knowledge of the schema: it runs ``session2memory auto console <view>`` and renders
what comes back. The two operations that change something, retiring and restoring a
memory by hand, go through the same store methods as the unattended run and are recorded
as events, with the person's reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from session2memory.auto.store import ACTIVE, AutoStore, MemoryRow, SessionRow

STATUSES = ("active", "superseded", "retired", "stale")
# By hand, a memory can come back only from a state nothing replaced it in.
RESTORABLE = ("retired", "stale")


def _memory(row: MemoryRow) -> dict[str, object]:
    return {
        "id": row.memory_id,
        "project": row.project,
        "scope": row.scope,
        "kind": row.kind,
        "text": row.text,
        "status": row.status,
        "created_at": row.created_at,
        "last_confirmed_at": row.last_confirmed_at,
        "supersedes_id": row.supersedes_id,
        "evidence": list(row.evidence),
    }


def _session(row: SessionRow, *, full: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "key": row.source_key,
        "tool": row.tool,
        "session_id": row.session_id,
        "agent": row.agent,
        "scope": row.scope,
        "project": row.project,
        "title": row.title,
        "summary": row.summary,
        "summary_origin": row.summary_origin,
        "last_activity": row.last_activity,
        "pending": row.digest_version > row.curated_version,
    }
    if full:
        data["digest"] = row.digest
    return data


def overview(store: AutoStore, store_dir: Path) -> dict[str, object]:
    last = store.last_run()
    return {
        "store": store_dir.as_posix(),
        "counts": store.counts(),
        "last_run": None
        if last is None
        else {"run_id": last[0], "finished_at": last[1], "report": last[2]},
    }


def projects(store: AutoStore) -> list[dict[str, object]]:
    """Every workspace that has memory or sessions, the most recently active first."""
    found: dict[str, dict[str, object]] = {}

    def entry(project: str) -> dict[str, object]:
        return found.setdefault(
            project,
            {
                "project": project,
                "name": Path(project).name or project,
                **{status: 0 for status in STATUSES},
                "sessions": 0,
                "sessions_pending": 0,
                "last_activity": None,
            },
        )

    for project, status, count in store._connection.execute(
        "SELECT project, status, count(*) FROM auto_memories GROUP BY project, status"
    ):
        if str(status) in STATUSES:
            entry(str(project))[str(status)] = int(count)
    for project, count, pending, latest in store._connection.execute(
        "SELECT project, count(*), sum(digest_version > curated_version), max(last_activity) "
        "FROM auto_sessions WHERE project IS NOT NULL GROUP BY project"
    ):
        item = entry(str(project))
        item["sessions"] = int(count)
        item["sessions_pending"] = int(pending or 0)
        item["last_activity"] = str(latest) if latest is not None else None
    return sorted(
        found.values(), key=lambda item: (str(item["last_activity"] or ""), str(item["project"]))
    )[::-1]


def memories(store: AutoStore, project: str, *, status: str = ACTIVE) -> list[dict[str, object]]:
    rows = store.memories(project=project, status=None if status == "all" else status, limit=1000)
    return [_memory(row) for row in rows]


def memory(store: AutoStore, memory_id: str) -> dict[str, object] | None:
    """One memory with its history, what it replaced, what replaced it, and its evidence."""
    row = store.get_memory(memory_id)
    if row is None:
        return None
    replaced_by = [
        str(found)
        for (found,) in store._connection.execute(
            "SELECT memory_id FROM auto_memories WHERE supersedes_id=? ORDER BY created_at",
            (memory_id,),
        )
    ]
    sessions = []
    for key in row.evidence:
        session = store.get_session(key)
        sessions.append(
            _session(session)
            if session is not None
            else {"key": key, "tool": key.partition(":")[0], "title": None, "summary": None}
        )
    return {
        **_memory(row),
        "replaced_by": replaced_by,
        "restorable": row.status in RESTORABLE,
        "events": [
            {"op": op, "at": at, "detail": detail} for op, detail, at in store.events(memory_id)
        ],
        "evidence_sessions": sessions,
    }


def sessions(
    store: AutoStore,
    *,
    project: str | None = None,
    tool: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, object]]:
    query = f"SELECT {store._SESSION_COLUMNS} FROM auto_sessions WHERE 1=1"
    args: list[object] = []
    if project is not None:
        query += " AND project=?"
        args.append(project)
    if tool is not None:
        query += " AND tool=?"
        args.append(tool)
    rows = store._connection.execute(
        query + " ORDER BY last_activity DESC, source_key LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return [_session(store._session(row)) for row in rows]


def session(store: AutoStore, source_key: str) -> dict[str, object] | None:
    row = store.get_session(source_key)
    if row is None:
        return None
    fed = [
        _memory(store._memory(found))
        for found in store._connection.execute(
            f"SELECT {store._MEMORY_COLUMNS} FROM auto_memories, json_each(evidence_json) "
            "WHERE json_each.value=? ORDER BY last_confirmed_at DESC",
            (source_key,),
        )
    ]
    return {**_session(row, full=True), "memories": fed}


def runs(store: AutoStore, *, limit: int = 30) -> list[dict[str, object]]:
    found = []
    for run_id, started, finished, report in store._connection.execute(
        "SELECT run_id, started_at, finished_at, report_json FROM auto_runs "
        "ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ):
        found.append(
            {
                "run_id": str(run_id),
                "started_at": str(started),
                "finished_at": str(finished) if finished is not None else None,
                "report": json.loads(str(report)) if report else {},
            }
        )
    return found


def retire(store: AutoStore, memory_id: str, reason: str) -> dict[str, object]:
    row = store.get_memory(memory_id)
    if row is None:
        raise ValueError(f"no memory {memory_id}")
    if row.status != ACTIVE:
        raise ValueError(f"{memory_id} is {row.status}; only an active memory can be retired")
    store.set_memory_status(
        memory_id, "retired", reason=f"by hand: {reason}".strip(), run_id=None, evidence=[]
    )
    updated = memory(store, memory_id)
    assert updated is not None
    return updated


def restore(store: AutoStore, memory_id: str, reason: str) -> dict[str, object]:
    row = store.get_memory(memory_id)
    if row is None:
        raise ValueError(f"no memory {memory_id}")
    if row.status not in RESTORABLE:
        raise ValueError(
            f"{memory_id} is {row.status}; only a retired or stale memory can be restored"
        )
    store.restore_memory(memory_id, reason=f"by hand: {reason}".strip())
    updated = memory(store, memory_id)
    assert updated is not None
    return updated
