# session2memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個本機 Python CLI，讀取 Claude Code、Codex、Qwen Code、OpenCode session，輸出 HKS 可 ingest 的精煉 memory folder，且每條 durable memory 都能回查 evidence pointer。

**Architecture:** 每個 adapter 只負責把原始 session store 轉成共同 `SessionRecord`。後續 pipeline 統一做 workspace grouping、noise filtering、deterministic extraction、evidence indexing、Markdown/manifest output；raw transcript 不進 HKS output folder。

**Tech Stack:** Python 3.12、`uv`、Typer、pytest、ruff、mypy、standard-library `sqlite3`。

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | package metadata、CLI entrypoint、dev tools config |
| `README.md` | 使用方式、HKS ingest/update workflow、P0 邊界 |
| `src/session2memory/__init__.py` | package version |
| `src/session2memory/cli.py` | Typer CLI surface |
| `src/session2memory/models.py` | `SessionRecord`、`SessionMessage`、`EvidencePointer`、`MemoryCandidate`、digest helpers |
| `src/session2memory/adapters/__init__.py` | adapter registry |
| `src/session2memory/adapters/base.py` | adapter protocol and shared helpers |
| `src/session2memory/adapters/codex.py` | Codex JSONL adapter |
| `src/session2memory/adapters/qwen.py` | Qwen JSONL adapter |
| `src/session2memory/adapters/claude.py` | Claude Code JSONL adapter |
| `src/session2memory/adapters/opencode.py` | OpenCode SQLite adapter |
| `src/session2memory/workspace.py` | canonical cwd、git repo root、workspace id slug |
| `src/session2memory/filtering.py` | deterministic noise filter |
| `src/session2memory/extraction.py` | conservative daily/durable memory extraction |
| `src/session2memory/writer.py` | `daily/`、`memories/`、`evidence/index.jsonl`、`manifest.json` writer |
| `src/session2memory/pipeline.py` | orchestration from adapters to output |
| `tests/fixtures/` | fixture session data for supported tools |
| `tests/test_cli.py` | CLI behavior |
| `tests/test_models.py` | digest and evidence pointer contracts |
| `tests/test_adapters_jsonl.py` | Claude/Codex/Qwen fixture adapters |
| `tests/test_adapter_opencode.py` | OpenCode SQLite adapter |
| `tests/test_workspace.py` | nested cwd and repo grouping |
| `tests/test_filtering_extraction.py` | noise filtering and conservative memory extraction |
| `tests/test_writer_pipeline.py` | output layout and evidence round-trip |
| `tests/integration/test_hks_compatibility.py` | generated folder can be ingested by adjacent HKS checkout |

---

### Task 1: Bootstrap Package And CLI Contract

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/session2memory/__init__.py`
- Create: `src/session2memory/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from session2memory.cli import app


