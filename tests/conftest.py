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
