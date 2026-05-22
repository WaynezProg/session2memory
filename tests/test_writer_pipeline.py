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


def candidate(kind: MemoryKind, text: str, workspace_id: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        text=text,
        workspace_id=workspace_id,
        evidence=EvidencePointer(
            tool="codex",
            session_id="s1",
            source_path=Path("/tmp/raw/session.jsonl"),
            message_start=2,
            message_end=2,
            workspace_path=Path("/tmp/repo"),
            digest="sha256:abc",
        ),
        durable=True,
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
