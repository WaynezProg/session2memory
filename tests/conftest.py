from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_local_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never load (or download) the real embedding and reranking models."""
    import session2memory.auto.run as run
    import session2memory.cli as cli

    monkeypatch.setattr(run, "load_embedder", lambda: None)
    monkeypatch.setattr(cli, "load_embedder", lambda: None)
    monkeypatch.setattr(cli, "load_reranker", lambda: None)


@pytest.fixture(autouse=True)
def isolated_sync_backups(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Sync backs up files it changes; tests keep those backups out of the real home."""
    monkeypatch.setenv(
        "SESSION2MEMORY_SYNC_BACKUP_DIR", tmp_path_factory.mktemp("sync-backups").as_posix()
    )


@pytest.fixture(autouse=True)
def no_shared_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never read the settings (and key) of the person running them."""
    monkeypatch.setenv("SESSION2MEMORY_ENV_FILE", (tmp_path / "no-settings").as_posix())
    for name in list(os.environ):
        if name.startswith(("SESSION2MEMORY_EMBED_API", "SESSION2MEMORY_RERANK_API")):
            monkeypatch.delenv(name)
