BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS capability_forge_proposal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id     TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    stage           TEXT    NOT NULL,
    session_id      TEXT,
    candidate_key   TEXT,
    reason_code     TEXT,
    actor           TEXT,
    payload         TEXT,
    ts_ms           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capability_forge_proposal_id
    ON capability_forge_proposal_log(proposal_id);
CREATE INDEX IF NOT EXISTS idx_capability_forge_status
    ON capability_forge_proposal_log(status);
CREATE INDEX IF NOT EXISTS idx_capability_forge_stage
    ON capability_forge_proposal_log(stage);
CREATE INDEX IF NOT EXISTS idx_capability_forge_ts
    ON capability_forge_proposal_log(ts_ms);

COMMIT;
