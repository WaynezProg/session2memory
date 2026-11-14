from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.registry import load_plugin_adapters, register_adapter
from session2memory.models import SessionRecord


class _FakeAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        del date
        return iter(())


def test_register_adapter_exposes_factory() -> None:
    register_adapter("fake-tool", lambda root: _FakeAdapter(root))
    factories = load_plugin_adapters()
    assert "fake-tool" in factories
    adapter = factories["fake-tool"](Path("/tmp"))
    assert isinstance(adapter, _FakeAdapter)