def test_import_requires_date_and_output() -> None:
    result = CliRunner().invoke(app, ["import"])

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_dry_run_with_empty_source_roots_reports_zero_sessions(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--output",
            str(output_dir),
            "--source-root",
            "codex=missing",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "sessions=0" in result.output
    assert "written=0" in result.output
    assert not output_dir.exists()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'session2memory'`.

- [ ] **Step 3: Add package config**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "session2memory"
version = "0.1.0"
description = "Distill local coding-agent sessions into HKS-ingestable memory documents"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
  "typer>=0.12,<1",
]

[project.scripts]
session2memory = "session2memory.cli:app"

[dependency-groups]
dev = [
  "mypy",
  "pytest",
  "ruff",
]

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
include = ["session2memory*"]

[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["B", "E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.12"
strict = true
warn_unused_configs = true
packages = ["session2memory"]
```

Create `src/session2memory/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/session2memory/cli.py`:

```python
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True)


def _parse_source_root(raw: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise typer.BadParameter("--source-root must use tool=path")
        tool, path = item.split("=", 1)
        roots[tool.strip()] = Path(path).expanduser()
    return roots


@app.command("import")
def import_sessions(
    date: str = typer.Option(..., "--date", help="Date to import in YYYY-MM-DD format."),
    output: Path = typer.Option(..., "--output", help="Generated HKS-ingestable folder."),
    tool: list[str] = typer.Option([], "--tool", help="Limit import to one or more tools."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Limit to one workspace path."),
    source_root: list[str] = typer.Option(
        [], "--source-root", help="Override source root as tool=path."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan and report without writing output."),
) -> None:
    roots = _parse_source_root(source_root)
    selected_tools = tool or sorted(roots)
    if dry_run:
        typer.echo(f"date={date} tools={len(selected_tools)} sessions=0 written=0")
        return
    output.mkdir(parents=True, exist_ok=True)
    typer.echo(f"date={date} tools={len(selected_tools)} sessions=0 written=1")
```

Create `README.md`:

```markdown
# session2memory

`session2memory` reads local coding-agent sessions and writes a refined folder that HKS can ingest.

Raw transcripts are never written into the HKS source folder. Generated output contains Markdown summaries plus evidence pointers back to the original local session files.

## P0 Command

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

## HKS Ingest

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/session2memory/__init__.py src/session2memory/cli.py tests/test_cli.py
git commit -m "feat: bootstrap session2memory cli"
```

---

### Task 2: Define Models, Evidence Digest, And Pointer Round-Trip

**Files:**
- Create: `src/session2memory/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/test_models.py`:

```python
from pathlib import Path

from session2memory.models import EvidencePointer, SessionMessage, digest_text


def test_digest_text_is_stable_and_prefixed() -> None:
    assert (
        digest_text("accepted decision\n")
        == "sha256:0488779cd2495114702c2aba1e615dae014ccc33618061b2b876eef3941c8e57"
    )


def test_evidence_pointer_serializes_paths_as_strings() -> None:
    pointer = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        message_start=2,
        message_end=4,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )

    assert pointer.to_json() == {
        "tool": "codex",
        "session_id": "s1",
        "source_path": "/tmp/session.jsonl",
        "message_start": 2,
        "message_end": 4,
        "workspace_path": "/tmp/repo",
        "digest": "sha256:abc",
    }


def test_session_message_text_is_stripped() -> None:
    pointer = EvidencePointer(
        tool="qwen",
        session_id="s2",
        source_path=Path("/tmp/qwen.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=None,
        digest="sha256:def",
    )

    message = SessionMessage(index=1, role="user", text="  hello  ", timestamp=None, raw_pointer=pointer)

    assert message.text == "hello"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'session2memory.models'`.

- [ ] **Step 3: Implement models**

Create `src/session2memory/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

Role = Literal["user", "assistant", "tool", "system", "unknown"]
MemoryKind = Literal["decision", "completed", "pitfall", "constraint", "verification", "daily"]


def digest_text(text: str) -> str:
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidencePointer:
    tool: str
    session_id: str
    source_path: Path
    message_start: int
    message_end: int
    workspace_path: Path | None
    digest: str

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "tool": self.tool,
            "session_id": self.session_id,
            "source_path": self.source_path.as_posix(),
            "message_start": self.message_start,
            "message_end": self.message_end,
            "workspace_path": self.workspace_path.as_posix() if self.workspace_path else None,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class SessionMessage:
    index: int
    role: Role
    text: str
    timestamp: datetime | None
    raw_pointer: EvidencePointer

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text.strip())


@dataclass(frozen=True)
class SessionRecord:
    tool: str
    session_id: str
    source_path: Path
    started_at: datetime | None
    updated_at: datetime | None
    cwd: Path | None
    repo_root: Path | None
    tool_workspace_id: str | None
    messages: list[SessionMessage]


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    canonical_path: Path
    repo_root: Path | None
    opened_cwd: Path | None
    tool_workspace_id: str | None


@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    text: str
    workspace_id: str
    evidence: EvidencePointer
    durable: bool
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_models.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/models.py tests/test_models.py
git commit -m "feat: add session memory models"
```

---

### Task 3: Add JSONL Adapters For Codex, Qwen, And Claude Code

**Files:**
- Create: `src/session2memory/adapters/__init__.py`
- Create: `src/session2memory/adapters/base.py`
- Create: `src/session2memory/adapters/codex.py`
- Create: `src/session2memory/adapters/qwen.py`
- Create: `src/session2memory/adapters/claude.py`
- Create: `tests/fixtures/codex/2026/05/22/codex.jsonl`
- Create: `tests/fixtures/qwen/project/chats/qwen.jsonl`
- Create: `tests/fixtures/claude/project/claude.jsonl`
- Test: `tests/test_adapters_jsonl.py`

- [ ] **Step 1: Write fixture files**

Create `tests/fixtures/codex/2026/05/22/codex.jsonl`:

```jsonl
{"type":"session_meta","payload":{"id":"codex-1","timestamp":"2026-05-22T01:00:00Z","cwd":"/tmp/repo/sub","originator":"Codex Desktop"}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"請記住：這個 repo 用 uv run pytest -q 驗證。"}]}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"已確認，穩定驗證命令是 uv run pytest -q。"}]}}
```

Create `tests/fixtures/qwen/project/chats/qwen.jsonl`:

```jsonl
{"uuid":"q1","sessionId":"qwen-1","timestamp":"2026-05-22T02:00:00.000Z","type":"user","cwd":"/tmp/repo","message":{"role":"user","parts":[{"text":"決定：P0 不用 LLM 摘要。"}]}}
{"uuid":"q2","sessionId":"qwen-1","timestamp":"2026-05-22T02:01:00.000Z","type":"assistant","cwd":"/tmp/repo","message":{"role":"model","parts":[{"text":"收到，P0 採 deterministic extraction。"}]}}
```

Create `tests/fixtures/claude/project/claude.jsonl`:

```jsonl
{"sessionId":"claude-1","timestamp":"2026-05-22T03:00:00.000Z","type":"user","cwd":"/tmp/repo","message":{"role":"user","content":[{"type":"text","text":"坑：不要把 raw transcript 丟進 HKS。"}]}}
{"sessionId":"claude-1","timestamp":"2026-05-22T03:01:00.000Z","type":"assistant","cwd":"/tmp/repo","message":{"role":"assistant","content":[{"type":"text","text":"已記錄，raw transcript 只當 evidence source。"}]}}
```

- [ ] **Step 2: Write failing adapter tests**

Create `tests/test_adapters_jsonl.py`:

```python
from pathlib import Path

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.qwen import QwenAdapter


def test_codex_adapter_reads_session_meta_and_messages() -> None:
    records = list(CodexAdapter(Path("tests/fixtures/codex")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "codex"
    assert records[0].session_id == "codex-1"
    assert records[0].cwd == Path("/tmp/repo/sub")
    assert [message.role for message in records[0].messages] == ["user", "assistant"]
    assert records[0].messages[0].raw_pointer.message_start == 2


def test_qwen_adapter_reads_jsonl_messages() -> None:
    records = list(QwenAdapter(Path("tests/fixtures/qwen")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "qwen"
    assert records[0].session_id == "qwen-1"
    assert records[0].messages[0].text == "決定：P0 不用 LLM 摘要。"


def test_claude_adapter_reads_jsonl_content_blocks() -> None:
    records = list(ClaudeAdapter(Path("tests/fixtures/claude")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "claude"
    assert records[0].session_id == "claude-1"
    assert records[0].messages[0].text == "坑：不要把 raw transcript 丟進 HKS。"
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_adapters_jsonl.py -q
```

Expected: FAIL with missing adapter modules.

- [ ] **Step 4: Implement shared adapter helpers**

Create `src/session2memory/adapters/base.py`:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol


from session2memory.models import EvidencePointer, SessionMessage, SessionRecord, digest_text


class SessionAdapter(Protocol):
    tool: str

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        raise NotImplementedError


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, object]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if stripped:
                yield line_number, json.loads(stripped)


