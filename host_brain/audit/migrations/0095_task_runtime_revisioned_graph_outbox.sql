-- NE-01: exact TaskGraph revision identity for the canonical Runtime Outbox.
--
-- The generic Outbox predates revisioned TaskGraphs.  A nullable column keeps
-- every non-Graph aggregate unchanged, while guards require every TaskGraph
-- event to bind one existing (graph_id, revision) authority.  Historical rows
-- are backfilled only from independently resolvable durable authorities; an
-- ambiguous row aborts the whole migration instead of guessing from time or
-- aggregate_version.

BEGIN IMMEDIATE;

ALTER TABLE task_runtime_outbox ADD COLUMN graph_revision INTEGER
    CHECK(
        graph_revision IS NULL
        OR (
            typeof(graph_revision) = 'integer'
            AND graph_revision >= 1
        )
    );

CREATE TEMP TABLE task_runtime_graph_outbox_revision_candidate (
    outbox_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    source_kind TEXT NOT NULL,
    UNIQUE(outbox_id, graph_revision, source_kind)
);

-- Canonical 0090/0091/0092 rows name their exact Graph Outbox and revision.
INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, decision.graph_revision, '0090_DECISION'
FROM task_runtime_outbox AS outbox
JOIN task_runtime_parallel_settlement_decision AS decision
  ON decision.graph_outbox_id = outbox.outbox_id
 AND decision.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = decision.graph_id
 AND graph.revision = decision.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH';

INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, decision.graph_revision, '0091_DECISION'
FROM task_runtime_outbox AS outbox
JOIN task_runtime_parallel_success_decision AS decision
  ON decision.graph_outbox_id = outbox.outbox_id
 AND decision.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = decision.graph_id
 AND graph.revision = decision.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH';

INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, intent.graph_revision, '0092_INTENT'
FROM task_runtime_outbox AS outbox
JOIN task_runtime_graph_cancellation_intent AS intent
  ON intent.graph_outbox_id = outbox.outbox_id
 AND intent.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = intent.graph_id
 AND graph.revision = intent.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH';

INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, decision.graph_revision, '0092_DECISION'
FROM task_runtime_outbox AS outbox
JOIN task_runtime_graph_cancellation_decision AS decision
  ON decision.graph_outbox_id = outbox.outbox_id
 AND decision.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = decision.graph_id
 AND graph.revision = decision.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH';

-- Host command admission is the exact authority for CREATED Outboxes.  Its
-- immutable command_id is included in the graph Outbox evidence and remains
-- unique across replan revisions, so normal multi-revision history can be
-- recovered without time/version inference.
INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, admission.graph_revision, 'HOST_COMMAND'
FROM task_runtime_outbox AS outbox
JOIN host_command_journal AS admission
  ON admission.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = admission.graph_id
 AND graph.revision = admission.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH'
  AND outbox.aggregate_version = 0
  AND outbox.event_type = 'TASK_GRAPH_CREATED'
  AND outbox.attempt_id IS NULL
  AND json_valid(outbox.evidence_refs_json)
  AND EXISTS (
      SELECT 1
      FROM json_each(outbox.evidence_refs_json) AS evidence
      WHERE evidence.type = 'text'
        AND evidence.value = admission.command_id
  );

-- Governed Runtime Outboxes before 0095 used a revision-bound HostCommand id
-- inside their deterministic digest, but did not persist the revision as a
-- column.  Recompute that immutable identity exactly; this recovers ACTIVE and
-- other non-attempt transitions without relying on timestamps or versions.
INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, admission.graph_revision, 'GOVERNED_IDENTITY'
FROM task_runtime_outbox AS outbox
JOIN host_command_journal AS admission
  ON admission.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = admission.graph_id
 AND graph.revision = admission.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH'
  AND outbox.payload_ref = (
      'taskstore://event/TASK_GRAPH/' || outbox.aggregate_id
      || '/' || outbox.aggregate_version
  )
  AND outbox.idempotency_key = 'governed:' || natsume_sha256_hex(
      admission.command_id || char(10)
      || 'TASK_GRAPH' || char(10)
      || outbox.aggregate_id || char(10)
      || CAST(outbox.aggregate_version AS TEXT) || char(10)
      || outbox.event_type
  )
  AND outbox.outbox_id = 'outbox::governed::' || natsume_sha256_hex(
      admission.command_id || char(10)
      || 'TASK_GRAPH' || char(10)
      || outbox.aggregate_id || char(10)
      || CAST(outbox.aggregate_version AS TEXT) || char(10)
      || outbox.event_type
  );

-- An attempt-bound Graph event inherits the exact attempt lineage.
INSERT OR IGNORE INTO task_runtime_graph_outbox_revision_candidate(
    outbox_id, graph_revision, source_kind
)
SELECT outbox.outbox_id, attempt.graph_revision, 'ATTEMPT'
FROM task_runtime_outbox AS outbox
JOIN task_runtime_attempt AS attempt
  ON attempt.attempt_id = outbox.attempt_id
 AND attempt.graph_id = outbox.aggregate_id
