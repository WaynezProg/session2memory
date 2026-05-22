import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from session2memory.models import (
    EvidencePointer,
    MemoryCandidate,
    MemoryKind,
    Role,
    SessionMessage,
    SessionRecord,
    WorkspaceIdentity,
)
from session2memory.pipeline import run_pipeline
from session2memory.writer import write_output


def candidate(
    kind: MemoryKind,
    text: str,
    workspace_id: str,
    *,
    source_path: Path = Path("/tmp/raw/session.jsonl"),
    message_start: int = 2,
    message_end: int = 2,
    digest: str = "sha256:abc",
    workspace_path: Path | None = Path("/tmp/repo"),
    durable: bool = True,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        text=text,
        workspace_id=workspace_id,
        evidence=EvidencePointer(
            tool="codex",
            session_id="s1",
            source_path=source_path,
            message_start=message_start,
            message_end=message_end,
            workspace_path=workspace_path,
            digest=digest,
        ),
        durable=durable,
    )


def workspace(workspace_id: str) -> WorkspaceIdentity:
    return WorkspaceIdentity(
        workspace_id=workspace_id,
        canonical_path=Path("/tmp/repo"),
        repo_root=Path("/tmp/repo"),
        opened_cwd=Path("/tmp/repo/sub"),
        tool_workspace_id="tool-ws",
    )


def message(index: int, role: Role, text: str) -> SessionMessage:
    return SessionMessage(
        index=index,
        role=role,
        text=text,
        timestamp=datetime(2026, 5, 22, 1, 2, 3),
        raw_pointer=EvidencePointer(
            tool="codex",
            session_id="s1",
            source_path=Path("/tmp/raw/session.jsonl"),
            message_start=index,
            message_end=index,
            workspace_path=Path("/tmp/repo"),
            digest=f"sha256:{index}",
        ),
    )


def record(messages: list[SessionMessage]) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/raw/session.jsonl"),
        started_at=None,
        updated_at=None,
        cwd=Path("/tmp/repo"),
        repo_root=Path("/tmp/repo"),
        tool_workspace_id="tool-ws",
        messages=messages,
    )


@dataclass(frozen=True)
class FakeAdapter:
    sessions: list[SessionRecord]

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        assert date == "2026-05-22"
        yield from self.sessions


