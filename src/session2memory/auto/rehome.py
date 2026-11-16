"""Give back to OpenClaw agents what was registered as coding work by mistake.

A plugin that runs an agent's turn through another command line (muse-cli) gives each
session a folder under ``plugin-state/<plugin>/sessions``. Before those folders counted
as OpenClaw's, such sessions were registered as coding sessions of that folder, and what
was distilled from them became coding memory. This moves the sessions to the agent's
workspace and scope, and hands the memory that only they are evidence for to the agent.

Nothing is deleted and no text changes. A memory that a real coding session also
contributed to stays coding memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from session2memory.auto.scope import plugin_session_owner, within
from session2memory.auto.store import AutoStore, now_iso

REASON = "from an OpenClaw agent's plugin sessions"


@dataclass
class RehomeReport:
    sessions_moved: int = 0
    sessions_without_owner: int = 0
    memories_handed_over: int = 0
    memories_refiled: int = 0
    memories_kept_coding: int = 0
    topics_emptied: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def _plugin_folders(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(
        root
        for root in roots
        if root.name == "sessions" and root.parent.parent.name == "plugin-state"
    )


def rehome(store: AutoStore, roots: tuple[Path, ...], *, dry_run: bool = False) -> RehomeReport:
    """``roots`` are OpenClaw's directories (``openclaw_roots``)."""
    report = RehomeReport()
    folders = _plugin_folders(roots)
    if not folders:
        return report
    connection = store._connection
    owners: dict[str, Path | None] = {}

    def owner(project: str) -> Path | None:
        if project not in owners:
            owners[project] = plugin_session_owner(Path(project), roots)
        return owners[project]

    # Sessions: scope and workspace. Their memory is looked at before they move, while
    # the folder still says which sessions these are.
    sessions = [
        (str(key), str(project), str(scope))
        for key, project, scope in connection.execute(
            "SELECT source_key, project, scope FROM auto_sessions WHERE project IS NOT NULL"
        )
        if within(Path(str(project)), folders)
    ]
    plugin_keys = {key for key, _, _ in sessions}
    session_scope = {
        str(key): str(scope)
        for key, scope in connection.execute("SELECT source_key, scope FROM auto_sessions")
    }
    home_of = {key: owner(project) for key, project, _ in sessions}

    emptied: set[str] = set()
    rows = connection.execute(
        "SELECT memory_id, project, evidence_json, topic_id FROM auto_memories WHERE scope='coding'"
    ).fetchall()
    for memory_id, project, evidence_json, topic_id in rows:
        evidence = [str(item) for item in json.loads(str(evidence_json))]
        from_plugin = [key for key in evidence if key in plugin_keys]
        if not from_plugin and not within(Path(str(project)), folders):
            continue
        # Evidence of any other coding session keeps it coding memory.
        others = [
            key
            for key in evidence
            if key not in plugin_keys and session_scope.get(key) != "openclaw"
        ]
        if others or not from_plugin:
            report.memories_kept_coding += 1
            continue
        homes = [home_of[key] for key in from_plugin if home_of[key] is not None]
        home = max(homes, key=homes.count) if homes else owner(str(project))
        report.memories_handed_over += 1
        if home is not None:
            report.by_agent[home.name] = report.by_agent.get(home.name, 0) + 1
        if dry_run:
            continue
        agent_topic = store.agent_topic(home.as_posix(), []) if home is not None else None
        before = store.get_memory(str(memory_id))
        store.hand_to_agent(str(memory_id), agent_topic, reason=REASON)
        after = store.get_memory(str(memory_id))
        if before and after and before.topic_id != after.topic_id:
            report.memories_refiled += 1
            if topic_id is not None:
                emptied.add(str(topic_id))

    for key, _project, scope in sessions:
        home = home_of[key]
        if home is None:
            report.sessions_without_owner += 1
        if scope == "openclaw" and home is None:
            continue
        report.sessions_moved += 1
        if dry_run:
            continue
        connection.execute(
            "UPDATE auto_sessions SET scope='openclaw', project=COALESCE(?, project), "
            "updated_at=? WHERE source_key=?",
            (home.as_posix() if home is not None else None, now_iso(), key),
        )
    if not dry_run:
        connection.commit()
        report.topics_emptied = sum(store.drop_empty_topic(topic_id) for topic_id in emptied)
    return report
