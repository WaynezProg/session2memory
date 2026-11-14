CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    canonical_path TEXT,
    repo_root TEXT,
    opened_cwd TEXT,
    tool_workspace_id TEXT
);

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    path TEXT NOT NULL,
    digest TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    last_imported_at TEXT,
    UNIQUE (tool, path)
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    import_date TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    text_normalized TEXT NOT NULL,
    extraction TEXT NOT NULL DEFAULT 'marker',
    confidence REAL,
    evidence_quote TEXT,
    durable INTEGER NOT NULL,
    evidence_id TEXT NOT NULL,
    review_id TEXT NOT NULL UNIQUE,
    review_status TEXT NOT NULL DEFAULT 'pending',
    review_note TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    message_start INTEGER NOT NULL,
    message_end INTEGER NOT NULL,
    message_digest TEXT NOT NULL,
    workspace_path TEXT,
    agent_metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidates_import_date ON candidates (import_date);

CREATE TABLE IF NOT EXISTS memory_entries (
    memory_entry_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    candidate_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    review_ref TEXT NOT NULL,
    tool TEXT,
    session_id TEXT,
    message_start INTEGER,
    message_end INTEGER,
    agent_metadata_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_id TEXT,
    promoted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace ON memory_entries (workspace_id, status);

CREATE TABLE IF NOT EXISTS sync_targets (
    workspace_id TEXT NOT NULL,
    target TEXT NOT NULL,
    dest_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, target, dest_path)
);

-- Source registry: sessions are identified by tool + native session id, messages by a
-- native locator, and every on-disk copy (plain, compressed, archived, another home) is
-- a location of that one source. No transcript text is stored here.
CREATE TABLE IF NOT EXISTS source_homes (
    home_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    agent TEXT NOT NULL,
    root TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent TEXT,
    workspace_id TEXT,
    first_home_id TEXT,
    version_no INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS source_versions (
    source_key TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    reason TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (source_key, version_no)
);

CREATE INDEX IF NOT EXISTS idx_source_versions_digest
    ON source_versions (source_key, content_digest);

CREATE TABLE IF NOT EXISTS source_locations (
    source_key TEXT NOT NULL,
    path TEXT NOT NULL,
    home_id TEXT,
    encoding TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'present',
    content_digest TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (source_key, path)
);

CREATE TABLE IF NOT EXISTS source_messages (
    source_key TEXT NOT NULL,
    locator TEXT NOT NULL,
    ord INTEGER NOT NULL,
    role TEXT NOT NULL,
    message_digest TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    PRIMARY KEY (source_key, locator)
);

CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    locator TEXT NOT NULL,
    message_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_sources_source ON candidate_sources (source_key);

CREATE TABLE IF NOT EXISTS source_refs (
    parent_kind TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    PRIMARY KEY (parent_kind, parent_id, source_key)
);
