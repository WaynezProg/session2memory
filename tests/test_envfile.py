"""The shared settings file: what is read from it, and when it is left alone."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app
from session2memory.envfile import ENV_FILE_ENV, load_env_file, parse_env


def test_only_this_programs_variables_are_read_and_the_environment_wins(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text(
        "# search settings\n"
        "export SESSION2MEMORY_EMBED_API_URL=https://embed.test/v1/embeddings\n"
        "SESSION2MEMORY_EMBED_API_KEY='k-3'\n"
        'SESSION2MEMORY_EMBED_API_MODEL="text-embedding-3-large"\n'
        "PATH=/somewhere/else\n"
        "a line that is not a setting\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    environ = {ENV_FILE_ENV: path.as_posix(), "SESSION2MEMORY_EMBED_API_MODEL": "set-by-hand"}

    assert load_env_file(environ) is None

    assert environ["SESSION2MEMORY_EMBED_API_URL"] == "https://embed.test/v1/embeddings"
    assert environ["SESSION2MEMORY_EMBED_API_KEY"] == "k-3"
    assert environ["SESSION2MEMORY_EMBED_API_MODEL"] == "set-by-hand"
    assert "PATH" not in environ
    assert parse_env("sk-a-bare-key\n") == {}


def test_a_file_others_can_read_is_ignored_and_a_missing_one_is_silent(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("SESSION2MEMORY_EMBED_API_KEY=k-3\n", encoding="utf-8")
    path.chmod(0o644)
    environ = {ENV_FILE_ENV: path.as_posix()}

    note = load_env_file(environ)

    assert note is not None and "chmod 600" in note and "k-3" not in note
    assert "SESSION2MEMORY_EMBED_API_KEY" not in environ
    assert load_env_file({ENV_FILE_ENV: (tmp_path / "missing").as_posix()}) is None


def test_every_command_reads_the_file_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "env"
    path.write_text("SESSION2MEMORY_EMBED_API_MODEL=from-the-file\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(ENV_FILE_ENV, path.as_posix())
    monkeypatch.delenv("SESSION2MEMORY_EMBED_API_MODEL", raising=False)
    import os

    result = CliRunner().invoke(app, ["auto", "status", "--store", (tmp_path / "s").as_posix()])

    assert result.exit_code == 1, "no store yet"
    assert os.environ["SESSION2MEMORY_EMBED_API_MODEL"] == "from-the-file"
    monkeypatch.delenv("SESSION2MEMORY_EMBED_API_MODEL")
