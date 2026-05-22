# session2memory Design

## Status

Approved in conversation on 2026-05-22.

## Problem

Developers move between Claude Code, Codex, Qwen Code, OpenCode, and similar coding agents. Each tool stores sessions locally in a different format, so later agents lose prior work context: decisions, verified commands, failed paths, workspace-specific assumptions, and source evidence.

The failure mode is not lack of storage. The failure mode is low-quality ingestion. Raw transcripts contain system prompts, tool logs, stack traces, huge stdout, and unrelated environment context. Putting that directly into HKS would damage retrieval quality. Very short summaries are also insufficient because reviewers and future agents need provenance.

## Goals

- Generate one HKS-ingestable folder for a target date.
- Keep raw transcripts outside HKS.
- Preserve provenance for every durable memory.
- Separate daily work logs from durable workspace memories.
- Group memories by workspace/repo so unrelated projects do not pollute each other.
- Support P0 adapters for Claude Code, Codex, Qwen Code, and OpenCode.
- Keep extraction conservative and deterministic for P0.

## Non-Goals

- General Claude Desktop chat.
- Cloud-side IDE plugin chat.
- Gemini/Antigravity protobuf decoding.
- Daemon, watcher, or background service.
- HKS core changes.
- Automatic writeback into HKS wiki.
- Treating summaries as source of truth without evidence.
- LLM summarization in P0.

## Recommended Approach

Build `session2memory` as a separate Python CLI using `uv`, not as an HKS core feature. The CLI reads local session stores, normalizes supported formats into a shared model, filters noise, extracts conservative memory candidates, writes Markdown and JSONL output, then lets HKS ingest or update that generated folder.

This keeps HKS responsible for retrieval and source ingestion, while `session2memory` owns session decoding, provenance, and memory distillation.

## CLI Surface

P0 command:

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

Optional flags:

```bash
uv run session2memory import --date 2026-05-22 --tool codex --output ./out/session-memory
uv run session2memory import --date 2026-05-22 --workspace /path/to/repo --output ./out/session-memory
uv run session2memory import --date 2026-05-22 --dry-run
```

