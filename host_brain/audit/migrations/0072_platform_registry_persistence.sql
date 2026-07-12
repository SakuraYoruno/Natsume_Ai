-- AW-03 Platform Registry persistence authority.
--
-- VTS mapping revisions and both registry journals are append-only.  The MCP
-- registry head is the sole mutable projection.  Repositories bind every
-- natural revision identity to an exact canonical request/result in the same
-- transaction as its mapping/head write.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS vts_expression_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL CHECK(length(trim(version)) BETWEEN 1 AND 80),
    emotion_tag TEXT NOT NULL CHECK(length(trim(emotion_tag)) BETWEEN 1 AND 120),
    expression_id TEXT NOT NULL CHECK(length(trim(expression_id)) BETWEEN 1 AND 240),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(version, emotion_tag)
);

CREATE TABLE IF NOT EXISTS vts_mapping_change_journal (
    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(operation_ref)) BETWEEN 1 AND 512),
    version TEXT NOT NULL UNIQUE
        CHECK(length(trim(version)) BETWEEN 1 AND 80),
    prev_version TEXT,
    prev_hash TEXT,
    new_hash TEXT NOT NULL CHECK(length(trim(new_hash)) BETWEEN 1 AND 80),
    changed_by TEXT NOT NULL CHECK(length(trim(changed_by)) BETWEEN 1 AND 240),
    request_json TEXT NOT NULL
        CHECK(length(CAST(request_json AS BLOB)) BETWEEN 2 AND 1048576),
    request_digest TEXT NOT NULL
        CHECK(length(request_digest) = 71 AND substr(request_digest, 1, 7) = 'sha256:'),
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 65536),
    result_digest TEXT NOT NULL
        CHECK(length(result_digest) = 71 AND substr(result_digest, 1, 7) = 'sha256:'),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS mcp_tool_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL CHECK(length(trim(provider_id)) BETWEEN 1 AND 240),
    tool_id TEXT NOT NULL CHECK(length(trim(tool_id)) BETWEEN 1 AND 240),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 500),
    description TEXT,
    access_level TEXT NOT NULL CHECK(access_level IN ('PUBLIC', 'INTERNAL', 'RESTRICTED')),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    reversible INTEGER NOT NULL DEFAULT 0 CHECK(reversible IN (0, 1)),
    version TEXT NOT NULL DEFAULT '1.0.0'
        CHECK(length(trim(version)) BETWEEN 1 AND 120),
    schema_hash TEXT,
    domain_tags TEXT NOT NULL DEFAULT '[]',
    emotion_requirement TEXT NOT NULL DEFAULT '[]',
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    UNIQUE(provider_id, tool_id)
);

CREATE TABLE IF NOT EXISTS mcp_tool_sync_journal (
    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(operation_ref)) BETWEEN 1 AND 512),
    provider_id TEXT NOT NULL CHECK(length(trim(provider_id)) BETWEEN 1 AND 240),
    tool_id TEXT NOT NULL CHECK(length(trim(tool_id)) BETWEEN 1 AND 240),
    tool_version TEXT NOT NULL CHECK(length(trim(tool_version)) BETWEEN 1 AND 120),
    change_kind TEXT NOT NULL CHECK(change_kind IN ('INSERT', 'UPDATE')),
    request_json TEXT NOT NULL
        CHECK(length(CAST(request_json AS BLOB)) BETWEEN 2 AND 1048576),
    request_digest TEXT NOT NULL
        CHECK(length(request_digest) = 71 AND substr(request_digest, 1, 7) = 'sha256:'),
    before_json TEXT,
    after_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 2097152),
    result_digest TEXT NOT NULL
        CHECK(length(result_digest) = 71 AND substr(result_digest, 1, 7) = 'sha256:'),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(provider_id, tool_id, tool_version)
);

CREATE INDEX IF NOT EXISTS idx_vts_mapping_version
    ON vts_expression_mapping(version, emotion_tag);
CREATE INDEX IF NOT EXISTS idx_vts_mapping_journal_created
    ON vts_mapping_change_journal(created_at_ms DESC, journal_id DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_registry_provider
    ON mcp_tool_registry(provider_id, tool_id);
CREATE INDEX IF NOT EXISTS idx_mcp_registry_tool
    ON mcp_tool_registry(tool_id, provider_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_sync_provider
    ON mcp_tool_sync_journal(provider_id, tool_id, journal_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_sync_created
    ON mcp_tool_sync_journal(created_at_ms, journal_id);

CREATE TRIGGER IF NOT EXISTS trg_vts_expression_mapping_no_insert_after_journal
BEFORE INSERT ON vts_expression_mapping
WHEN EXISTS (
    SELECT 1 FROM vts_mapping_change_journal WHERE version = NEW.version
)
BEGIN
    SELECT RAISE(ABORT, 'VTS_EXPRESSION_MAPPING_VERSION_SEALED');
END;

CREATE TRIGGER IF NOT EXISTS trg_vts_expression_mapping_no_update
BEFORE UPDATE ON vts_expression_mapping
BEGIN
    SELECT RAISE(ABORT, 'VTS_EXPRESSION_MAPPING_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_vts_expression_mapping_no_delete
BEFORE DELETE ON vts_expression_mapping
BEGIN
    SELECT RAISE(ABORT, 'VTS_EXPRESSION_MAPPING_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_vts_mapping_journal_no_update
BEFORE UPDATE ON vts_mapping_change_journal
BEGIN
    SELECT RAISE(ABORT, 'VTS_MAPPING_CHANGE_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_vts_mapping_journal_no_delete
BEFORE DELETE ON vts_mapping_change_journal
BEGIN
    SELECT RAISE(ABORT, 'VTS_MAPPING_CHANGE_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_mcp_tool_sync_journal_no_update
BEFORE UPDATE ON mcp_tool_sync_journal
BEGIN
    SELECT RAISE(ABORT, 'MCP_TOOL_SYNC_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_mcp_tool_sync_journal_no_delete
BEFORE DELETE ON mcp_tool_sync_journal
BEGIN
    SELECT RAISE(ABORT, 'MCP_TOOL_SYNC_JOURNAL_IMMUTABLE');
END;

COMMIT;