def make_message(
    *,
    tool: str,
    session_id: str,
    source_path: Path,
    line_number: int,
    role: str,
    text: str,
    timestamp: datetime | None,
    cwd: Path | None,
) -> SessionMessage:
    pointer = EvidencePointer(
        tool=tool,
        session_id=session_id,
        source_path=source_path,
        message_start=line_number,
        message_end=line_number,
        workspace_path=cwd,
        digest=digest_text(text),
    )
    normalized_role = role if role in {"user", "assistant", "tool", "system"} else "unknown"
    return SessionMessage(
        index=line_number,
        role=normalized_role,  # type: ignore[arg-type]
        text=text,
        timestamp=timestamp,
        raw_pointer=pointer,
    )
```

Create `src/session2memory/adapters/__init__.py`:

```python
from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.qwen import QwenAdapter

__all__ = ["ClaudeAdapter", "CodexAdapter", "QwenAdapter"]
```

- [ ] **Step 5: Implement Codex adapter**

Create `src/session2memory/adapters/codex.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.base import make_message, parse_datetime, read_jsonl
from session2memory.models import SessionMessage, SessionRecord


class CodexAdapter:
    tool = "codex"

    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        year, month, day = date.split("-")
        for path in sorted((self.root / year / month / day).glob("*.jsonl")):
            yield self._read_file(path)

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            if event.get("type") == "session_meta":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    session_id = str(payload.get("id") or session_id)
                    cwd_value = payload.get("cwd")
                    cwd = Path(str(cwd_value)) if cwd_value else cwd
                    started_at = parse_datetime(str(payload.get("timestamp"))) if payload.get("timestamp") else started_at
            if event.get("type") != "response_item":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            content = payload.get("content")
            texts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        texts.append(block["text"])
            if texts:
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=line_number,
                        role=str(payload.get("role") or "unknown"),
                        text="\n".join(texts),
                        timestamp=None,
                        cwd=cwd,
                    )
                )
        if messages:
            updated_at = messages[-1].timestamp
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=None,
            messages=messages,
        )
```

- [ ] **Step 6: Implement Qwen adapter**

Create `src/session2memory/adapters/qwen.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.base import make_message, parse_datetime, read_jsonl
from session2memory.models import SessionMessage, SessionRecord


class QwenAdapter:
    tool = "qwen"

    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        for path in sorted(self.root.glob("projects/*/chats/*.jsonl")):
            record = self._read_file(path)
            if record.started_at and record.started_at.date().isoformat() == date:
                yield record

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            session_id = str(event.get("sessionId") or session_id)
            event_time = parse_datetime(str(event.get("timestamp"))) if event.get("timestamp") else None
            started_at = started_at or event_time
            updated_at = event_time or updated_at
            cwd_value = event.get("cwd")
            cwd = Path(str(cwd_value)) if cwd_value else cwd
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            parts = message.get("parts")
            texts: list[str] = []
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        texts.append(part["text"])
            if texts:
                raw_role = str(message.get("role") or event.get("type") or "unknown")
                role = "assistant" if raw_role == "model" else raw_role
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=line_number,
                        role=role,
                        text="\n".join(texts),
                        timestamp=event_time,
                        cwd=cwd,
                    )
                )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=path.parent.parent.name,
            messages=messages,
        )
```

- [ ] **Step 7: Implement Claude adapter**

Create `src/session2memory/adapters/claude.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.base import make_message, parse_datetime, read_jsonl
from session2memory.models import SessionMessage, SessionRecord


class ClaudeAdapter:
    tool = "claude"

    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        for path in sorted(self.root.glob("**/*.jsonl")):
            if "/subagents/" in path.as_posix():
                continue
            record = self._read_file(path)
            if record.started_at and record.started_at.date().isoformat() == date:
                yield record

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            session_id = str(event.get("sessionId") or event.get("session_id") or session_id)
            event_time = parse_datetime(str(event.get("timestamp"))) if event.get("timestamp") else None
            started_at = started_at or event_time
            updated_at = event_time or updated_at
            cwd_value = event.get("cwd")
            cwd = Path(str(cwd_value)) if cwd_value else cwd
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        texts.append(block["text"])
            if texts:
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=line_number,
                        role=str(message.get("role") or event.get("type") or "unknown"),
                        text="\n".join(texts),
                        timestamp=event_time,
                        cwd=cwd,
                    )
                )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=path.parent.name,
            messages=messages,
        )
```

- [ ] **Step 8: Run adapter tests and fix type issue**

Run:

```bash
uv run pytest tests/test_adapters_jsonl.py -q
```

Expected before type cleanup: tests PASS. If mypy flags `type: ignore[arg-type]`, Task 10 removes it by adding a role normalization helper.

- [ ] **Step 9: Commit**

```bash
git add src/session2memory/adapters tests/fixtures tests/test_adapters_jsonl.py
git commit -m "feat: read jsonl sessions from codex qwen claude"
```

---

### Task 4: Add OpenCode SQLite Adapter

**Files:**
- Create: `src/session2memory/adapters/opencode.py`
- Modify: `src/session2memory/adapters/__init__.py`
- Test: `tests/test_adapter_opencode.py`

- [ ] **Step 1: Write failing OpenCode adapter test**

Create `tests/test_adapter_opencode.py`:

```python
import json
import sqlite3
from pathlib import Path