HKS usage stays external:

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```

## Input Discovery

Default source roots are discovered per tool:

- Codex: `~/.codex/sessions/YYYY/MM/DD/*.jsonl`
- Claude Code: `~/.claude/projects/**/*.jsonl` and local metadata under `~/Library/Application Support/Claude/claude-code-sessions`
- Qwen Code: `~/.qwen/projects/*/chats/*.jsonl`
- OpenCode: `~/.local/share/opencode/opencode.db`

Adapters must tolerate missing roots and unreadable sessions. Missing roots are reported in the run manifest, not treated as fatal.

## Normalized Model

All adapters emit `SessionRecord`:

```python
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
```

Each `SessionMessage` contains:

```python
@dataclass(frozen=True)
class SessionMessage:
    index: int
    role: Literal["user", "assistant", "tool", "system", "unknown"]
    text: str
    timestamp: datetime | None
    raw_pointer: EvidencePointer
```

Each `EvidencePointer` contains:

```python
@dataclass(frozen=True)
class EvidencePointer:
    tool: str
    session_id: str
    source_path: Path
    message_start: int
    message_end: int
    workspace_path: Path | None
    digest: str
```

For JSONL sources, `message_start` and `message_end` are 1-based inclusive line indexes. For JSON object sources, they are 1-based inclusive message indexes. For OpenCode SQLite, they are 1-based inclusive ordered message or part indexes inside a session.

## Workspace Identity

Workspace identity is deterministic:

1. Resolve canonical opened `cwd`.
2. Find nearest git repo root from `cwd`.
3. If a repo root exists, group by repo root.
4. If no repo root exists, group by canonical `cwd`.
5. Include tool-specific workspace id when available, but do not use it as the primary grouping key.

The workspace id slug is derived from canonical root path plus a short digest:

```text
hks-8e13a9c2
openclaw-4b19d1e0
home-waynetu-37a4b991
```

Nested paths inside one repository must map to the same workspace memory file.

## Noise Filtering

The extraction pipeline filters before memory creation. P0 excludes:

- system prompts and base instructions
- AGENTS.md / CLAUDE.md / GEMINI.md pasted instruction blocks
- tool call arguments unless they contain a user-visible decision or command
- huge command output
- stack traces without a decision, fix, or verified result
- telemetry events
- raw model reasoning or thought blocks
- dependency install chatter
- repeated environment boilerplate

Filtered content can still be referenced by evidence pointer if a durable memory depends on it. It is not copied into Markdown output.

## Memory Extraction

P0 is deterministic and conservative. It extracts only high-signal items:

- decisions explicitly accepted by the user
- completed work with concrete file, command, or artifact evidence
- failed approaches that future agents should avoid
- workspace-specific constraints or operating rules
- verification commands and their meaningful pass/fail outcome

The extractor prefers missing a weak memory over inventing one. If a candidate cannot point to source evidence, it is discarded.

Durable memory candidates are grouped by workspace and written into `memories/<workspace-id>.md`. Short-lived activity is written into `daily/YYYY-MM-DD.md`.

## Output Layout

The output folder is the only folder HKS should ingest:

```text
session-memory/
  daily/
    2026-05-22.md
  memories/
    hks-8e13a9c2.md
    openclaw-4b19d1e0.md
  evidence/
    index.jsonl
  manifest.json
```

`daily/YYYY-MM-DD.md` contains:

- sessions scanned by tool
- workspace sections
- completed work summary
- open follow-ups
- evidence ids for each item

`memories/<workspace-id>.md` contains durable workspace facts only:

- accepted decisions
- repo conventions
- known pitfalls
- stable commands
- verified workflow notes

`evidence/index.jsonl` contains one evidence record per pointer:

```json
{"id":"ev_20260522_0001","tool":"codex","session_id":"019e4d56-b5da-7350-8cca-bec273fe8b6e","source_path":"/Users/waynetu/.codex/sessions/2026/05/22/example.jsonl","message_start":12,"message_end":18,"workspace_path":"/Users/waynetu/claw_prog/projects/04-kurisu-github/hks","digest":"sha256:2d711642b726b04401627ca9fbac32f5da7f1f3d5cb70902fe974225a5f2a2a3"}
```

`manifest.json` contains:

- generator version
- run date
- scanned tools
- source roots
- output file list
- skipped sessions with reasons
- counts for sessions, messages, filtered items, daily entries, durable memories, and evidence records

## Provenance and Integrity

Each memory item includes an evidence id, not copied raw transcript. Evidence ids resolve through `evidence/index.jsonl` to the original tool, session id, source path, message range, workspace path, and digest.

Digest is computed from the normalized source message text used for that evidence range. This allows later verification even if line endings or unrelated metadata change.

## HKS Compatibility

The generated folder uses Markdown plus JSONL/JSON metadata so HKS can ingest it through existing `ks ingest` or `ks update`. The HKS source root is the generated folder, not the original session roots.

The compatibility test must prove:

- HKS can ingest the generated output folder.
- HKS source catalog does not include raw transcript session paths.
- Querying HKS can retrieve the generated daily and durable memory documents.

## Error Handling

- Missing tool roots produce manifest warnings.
- Malformed session records are skipped with a reason and source path.
- If no sessions match the date, write a manifest and no memory docs.
- If output already exists, rewrite generated files atomically from an in-memory run result.
- If evidence cannot be written, fail the run because durable memories without evidence violate the product contract.

## Testing Strategy

Test external behavior, not parser internals.

Required tests:

- Fixture adapters for Claude Code, Codex, Qwen Code, and OpenCode.
- Workspace grouping from nested cwd/repo paths.
- Evidence pointer round-trip from Markdown item to `evidence/index.jsonl` and back to source range.
- Noise filtering for tool output, system prompts, AGENTS.md, stack traces, huge command output, telemetry, and thought blocks.
- HKS compatibility using generated output as the ingest source.

P0 test command:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

## Implementation Boundary

Do not add a daemon, watcher, HKS plugin, or HKS core patch. Do not import raw transcript directories into HKS. Do not add LLM summarization until deterministic provenance and filtering are working end to end.
