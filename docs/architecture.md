# Architecture, flows and sequences

Diagrams of what the code in `src/session2memory/` does today, written in Mermaid so
they are reviewed and changed with the code. The README describes the commands; when a
diagram and the code disagree, the code is right and this page needs fixing.

## 1. Components

session2memory only reads the agents' session stores. What it writes is its own store
(one SQLite database plus a rendered Markdown tree) and, when asked, one hook entry in an
agent's settings.

```mermaid
flowchart LR
    subgraph agents["Agent session stores (read-only)"]
        codex["Codex<br/>rollout-*.jsonl(.zst)"]
        claude["Claude Code<br/>projects/*/*.jsonl"]
        grok["Grok<br/>chat_history.jsonl"]
        muse["Muse<br/>session.jsonl"]
        agy["Antigravity CLI<br/>transcript_full.jsonl"]
        cursor["Cursor<br/>store.db · agent-transcripts"]
        oc["OpenClaw native<br/>openclaw-agent.sqlite"]
    end

    subgraph s2m["session2memory"]
        adapters["adapters/<br/>one reader per tool"]
        registry["state/sources<br/>source registry"]
        auto["auto/<br/>scan · files · curate · reconcile<br/>semantic · export"]
        hook["session_start<br/>hook_install"]
        handoff["handoff<br/>handoff_find"]
        llm["LLM command<br/>claude -p, haiku, no tools"]
    end

    subgraph store["Store (~/.local/share/session2memory/store)"]
        db[("session2memory.db")]
        md[("memory/<br/>projects/*.md · sessions/YYYY-MM/*.md")]
        arch[("archive/ · holding/")]
    end

    hks["HKS<br/>ks update"]
    newsession["A new agent session"]

    codex --> adapters
    claude --> adapters
    grok --> adapters
    muse --> adapters
    agy --> adapters
    cursor --> adapters
    oc --> adapters
    adapters --> registry
    adapters --> auto
    adapters --> handoff
    registry --> db
    auto --> db
    auto <--> llm
    auto --> md
    auto --> arch
    md -- "--index-cmd" --> hks
    db --> hook
    handoff --> hook
    hook -- "context at session start" --> newsession
```

## 2. The unattended run

`session2memory auto run` (`auto/run.py`), usually from `scripts/nightly-auto.sh`. A
lock file keeps two runs apart. Every step reports on its own, and a failing step does
not stop the ones after it.

```mermaid
flowchart TD
    start(["auto run"]) --> lock{"lock free?"}
    lock -- "no" --> skipped(["skipped: another run holds the lock"])
    lock -- "yes" --> scan

    scan["scan<br/>read sessions changed since the last scan<br/>register them, store a digest"]
    compress["compress<br/>zstd-compress cold Codex rollouts of homes Codex<br/>does not maintain; purge the holding area"]
    archive["archive<br/>copy cold Claude transcripts to the archive"]
    curate["curate<br/>one LLM call per project batch:<br/>summarise sessions, add / confirm / supersede / retire memory<br/>then mark long-unconfirmed memory stale"]
    reconcile["reconcile<br/>one LLM call per project whose memory changed:<br/>retire the older of two contradicting entries, merge repeats"]
    embed["embed<br/>vectors for auto search<br/>(only with the semantic extra)"]
    export["export<br/>render the database as Markdown"]
    index["index<br/>optional --index-cmd, e.g. HKS ks update"]
    report(["store the run report"])

    scan --> compress --> archive --> curate --> reconcile --> embed --> export --> index --> report
```

`--dry-run` works on an in-memory copy of the database and skips `curate`, `reconcile`,
`embed`, `export` and `index`.

## 3. From a session to memory

What `scan` and `curate` do with one session. The model never sees a transcript, only
the digest: the person's requests and the reply each one ended with.

```mermaid
flowchart TD
    file["session file changed since the last scan"] --> read["adapter reads it<br/>session id, messages, content digest"]
    read --> observe{"source registry:<br/>seen this content?"}
    observe -- "same content, another path" --> alias["record the path as another location<br/>nothing is read again"]
    observe -- "new session or new content" --> digest["build_digest<br/>first turn plus as many recent turns as fit"]
    digest --> pending["auto_sessions: pending curation"]

    pending --> scope{"in --curate-scope?"}
    scope -- "no" --> wait["stays pending, no call spent"]
    scope -- "yes" --> filter{"too old, temporary<br/>workspace, or trivial?"}
    filter -- "yes" --> closed["marked curated without a call"]
    filter -- "no" --> batch["batched with other sessions of the project,<br/>together with the project's active memory"]
    batch --> budget{"call budget left?"}
    budget -- "no" --> later["left for the next run"]
    budget -- "yes" --> llmcall["LLM call"]
    llmcall --> validate["every proposed operation is validated:<br/>evidence must be sessions of this batch,<br/>ids must be active memories of this project"]
    validate --> apply["apply: summaries stored,<br/>memory added / confirmed / superseded / retired"]
```

