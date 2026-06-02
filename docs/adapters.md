# session2memory Adapter Plugins

Register a custom harness adapter when your session format is not built into P0.

## Contract

Implement `PipelineAdapter`:

- `iter_sessions(date: str) -> Iterator[SessionRecord]`
- optional `skipped: list[str]` populated with skip reasons

## Register in Python

```python
from pathlib import Path
from session2memory.adapters.registry import register_adapter

class MyToolAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_sessions(self, date: str):
        ...

register_adapter("mytool", lambda root: MyToolAdapter(root))
```

## Register via entry point

```toml
[project.entry-points."session2memory.adapters"]
mytool = "my_pkg.adapter:factory"
```

`factory` must be `Callable[[Path], PipelineAdapter]`.

## Import

```bash
uv run session2memory import --date 2026-05-22 --tool mytool --output ./out/session-memory
```
