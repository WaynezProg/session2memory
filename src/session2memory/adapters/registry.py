from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path

from session2memory.pipeline import PipelineAdapter

_ADAPTER_REGISTRY: dict[str, Callable[[Path], PipelineAdapter]] = {}


def register_adapter(tool: str, factory: Callable[[Path], PipelineAdapter]) -> None:
    if not tool.strip():
        raise ValueError("tool name is required")
    _ADAPTER_REGISTRY[tool] = factory


def load_plugin_adapters() -> dict[str, Callable[[Path], PipelineAdapter]]:
    discovered: dict[str, Callable[[Path], PipelineAdapter]] = dict(_ADAPTER_REGISTRY)
    group = entry_points(group="session2memory.adapters")
    for entry in group:
        factory = entry.load()
        discovered[entry.name] = factory
    return discovered
