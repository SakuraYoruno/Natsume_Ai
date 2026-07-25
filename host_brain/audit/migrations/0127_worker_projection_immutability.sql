-- NE-01: make the durable worker checkpoint a stable reconciliation subject.
--
-- Signed unknown-effect COMMIT reopens this row after the original worker has
-- stopped.  The existing repository is insert-only, but reconciliation also
-- needs the database itself to reject direct UPDATE/DELETE attempts between
-- signature verification and the Operation CAS.

BEGIN IMMEDIATE;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_worker_projection_no_update
BEFORE UPDATE ON task_runtime_worker_projection
BEGIN
    SELECT RAISE(ABORT, 'TASK_RUNTIME_WORKER_PROJECTION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_worker_projection_no_delete
BEFORE DELETE ON task_runtime_worker_projection
BEGIN
    SELECT RAISE(ABORT, 'TASK_RUNTIME_WORKER_PROJECTION_IMMUTABLE');
END;

COMMIT;
