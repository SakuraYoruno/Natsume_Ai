-- TASK-0488 / M48-B: durable Gateway inbox, outbox, retry, ack and DLQ state.
-- Expand-only. Runtime stores must verify this catalog-owned schema and never create it.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS gateway_transport_inbox (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_id TEXT NOT NULL UNIQUE CHECK(length(trim(inbox_id)) > 0),
    surface TEXT NOT NULL CHECK(length(trim(surface)) > 0),
    platform TEXT NOT NULL CHECK(length(trim(platform)) > 0),
    principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) > 0),
    tenant_id TEXT NOT NULL CHECK(length(trim(tenant_id)) > 0),
    secure_claim_ref TEXT UNIQUE,
    verification_audit_ref TEXT,
    platform_event_id TEXT,
    idempotency_digest TEXT NOT NULL
        CHECK(
            length(idempotency_digest) = 71
            AND substr(idempotency_digest, 1, 7) = 'sha256:'
            AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    ingress_ack_ref TEXT NOT NULL UNIQUE CHECK(length(trim(ingress_ack_ref)) > 0),
    raw_body_sha256 TEXT NOT NULL
        CHECK(
            length(raw_body_sha256) = 71
            AND substr(raw_body_sha256, 1, 7) = 'sha256:'
            AND substr(raw_body_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    raw_body_size_bytes INTEGER NOT NULL
        CHECK(raw_body_size_bytes BETWEEN 1 AND 1048576),
    safe_headers_json TEXT NOT NULL DEFAULT '{}'
        CHECK(length(CAST(safe_headers_json AS BLOB)) BETWEEN 2 AND 4096),
    safe_headers_sha256 TEXT NOT NULL
        CHECK(
            length(safe_headers_sha256) = 71
            AND substr(safe_headers_sha256, 1, 7) = 'sha256:'
            AND substr(safe_headers_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    normalized_payload_json TEXT NOT NULL
        CHECK(length(CAST(normalized_payload_json AS BLOB)) BETWEEN 2 AND 1048576),
    normalized_payload_digest TEXT NOT NULL
        CHECK(
            length(normalized_payload_digest) = 71
            AND substr(normalized_payload_digest, 1, 7) = 'sha256:'
            AND substr(normalized_payload_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    ordering_key TEXT NOT NULL CHECK(length(trim(ordering_key)) > 0),
    source_sequence INTEGER CHECK(source_sequence IS NULL OR source_sequence >= 0),
    status TEXT NOT NULL DEFAULT 'READY' CHECK(status IN (
        'READY', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED', 'DLQ'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    next_attempt_at_ms INTEGER NOT NULL CHECK(next_attempt_at_ms >= 0),
    claim_token TEXT,
    claim_owner TEXT,
    claim_expires_at_ms INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    delivery_receipt_ref TEXT,
    last_reason_code TEXT,
    last_replay_operator_ref TEXT,
    replay_count INTEGER NOT NULL DEFAULT 0 CHECK(replay_count >= 0),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    delivered_at_ms INTEGER,
    dlq_at_ms INTEGER,
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    UNIQUE(tenant_id, principal_id, surface, idempotency_digest),
    CHECK(
        status = 'CLAIMED'
        OR (claim_token IS NULL AND claim_owner IS NULL AND claim_expires_at_ms IS NULL)
    ),
    CHECK(
        status != 'CLAIMED'
        OR (
            claim_token IS NOT NULL AND length(trim(claim_token)) > 0
            AND claim_owner IS NOT NULL AND length(trim(claim_owner)) > 0
            AND claim_expires_at_ms IS NOT NULL
            AND fencing_token >= 1
        )
    ),
    CHECK(status != 'DELIVERED' OR (delivery_receipt_ref IS NOT NULL AND delivered_at_ms IS NOT NULL)),
    CHECK(status != 'DLQ' OR dlq_at_ms IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS gateway_transport_outbox (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT NOT NULL UNIQUE CHECK(length(trim(outbox_id)) > 0),
    origin_inbox_id TEXT,
    tenant_id TEXT NOT NULL CHECK(length(trim(tenant_id)) > 0),
    principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) > 0),
    destination TEXT NOT NULL CHECK(length(trim(destination)) > 0),
    idempotency_digest TEXT NOT NULL
        CHECK(
            length(idempotency_digest) = 71
            AND substr(idempotency_digest, 1, 7) = 'sha256:'
            AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    payload_body BLOB NOT NULL CHECK(length(payload_body) BETWEEN 1 AND 1048576),
    payload_sha256 TEXT NOT NULL
        CHECK(
            length(payload_sha256) = 71
            AND substr(payload_sha256, 1, 7) = 'sha256:'
            AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    ordering_key TEXT NOT NULL CHECK(length(trim(ordering_key)) > 0),
    source_sequence INTEGER CHECK(source_sequence IS NULL OR source_sequence >= 0),
    status TEXT NOT NULL DEFAULT 'READY' CHECK(status IN (
        'READY', 'CLAIMED', 'RETRY_WAIT', 'ACKED', 'DLQ', 'CANCELLED'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    next_attempt_at_ms INTEGER NOT NULL CHECK(next_attempt_at_ms >= 0),
    claim_token TEXT,
    claim_owner TEXT,
    claim_expires_at_ms INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    ack_ref TEXT,
    ack_digest TEXT CHECK(
        ack_digest IS NULL
        OR (
            length(ack_digest) = 71
            AND substr(ack_digest, 1, 7) = 'sha256:'
            AND substr(ack_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    last_reason_code TEXT,
    last_replay_operator_ref TEXT,
    replay_count INTEGER NOT NULL DEFAULT 0 CHECK(replay_count >= 0),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    acked_at_ms INTEGER,
    dlq_at_ms INTEGER,
    cancelled_at_ms INTEGER,
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    UNIQUE(tenant_id, principal_id, destination, idempotency_digest),
    FOREIGN KEY(origin_inbox_id) REFERENCES gateway_transport_inbox(inbox_id) ON DELETE RESTRICT,
    CHECK(
        status = 'CLAIMED'
        OR (claim_token IS NULL AND claim_owner IS NULL AND claim_expires_at_ms IS NULL)
    ),
    CHECK(
        status != 'CLAIMED'
        OR (
            claim_token IS NOT NULL AND length(trim(claim_token)) > 0
            AND claim_owner IS NOT NULL AND length(trim(claim_owner)) > 0
            AND claim_expires_at_ms IS NOT NULL
            AND fencing_token >= 1
        )
    ),
    CHECK(status != 'ACKED' OR (ack_ref IS NOT NULL AND acked_at_ms IS NOT NULL)),
    CHECK(status != 'DLQ' OR dlq_at_ms IS NOT NULL),
    CHECK(status != 'CANCELLED' OR cancelled_at_ms IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS gateway_transport_attempt (
    attempt_id TEXT PRIMARY KEY CHECK(length(trim(attempt_id)) > 0),
    direction TEXT NOT NULL CHECK(direction IN ('INBOX', 'OUTBOX')),
    message_id TEXT NOT NULL CHECK(length(trim(message_id)) > 0),
    replay_no INTEGER NOT NULL CHECK(replay_no >= 0),
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    claim_token TEXT NOT NULL UNIQUE CHECK(length(trim(claim_token)) > 0),
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    worker_id TEXT NOT NULL CHECK(length(trim(worker_id)) > 0),
    started_at_ms INTEGER NOT NULL CHECK(started_at_ms >= 0),
    lease_expires_at_ms INTEGER NOT NULL CHECK(lease_expires_at_ms >= started_at_ms),
    dispatch_started_at_ms INTEGER,
    completed_at_ms INTEGER,
    outcome TEXT NOT NULL CHECK(outcome IN (
        'CLAIMED', 'DISPATCHING', 'DELIVERED', 'ACKED', 'RETRY_WAIT', 'DLQ', 'LEASE_EXPIRED'
    )),
    reason_code TEXT,
    receipt_ref TEXT,
    UNIQUE(direction, message_id, replay_no, attempt_no),
    CHECK(dispatch_started_at_ms IS NULL OR dispatch_started_at_ms >= started_at_ms),
    CHECK(outcome != 'CLAIMED' OR dispatch_started_at_ms IS NULL),
    CHECK(outcome != 'DISPATCHING' OR dispatch_started_at_ms IS NOT NULL),
    CHECK(outcome IN ('CLAIMED', 'DISPATCHING') OR completed_at_ms IS NOT NULL),
    CHECK(outcome NOT IN ('CLAIMED', 'DISPATCHING') OR completed_at_ms IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gateway_transport_inbox_platform_event
    ON gateway_transport_inbox(tenant_id, principal_id, platform, platform_event_id)
    WHERE platform_event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_gateway_transport_inbox_active_order
    ON gateway_transport_inbox(tenant_id, principal_id, ordering_key)
    WHERE status = 'CLAIMED';
CREATE INDEX IF NOT EXISTS idx_gateway_transport_inbox_ready
    ON gateway_transport_inbox(
        status, next_attempt_at_ms, tenant_id, principal_id, ordering_key, cursor
    );
CREATE INDEX IF NOT EXISTS idx_gateway_transport_inbox_claim_expiry
    ON gateway_transport_inbox(status, claim_expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_gateway_transport_inbox_order_sequence
    ON gateway_transport_inbox(
        tenant_id, principal_id, ordering_key, source_sequence, cursor, status
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_gateway_transport_outbox_active_order
    ON gateway_transport_outbox(tenant_id, principal_id, ordering_key)
    WHERE status = 'CLAIMED';
CREATE INDEX IF NOT EXISTS idx_gateway_transport_outbox_ready
    ON gateway_transport_outbox(
        status, next_attempt_at_ms, tenant_id, principal_id, ordering_key, cursor
    );
CREATE INDEX IF NOT EXISTS idx_gateway_transport_outbox_claim_expiry
    ON gateway_transport_outbox(status, claim_expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_gateway_transport_outbox_order_sequence
    ON gateway_transport_outbox(
        tenant_id, principal_id, ordering_key, source_sequence, cursor, status
    );

CREATE INDEX IF NOT EXISTS idx_gateway_transport_attempt_message
    ON gateway_transport_attempt(direction, message_id, replay_no, attempt_no);
CREATE INDEX IF NOT EXISTS idx_gateway_transport_attempt_outcome
    ON gateway_transport_attempt(outcome, lease_expires_at_ms);

COMMIT;