def test_write_output_creates_hks_ingestable_folder_without_raw_markdown(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"

    write_output(
        output_dir=output,
        date="2026-05-22",
        candidates=[candidate("decision", "P0 不用 LLM 摘要。", "repo-123")],
        workspaces={"repo-123": workspace("repo-123")},
        scanned_tools=["codex"],
        source_roots={"codex": Path("/tmp/raw")},
        skipped=[],
        session_count=1,
        message_count=2,
        filtered_count=0,
        dry_run=False,
    )

    daily = (output / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    evidence = json.loads((output / "evidence" / "index.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert "P0 不用 LLM 摘要。" in daily
    assert "P0 不用 LLM 摘要。" in memory
    assert "/tmp/raw/session.jsonl" not in daily
    assert evidence["source_path"] == "/tmp/raw/session.jsonl"
    assert manifest["generator"] == "session2memory"
    assert manifest["version"] == "0.1.0"
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["counts"]["durable_memories"] == 1
    assert manifest["counts"]["evidence_records"] == 1


def test_write_output_removes_stale_managed_files(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    stale_memory = output / "memories" / "old.md"
    stale_daily = output / "daily" / "2026-05-21.md"
    stale_evidence = output / "evidence" / "old.jsonl"
    unmanaged = output / "notes.txt"
    stale_memory.parent.mkdir(parents=True)
    stale_daily.parent.mkdir(parents=True)
    stale_evidence.parent.mkdir(parents=True)
    stale_memory.write_text("obsolete", encoding="utf-8")
    stale_daily.write_text("obsolete", encoding="utf-8")
    stale_evidence.write_text("obsolete", encoding="utf-8")
    unmanaged.write_text("keep", encoding="utf-8")

    write_output(
        output_dir=output,
        date="2026-05-22",
        candidates=[],
        workspaces={},
        scanned_tools=["codex"],
        source_roots={"codex": Path("/tmp/raw")},
        skipped=[],
        session_count=0,
        message_count=0,
        filtered_count=0,
        dry_run=False,
    )

    assert not stale_memory.exists()
    assert not stale_daily.exists()
    assert not stale_evidence.exists()
    assert unmanaged.read_text(encoding="utf-8") == "keep"


def test_write_output_keeps_non_durable_candidates_out_of_workspace_memories(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    durable_candidate = candidate("decision", "durable fact", "repo-123")
    non_durable_candidate = candidate(
        "daily",
        "daily note",
        "repo-123",
        durable=False,
        digest="sha256:daily",
    )
    only_daily_candidate = candidate(
        "daily",
        "other daily note",
        "repo-456",
        durable=False,
        digest="sha256:other-daily",
    )

    write_output(
        output_dir=output,
        date="2026-05-22",
        candidates=[durable_candidate, non_durable_candidate, only_daily_candidate],
        workspaces={"repo-123": workspace("repo-123"), "repo-456": workspace("repo-456")},
        scanned_tools=["codex"],
        source_roots={"codex": Path("/tmp/raw")},
        skipped=[],
        session_count=1,
        message_count=3,
        filtered_count=0,
        dry_run=False,
    )

    daily = (output / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")

    assert "daily note" in daily
    assert "other daily note" in daily
    assert "durable fact" in memory
    assert "daily note" not in memory
    assert not (output / "memories" / "repo-456.md").exists()


def test_evidence_order_is_stable_for_reversed_similar_candidates(tmp_path: Path) -> None:
    first = candidate(
        "decision",
        "same text",
        "repo-123",
        source_path=Path("/tmp/raw/a.jsonl"),
        message_end=3,
        digest="sha256:001",
    )
    second = candidate(
        "decision",
        "same text",
        "repo-123",
        source_path=Path("/tmp/raw/b.jsonl"),
        message_end=4,
        digest="sha256:002",
    )

    paths_by_order: list[list[str]] = []
    for index, candidates in enumerate(([first, second], [second, first]), start=1):
        output = tmp_path / f"run-{index}"
        write_output(
            output_dir=output,
            date="2026-05-22",
            candidates=candidates,
            workspaces={"repo-123": workspace("repo-123")},
            scanned_tools=["codex"],
            source_roots={"codex": Path("/tmp/raw")},
            skipped=[],
            session_count=1,
            message_count=2,
            filtered_count=0,
            dry_run=False,
        )
        evidence_text = (output / "evidence" / "index.jsonl").read_text(encoding="utf-8")
        evidence_rows = [
            json.loads(line)
            for line in evidence_text.splitlines()
        ]
        assert [row["evidence_id"] for row in evidence_rows] == ["e000001", "e000002"]
        paths_by_order.append([row["source_path"] for row in evidence_rows])

    assert paths_by_order[0] == paths_by_order[1]


def test_evidence_order_is_stable_when_only_workspace_path_or_durable_differs(
    tmp_path: Path,
) -> None:
    first = candidate(
        "decision",
        "same text",
        "repo-123",
        workspace_path=Path("/tmp/repo/a"),
        durable=False,
    )
    second = candidate(
        "decision",
        "same text",
        "repo-123",
        workspace_path=Path("/tmp/repo/b"),
        durable=True,
    )

    rows_by_order: list[list[tuple[str | None, bool]]] = []
    for index, candidates in enumerate(([first, second], [second, first]), start=1):
        output = tmp_path / f"run-tie-{index}"
        write_output(
            output_dir=output,
            date="2026-05-22",
            candidates=candidates,
            workspaces={"repo-123": workspace("repo-123")},
            scanned_tools=["codex"],
            source_roots={"codex": Path("/tmp/raw")},
            skipped=[],
            session_count=1,
            message_count=2,
            filtered_count=0,
            dry_run=False,
        )
        evidence_text = (output / "evidence" / "index.jsonl").read_text(encoding="utf-8")
        evidence_rows = [json.loads(line) for line in evidence_text.splitlines()]
        rows_by_order.append(
            [(row["workspace_path"], row["durable"]) for row in evidence_rows]
        )

    assert rows_by_order[0] == rows_by_order[1]


def test_run_pipeline_counts_sessions_and_writes_extracted_candidates(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    adapter = FakeAdapter(
        [
            record(
                [
                    message(1, "system", "system prompt"),
                    message(2, "user", "決定: P0 不用 LLM 摘要。"),
                ]
            )
        ]
    )

    sessions, memories = run_pipeline(
        adapters={"codex": adapter},
        output_dir=output,
        date="2026-05-22",
        source_roots={"codex": Path("/tmp/raw")},
        dry_run=False,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert (sessions, memories) == (1, 1)
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["counts"]["filtered"] == 1
    assert manifest["counts"]["evidence_records"] == 1