JOIN task_runtime_graph AS graph
  ON graph.graph_id = attempt.graph_id
 AND graph.revision = attempt.graph_revision
WHERE outbox.aggregate_type = 'TASK_GRAPH';

-- Independent authorities must never disagree about one Outbox revision.
CREATE TEMP TABLE task_runtime_graph_outbox_revision_guard (
    valid INTEGER NOT NULL CHECK(valid = 1)
);

INSERT INTO task_runtime_graph_outbox_revision_guard(valid)
SELECT CASE
    WHEN EXISTS (
        SELECT candidate.outbox_id
        FROM task_runtime_graph_outbox_revision_candidate AS candidate
        GROUP BY candidate.outbox_id
        HAVING COUNT(DISTINCT candidate.graph_revision) <> 1
    ) THEN 0
    ELSE 1
END;

UPDATE task_runtime_outbox
SET graph_revision = (
    SELECT candidate.graph_revision
    FROM task_runtime_graph_outbox_revision_candidate AS candidate
    WHERE candidate.outbox_id = task_runtime_outbox.outbox_id
    ORDER BY candidate.graph_revision
    LIMIT 1
)
WHERE aggregate_type = 'TASK_GRAPH'
  AND EXISTS (
      SELECT 1
      FROM task_runtime_graph_outbox_revision_candidate AS candidate
      WHERE candidate.outbox_id = task_runtime_outbox.outbox_id
  );

-- The final fallback is legal only when this graph_id has exactly one durable
-- revision.  Multi-revision rows without an exact authority remain unresolved.
UPDATE task_runtime_outbox
SET graph_revision = (
    SELECT MIN(graph.revision)
    FROM task_runtime_graph AS graph
    WHERE graph.graph_id = task_runtime_outbox.aggregate_id
    GROUP BY graph.graph_id
    HAVING COUNT(*) = 1
)
WHERE aggregate_type = 'TASK_GRAPH'
  AND graph_revision IS NULL;

DELETE FROM task_runtime_graph_outbox_revision_guard;
INSERT INTO task_runtime_graph_outbox_revision_guard(valid)
SELECT CASE
    WHEN EXISTS (
        SELECT 1
        FROM task_runtime_outbox AS outbox
        WHERE
            (
                outbox.aggregate_type = 'TASK_GRAPH'
                AND (
                    outbox.graph_revision IS NULL
                    OR typeof(outbox.graph_revision) <> 'integer'
                    OR outbox.graph_revision < 1
                    OR NOT EXISTS (
                        SELECT 1
                        FROM task_runtime_graph AS graph
                        WHERE graph.graph_id = outbox.aggregate_id
                          AND graph.revision = outbox.graph_revision
                    )
                )
            )
            OR (
                outbox.aggregate_type <> 'TASK_GRAPH'
                AND outbox.graph_revision IS NOT NULL
            )
    ) THEN 0
    ELSE 1
END;

DROP TABLE task_runtime_graph_outbox_revision_candidate;
DROP TABLE task_runtime_graph_outbox_revision_guard;

CREATE TRIGGER trg_task_runtime_outbox_graph_revision_insert_guard
BEFORE INSERT ON task_runtime_outbox
WHEN NOT (
    (
        NEW.aggregate_type = 'TASK_GRAPH'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/TASK_GRAPH/' || NEW.aggregate_id
            || '@' || NEW.graph_revision || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1
            FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.aggregate_id
              AND graph.revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type <> 'TASK_GRAPH'
        AND NEW.graph_revision IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox graph revision is invalid');
END;

CREATE TRIGGER trg_task_runtime_outbox_graph_revision_update_guard
BEFORE UPDATE OF aggregate_type, aggregate_id, graph_revision,
                 aggregate_version, payload_ref
ON task_runtime_outbox
WHEN NOT (
    (
        NEW.aggregate_type = 'TASK_GRAPH'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/TASK_GRAPH/' || NEW.aggregate_id
            || '@' || NEW.graph_revision || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1
            FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.aggregate_id
              AND graph.revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type <> 'TASK_GRAPH'
        AND NEW.graph_revision IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox graph revision is invalid');
END;

CREATE INDEX idx_task_runtime_outbox_revision_delivery_order
    ON task_runtime_outbox(
        aggregate_type, aggregate_id, graph_revision, status,
        aggregate_version, created_ts_ms, outbox_id
    );

-- One TaskStore Outbox can project to exactly one immutable Runtime Event.
-- This source identity remains stable even though 0095 changes the Graph
-- event's query-facing aggregate id from graph_id to graph_id@revision.
CREATE UNIQUE INDEX uq_runtime_event_task_outbox_source
    ON runtime_event_timeline(source_ref)
    WHERE source_kind = 'TASK_STORE_OUTBOX';

COMMIT;
