# session2memory Adjustments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend session2memory with openclaw/hermes P0 importers, read-only agentic-os evidence enrichment, and bulk/conflict/semantic-dedup promote workflow—while keeping HKS durable memory gated solely on `review promote`.

**Architecture:** Harness adapters stay the text source; `AgenticOsIndex` enriches `evidence/index.jsonl` from `~/.agentic-os/agentic-os.db` (`sessions.agent_id` + log paths). Review changes live in `review.py` (+ small helpers); CLI wires Typer commands. No agentic-os MemoryStore reads.

**Tech Stack:** Python 3.12, Typer, uv, pytest, sqlite3 (stdlib), existing `SessionRecord` / `MemoryCandidate` models.

**Spec:** `docs/superpowers/specs/2026-05-31-session2memory-adjustments-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `src/session2memory/agentic_os_index.py` | Read-only SQLite index; session/audit lookup |
| `src/session2memory/review_normalize.py` | `normalize_review_text()` for conflict/semantic dedup |
| `src/session2memory/review_bulk.py` | Bulk approve/reject filters + dry-run |
| `src/session2memory/review_conflicts.py` | Conflict groups + resolve strategies |
| `src/session2memory/adapters/text_log.py` | Shared line-scanner for openclaw/hermes logs |
| `src/session2memory/adapters/openclaw.py` | openclaw log adapter |
| `src/session2memory/adapters/hermes.py` | hermes log adapter |
| `src/session2memory/cli.py` | P0_TOOLS, flags, new review commands |
| `src/session2memory/adapters/__init__.py` | Export new adapters |
| `src/session2memory/writer.py` | Accept optional agentic-os enrichment callback |
| `src/session2memory/pipeline.py` | Pass `AgenticOsIndex` into writer; optional session filter |
| `src/session2memory/review.py` | Atomic promote, semantic dedup, integrate conflicts |
| `README.md`, `skills/session2memory/SKILL.md`, `skill.json` | Docs parity with P0_TOOLS |
| `tests/fixtures/openclaw/sample.log` | Minimal log fixture |
| `tests/fixtures/hermes/sample.log` | Minimal log fixture |
| `tests/fixtures/agentic_os/minimal.db` | Built in-test or committed sqlite |
| `tests/test_agentic_os_index.py` | Index unit tests |
| `tests/test_adapter_openclaw.py` | Adapter tests |
| `tests/test_adapter_hermes.py` | Adapter tests |
| `tests/test_review_bulk.py` | Bulk CLI + core |
| `tests/test_review_conflicts.py` | Conflicts + resolve |
| `tests/test_promote.py` | Extend semantic dedup / atomic |

---

## Pre-Task 0: Unblock openclaw/hermes parser (human, 10 min)

- [ ] Copy one real file each from `~/.openclaw/logs` and `~/.hermes/logs` into `tests/fixtures/openclaw/` and `tests/fixtures/hermes/` (sanitize secrets).
- [ ] Note session boundary rule (file-per-session vs subdirs) in fixture README one-liner at top of sample file comment.

If samples unavailable, use synthetic fixture:

```text
# tests/fixtures/openclaw/sample.log
[user] Decision: use marker extraction only
[assistant] Done: shipped adapter
```

---

## Phase 1 — Docs + P0 alignment + AgenticOsIndex

### Task 1: Align P0_TOOLS documentation

**Files:**
- Modify: `README.md`, `skills/session2memory/SKILL.md`, `skills/session2memory/skill.json`
- Test: `tests/test_skill.py`

- [ ] **Step 1: Update test expectation for supported tools**

```python
# tests/test_skill.py — extend existing skill metadata test
EXPECTED_TOOLS = {
    "codex", "claude", "qwen", "opencode",
    "cursor", "cursor-cli", "openclaw", "hermes",
}
# assert set(skill_json["supported_tools"]) == EXPECTED_TOOLS
```

- [ ] **Step 2: Run test (expect FAIL until skill.json updated)**

Run: `cd /Users/waynetu/claw_prog/projects/04-kurisu-github/seesion2memory && uv run pytest tests/test_skill.py -q`  
Expected: FAIL on missing openclaw/hermes in skill.json

- [ ] **Step 3: Update README Supported P0 Sources table + SKILL.md `--tool` list** (include cursor, cursor-cli, openclaw, hermes placeholders: "adapter landing in this change").

- [ ] **Step 4: Update `skill.json` `supported_tools`** to match future `P0_TOOLS` (all 8 names).

- [ ] **Step 5: Run test — PASS**

- [ ] **Step 6: Commit**

```bash
git add README.md skills/session2memory/SKILL.md skills/session2memory/skill.json tests/test_skill.py
git commit -m "docs: align P0 tool list with planned importers"
```

---

### Task 2: `normalize_review_text` helper

**Files:**
- Create: `src/session2memory/review_normalize.py`
- Create: `tests/test_review_normalize.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_review_normalize.py
from session2memory.review_normalize import normalize_review_text

