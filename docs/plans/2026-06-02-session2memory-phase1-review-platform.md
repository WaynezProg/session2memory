# Phase 1: LLM Extractor + Review TUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional LLM-based memory candidates (review-only) and a Textual TUI for review/promote, without changing HKS ingest contracts.

**Architecture:** Extend `MemoryCandidate` + pipeline hook after marker extraction; pluggable `LlmExtractBackend` with a mock backend for tests. TUI calls existing `review.py` functions — no duplicate state logic.

**Tech Stack:** Python 3.12, Typer, Textual (optional `ui` dependency group), pytest, uv.

**Spec:** `docs/specs/2026-06-02-session2memory-memory-platform-design.md` (Phase 1 sections)

---

## File map

| File | Responsibility |
|------|----------------|
| `src/session2memory/models.py` | Optional `extraction`, `confidence`, `evidence_quote` on `MemoryCandidate` |
| `src/session2memory/llm_extract.py` | Backend protocol, merge/dedup, subprocess backend |
| `src/session2memory/llm_extract_mock.py` | Test backend returning fixed JSON |
| `src/session2memory/prompts/llm_extract.txt` | Prompt template for subprocess backend |
| `src/session2memory/pipeline.py` | Wire `--llm-extract` flag through `run_pipeline` |
| `src/session2memory/writer.py` | Serialize new review fields (`extraction`, `confidence`, `evidence_quote`) |
| `src/session2memory/review_ui.py` | Textual app (list, detail, promote) |
| `src/session2memory/cli.py` | `--llm-extract` on import; `review ui` command |
| `tests/test_llm_extract.py` | Backend + dedup tests |
| `tests/test_review_ui.py` | Headless Textual / app wiring smoke |
| `pyproject.toml` | `[dependency-groups] ui = ["textual>=0.79"]` |
| `README.md` | Document `--llm-extract` and `review ui` |

---

### Task 1: Extend MemoryCandidate model

**Files:**
- Modify: `src/session2memory/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from session2memory.models import MemoryCandidate, EvidencePointer
from pathlib import Path


def test_memory_candidate_accepts_llm_metadata() -> None:
    evidence = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )
    candidate = MemoryCandidate(
        kind="decision",
        text="Prefer uv over pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=False,
        extraction="llm",
        confidence=0.82,
        evidence_quote="we should use uv",
    )
    assert candidate.extraction == "llm"
    assert candidate.confidence == 0.82
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_memory_candidate_accepts_llm_metadata -q`  
Expected: FAIL (`MemoryCandidate` unexpected keyword)

- [ ] **Step 3: Implement model fields**

```python
# models.py — add after durable field
ExtractionSource = Literal["marker", "llm"]

@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    text: str
    workspace_id: str
    evidence: EvidencePointer
    durable: bool
    extraction: ExtractionSource = "marker"
    confidence: float | None = None
    evidence_quote: str | None = None
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_models.py -q`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/models.py tests/test_models.py
git commit -m "feat: extend MemoryCandidate with LLM extraction metadata"
```

---

### Task 2: LLM extract backend protocol + mock

**Files:**
- Create: `src/session2memory/llm_extract.py`
- Create: `src/session2memory/llm_extract_mock.py`
- Test: `tests/test_llm_extract.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_extract.py
import json
from pathlib import Path

from session2memory.llm_extract import LlmExtractItem, merge_llm_candidates
from session2memory.llm_extract_mock import MockLlmExtractBackend
from session2memory.models import EvidencePointer, MemoryCandidate, SessionMessage


def _msg(text: str, index: int = 1) -> SessionMessage:
    evidence = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=index,
        message_end=index,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:x",
    )
    return SessionMessage(index=index, role="assistant", text=text, timestamp=None, raw_pointer=evidence)


def test_mock_backend_parses_items() -> None:
    payload = [
        {
            "kind": "decision",
            "text": "Use uv",
            "evidence_quote": "use uv for python",
            "confidence": 0.9,
            "durable_suggestion": False,
            "message_index": 1,
        }
    ]
    backend = MockLlmExtractBackend(items_payload=payload)
    items = backend.extract(messages=[_msg("use uv for python")], workspace_id="repo-123")
    assert len(items) == 1
    assert items[0].text == "Use uv"


def test_merge_dedupes_against_marker_candidate() -> None:
    evidence = _msg("Decision: use pip").raw_pointer
    marker = MemoryCandidate(
        kind="decision",
        text="use pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=True,
        extraction="marker",
    )
    llm = MemoryCandidate(
        kind="decision",
        text="use pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=False,
        extraction="llm",
        confidence=0.7,
    )
    merged = merge_llm_candidates(existing=[marker], llm_candidates=[llm])
    assert merged == []
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/test_llm_extract.py -q`

- [ ] **Step 3: Implement minimal modules**

`llm_extract.py` — define `LlmExtractItem` dataclass, `Protocol` for backend, `merge_llm_candidates` using `normalize_review_text` from `review_normalize.py`.

`llm_extract_mock.py` — map `message_index` to message list, build `LlmExtractItem` list.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/session2memory/llm_extract.py src/session2memory/llm_extract_mock.py tests/test_llm_extract.py
git commit -m "feat: add LLM extract backend protocol and mock"
```

---

### Task 3: Convert LLM items → MemoryCandidate in pipeline

**Files:**
- Modify: `src/session2memory/llm_extract.py`
- Modify: `src/session2memory/pipeline.py`
- Test: `tests/test_llm_extract.py` (add integration-style test)

- [ ] **Step 1: Failing test `test_items_to_candidates`**

