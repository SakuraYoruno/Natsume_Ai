-- NE-01: append-only authority for replacing one failed TaskGraph revision.
--
-- A supersession is admitted only after both immutable HostCommand journals
-- and both canonical graph revisions exist in the same SQLite transaction.
-- The predecessor remains immutable evidence; this row only fences any later
-- execution authority from being issued for it.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_graph_revision_supersession (
    supersession_id TEXT NOT NULL PRIMARY KEY CHECK(
        length(trim(supersession_id)) BETWEEN 1 AND 512
    ),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task_runtime.graph_revision_supersession.v1'
    ),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) > 0),
    previous_revision INTEGER NOT NULL CHECK(
        typeof(previous_revision) = 'integer' AND previous_revision >= 1
    ),
    replacement_revision INTEGER NOT NULL CHECK(
        typeof(replacement_revision) = 'integer'
        AND replacement_revision = previous_revision + 1
    ),
    previous_command_id TEXT NOT NULL CHECK(
        length(trim(previous_command_id)) > 0
    ),
    replacement_command_id TEXT NOT NULL CHECK(
        length(trim(replacement_command_id)) > 0
        AND replacement_command_id <> previous_command_id
    ),
    previous_graph_status_version INTEGER NOT NULL CHECK(
        typeof(previous_graph_status_version) = 'integer'
        AND previous_graph_status_version >= 1
    ),
    failure_evidence_json TEXT NOT NULL CHECK(
        length(failure_evidence_json) BETWEEN 3 AND 65536
        AND json_valid(failure_evidence_json)
        AND json_type(failure_evidence_json) = 'array'
        AND json_array_length(failure_evidence_json) BETWEEN 1 AND 64
    ),
    failure_evidence_sha256 TEXT NOT NULL CHECK(
        length(failure_evidence_sha256) = 71
        AND substr(failure_evidence_sha256, 1, 7) = 'sha256:'
        AND substr(failure_evidence_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    FOREIGN KEY (graph_id, previous_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, replacement_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (previous_command_id)
        REFERENCES host_command_journal(command_id) ON DELETE RESTRICT,
    FOREIGN KEY (replacement_command_id)
        REFERENCES host_command_journal(command_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_supersession_previous
    ON task_runtime_graph_revision_supersession(graph_id, previous_revision);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_supersession_replacement
    ON task_runtime_graph_revision_supersession(graph_id, replacement_revision);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_supersession_previous_command
    ON task_runtime_graph_revision_supersession(previous_command_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_supersession_replacement_command
    ON task_runtime_graph_revision_supersession(replacement_command_id);

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_graph_supersession_binding_insert
BEFORE INSERT ON task_runtime_graph_revision_supersession
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM task_runtime_graph AS predecessor
        JOIN task_runtime_graph AS replacement
          ON replacement.graph_id = predecessor.graph_id
         AND replacement.revision = NEW.replacement_revision
        WHERE predecessor.graph_id = NEW.graph_id
          AND predecessor.revision = NEW.previous_revision
          AND predecessor.status = 'FAILED'
          AND predecessor.status_version = NEW.previous_graph_status_version
          AND replacement.status = 'CREATED'
          AND replacement.status_version = 0
          AND replacement.root_work_order_id = predecessor.root_work_order_id
    ) THEN RAISE(ABORT, 'graph revision supersession binding mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_graph_supersession_immutable_update
BEFORE UPDATE ON task_runtime_graph_revision_supersession
BEGIN
    SELECT RAISE(ABORT, 'graph revision supersession is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_graph_supersession_immutable_delete
BEFORE DELETE ON task_runtime_graph_revision_supersession
BEGIN
    SELECT RAISE(ABORT, 'graph revision supersession is append-only');
END;

COMMIT;