## 4. Life of a memory entry

Nothing is deleted. An entry only changes status, and every change is written to
`auto_memory_events` with its reason and evidence. Only `active` entries are searched,
exported and injected.

```mermaid
stateDiagram-v2
    [*] --> active: curate adds it
    active --> active: confirmed by a later session
    active --> superseded: curate has a newer wording, or reconcile merges repeats
    active --> retired: curate finds it no longer true, or reconcile finds a newer entry contradicting it
    active --> stale: not confirmed for 120 days
    superseded --> active: the same text is added again
    retired --> active: the same text is added again
    stale --> active: the same text is added again
```

Guards in `reconcile`: an entry is retired only when the reply names a listed entry that
is at least as recently confirmed and stays active, and one call can retire at most half
of what it was shown.

## 5. Session start

`session2memory session-start-hook`, installed by `hook-install`. It never fails the
session: any problem means nothing is injected.

```mermaid
sequenceDiagram
    autonumber
    actor P as Person
    participant A as Agent (Claude Code, Codex, Muse, Grok, Antigravity, Cursor)
    participant H as session-start-hook
    participant D as session2memory.db (read-only)
    participant M as Other agents' memory files
    participant S as Agents' session stores
    participant L as Delivery ledger

    P->>A: start a session in a workspace
    A->>H: hook payload on stdin (session id, workspace)
    H->>H: skip temporary directories, the home directory, automated sessions
    alt started by an OpenClaw agent
        H->>D: OpenClaw-scoped project memory only
    else a new session or one just cleared (a resumed session gets nothing)
        H->>D: distilled project memory (active entries)
        H->>M: titles and paths of other agents' memory
        opt a new session (not after a clear)
            H->>S: most recent previous session of this workspace
            H->>L: was this state of it handed over already?
            L-->>H: no
            H->>H: build the handoff packet within the size budget
            H->>L: record the delivery
        end
    end
    H-->>A: context in the shape this agent reads
    A-->>P: the session starts with that context
```

Each agent takes the hook's answer in its own shape:

| Agent | Event | Answer |
| --- | --- | --- |
| Claude Code, Codex, Muse | `SessionStart` | `hookSpecificOutput.additionalContext` |
| Grok | `PostToolUse`, first tool call only | `hookSpecificOutput.additionalContext` |
| Antigravity CLI | `SessionStart` | `injectSteps` with a system message |
| Cursor | `sessionStart` | `additional_context` |

## 6. Data model

One SQLite database. The registry tables hold no transcript text.

```mermaid
erDiagram
    source_homes ||--o{ sources : "first seen in"
    sources ||--|{ source_versions : "has"
    sources ||--|{ source_locations : "is found at"
    sources ||--o{ source_messages : "has"
    sources ||--o| auto_sessions : "is digested as"
    auto_sessions }o--o{ auto_memories : "is evidence for"
    auto_memories ||--o{ auto_memory_events : "has"
    auto_memories |o--o| auto_memories : "supersedes"
    auto_memories ||--o{ auto_vectors : "is embedded as"
    auto_sessions ||--o{ auto_vectors : "is embedded as"
    auto_runs ||--o{ auto_memory_events : "caused"

    source_homes {
        text home_id PK
        text tool
        text agent
        text root
    }
    sources {
        text source_key PK "tool:session id, never a path"
        text tool
        text session_id
        text workspace_id
        int version_no
    }
    source_versions {
        text source_key PK
        int version_no PK
        text content_digest
        int record_count
    }
    source_locations {
        text source_key PK
        text path PK
        text encoding
        text state
        text content_digest
    }
    source_messages {
        text source_key PK
        text locator PK "path-independent position"
        text role
        text message_digest
    }
    auto_sessions {
        text source_key PK
        text scope "coding or openclaw"
        text project
        text digest
        text summary
        int digest_version
        int curated_version
    }
    auto_memories {
        text memory_id PK
        text project
        text scope
        text kind
        text text
        text status "active, superseded, retired, stale"
        text last_confirmed_at
        text supersedes_id
        text evidence_json "source keys"
    }
    auto_memory_events {
        int id PK
        text memory_id
        text op
        text detail_json
        text run_id
    }
    auto_vectors {
        text ref PK
        text model PK
        blob vector
    }
    auto_runs {
        text run_id PK
        text report_json
    }
```

## 7. Hand-over to HKS

HKS never reads an agent's session store. It indexes the Markdown that `export` writes.

```mermaid
flowchart LR
    db[("session2memory.db")] --> export["auto export"]
    export --> projects["memory/projects/*.md<br/>active memory of one workspace"]
    export --> sessions["memory/sessions/YYYY-MM/*.md<br/>one summarised session"]
    projects -- "--index-cmd" --> ks["HKS ks update"]
    sessions -- "--index-cmd" --> ks
    ks --> query["ks query through CLI, MCP or HTTP"]
```
