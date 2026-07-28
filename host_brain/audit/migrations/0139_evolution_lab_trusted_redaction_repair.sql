-- NE-04: repair the 0138 trusted redaction namespace without rewriting 0108.
--
-- 0108 already owns evolution_lab_redaction_receipt.  The 0138
-- CREATE TABLE IF NOT EXISTS therefore preserved the historical shape while
-- its insert guard accidentally targeted that V1 table.  Keep V1 compatible
-- and give trusted redaction evidence an unambiguous append-only table.

BEGIN IMMEDIATE;

DROP TRIGGER IF EXISTS evolution_lab_redaction_insert_guard;

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_redaction_receipt (
    redaction_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(redaction_id)) BETWEEN 1 AND 160),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    source_digest TEXT NOT NULL CHECK(length(source_digest) = 64),
    output_dataset_digest TEXT NOT NULL CHECK(length(output_dataset_digest) = 64),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(
        json_valid(receipt_json) AND length(receipt_json) BETWEEN 2 AND 524288
    ),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    UNIQUE(workspace_id, source_digest, output_dataset_digest)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_trusted_redaction_workspace
    ON evolution_lab_trusted_redaction_receipt(
        workspace_id,
        created_at_ms,
        redaction_id
    );

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_redaction_insert_guard
BEFORE INSERT ON evolution_lab_trusted_redaction_receipt
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted redaction insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_redaction_no_update
BEFORE UPDATE ON evolution_lab_trusted_redaction_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted redaction receipt is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_redaction_no_delete
BEFORE DELETE ON evolution_lab_trusted_redaction_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted redaction receipt is append-only'); END;

COMMIT;
