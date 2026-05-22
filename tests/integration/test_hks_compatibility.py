import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app

MEMORY_TEXT = "use HKS generated docs only."


def _write_codex_session(source_root: Path, raw_transcript_path: Path, cwd: Path) -> None:
    session_dir = source_root / "2026" / "05" / "22"
    session_dir.mkdir(parents=True)
    raw_transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "codex-hks-compat",
                            "timestamp": "2026-05-22T01:00:00Z",
                            "cwd": cwd.as_posix(),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"Decision: {MEMORY_TEXT}",
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


def test_generated_folder_can_be_ingested_by_adjacent_hks(tmp_path: Path) -> None:
    hks_root = Path(os.environ.get("SESSION2MEMORY_HKS_ROOT", "../hks")).resolve()
    if not (hks_root / "pyproject.toml").exists():
        pytest.skip("adjacent HKS checkout is not available")

    source_root = tmp_path / "codex-source"
    raw_transcript_path = source_root / "2026" / "05" / "22" / "session.jsonl"
    _write_codex_session(source_root, raw_transcript_path, tmp_path / "repo")

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
            f"codex={source_root}",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output

    env = os.environ.copy()
    env["HKS_EMBEDDING_MODEL"] = "simple"
    ks_root = tmp_path / "ks"
    env["KS_ROOT"] = str(ks_root)
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

    source_payload = json.loads(source_list.stdout)
    detail = source_payload["trace"]["steps"][0]["detail"]
    sources = detail["sources"]
    relpaths = [source["relpath"] for source in sources]
    formats_by_relpath = {source["relpath"]: source["format"] for source in sources}
    raw_transcript = raw_transcript_path.as_posix()

    assert detail["ks_root"] == ks_root.resolve(strict=False).as_posix()
    assert detail["total_count"] == 2
    assert relpaths[0] == "daily/2026-05-22.md"
    assert relpaths[1].startswith("memories/")
    assert relpaths[1].endswith(".md")
    assert formats_by_relpath[relpaths[0]] == "md"
    assert formats_by_relpath[relpaths[1]] == "md"
    assert MEMORY_TEXT in daily
    assert raw_transcript not in source_list.stdout
    assert raw_transcript not in daily

    query = subprocess.run(
        ["uv", "run", "ks", "query", "HKS generated docs", "--writeback=no"],
        cwd=hks_root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert query.returncode == 0, query.stdout + query.stderr
    query_payload = json.loads(query.stdout)
    query_text = json.dumps(query_payload, ensure_ascii=False)
    assert MEMORY_TEXT in query_text
    assert "daily/2026-05-22.md" in query_text


def test_readme_documents_hks_safe_workflow() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "## Supported P0 Sources" in readme
    assert "Claude Code JSONL under `~/.claude/projects`" in readme
    assert "Codex JSONL under `~/.codex/sessions`" in readme
    assert "Qwen Code JSONL under `~/.qwen/projects`" in readme
    assert "OpenCode SQLite under `~/.local/share/opencode/opencode.db`" in readme
    assert "--tool codex" in readme
    assert "--source-root" in readme
    assert "codex=" in readme
    assert "export KS_ROOT=" in readme
    assert "uv run ks ingest /path/to/out/session-memory" in readme
    assert "uv run ks update /path/to/out/session-memory" in readme
    assert "Do not ingest" in readme
    assert "`~/.codex/sessions`" in readme
    assert "`~/.claude/projects`" in readme
    assert "`~/.qwen/projects`" in readme
    assert "OpenCode" in readme
    assert "SQLite directly" in readme
    assert "uv run pytest -q" in readme
    assert "uv run ruff check ." in readme
    assert "uv run mypy src/session2memory" in readme
