import json
from pathlib import Path

from typer.testing import CliRunner

from session2memory.cli import app


def write_review_fixture(output: Path) -> None:
    (output / "review").mkdir(parents=True)
    (output / "evidence").mkdir(parents=True)
    (output / "daily").mkdir(parents=True)
    (output / "memories").mkdir(parents=True)
    (output / "daily" / "2026-05-22.md").write_text("# 2026-05-22\n", encoding="utf-8")
    (output / "evidence" / "index.jsonl").write_text(
        json.dumps(
            {
                "id": "e000001",
                "evidence_id": "e000001",
                "kind": "decision",
                "workspace_id": "repo-123",
                "source_path": "/tmp/raw/session.jsonl",
                "message_start": 2,
                "message_end": 2,
                "tool": "codex",
                "session_id": "s1",
                "workspace_path": "/tmp/repo",
                "digest": "sha256:abc",
                "durable": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "workspaces": {
                    "repo-123": {
                        "canonical_path": "/tmp/repo",
                        "repo_root": "/tmp/repo",
                        "opened_cwd": "/tmp/repo/sub",
                        "tool_workspace_id": "tool-ws",
                    }
                },
                "output_files": [
                    "daily/2026-05-22.md",
                    "evidence/index.jsonl",
                    "manifest.json",
                    "review/2026-05-22.jsonl",
                ],
                "counts": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "review" / "2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "approved",
                "kind": "decision",
                "text": "Use evidence-backed memory compiler.",
                "workspace_id": "repo-123",
                "evidence_id": "e000001",
                "source": {
                    "tool": "codex",
                    "session_id": "s1",
                    "message_start": 2,
                    "message_end": 2,
                },
                "durable_suggestion": True,
                "review_note": "approved by reviewer",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_promote_approved_review_entries_to_workspace_memories(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)

    result = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert result.output == "date=2026-05-22 reviewed=1 promoted=1\n"
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    review_row = json.loads((output / "review" / "2026-05-22.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert memory.startswith(
        "---\n"
        "hks_type: workspace_memory\n"
        "source_domain: coding_session\n"
        "workspace_id: repo-123\n"
        "generator: session2memory\n"
        "schema_version: 1\n"
        "---\n"
        "# repo-123\n"
    )
    assert "Use evidence-backed memory compiler." in memory
    assert "e000001" in memory
    assert (
        "{workspace_id=repo-123 memory_kind=decision tool=codex "
        "session_id=s1 evidence_id=e000001 lines=2-2"
    ) in memory
    assert "/tmp/raw/session.jsonl" not in memory
    assert review_row["status"] == "promoted"
    assert "memories/repo-123.md" in manifest["output_files"]
    assert manifest["hks"] == {
        "source_type": "session_memory",
        "primary_documents": ["daily/2026-05-22.md"],
        "metadata_fields": ["date", "workspace_id", "tool", "memory_kind"],
    }


def test_review_list_prints_compact_candidate_rows(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)

    result = CliRunner().invoke(
        app,
        ["review", "list", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert (
        result.output
        == "r000001 approved durable decision repo-123 e000001 "
        "source=codex session=s1 lines=2-2 Use evidence-backed memory compiler.\n"
    )


def test_review_list_missing_date_prints_no_rows(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"

    result = CliRunner().invoke(
        app,
        ["review", "list", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert result.output == ""


def test_review_inspect_prints_candidate_and_evidence_preview(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    source_path = tmp_path / "raw" / "session.jsonl"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "s1"}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Decision: use evidence-backed memory compiler.",
                                }
                            ],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    evidence_path = output / "evidence" / "index.jsonl"
    evidence_row = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_row["source_path"] = source_path.as_posix()
    evidence_path.write_text(json.dumps(evidence_row, sort_keys=True) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["review", "inspect", "r000001", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert result.output == (
        "id=r000001 status=approved durable=durable kind=decision "
        "workspace=repo-123 evidence=e000001\n"
        "candidate:\n"
        "Use evidence-backed memory compiler.\n"
        "evidence:\n"
        f"tool=codex session=s1 source={source_path.as_posix()} lines=2-2 "
        "digest=sha256:abc\n"
        "preview:\n"
        "Decision: use evidence-backed memory compiler.\n"
    )


def test_review_approve_updates_status_and_note_without_manual_jsonl_edit(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["status"] = "pending"
    row["review_note"] = ""
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review",
            "approve",
            "r000001",
            "--date",
            "2026-05-22",
            "--output",
            str(output),
            "--note",
            "keep this",
        ],
    )

    assert result.exit_code == 0
    assert result.output == "date=2026-05-22 id=r000001 status=approved\n"
    updated = json.loads(review_path.read_text(encoding="utf-8"))
    assert updated["status"] == "approved"
    assert updated["review_note"] == "keep this"
    assert updated["durable_suggestion"] is True


def test_review_reject_updates_status_and_note_without_promoting(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)

    result = CliRunner().invoke(
        app,
        [
            "review",
            "reject",
            "r000001",
            "--date",
            "2026-05-22",
            "--output",
            str(output),
            "--note",
            "too local",
        ],
    )

    assert result.exit_code == 0
    assert result.output == "date=2026-05-22 id=r000001 status=rejected\n"
    review_row = json.loads((output / "review" / "2026-05-22.jsonl").read_text(encoding="utf-8"))
    assert review_row["status"] == "rejected"
    assert review_row["review_note"] == "too local"

    promote_result = CliRunner().invoke(
        app,
        ["review", "promote", "--date", "2026-05-22", "--output", str(output)],
    )
    assert promote_result.exit_code == 0
    assert promote_result.output == "date=2026-05-22 reviewed=1 promoted=0\n"
    assert not list((output / "memories").glob("*.md"))


def test_review_approve_can_mark_candidate_as_durable_for_promotion(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["status"] = "pending"
    row["durable_suggestion"] = False
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    approved = CliRunner().invoke(
        app,
        [
            "review",
            "approve",
            "r000001",
            "--date",
            "2026-05-22",
            "--output",
            str(output),
            "--durable",
        ],
    )
    promoted = CliRunner().invoke(
        app,
        ["review", "promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert approved.exit_code == 0
    assert promoted.exit_code == 0
    assert promoted.output == "date=2026-05-22 reviewed=1 promoted=1\n"
    review_row = json.loads(review_path.read_text(encoding="utf-8"))
    assert review_row["durable_suggestion"] is True
    assert review_row["status"] == "promoted"


def test_review_action_reports_unknown_review_id(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)

    result = CliRunner().invoke(
        app,
        [
            "review",
            "approve",
            "missing",
            "--date",
            "2026-05-22",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "Review id not found: missing" in result.output


def test_promote_pending_review_entries_does_not_write_memories(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["status"] = "pending"
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert result.output == "date=2026-05-22 reviewed=1 promoted=0\n"
    assert not list((output / "memories").glob("*.md"))


def test_promote_approved_non_durable_suggestion_stays_out_of_memories(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["kind"] = "verification"
    row["durable_suggestion"] = False
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert result.output == "date=2026-05-22 reviewed=1 promoted=0\n"
    assert not list((output / "memories").glob("*.md"))


def test_promote_does_not_write_workspace_paths_into_memory_markdown(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    raw_session_store = "/tmp/raw/codex/2026/05/22"
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workspaces"]["repo-123"] = {
        "canonical_path": raw_session_store,
        "repo_root": None,
        "opened_cwd": None,
        "tool_workspace_id": None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert result.exit_code == 0
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    assert raw_session_store not in memory
    assert "canonical_path" not in memory
    assert "repo_root" not in memory
    assert "opened_cwd" not in memory


def test_promote_same_workspace_same_evidence_id_on_different_dates(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    first = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )
    assert first.exit_code == 0

    review_path = output / "review" / "2026-05-23.jsonl"
    review_path.write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "approved",
                "kind": "decision",
                "text": "Second day durable memory.",
                "workspace_id": "repo-123",
                "evidence_id": "e000001",
                "durable_suggestion": True,
                "review_note": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    second = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-23", "--output", str(output)],
    )

    assert second.exit_code == 0
    assert second.output == "date=2026-05-23 reviewed=1 promoted=1\n"
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    assert "Use evidence-backed memory compiler." in memory
    assert "Second day durable memory." in memory


def test_promote_same_date_reimport_reordered_review_ids_do_not_drop_memory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    first = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )
    assert first.exit_code == 0

    review_path = output / "review" / "2026-05-22.jsonl"
    review_path.write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "approved",
                "kind": "decision",
                "text": "A memory inserted before the original candidate.",
                "workspace_id": "repo-123",
                "evidence_id": "e000001",
                "durable_suggestion": True,
                "review_note": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    second = CliRunner().invoke(
        app,
        ["promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert second.exit_code == 0
    assert second.output == "date=2026-05-22 reviewed=1 promoted=1\n"
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    assert "Use evidence-backed memory compiler." in memory
    assert "A memory inserted before the original candidate." in memory


def test_promote_exact_duplicate_repromotion_does_not_increment_durable_count(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)
    first = CliRunner().invoke(
        app,
        ["review", "promote", "--date", "2026-05-22", "--output", str(output)],
    )
    assert first.exit_code == 0
    assert first.output == "date=2026-05-22 reviewed=1 promoted=1\n"

    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["status"] = "approved"
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    second = CliRunner().invoke(
        app,
        ["review", "promote", "--date", "2026-05-22", "--output", str(output)],
    )

    assert second.exit_code == 0
    assert second.output == "date=2026-05-22 reviewed=1 promoted=0\n"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    assert manifest["counts"]["durable_memories"] == 1
    assert memory.count("Use evidence-backed memory compiler.") == 1
