CREATE TABLE IF NOT EXISTS edge_transport_schema (
    component TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_session_lease (
    session_id TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    issued_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    CHECK (expires_at_ms > issued_at_ms)
);

CREATE TABLE IF NOT EXISTS edge_embodiment_command (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_digest TEXT NOT NULL,
    session_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    status TEXT NOT NULL CHECK (
        status IN ('DISPATCHING', 'APPLIED', 'REJECTED', 'IN_DOUBT')
    ),
    receipt_json TEXT,
    receipt_sha256 TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (session_id) REFERENCES edge_session_lease(session_id)
);

CREATE INDEX IF NOT EXISTS ix_edge_embodiment_status
    ON edge_embodiment_command(status, updated_at_ms);

INSERT OR IGNORE INTO edge_transport_schema(component, schema_version)
VALUES ('embodiment', 1);
