import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app

RAW_TRANSCRIPT_MARKERS = (
    "/tmp/raw/session.jsonl",
    "tests/fixtures/codex",
)


def test_generated_folder_can_be_ingested_by_adjacent_hks(tmp_path: Path) -> None:
    hks_root = Path(os.environ.get("SESSION2MEMORY_HKS_ROOT", "../hks")).resolve()
    if not (hks_root / "pyproject.toml").exists():
        pytest.skip("adjacent HKS checkout is not available")

    output_dir = tmp_path / "session-memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output

    env = os.environ.copy()
    env["HKS_EMBEDDING_MODEL"] = "simple"
    env["HKS_ROOT"] = str(tmp_path / "ks")
    ingest = subprocess.run(
        ["uv", "run", "ks", "ingest", str(output_dir)],
        cwd=hks_root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    source_list = subprocess.run(
        ["uv", "run", "ks", "source", "list"],
        cwd=hks_root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    daily = (output_dir / "daily" / "2026-05-22.md").read_text(encoding="utf-8")

    assert source_list.returncode == 0, source_list.stdout + source_list.stderr
    assert "daily/2026-05-22.md" in source_list.stdout
    for raw_path in RAW_TRANSCRIPT_MARKERS:
        assert raw_path not in source_list.stdout
        assert raw_path not in daily


def test_readme_documents_hks_safe_workflow() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "## Supported P0 Sources" in readme
    assert "Claude Code JSONL under `~/.claude/projects`" in readme
    assert "Codex JSONL under `~/.codex/sessions`" in readme
    assert "Qwen Code JSONL under `~/.qwen/projects`" in readme
    assert "OpenCode SQLite under `~/.local/share/opencode/opencode.db`" in readme
    assert "--tool codex" in readme
    assert "--source-root codex=/Users/waynetu/.codex/sessions" in readme
    assert "uv run ks ingest /path/to/out/session-memory" in readme
    assert "uv run ks update /path/to/out/session-memory" in readme
    assert "Do not ingest `~/.codex/sessions`" in readme
    assert "uv run pytest -q" in readme
    assert "uv run ruff check ." in readme
    assert "uv run mypy src/session2memory" in readme
