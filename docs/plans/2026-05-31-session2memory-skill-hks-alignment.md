# session2memory Skill HKS Alignment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align agent-facing session2memory skill docs with HKS agent profile ingest (`hks_workspace_ingest_session_memory`) and `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`, without changing CLI code or operator scripts.

**Architecture:** Rewrite `SKILL.md` into agent vs operator paths; mirror contract in `skill.json`, `openai.yaml`, and README; update `tests/test_skill.py` assertions. Spec: `docs/specs/2026-05-31-session2memory-skill-hks-alignment-design.md`.

**Tech Stack:** Markdown skill, JSON metadata, pytest string contract tests.

---

### Task 1: Update test contract (TDD)

**Files:**
- Modify: `tests/test_skill.py`

- [ ] **Step 1:** Replace `ks ingest` assertions with agent-profile + security assertions (see Task 1 code in implementation).
- [ ] **Step 2:** `uv run pytest tests/test_skill.py -q` — expect FAIL until SKILL updated.
- [ ] **Step 3:** Commit after Task 2 passes.

### Task 2: Rewrite SKILL.md

**Files:**
- Modify: `skills/session2memory/SKILL.md`

Sections: Scope, Import (export root), Review, Security, HKS ingest (agents), Operator batch ingest, Expected noise, Sanity checks.

- [ ] **Step 1:** Apply full SKILL rewrite; grep must show zero `seesion2memory`.
- [ ] **Step 2:** `uv run pytest tests/test_skill.py -q` — PASS.

### Task 3: skill.json + openai.yaml

**Files:**
- Modify: `skills/session2memory/skill.json`, `skills/session2memory/agents/openai.yaml`

- [ ] Update `outputs.generated_source`, `commands.hks_*`, `default_prompt`.
- [ ] `uv run pytest tests/test_skill.py -q` — PASS.

### Task 4: README.md

**Files:**
- Modify: `README.md` (HKS + Operator subsections)

- [ ] Agent HKS section matches SKILL; operator points to `scripts/daily-session-memory-to-hks.sh`.

### Task 5: Final verification

```bash
uv run pytest tests/test_skill.py -q
uv run ruff check tests/test_skill.py
rg -n 'seesion2memory' skills/session2memory/SKILL.md README.md || true
```

- [ ] Commit: `docs: align session2memory skill with HKS agent profile ingest`
