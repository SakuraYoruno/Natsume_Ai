-- D6: cold-migrate the legacy model-node bootstrap table into the canonical
-- model-attempt lifecycle journal, then remove the second execution truth.
--
-- Fresh migration-only databases never owned llm_node_call_log.  Creating an
-- empty compatibility shell makes this one asset valid for both fresh installs
-- and upgraded inline-schema databases.  Legacy input/output "digest" columns
-- sometimes contain raw prompt/output excerpts, so they are deliberately not
-- copied into the canonical privacy-bounded journal.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS llm_node_call_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    provider      TEXT,
    model_id      TEXT,
    status        TEXT NOT NULL,
    reason_code   TEXT,
    latency_ms    INTEGER,
    input_digest  TEXT,
    output_digest TEXT,
    ts_ms         INTEGER NOT NULL
);

-- A deterministic ID collision must abort the migration and preserve the
-- legacy table.  Silently ignoring a conflicting row before DROP would lose
-- the only copy of that historical evidence.  Transaction replay is already
-- safe: a committed run has dropped the table, while an interrupted run rolls
-- back both the inserts and the DROP.
--
-- ``model_attempt_log`` permits one STARTED and one terminal row for the same
-- attempt ID, so its UNIQUE(attempt_id, status) constraint alone is not a
-- migration collision fence.  Reject *any* pre-existing deterministic ID,
-- regardless of status, before inserting or dropping legacy evidence.
CREATE TEMP TABLE natsume_0103_attempt_collision_guard (
    collision_count INTEGER NOT NULL CHECK(collision_count = 0)
);

INSERT INTO natsume_0103_attempt_collision_guard(collision_count)
SELECT COUNT(*)
FROM llm_node_call_log AS legacy
WHERE EXISTS (
    SELECT 1
    FROM model_attempt_log AS canonical
    WHERE canonical.attempt_id =
          'legacy-llm-node-' || printf('%016x', legacy.id)
);

DROP TABLE natsume_0103_attempt_collision_guard;

INSERT INTO model_attempt_log (
    attempt_id,
    request_id,
    route_decision_id,
    attempt_index,
    status,
    retry_reason,
    adapter_id,
    provider,
    model_id,
    prompt_id,
    prompt_version,
    prompt_digest,
    started_at_ms,
    ended_at_ms,
    latency_ms,
    output_digest,
    error_code,
    error_message,
    metadata,
    ts_ms
)
SELECT
    'legacy-llm-node-' || printf('%016x', id),
    request_id,
    'legacy-llm-node-route-' || printf('%016x', id),
    1,
    CASE
        WHEN upper(trim(COALESCE(status, ''))) IN (
            'OK', 'UP', 'SUCCEEDED', 'SUCCESS', 'COMPLETED', 'ACCEPTED'
        ) THEN 'succeeded'
        ELSE 'failed'
    END,
    'legacy_cold_migration',
    'legacy:' || lower(COALESCE(NULLIF(trim(provider), ''), 'unknown')),
    lower(COALESCE(NULLIF(trim(provider), ''), 'unknown')),
    COALESCE(NULLIF(trim(model_id), ''), 'unknown'),
    'legacy:' || COALESCE(NULLIF(trim(node_id), ''), 'model-node'),
    '0',
    '0000000000000000000000000000000000000000000000000000000000000000',
    max(
        0,
        COALESCE(ts_ms, 0) - max(0, COALESCE(latency_ms, 0))
    ),
    max(0, COALESCE(ts_ms, 0)),
    CASE WHEN latency_ms >= 0 THEN latency_ms ELSE NULL END,
    NULL,
    CASE
        WHEN upper(trim(COALESCE(status, ''))) IN (
            'OK', 'UP', 'SUCCEEDED', 'SUCCESS', 'COMPLETED', 'ACCEPTED'
        ) THEN NULL
        ELSE COALESCE(NULLIF(trim(reason_code), ''), 'LEGACY_MODEL_ATTEMPT_FAILED')
    END,
    NULL,
    json_object(
        'legacy_source', 'llm_node_call_log',
        'model_node_id', COALESCE(NULLIF(trim(node_id), ''), 'legacy_model_node')
    ),
    max(0, COALESCE(ts_ms, 0))
FROM llm_node_call_log;

DROP TABLE llm_node_call_log;

COMMIT;
