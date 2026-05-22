import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from session2memory.adapters.codex import CodexAdapter
from session2memory.models import (
    EvidencePointer,
    MemoryCandidate,
    MemoryKind,
    Role,
    SessionMessage,
    SessionRecord,
    WorkspaceIdentity,
    digest_text,
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
    assert evidence["id"] == "e000001"
    assert evidence["evidence_id"] == "e000001"
    assert manifest["generator"] == "session2memory"
    assert manifest["version"] == "0.1.0"
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["counts"]["durable_memories"] == 1
    assert manifest["counts"]["evidence_records"] == 1
    assert manifest["counts"]["daily_entries"] == 1
    assert manifest["output_files"] == [
        "daily/2026-05-22.md",
        "evidence/index.jsonl",
        "manifest.json",
        "memories/repo-123.md",
    ]


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
    source_root = tmp_path / "raw"
    source_root.mkdir()
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
        source_roots={"codex": source_root},
        dry_run=False,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert (sessions, memories) == (1, 1)
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["counts"]["filtered"] == 1
    assert manifest["counts"]["evidence_records"] == 1


def test_run_pipeline_keeps_completed_and_verification_out_of_workspace_memory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    source_root = tmp_path / "raw"
    source_root.mkdir()
    adapter = FakeAdapter(
        [
            record(
                [
                    message(1, "user", "Decision: durable architecture choice."),
                    message(2, "assistant", "Done: implemented contract fix."),
                    message(3, "assistant", "Verification: uv run pytest -q passed."),
                ]
            )
        ]
    )

    sessions, memories = run_pipeline(
        adapters={"codex": adapter},
        output_dir=output,
        date="2026-05-22",
        source_roots={"codex": source_root},
        dry_run=False,
    )

    daily = (output / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    memory = next((output / "memories").glob("*.md")).read_text(encoding="utf-8")
    evidence_rows = [
        json.loads(line)
        for line in (output / "evidence" / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert (sessions, memories) == (1, 3)
    assert "implemented contract fix" in daily
    assert "uv run pytest -q passed" in daily
    assert "durable architecture choice" in memory
    assert "implemented contract fix" not in memory
    assert "uv run pytest -q passed" not in memory
    assert {(row["kind"], row["durable"]) for row in evidence_rows} == {
        ("decision", True),
        ("completed", False),
        ("verification", False),
    }


def test_run_pipeline_merges_malformed_jsonl_skips_into_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "codex-source"
    source_dir = source_root / "2026" / "05" / "22"
    source_dir.mkdir(parents=True)
    valid_path = source_dir / "valid.jsonl"
    bad_path = source_dir / "bad.jsonl"
    valid_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "valid-1",
                            "timestamp": "2026-05-22T01:00:00Z",
                            "cwd": "/tmp/repo",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Decision: keep valid."}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bad_path.write_text("{bad json\n", encoding="utf-8")
    output = tmp_path / "session-memory"

    sessions, memories = run_pipeline(
        adapters={"codex": CodexAdapter(source_root)},
        output_dir=output,
        date="2026-05-22",
        source_roots={"codex": source_root},
        dry_run=False,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert (sessions, memories) == (1, 1)
    assert manifest["counts"]["sessions"] == 1
    assert any(bad_path.as_posix() in reason for reason in manifest["skipped"])
    assert any("malformed JSON" in reason for reason in manifest["skipped"])


def test_run_pipeline_records_missing_source_root_in_manifest(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    missing_root = tmp_path / "missing-codex"

    sessions, memories = run_pipeline(
        adapters={"codex": FakeAdapter([record([message(1, "user", "Decision: ignore.")])])},
        output_dir=output,
        date="2026-05-22",
        source_roots={"codex": missing_root},
        dry_run=False,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert (sessions, memories) == (0, 0)
    assert any(missing_root.as_posix() in reason for reason in manifest["skipped"])
    assert any("missing source root" in reason for reason in manifest["skipped"])


def test_pipeline_evidence_round_trips_to_raw_source_range_without_markdown_paths(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "codex-source"
    source_path = source_root / "2026" / "05" / "22" / "session.jsonl"
    source_path.parent.mkdir(parents=True)
    source_text = "Decision: evidence digest round trip."
    source_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "codex-evidence",
                            "timestamp": "2026-05-22T01:00:00Z",
                            "cwd": (tmp_path / "repo").as_posix(),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": source_text}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "session-memory"

    run_pipeline(
        adapters={"codex": CodexAdapter(source_root)},
        output_dir=output,
        date="2026-05-22",
        source_roots={"codex": source_root},
        dry_run=False,
    )

    evidence = json.loads(
        (output / "evidence" / "index.jsonl").read_text(encoding="utf-8").strip()
    )
    source_lines = source_path.read_text(encoding="utf-8").splitlines()
    raw_range = source_lines[evidence["message_start"] - 1 : evidence["message_end"]]
    extracted = json.loads(raw_range[0])["payload"]["content"][0]["text"]
    daily = (output / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    memory = next((output / "memories").glob("*.md")).read_text(encoding="utf-8")

    assert evidence["source_path"] == source_path.as_posix()
    assert evidence["message_start"] == 2
    assert evidence["message_end"] == 2
    assert evidence["digest"] == digest_text(extracted)
    assert source_path.as_posix() not in daily
    assert source_path.as_posix() not in memory