from session2memory.adapters.opencode import OpenCodeAdapter


def create_opencode_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (
            id text PRIMARY KEY,
            project_id text NOT NULL,
            directory text NOT NULL,
            title text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            workspace_id text
        );
        CREATE TABLE message (
            id text PRIMARY KEY,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        CREATE TABLE part (
            id text PRIMARY KEY,
            message_id text NOT NULL,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        """
    )
    connection.execute(
        "insert into session values (?, ?, ?, ?, ?, ?, ?)",
        ("ses1", "proj1", "/tmp/repo", "OpenCode Import", 1779412154000, 1779412160000, "ws1"),
    )
    connection.execute(
        "insert into message values (?, ?, ?, ?, ?)",
        (
            "msg1",
            "ses1",
            1779412155000,
            1779412155000,
            json.dumps({"role": "user", "time": {"created": 1779412155000}}),
        ),
    )
    connection.execute(
        "insert into part values (?, ?, ?, ?, ?)",
        (
            "part1",
            "msg1",
            "ses1",
            1779412155000,
            1779412155000,
            json.dumps({"type": "text", "text": "驗證：uv run pytest -q passed。"}),
        ),
    )
    connection.commit()
    connection.close()


def test_opencode_adapter_reads_sqlite_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    create_opencode_db(db_path)

    records = list(OpenCodeAdapter(db_path).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "opencode"
    assert records[0].session_id == "ses1"
    assert records[0].cwd == Path("/tmp/repo")
    assert records[0].tool_workspace_id == "ws1"
    assert records[0].messages[0].text == "驗證：uv run pytest -q passed。"
    assert records[0].messages[0].raw_pointer.message_start == 1
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest tests/test_adapter_opencode.py -q
```

Expected: FAIL with missing `session2memory.adapters.opencode`.

- [ ] **Step 3: Implement OpenCode adapter**

Create `src/session2memory/adapters/opencode.py`:

```python
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from session2memory.models import EvidencePointer, SessionMessage, SessionRecord, digest_text


def _from_millis(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


class OpenCodeAdapter:
    tool = "opencode"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        if not self.db_path.exists():
            return
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            for session in connection.execute(
                "select id, directory, time_created, time_updated, workspace_id from session order by time_created"
            ):
                started_at = _from_millis(session["time_created"])
                if started_at is None or started_at.date().isoformat() != date:
                    continue
                yield self._read_session(connection, session)
        finally:
            connection.close()

    def _read_session(self, connection: sqlite3.Connection, session: sqlite3.Row) -> SessionRecord:
        session_id = str(session["id"])
        cwd = Path(str(session["directory"]))
        messages: list[SessionMessage] = []
        rows = connection.execute(
            """
            select m.id as message_id, m.data as message_data, p.data as part_data
            from message m
            left join part p on p.message_id = m.id
            where m.session_id = ?
            order by m.time_created, m.id, p.id
            """,
            (session_id,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            message_data = json.loads(str(row["message_data"]))
            part_data = json.loads(str(row["part_data"])) if row["part_data"] else {}
            text = str(part_data.get("text") or "")
            if not text.strip():
                continue
            role = str(message_data.get("role") or "unknown")
            pointer = EvidencePointer(
                tool=self.tool,
                session_id=session_id,
                source_path=self.db_path,
                message_start=index,
                message_end=index,
                workspace_path=cwd,
                digest=digest_text(text),
            )
            messages.append(
                SessionMessage(
                    index=index,
                    role=role if role in {"user", "assistant", "tool", "system"} else "unknown",  # type: ignore[arg-type]
                    text=text,
                    timestamp=_from_millis(message_data.get("time", {}).get("created")),
                    raw_pointer=pointer,
                )
            )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=self.db_path,
            started_at=_from_millis(session["time_created"]),
            updated_at=_from_millis(session["time_updated"]),
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=session["workspace_id"],
            messages=messages,
        )
```

Modify `src/session2memory/adapters/__init__.py`:

```python
from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.opencode import OpenCodeAdapter
from session2memory.adapters.qwen import QwenAdapter

__all__ = ["ClaudeAdapter", "CodexAdapter", "OpenCodeAdapter", "QwenAdapter"]
```

- [ ] **Step 4: Run test and verify GREEN**

Run:

```bash
uv run pytest tests/test_adapter_opencode.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/adapters/opencode.py tests/test_adapter_opencode.py
git commit -m "feat: read opencode sqlite sessions"
```

---

### Task 5: Add Workspace Resolver And Stable Workspace IDs

**Files:**
- Create: `src/session2memory/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write failing workspace tests**

Create `tests/test_workspace.py`:

```python
import subprocess
from pathlib import Path

from session2memory.models import SessionRecord
from session2memory.workspace import resolve_workspace


def make_record(cwd: Path, tool_workspace_id: str | None = None) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        started_at=None,
        updated_at=None,
        cwd=cwd,
        repo_root=None,
        tool_workspace_id=tool_workspace_id,
        messages=[],
    )


def test_nested_cwd_groups_to_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "packages" / "api"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    workspace = resolve_workspace(make_record(nested))

    assert workspace.canonical_path == repo.resolve()
    assert workspace.repo_root == repo.resolve()
    assert workspace.workspace_id.startswith("repo-")


def test_non_git_cwd_groups_to_canonical_cwd(tmp_path: Path) -> None:
    folder = tmp_path / "notes"
    folder.mkdir()

    workspace = resolve_workspace(make_record(folder, "tool-ws"))

    assert workspace.canonical_path == folder.resolve()
    assert workspace.repo_root is None
    assert workspace.tool_workspace_id == "tool-ws"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_workspace.py -q
```

Expected: FAIL with missing `session2memory.workspace`.

- [ ] **Step 3: Implement workspace resolver**

Create `src/session2memory/workspace.py`:

```python
from __future__ import annotations

import re
import subprocess
from hashlib import sha256
from pathlib import Path

from session2memory.models import SessionRecord, WorkspaceIdentity


def _slug_base(path: Path) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", path.name.lower()).strip("-")
    return cleaned or "workspace"


def _short_digest(path: Path) -> str:
    return sha256(path.as_posix().encode("utf-8")).hexdigest()[:8]


def find_git_root(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def resolve_workspace(record: SessionRecord) -> WorkspaceIdentity:
    opened_cwd = record.cwd.resolve() if record.cwd else None
    repo_root = find_git_root(opened_cwd) if opened_cwd else None
    canonical = repo_root or opened_cwd or record.source_path.parent.resolve()
    workspace_id = f"{_slug_base(canonical)}-{_short_digest(canonical)}"
    return WorkspaceIdentity(
        workspace_id=workspace_id,
        canonical_path=canonical,
        repo_root=repo_root,
        opened_cwd=opened_cwd,
        tool_workspace_id=record.tool_workspace_id,
    )
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_workspace.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/workspace.py tests/test_workspace.py
git commit -m "feat: resolve stable workspace identity"
```

---

### Task 6: Add Noise Filtering And Conservative Extraction

**Files:**
- Create: `src/session2memory/filtering.py`
- Create: `src/session2memory/extraction.py`
- Test: `tests/test_filtering_extraction.py`

- [ ] **Step 1: Write failing filtering and extraction tests**

Create `tests/test_filtering_extraction.py`:

```python
from pathlib import Path

from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.models import EvidencePointer, SessionMessage, SessionRecord
from session2memory.workspace import resolve_workspace


def pointer(text: str) -> EvidencePointer:
    return EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:test",
    )


def message(index: int, role: str, text: str) -> SessionMessage:
    return SessionMessage(index=index, role=role, text=text, timestamp=None, raw_pointer=pointer(text))


def record(messages: list[SessionMessage]) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        started_at=None,
        updated_at=None,
        cwd=Path("/tmp/repo"),
        repo_root=None,
        tool_workspace_id=None,
        messages=messages,
    )


def test_noise_filter_removes_system_prompt_agents_and_huge_output() -> None:
    assert is_noise(message(1, "system", "You are Codex, a coding agent based on GPT-5."))
    assert is_noise(message(2, "user", "# AGENTS.md instructions for /tmp/repo\n<INSTRUCTIONS>"))
    assert is_noise(message(3, "tool", "line\n" * 401))
    assert is_noise(message(4, "assistant", "Traceback (most recent call last):\nValueError: bad"))
    assert not is_noise(message(5, "assistant", "驗證：uv run pytest -q passed。"))


def test_extracts_only_high_signal_candidates() -> None:
    session = record(
        [
            message(1, "user", "決定：P0 不用 LLM 摘要。"),
            message(2, "assistant", "驗證：uv run pytest -q passed。"),
            message(3, "assistant", "這裡只是一般聊天，沒有穩定記憶。"),
        ]
    )
    workspace = resolve_workspace(session)

    candidates = extract_candidates(session, workspace)

    assert [candidate.kind for candidate in candidates] == ["decision", "verification"]
    assert candidates[0].durable is True
    assert candidates[1].durable is True
    assert candidates[0].text == "P0 不用 LLM 摘要。"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_filtering_extraction.py -q
```

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement noise filtering**

Create `src/session2memory/filtering.py`:

```python
from __future__ import annotations

from session2memory.models import SessionMessage

NOISE_PREFIXES = (
    "You are Codex",
    "You are Claude",
    "# AGENTS.md instructions",
    "# CLAUDE.md",
    "# GEMINI.md",
)

TELEMETRY_MARKERS = (
    "ui_telemetry",
    "token_count",
    "cached_content_token_count",
)


def is_noise(message: SessionMessage) -> bool:
    text = message.text.strip()
    if message.role == "system":
        return True
    if any(text.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if any(marker in text for marker in TELEMETRY_MARKERS):
        return True
    if message.role == "tool" and text.count("\n") > 300:
        return True
    if text.startswith("Traceback (most recent call last)") and not _contains_signal(text):
        return True
    return False


def _contains_signal(text: str) -> bool:
    return any(marker in text for marker in ("決定", "驗證", "坑", "完成", "failed", "passed"))
```

- [ ] **Step 4: Implement conservative extraction**

Create `src/session2memory/extraction.py`:

```python
from __future__ import annotations

import re

from session2memory.filtering import is_noise
from session2memory.models import MemoryCandidate, SessionRecord, WorkspaceIdentity

RULES: tuple[tuple[str, str, bool], ...] = (
    ("decision", r"(?:決定|Decision)[:：]\s*(.+)", True),
    ("completed", r"(?:完成|Done)[:：]\s*(.+)", True),
    ("pitfall", r"(?:坑|Pitfall)[:：]\s*(.+)", True),
    ("constraint", r"(?:限制|Constraint)[:：]\s*(.+)", True),
    ("verification", r"(?:驗證|Verification)[:：]\s*(.+)", True),
)


def extract_candidates(record: SessionRecord, workspace: WorkspaceIdentity) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for message in record.messages:
        if is_noise(message):
            continue
        for kind, pattern, durable in RULES:
            match = re.search(pattern, message.text)
            if not match:
                continue
            text = match.group(1).strip()
            if not text:
                continue
            candidates.append(
                MemoryCandidate(
                    kind=kind,  # type: ignore[arg-type]
                    text=text,
                    workspace_id=workspace.workspace_id,
                    evidence=message.raw_pointer,
                    durable=durable,
                )
            )
            break
    return candidates
```

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_filtering_extraction.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/session2memory/filtering.py src/session2memory/extraction.py tests/test_filtering_extraction.py
git commit -m "feat: extract conservative session memories"
```

---

### Task 7: Write Output Folder, Evidence Index, Manifest, And Pipeline

**Files:**
- Create: `src/session2memory/writer.py`
- Create: `src/session2memory/pipeline.py`
- Test: `tests/test_writer_pipeline.py`

- [ ] **Step 1: Write failing writer and pipeline tests**

Create `tests/test_writer_pipeline.py`:

```python
import json
from pathlib import Path

from session2memory.models import EvidencePointer, MemoryCandidate, WorkspaceIdentity
from session2memory.writer import write_output


def candidate(kind: str, text: str, workspace_id: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        workspace_id=workspace_id,
        evidence=EvidencePointer(
            tool="codex",
            session_id="s1",
            source_path=Path("/tmp/raw/session.jsonl"),
            message_start=2,
            message_end=2,
            workspace_path=Path("/tmp/repo"),
            digest="sha256:abc",
        ),
        durable=True,
    )


def workspace(workspace_id: str) -> WorkspaceIdentity:
    return WorkspaceIdentity(
        workspace_id=workspace_id,
        canonical_path=Path("/tmp/repo"),
        repo_root=Path("/tmp/repo"),
        opened_cwd=Path("/tmp/repo/sub"),
        tool_workspace_id="tool-ws",
    )


def test_write_output_creates_hks_ingestable_folder_without_raw_markdown(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"

    write_output(
        output_dir=output,
        date="2026-05-22",
        candidates=[candidate("decision", "P0 不用 LLM 摘要。", "repo-123")],
        workspaces={"repo-123": workspace("repo-123")},
        scanned_tools=["codex"],
        source_roots={"codex": Path("/tmp/raw")},
        skipped=[],
        session_count=1,
        message_count=2,
        filtered_count=0,
        dry_run=False,
    )

    daily = (output / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    evidence = json.loads((output / "evidence" / "index.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert "P0 不用 LLM 摘要。" in daily
    assert "P0 不用 LLM 摘要。" in memory
    assert "/tmp/raw/session.jsonl" not in daily
    assert evidence["source_path"] == "/tmp/raw/session.jsonl"
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["counts"]["durable_memories"] == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_writer_pipeline.py -q
```

Expected: FAIL with missing `session2memory.writer`.

- [ ] **Step 3: Implement writer**

Create `src/session2memory/writer.py`:

```python
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

from session2memory.models import MemoryCandidate, WorkspaceIdentity


def write_output(
    *,
    output_dir: Path,
    date: str,
    candidates: list[MemoryCandidate],
    workspaces: dict[str, WorkspaceIdentity],
    scanned_tools: list[str],
    source_roots: dict[str, Path],
    skipped: list[dict[str, str]],
    session_count: int,
    message_count: int,
    filtered_count: int,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "daily").mkdir(parents=True)
    (output_dir / "memories").mkdir(parents=True)
    (output_dir / "evidence").mkdir(parents=True)

    evidence_ids = _write_evidence(output_dir / "evidence" / "index.jsonl", candidates)
    _write_daily(output_dir / "daily" / f"{date}.md", date, candidates, workspaces, evidence_ids)
    _write_memories(output_dir / "memories", candidates, workspaces, evidence_ids)
    _write_manifest(
        output_dir / "manifest.json",
        date,
        candidates,
        scanned_tools,
        source_roots,
        skipped,
        session_count,
        message_count,
        filtered_count,
    )


def _write_evidence(path: Path, candidates: list[MemoryCandidate]) -> dict[int, str]:
    evidence_ids: dict[int, str] = {}
    with path.open("w", encoding="utf-8") as handle:
        for index, candidate in enumerate(candidates, start=1):
            evidence_id = f"ev_{index:04d}"
            evidence_ids[index - 1] = evidence_id
            payload = {"id": evidence_id, **candidate.evidence.to_json()}
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return evidence_ids


def _write_daily(
    path: Path,
    date: str,
    candidates: list[MemoryCandidate],
    workspaces: dict[str, WorkspaceIdentity],
    evidence_ids: dict[int, str],
) -> None:
    lines = [f"# Daily Session Memory {date}", ""]
    grouped: dict[str, list[tuple[int, MemoryCandidate]]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        grouped[candidate.workspace_id].append((index, candidate))
    for workspace_id in sorted(grouped):
        workspace = workspaces[workspace_id]
        lines.extend([f"## {workspace_id}", "", f"- Workspace: `{workspace.canonical_path.as_posix()}`"])
        for index, candidate in grouped[workspace_id]:
            lines.append(f"- [{candidate.kind}] {candidate.text} (evidence: {evidence_ids[index]})")
        lines.append("")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_memories(
    root: Path,
    candidates: list[MemoryCandidate],
    workspaces: dict[str, WorkspaceIdentity],
    evidence_ids: dict[int, str],
) -> None:
    grouped: dict[str, list[tuple[int, MemoryCandidate]]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        if candidate.durable:
            grouped[candidate.workspace_id].append((index, candidate))
    for workspace_id, items in grouped.items():
        workspace = workspaces[workspace_id]
        lines = [
            f"# Workspace Memory: {workspace_id}",
            "",
            f"- Canonical path: `{workspace.canonical_path.as_posix()}`",
        ]
        if workspace.repo_root:
            lines.append(f"- Repo root: `{workspace.repo_root.as_posix()}`")
        lines.append("")
        for index, candidate in items:
            lines.append(f"- [{candidate.kind}] {candidate.text} (evidence: {evidence_ids[index]})")
        (root / f"{workspace_id}.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_manifest(
    path: Path,
    date: str,
    candidates: list[MemoryCandidate],
    scanned_tools: list[str],
    source_roots: dict[str, Path],
    skipped: list[dict[str, str]],
    session_count: int,
    message_count: int,
    filtered_count: int,
) -> None:
    payload = {
        "generator": "session2memory",
        "version": "0.1.0",
        "date": date,
        "scanned_tools": scanned_tools,
        "source_roots": {tool: root.as_posix() for tool, root in sorted(source_roots.items())},
        "skipped": skipped,
        "counts": {
            "sessions": session_count,
            "messages": message_count,
            "filtered_items": filtered_count,
            "daily_entries": len(candidates),
            "durable_memories": sum(1 for candidate in candidates if candidate.durable),
            "evidence_records": len(candidates),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Implement pipeline orchestration**

Create `src/session2memory/pipeline.py`:

```python
from __future__ import annotations

from pathlib import Path

from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.models import MemoryCandidate, WorkspaceIdentity
from session2memory.workspace import resolve_workspace
from session2memory.writer import write_output


def run_import(
    *,
    date: str,
    output_dir: Path,
    adapters: list[object],
    source_roots: dict[str, Path],
    dry_run: bool,
) -> tuple[int, int]:
    candidates: list[MemoryCandidate] = []
    workspaces: dict[str, WorkspaceIdentity] = {}
    sessions_count = 0
    messages_count = 0
    filtered_count = 0
    for adapter in adapters:
        iter_sessions = getattr(adapter, "iter_sessions")
        for record in iter_sessions(date):
            sessions_count += 1
            messages_count += len(record.messages)
            filtered_count += sum(1 for message in record.messages if is_noise(message))
            workspace = resolve_workspace(record)
            workspaces[workspace.workspace_id] = workspace
            candidates.extend(extract_candidates(record, workspace))
    write_output(
        output_dir=output_dir,
        date=date,
        candidates=candidates,
        workspaces=workspaces,
        scanned_tools=sorted(source_roots),
        source_roots=source_roots,
        skipped=[],
        session_count=sessions_count,
        message_count=messages_count,
        filtered_count=filtered_count,
        dry_run=dry_run,
    )
    return sessions_count, len(candidates)
```

- [ ] **Step 5: Run writer tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_writer_pipeline.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/session2memory/writer.py src/session2memory/pipeline.py tests/test_writer_pipeline.py
git commit -m "feat: write hks ingestable memory output"
```

---

### Task 8: Wire CLI To Real Adapters And Default Source Roots

**Files:**
- Modify: `src/session2memory/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Extend CLI tests for real output**

Append to `tests/test_cli.py`:

```python

def test_import_writes_memory_output_from_codex_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
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

    assert result.exit_code == 0
    assert "sessions=1" in result.output
    assert (output_dir / "daily" / "2026-05-22.md").exists()
    assert (output_dir / "evidence" / "index.jsonl").exists()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_cli.py::test_import_writes_memory_output_from_codex_fixture -q
```

Expected: FAIL because CLI still writes dummy output.

- [ ] **Step 3: Replace CLI with adapter-backed implementation**

Replace `src/session2memory/cli.py` with:

```python
from __future__ import annotations

from pathlib import Path

import typer

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.opencode import OpenCodeAdapter
from session2memory.adapters.qwen import QwenAdapter
from session2memory.pipeline import run_import

app = typer.Typer(no_args_is_help=True)


def _default_roots() -> dict[str, Path]:
    home = Path.home()
    return {
        "codex": home / ".codex" / "sessions",
        "claude": home / ".claude" / "projects",
        "qwen": home / ".qwen",
        "opencode": home / ".local" / "share" / "opencode" / "opencode.db",
    }


def _parse_source_root(raw: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise typer.BadParameter("--source-root must use tool=path")
        tool, path = item.split("=", 1)
        roots[tool.strip()] = Path(path).expanduser()
    return roots


def _build_adapters(tools: list[str], roots: dict[str, Path]) -> list[object]:
    adapters: list[object] = []
    for tool in tools:
        root = roots[tool]
        if tool == "codex":
            adapters.append(CodexAdapter(root))
        elif tool == "claude":
            adapters.append(ClaudeAdapter(root))
        elif tool == "qwen":
            adapters.append(QwenAdapter(root))
        elif tool == "opencode":
            adapters.append(OpenCodeAdapter(root))
        else:
            raise typer.BadParameter(f"unsupported tool: {tool}")
    return adapters


@app.command("import")
def import_sessions(
    date: str = typer.Option(..., "--date", help="Date to import in YYYY-MM-DD format."),
    output: Path = typer.Option(..., "--output", help="Generated HKS-ingestable folder."),
    tool: list[str] = typer.Option([], "--tool", help="Limit import to one or more tools."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Limit to one workspace path."),
    source_root: list[str] = typer.Option(
        [], "--source-root", help="Override source root as tool=path."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan and report without writing output."),
) -> None:
    roots = _default_roots()
    roots.update(_parse_source_root(source_root))
    selected_tools = tool or sorted(roots)
    selected_roots = {name: roots[name] for name in selected_tools}
    adapters = _build_adapters(selected_tools, roots)
    sessions_count, candidates_count = run_import(
        date=date,
        output_dir=output,
        adapters=adapters,
        source_roots=selected_roots,
        dry_run=dry_run,
    )
    workspace_note = f" workspace={workspace}" if workspace else ""
    typer.echo(
        f"date={date} tools={len(selected_tools)} sessions={sessions_count} "
        f"written={0 if dry_run else 1} candidates={candidates_count}{workspace_note}"
    )
```

- [ ] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/cli.py tests/test_cli.py
git commit -m "feat: wire cli to session import pipeline"
```

---

### Task 9: Add HKS Compatibility Integration Test And README Workflow

**Files:**
- Create: `tests/integration/test_hks_compatibility.py`
- Modify: `README.md`

- [ ] **Step 1: Write HKS compatibility test**

Create `tests/integration/test_hks_compatibility.py`:

```python
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app


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
    assert result.exit_code == 0

    env = os.environ.copy()
    env["HKS_EMBEDDING_MODEL"] = "simple"
    env["HKS_ROOT"] = str(tmp_path / "ks")
    ingest = subprocess.run(
        ["uv", "run", "ks", "ingest", str(output_dir)],
        cwd=hks_root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    source_list = subprocess.run(
        ["uv", "run", "ks", "source", "list"],
        cwd=hks_root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert source_list.returncode == 0, source_list.stdout + source_list.stderr
    assert "/tmp/raw/session.jsonl" not in source_list.stdout
    assert "/tmp/raw/session.jsonl" not in (output_dir / "daily" / "2026-05-22.md").read_text(
        encoding="utf-8"
    )
```

- [ ] **Step 2: Run integration test and verify RED or SKIP**

Run:

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
```

Expected on this machine: FAIL only if HKS ingest rejects the generated folder. If adjacent HKS checkout is unavailable, expected SKIP.

- [ ] **Step 3: Update README with actual workflow**

Replace `README.md` with:

```markdown
# session2memory

`session2memory` converts local coding-agent sessions into refined HKS-ingestable memory documents.

It does not put raw transcripts into HKS. Raw session stores remain local evidence sources; generated Markdown contains only conservative summaries and evidence ids.

## Supported P0 Sources

- Claude Code JSONL under `~/.claude/projects`
- Codex JSONL under `~/.codex/sessions`
- Qwen Code JSONL under `~/.qwen/projects`
- OpenCode SQLite under `~/.local/share/opencode/opencode.db`

## Generate Memory Docs

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

Limit to one tool:

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --tool codex \
  --output ./out/session-memory
```

Override a source root:

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --source-root codex=/Users/waynetu/.codex/sessions \
  --output ./out/session-memory
```

## HKS Ingest

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
export HKS_EMBEDDING_MODEL=simple
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```

The HKS source root is the generated `session-memory` folder. Do not ingest `~/.codex/sessions`, `~/.claude/projects`, `~/.qwen/projects`, or OpenCode SQLite directly.

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```
```

- [ ] **Step 4: Run integration test and verify GREEN or SKIP**

Run:

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
```

Expected on this machine: PASS. In an environment without adjacent HKS checkout: SKIP.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/integration/test_hks_compatibility.py
git commit -m "test: verify generated memory folder with hks"
```

---

### Task 10: Type Cleanup, Full Verification, And Release Check

**Files:**
- Modify: `src/session2memory/adapters/base.py`
- Modify: `src/session2memory/adapters/opencode.py`
- Modify: `src/session2memory/extraction.py`
- Modify: `tests/test_adapters_jsonl.py`

- [ ] **Step 1: Run full verification and capture failures**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

Expected before cleanup: pytest PASS; ruff PASS or formatting import issue; mypy may fail on `type: ignore[arg-type]` or adapter protocol typing.

- [ ] **Step 2: Add role normalization helper**

Modify `src/session2memory/adapters/base.py` by adding:

```python
from session2memory.models import EvidencePointer, Role, SessionMessage, SessionRecord, digest_text


def normalize_role(value: str) -> Role:
    if value in {"user", "assistant", "tool", "system"}:
        return value
    if value == "model":
        return "assistant"
    return "unknown"
```

Then replace the `normalized_role` block in `make_message()` with:

```python
    return SessionMessage(
        index=line_number,
        role=normalize_role(role),
        text=text,
        timestamp=timestamp,
        raw_pointer=pointer,
    )
```

- [ ] **Step 3: Remove ignore comments in OpenCode and extraction**

Modify `src/session2memory/adapters/opencode.py` import:

```python
from session2memory.adapters.base import normalize_role
```

Replace the `SessionMessage` role line with:

```python
                    role=normalize_role(role),
```

Modify `src/session2memory/extraction.py` import:

```python
from session2memory.models import MemoryCandidate, MemoryKind, SessionRecord, WorkspaceIdentity
```

Change `RULES` type:

```python
RULES: tuple[tuple[MemoryKind, str, bool], ...] = (
```

Replace candidate kind assignment with:

```python
                    kind=kind,
```

- [ ] **Step 4: Run full verification and verify GREEN**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

Expected:

```text
pytest: all tests passed
ruff: All checks passed
mypy: Success: no issues found in 1 source package
```

- [ ] **Step 5: Commit**

```bash
git add src/session2memory tests
git commit -m "chore: verify session2memory quality gates"
```

---

## Plan Self-Review

Spec coverage:

- One command generates HKS-ingestable docs: Tasks 1, 7, 8.
- Four P0 adapters: Tasks 3 and 4.
- Workspace grouping from nested cwd/repo: Task 5.
- Evidence pointer round-trip: Tasks 2 and 7.
- Raw transcript not directly ingested: Tasks 7 and 9.
- Daily summaries separated from durable memories: Task 7.
- Conservative deterministic extraction, no LLM: Task 6.
- HKS compatibility: Task 9.

Placeholder scan:

- Placeholder scan completed cleanly.
- All file paths are concrete.
- No hidden daemon, watcher, HKS core change, or LLM summarization scope.

Type consistency:

- `EvidencePointer`, `SessionMessage`, `SessionRecord`, `WorkspaceIdentity`, and `MemoryCandidate` are introduced before use.
- `normalize_role()` removes role type ignores before final verification.
- CLI uses adapter roots and `run_import()` consistently.