def test_normalize_collapses_whitespace() -> None:
    assert normalize_review_text("  foo \n  bar  ") == "foo bar"
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError`)

Run: `uv run pytest tests/test_review_normalize.py -q`

- [ ] **Step 3: Implement**

```python
# src/session2memory/review_normalize.py
import re

_COLLAPSE = re.compile(r"\s+")

def normalize_review_text(text: str) -> str:
    return _COLLAPSE.sub(" ", text.strip())
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit** `feat: add review text normalizer for conflicts`

---

### Task 3: `AgenticOsIndex` read-only

**Files:**
- Create: `src/session2memory/agentic_os_index.py`
- Create: `tests/test_agentic_os_index.py`

**Schema reference (agentic-os `sessions`):** `id`, `agent_id` (harness), `cwd`, `stdout_log`, `stderr_log`, `started_at`, `updated_at`.

- [ ] **Step 1: Failing test with in-memory sqlite**

```python
# tests/test_agentic_os_index.py
import sqlite3
from pathlib import Path

from session2memory.agentic_os_index import AgenticOsIndex

def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE sessions (
      id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, cwd TEXT NOT NULL,
      argv_json TEXT NOT NULL, env_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL, artifact_dir TEXT NOT NULL,
      stdout_log TEXT NOT NULL, stderr_log TEXT NOT NULL,
      started_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE audit_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      domain TEXT NOT NULL, entity_id TEXT NOT NULL,
      event_type TEXT NOT NULL, message TEXT NOT NULL,
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO sessions VALUES (
      'aos-1','codex','/tmp/repo','[]','{}','stopped',
      '/tmp/a','/tmp/out.log','/tmp/err.log',
      '2026-05-22T10:00:00+00:00','2026-05-22T10:05:00+00:00'
    );
    INSERT INTO audit_events (domain, entity_id, event_type, message)
    VALUES ('session','aos-1','started','ok');
    """)
    conn.commit()
    conn.close()

def test_lookup_by_log_path(tmp_path: Path) -> None:
    db = tmp_path / "agentic-os.db"
    _make_db(db)
    index = AgenticOsIndex.open(db)
    meta = index.lookup_log_path(Path("/tmp/out.log"))
    assert meta is not None
    assert meta.session_id == "aos-1"
    assert meta.agent_id == "codex"
    assert meta.audit_ids == [1]
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `AgenticOsMeta` dataclass + `open` + `sessions_for_date` + `lookup_log_path` + `enrich_evidence_record(record: dict) -> dict`**

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit** `feat: add read-only agentic-os evidence index`

---

### Task 4: Wire index into import (writer enrichment)

**Files:**
- Modify: `src/session2memory/writer.py` (`_evidence_record`)
- Modify: `src/session2memory/pipeline.py`
- Modify: `src/session2memory/cli.py` (`--agentic-os-root`, `--no-agentic-os`, discover line)
- Test: `tests/test_writer_pipeline.py` or new `tests/test_agentic_os_import.py`

- [ ] **Step 1: Test — evidence gets optional fields when index matches `source_path`**

```python
def test_write_output_enriches_evidence_from_agentic_os(tmp_path: Path) -> None:
    # build minimal candidate + AgenticOsIndex fixture db pointing at candidate source_path
    # run write_output(..., agentic_os_index=index)
    # parse evidence/index.jsonl → assert agentic_os_session_id present
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Add optional `agentic_os_index: AgenticOsIndex | None` to `write_output` / `run_pipeline`; CLI builds index unless `--no-agentic-os`**

- [ ] **Step 4: `discover` prints** `agentic-os evidence=found db=...` when db exists

- [ ] **Step 5: Run targeted tests — PASS**

Run: `uv run pytest tests/test_agentic_os_index.py tests/test_writer_pipeline.py -q`

- [ ] **Step 6: Commit** `feat: enrich evidence from agentic-os index on import`

---

## Phase 2 — openclaw / hermes adapters

### Task 5: Shared text log adapter helper

**Files:**
- Create: `src/session2memory/adapters/text_log.py`
- Create: `tests/test_adapter_text_log.py`

- [ ] **Step 1: Test scans mtime + emits one SessionRecord per file**

