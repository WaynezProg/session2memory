import json
from pathlib import Path

from session2memory.state.store import StateStore


def test_migrate_legacy_output_backfills_candidates(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    (output / "evidence").mkdir(parents=True)
    (output / "review").mkdir(parents=True)
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
    (output / "review" / "2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "pending",
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
                "review_note": "",
                "extraction": "marker",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    store = StateStore.open(output / "session2memory.db", output_dir=output)
    assert (output / "migration_report.json").exists()
    stored = store.list_candidates_for_date("2026-05-22")
    assert len(stored) == 1
    assert stored[0].evidence_id.startswith("e_")
    store.close()


def test_migrate_legacy_output_backfills_promoted_memory_entries(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    (output / "evidence").mkdir(parents=True)
    (output / "review").mkdir(parents=True)
    (output / "memories").mkdir(parents=True)
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
    (output / "review" / "2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "promoted",
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
                "review_note": "",
                "extraction": "marker",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "memories" / "repo-123.md").write_text(
        (
            "---\n"
            "hks_type: workspace_memory\n"
            "workspace_id: repo-123\n"
            "---\n"
            "# repo-123\n\n"
            "- [decision] Use evidence-backed memory compiler. "
            "{workspace_id=repo-123 memory_kind=decision tool=codex "
            "session_id=s1 evidence_id=e000001 lines=2-2 review=2026-05-22/pabc}\n"
        ),
        encoding="utf-8",
    )

    store = StateStore.open(output / "session2memory.db", output_dir=output)

    active = store.list_active_memory_entries(workspace_id="repo-123")
    report = json.loads((output / "migration_report.json").read_text(encoding="utf-8"))
    assert len(active) == 1
    assert active[0]["text"] == "Use evidence-backed memory compiler."
    assert report["migrated_memory_entries"] == 1
    store.close()
