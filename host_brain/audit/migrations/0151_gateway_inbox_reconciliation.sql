-- DL-07: persist evidence before re-driving an uncertain Gateway -> Host inbox.
--
-- A dispatch-started inbox cannot be replayed from an operator label alone.
-- This append-only record binds one exact transport attempt to a canonical
-- Secure Ingress denial which proves that Host executed no side effect.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS gateway_transport_inbox_reconciliation (
    reconciliation_ref TEXT PRIMARY KEY
        CHECK(length(trim(reconciliation_ref)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.gateway.inbox-reconciliation.v1'
    ),
    inbox_id TEXT NOT NULL,
    replay_no INTEGER NOT NULL CHECK(replay_no >= 0),
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    attempt_id TEXT NOT NULL,
    prior_status_version INTEGER NOT NULL CHECK(prior_status_version >= 1),
    outcome TEXT NOT NULL CHECK(outcome = 'NO_SIDE_EFFECT_SAFE_RETRY'),
    secure_ingress_audit_ref TEXT NOT NULL UNIQUE,
    secure_ingress_reason_code TEXT NOT NULL,
    operator_ref TEXT NOT NULL
        CHECK(length(trim(operator_ref)) BETWEEN 1 AND 2048),
    evidence_json TEXT NOT NULL
        CHECK(length(CAST(evidence_json AS BLOB)) BETWEEN 2 AND 16384),
    evidence_digest TEXT NOT NULL UNIQUE CHECK(
        length(evidence_digest) = 71
        AND substr(evidence_digest, 1, 7) = 'sha256:'
        AND substr(evidence_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(inbox_id, replay_no, attempt_no),
    FOREIGN KEY(inbox_id)
        REFERENCES gateway_transport_inbox(inbox_id) ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id)
        REFERENCES gateway_transport_attempt(attempt_id) ON DELETE RESTRICT,
    FOREIGN KEY(secure_ingress_audit_ref)
        REFERENCES secure_ingress_audit(audit_ref) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_gateway_inbox_reconciliation_inbox
    ON gateway_transport_inbox_reconciliation(
        inbox_id, created_at_ms, reconciliation_ref
    );

CREATE TRIGGER IF NOT EXISTS trg_gateway_inbox_reconciliation_no_update
BEFORE UPDATE ON gateway_transport_inbox_reconciliation
BEGIN
    SELECT RAISE(ABORT, 'GATEWAY_INBOX_RECONCILIATION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_gateway_inbox_reconciliation_no_delete
BEFORE DELETE ON gateway_transport_inbox_reconciliation
BEGIN
    SELECT RAISE(ABORT, 'GATEWAY_INBOX_RECONCILIATION_IMMUTABLE');
END;

COMMIT;