```python
from pathlib import Path
from session2memory.adapters.text_log import iter_text_log_sessions

def test_iter_text_log_sessions_by_mtime(tmp_path: Path) -> None:
    log = tmp_path / "2026" / "s1.log"
    log.parent.mkdir(parents=True)
    log.write_text("[user] Decision: x\n", encoding="utf-8")
    # touch mtime to 2026-05-22 (use os.utime in test)
    records = list(iter_text_log_sessions(root=tmp_path, tool="openclaw", date="2026-05-22"))
    assert len(records) == 1
    assert records[0].messages[0].text == "Decision: x"
```

- [ ] **Step 2–4: Implement line parser** — strip `[role]` prefix if present; else `role=unknown`; `session_id=log.stem`.

- [ ] **Step 5: Commit** `feat: add shared text log session iterator`

---

### Task 6: OpenClaw adapter

**Files:**
- Create: `src/session2memory/adapters/openclaw.py`
- Create: `tests/test_adapter_openclaw.py`
- Modify: `src/session2memory/adapters/__init__.py`, `cli.py` (`P0_TOOLS`, `DEFAULT_SOURCE_ROOTS`, `ADAPTERS`)

- [ ] **Step 1: Extend `P0_TOOLS` test in `tests/test_cli.py`**

```python
def test_import_accepts_openclaw_tool(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["import", "--date", "2026-05-22",
        "--tool", "openclaw", "--source-root", f"openclaw={tmp_path}",
        "--output", str(tmp_path / "out"), "--dry-run"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Implement `OpenClawAdapter` wrapping `iter_text_log_sessions`**

- [ ] **Step 3: Fixture test with `tests/fixtures/openclaw/sample.log`**

- [ ] **Step 4: Run** `uv run pytest tests/test_adapter_openclaw.py tests/test_cli.py -q`

- [ ] **Step 5: Commit** `feat: add openclaw log adapter`

---

### Task 7: Hermes adapter

**Files:**
- Create: `src/session2memory/adapters/hermes.py`
- Create: `tests/test_adapter_hermes.py`
- Modify: `cli.py` registrations (mirror Task 6)

- [ ] **Step 1–5:** Same pattern as Task 6 with `tool="hermes"` and `tests/fixtures/hermes/sample.log`

- [ ] **Commit** `feat: add hermes log adapter`

---

### Task 8: `--agentic-os-sessions-only` filter (optional)

**Files:**
- Modify: `src/session2memory/pipeline.py`
- Test: `tests/test_agentic_os_import.py`

- [ ] **Step 1: Test — session dropped when log path not in index set for date**

- [ ] **Step 2: Implement filter in pipeline loop when flag set from CLI**

- [ ] **Step 3: Commit** `feat: filter import to agentic-os registered sessions`

---

## Phase 3 — Review bulk, conflicts, promote hardening

### Task 9: Bulk approve/reject core

**Files:**
- Create: `src/session2memory/review_bulk.py`
- Create: `tests/test_review_bulk.py`
- Modify: `src/session2memory/cli.py`

- [ ] **Step 1: Failing unit test**

```python
from pathlib import Path
from session2memory.review_bulk import bulk_update_reviews, BulkFilter

def test_bulk_approve_pending_only(tmp_path: Path) -> None:
    # write review/2026-05-22.jsonl with pending + promoted rows
    result = bulk_update_reviews(
        output_dir=tmp_path, date="2026-05-22",
        target_status="approved", filters=BulkFilter(status="pending"),
        durable=None, note=None, dry_run=False,
    )
    assert result.matched == 1
    assert result.updated == 1
```

- [ ] **Step 2: Implement `BulkFilter` + `BulkResult` + skip promoted rows**

- [ ] **Step 3: CLI** `review approve-bulk` / `review reject-bulk` with Typer options from spec

- [ ] **Step 4: CLI test**

```python
def test_cli_approve_bulk_dry_run(tmp_path: Path) -> None:
    # fixture output dir
    result = CliRunner().invoke(app, [
        "review", "approve-bulk", "--date", "2026-05-22",
        "--output", str(tmp_path), "--dry-run",
    ])
    assert result.exit_code == 0
    assert "matched=" in result.output
```

- [ ] **Step 5: Commit** `feat: add bulk review approve and reject`

---

### Task 10: Conflict detection + `review conflicts`

**Files:**
- Create: `src/session2memory/review_conflicts.py`
- Create: `tests/test_review_conflicts.py`
- Modify: `cli.py`

- [ ] **Step 1: Test groups rows**

```python
from session2memory.review_conflicts import find_conflicts

