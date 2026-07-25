-- NE-01: seal raw SQLite TaskGraph/TaskNode creation behind HostCommand admission.
--
-- Existing rows remain readable and are not rewritten.  Every future insert
-- must execute on a connection that registered the process-local admission
-- capability and entered the exact graph/revision scope.  A plain SQLite
-- connection has no such function and therefore fails closed.

BEGIN IMMEDIATE;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_graph_admission_authority_insert
BEFORE INSERT ON task_runtime_graph
BEGIN
    SELECT CASE
        WHEN NEW.source_kind <> 'CANONICAL' THEN
            RAISE(ABORT, 'TASK_GRAPH_LEGACY_INSERT_RETIRED')
        WHEN natsume_task_graph_admission_authorized(
            NEW.graph_id,
            NEW.revision
        ) <> 1 THEN
            RAISE(ABORT, 'TASK_GRAPH_ADMISSION_AUTHORITY_REQUIRED')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_admission_authority_insert
BEFORE INSERT ON task_runtime_node
BEGIN
    SELECT CASE
        WHEN natsume_task_graph_admission_authorized(
            NEW.graph_id,
            NEW.graph_revision
        ) <> 1 THEN
            RAISE(ABORT, 'TASK_NODE_ADMISSION_AUTHORITY_REQUIRED')
        WHEN NOT EXISTS (
            SELECT 1
            FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.graph_id
              AND graph.revision = NEW.graph_revision
              AND graph.source_kind = 'CANONICAL'
        ) THEN
            RAISE(ABORT, 'TASK_NODE_CANONICAL_GRAPH_REQUIRED')
        WHEN EXISTS (
            SELECT 1
            FROM host_command_journal AS admission
            WHERE admission.graph_id = NEW.graph_id
              AND admission.graph_revision = NEW.graph_revision
        ) THEN
            RAISE(ABORT, 'TASK_NODE_ADMISSION_ALREADY_CLOSED')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_host_command_journal_admission_authority_insert
BEFORE INSERT ON host_command_journal
BEGIN
    SELECT CASE
        WHEN natsume_task_graph_admission_authorized(
            NEW.graph_id,
            NEW.graph_revision
        ) <> 1 THEN
            RAISE(ABORT, 'HOST_COMMAND_ADMISSION_AUTHORITY_REQUIRED')
        WHEN NOT EXISTS (
            SELECT 1
            FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.graph_id
              AND graph.revision = NEW.graph_revision
              AND graph.source_kind = 'CANONICAL'
              AND graph.root_work_order_id = NEW.root_work_order_id
        ) THEN
            RAISE(ABORT, 'HOST_COMMAND_CANONICAL_GRAPH_REQUIRED')
    END;
END;

COMMIT;
