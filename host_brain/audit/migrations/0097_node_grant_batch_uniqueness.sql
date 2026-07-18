-- NE-01: fail-closed durable uniqueness for canonical TaskGraph node grants.
--
-- NodeBoundGrantService already writes one graph batch in a single
-- transaction.  This partial unique index makes the deterministic
-- request+intent identity authoritative across processes and also fences any
-- producer that bypasses the service.  Historical duplicates intentionally
-- make this migration fail so they can be governed explicitly rather than
-- silently choosing one bearer credential.

BEGIN IMMEDIATE;

CREATE TRIGGER IF NOT EXISTS trg_governor_node_grant_identity_insert
BEFORE INSERT ON governor_log
WHEN NEW.stage = 'S_TASK_GRAPH_NODE_GRANT'
 AND (
     NEW.request_id IS NULL
     OR length(trim(NEW.request_id)) = 0
     OR NEW.intent_id IS NULL
     OR length(trim(NEW.intent_id)) = 0
 )
BEGIN
    SELECT RAISE(ABORT, 'node grant request/intent identity is required');
END;

CREATE TRIGGER IF NOT EXISTS trg_governor_node_grant_identity_update
BEFORE UPDATE OF request_id, intent_id, stage ON governor_log
WHEN NEW.stage = 'S_TASK_GRAPH_NODE_GRANT'
 AND (
     NEW.request_id IS NULL
     OR length(trim(NEW.request_id)) = 0
     OR NEW.intent_id IS NULL
     OR length(trim(NEW.intent_id)) = 0
 )
BEGIN
    SELECT RAISE(ABORT, 'node grant request/intent identity is required');
END;

CREATE UNIQUE INDEX IF NOT EXISTS uq_governor_node_grant_request_intent
ON governor_log(request_id, intent_id)
WHERE stage = 'S_TASK_GRAPH_NODE_GRANT';

COMMIT;