def test_find_conflicts_same_text_different_evidence() -> None:
    rows = [
        {"id": "r1", "workspace_id": "w", "kind": "decision",
         "text": "Same", "evidence_id": "e1", "status": "approved"},
        {"id": "r2", "workspace_id": "w", "kind": "decision",
         "text": "Same", "evidence_id": "e2", "status": "approved"},
    ]
    groups = find_conflicts(rows)
    assert len(groups) == 1
    assert len(groups[0].review_ids) == 2
```

- [ ] **Step 2: Implement `ConflictGroup` + `find_conflicts`**

- [ ] **Step 3: CLI `review conflicts`**

- [ ] **Step 4: Commit** `feat: detect review conflicts for promote`

---

### Task 11: Promote `--resolve`, semantic dedup, atomic write

**Files:**
- Modify: `src/session2memory/review.py`
- Extend: `tests/test_promote.py`, `tests/test_review_conflicts.py`

- [ ] **Step 1: Test promote aborts without resolve when conflicts**

```python
def test_promote_exits_2_on_unresolved_conflicts(tmp_path: Path) -> None:
    # two approved durable rows, same normalized text, different evidence_id
    result = CliRunner().invoke(app, [
        "review", "promote", "--date", "2026-05-22", "--output", str(tmp_path),
    ])
    assert result.exit_code == 2
```

- [ ] **Step 2: Test semantic dedup skips append but marks promoted**

```python
def test_promote_semantic_duplicate_skips_append(tmp_path: Path) -> None:
    # memories/repo.md already contains normalized text from prior promote
    # second approved row same workspace/kind/text new evidence → promoted=0 append count 1 line total
```

- [ ] **Step 3: Implement in `promote_reviews`:**
  - `resolve: Literal["keep-new","keep-old","skip"] | None`
  - `_semantic_key(row)` without evidence_id
  - `_write_atomic(paths, writer_fn)` using temp files + `Path.replace`

- [ ] **Step 4: Wire `--resolve` on CLI promote; manifest optional counts**

- [ ] **Step 5: Run** `uv run pytest tests/test_promote.py tests/test_review_conflicts.py -q`

- [ ] **Step 6: Commit** `feat: promote conflicts resolve and semantic dedup`

---

## Phase 4 — Integration & verification

### Task 12: HKS compatibility + full suite

**Files:**
- Maybe modify: `tests/integration/test_hks_compatibility.py` if evidence optional keys need assertion

- [ ] **Step 1: Run integration**

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/seesion2memory
uv run pytest tests/integration/test_hks_compatibility.py -q
```

- [ ] **Step 2: If failure, allow extra keys in evidence schema validation only (backward compatible)**

- [ ] **Step 3: Full verification**

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

Expected: all pass

- [ ] **Step 4: Commit** `test: verify HKS compatibility after evidence enrichment`

---

### Task 13: Close README roadmap + final docs

**Files:**
- Modify: `README.md` (Review section: bulk, conflicts; Supported P0 complete; remove stale roadmap bullets)

- [ ] **Step 1: Document new commands with copy-paste examples**

- [ ] **Step 2: Update SKILL.md review section**

- [ ] **Step 3: Commit** `docs: document bulk review and conflict promote`

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| HKS sole gate via promote | Tasks 11, 12 (no agentic-os writes) |
| Dual operational gate unchanged | Documented; no agentic-os API calls |
| P0 openclaw/hermes | Tasks 6–7 |
| cursor in docs | Task 1 |
| agentic-os evidence index | Tasks 3–4, 8 |
| CLI flags | Tasks 4, 8 |
| bulk approve/reject | Task 9 |
| conflicts + --resolve | Tasks 10–11 |
| semantic dedup + atomic promote | Task 11 |
| manifest counts | Task 11 |
| Tests per spec | All test files |
| Out of scope respected | No MemoryStore ingest, no seed automation |

## Plan Self-Review

- [x] Each spec §1–§3 requirement maps to a task.
- [x] No TBD steps; Pre-Task 0 handles log samples.
- [x] `normalize_review_text` defined before conflict/promote tasks.
- [x] `AgenticOsIndex` before writer enrichment.

---

## Execution Handoff

**Plan saved to:** `docs/superpowers/plans/2026-05-31-session2memory-adjustments.md`

**Two execution options:**

1. **Subagent-Driven (recommended)** — one subagent per task, review between tasks  
2. **Inline Execution** — same session, `executing-plans` checkpoints after each phase  

**Which approach?**

**Start command (after choosing):**

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/seesion2memory
# Pre-Task 0: add log fixtures if missing
uv run pytest tests/test_review_normalize.py -q  # first implementation test
```
