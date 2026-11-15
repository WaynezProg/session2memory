from __future__ import annotations

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
