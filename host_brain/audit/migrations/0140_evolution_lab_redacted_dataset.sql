-- NE-04: durable, content-addressed redacted real-trace datasets.
--
-- Raw traces never enter this table.  The trusted redactor commits only the
-- sanitized dataset together with its manifest and a digest-only 0139
-- redaction receipt.  The dataset remains append-only and proposal-only.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_redacted_dataset (
    dataset_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(dataset_id)) BETWEEN 1 AND 160),
    workspace_id TEXT NOT NULL
        CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_digest TEXT NOT NULL UNIQUE CHECK(length(dataset_digest) = 64),
    manifest_digest TEXT NOT NULL UNIQUE CHECK(length(manifest_digest) = 64),
    redaction_receipt_digest TEXT NOT NULL UNIQUE
        REFERENCES evolution_lab_trusted_redaction_receipt(receipt_digest)
        ON DELETE RESTRICT,
    dataset_json TEXT NOT NULL CHECK(
        json_valid(dataset_json) AND length(dataset_json) BETWEEN 2 AND 8388608
    ),
    manifest_json TEXT NOT NULL CHECK(
        json_valid(manifest_json) AND length(manifest_json) BETWEEN 2 AND 524288
    ),
    record_count INTEGER NOT NULL CHECK(
        typeof(record_count) = 'integer' AND record_count > 0
    ),
    source_kind TEXT NOT NULL CHECK(source_kind = 'redacted_trace'),
    created_at_ms INTEGER NOT NULL CHECK(
        typeof(created_at_ms) = 'integer' AND created_at_ms >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_redacted_dataset_workspace
    ON evolution_lab_trusted_redacted_dataset(
        workspace_id,
        created_at_ms,
        dataset_id
    );

CREATE TRIGGER IF NOT EXISTS evolution_lab_redacted_dataset_insert_guard
BEFORE INSERT ON evolution_lab_trusted_redacted_dataset
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN
    SELECT RAISE(
        ABORT,
        'Evolution Lab redacted dataset insert requires repository authority'
    );
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_redacted_dataset_no_update
BEFORE UPDATE ON evolution_lab_trusted_redacted_dataset
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab redacted dataset is immutable');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_redacted_dataset_no_delete
BEFORE DELETE ON evolution_lab_trusted_redacted_dataset
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab redacted dataset is append-only');
END;

COMMIT;