```python
def test_items_to_candidates_maps_message_index() -> None:
    from session2memory.llm_extract import items_to_candidates

    messages = [_msg("hello world", 2)]
    items = [
        LlmExtractItem(
            kind="decision",
            text="Greet",
            evidence_quote="hello",
            confidence=0.8,
            durable_suggestion=False,
            message_index=2,
        )
    ]
    out = items_to_candidates(
        items=items, messages=messages, workspace_id="repo-123"
    )
    assert len(out) == 1
    assert out[0].extraction == "llm"
    assert out[0].evidence.message_start == 2
```

- [ ] **Step 2–4: Implement `items_to_candidates`; extend `run_pipeline` signature:**

```python
def run_pipeline(..., llm_extract: bool = False, llm_backend: LlmExtractBackend | None = None) -> ...
```

After marker `extract_candidates`, if `llm_extract` and backend: collect non-noise messages per session, call backend, merge.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: wire LLM extraction into import pipeline"
```

---

### Task 4: Writer emits new review fields

**Files:**
- Modify: `src/session2memory/writer.py`
- Test: `tests/test_writer.py` or extend existing writer test file (grep for `_review_record`)

- [ ] **Step 1: Failing test — review JSONL contains extraction/confidence**

```python
def test_review_record_includes_llm_fields(tmp_path: Path) -> None:
    # build candidate with extraction="llm", confidence=0.5
    # call _review_record or write_output small fixture
    # assert "extraction" in json.loads(line)
```

- [ ] **Step 2–4: Update `_review_record`:**

```python
record = {
    ...
    "extraction": candidate.extraction,
}
if candidate.confidence is not None:
    record["confidence"] = candidate.confidence
if candidate.evidence_quote:
    record["evidence_quote"] = candidate.evidence_quote
```

- [ ] **Step 5: Commit**

---

### Task 5: CLI flags for import

**Files:**
- Modify: `src/session2memory/cli.py`
- Test: `tests/test_cli_import.py` (create if missing) or `tests/test_llm_extract.py`

- [ ] **Step 1: Test dry-run import with mock backend via env or `--llm-backend mock`**

Add `--llm-extract` and `--llm-backend` (`mock` | `subprocess`, default subprocess when extract on).

For tests, use `--llm-backend mock` wired to `MockLlmExtractBackend` in cli when name is `mock`.

- [ ] **Step 2–4: Implement options on `import_sessions`**

- [ ] **Step 5: Commit**

---

### Task 6: Subprocess backend + prompt file

**Files:**
- Create: `src/session2memory/prompts/llm_extract.txt`
- Modify: `src/session2memory/llm_extract.py` (`SubprocessLlmExtractBackend`)

- [ ] **Step 1: Implement backend that writes JSON messages to temp file, calls:**

`acpx codex exec --timeout 120 '<prompt>'` (or configurable via `SESSION2MEMORY_LLM_CMD` env)

- [ ] **Step 2: Parse JSON array from stdout; validate schema; on failure return `[]` and log to stderr**

- [ ] **Step 3: Test with mock only in CI; subprocess test marked `@pytest.mark.integration`**

- [ ] **Step 4: Commit**

---

### Task 7: Textual review UI

**Files:**
- Create: `src/session2memory/review_ui.py`
- Modify: `src/session2memory/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_review_ui.py`

- [ ] **Step 1: Add dependency group**

```toml
[dependency-groups]
ui = ["textual>=0.79,<1"]
```

- [ ] **Step 2: Failing smoke test**

```python
# tests/test_review_ui.py
from session2memory.review_ui import ReviewAppConfig, build_review_app


def test_build_review_app_has_list_screen(tmp_path: Path) -> None:
    # use write_review_fixture from test_promote.py
    app = build_review_app(
        ReviewAppConfig(output_dir=tmp_path, date="2026-05-22")
    )
    assert app is not None
```

- [ ] **Step 3: Implement `review_ui.py`**

Screens:
- `ReviewListScreen` — `DataTable` from `list_reviews()`
- `ReviewDetailScreen` — text + keybindings `a` approve, `r` reject, `p` promote (confirm)
- Use `inspect_review()` for preview pane

`build_review_app` returns `ReviewApp` without running; `run_review_ui(config)` calls `app.run()`.

- [ ] **Step 4: CLI**

```python
@review_app.command("ui")
def review_ui(...):
    try:
        from session2memory.review_ui import run_review_ui, ReviewAppConfig
    except ImportError:
        raise typer.BadParameter("Install UI deps: uv sync --group ui")
    run_review_ui(ReviewAppConfig(...))
```

- [ ] **Step 5: Headless pilot test (optional)**

```python
async def test_approve_keybinding():
    async with app.run_test() as pilot:
        await pilot.press("a")
```

- [ ] **Step 6: Commit**

---

### Task 8: Docs + review list shows LLM fields

**Files:**
- Modify: `README.md`
- Modify: `src/session2memory/cli.py` (`_review_source_label` or list formatter)

- [ ] **Step 1: README section under Review workflow for `review ui` and `--llm-extract`**

- [ ] **Step 2: `review list` prints `extraction` and `confidence` when present**

- [ ] **Step 3: Run full suite**

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: document LLM extract and review TUI"
```

---

## Spec coverage (Phase 1 self-review)

| Spec requirement | Task |
|------------------|------|
| Optional LLM extractor | 2–6 |
| Review-only (no auto memories) | 3 (pipeline only adds candidates) |
| Dedup with markers | 2 |
| Textual TUI | 7 |
| CLI preserved | 7 |
| Pluggable backend | 2, 6 |
| Sync-back unchanged | (no tasks — already shipped) |

## Execution handoff

Plan saved. **Phase 2 plan:** `docs/plans/2026-06-02-session2memory-phase2-state-platform.md`

**1. Subagent-Driven** — fresh subagent per task, review between tasks  
**2. Inline Execution** — execute in this session with checkpoints

Which approach?
