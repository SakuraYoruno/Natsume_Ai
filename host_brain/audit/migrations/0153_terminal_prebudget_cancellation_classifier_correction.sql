-- Allow one narrowly defined, Operator-signed no-op proof to close the
-- historical false-positive produced when a Graph was cancelled before its
-- graph-level Budget was admitted. Existing quarantines remain unprovable.

BEGIN IMMEDIATE;

DROP TRIGGER IF EXISTS trg_task_runtime_terminal_proof_binding_insert;

CREATE TRIGGER trg_task_runtime_terminal_proof_binding_insert
BEFORE INSERT ON task_runtime_terminal_reconciliation_proof
WHEN
    EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_supersession AS supersession
        WHERE supersession.prior_case_id = NEW.case_id
    )
    OR
    NOT EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_case AS item
        WHERE item.case_id = NEW.case_id
          AND item.snapshot_sha256 = NEW.case_snapshot_sha256
          AND item.recommended_action = NEW.action
          AND (
              (item.classification = 'EXACT_REPAIRABLE'
                  AND NEW.authority_kind IN (
                      'SYSTEM_DURABLE_EVIDENCE', 'SIGNED_OPERATOR'
                  ))
              OR
              (item.classification = 'SIGNED_REPAIR_REQUIRED'
                  AND NEW.authority_kind = 'SIGNED_OPERATOR')
              OR
              (item.classification = 'QUARANTINED_STRUCTURAL'
                  AND item.case_kind = 'TERMINAL_STRUCTURAL_ANOMALY'
                  AND item.reason_code =
                      'TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS'
                  AND item.graph_status = 'CANCELLED'
                  AND item.budget_id IS NULL
                  AND item.budget_status IS NULL
                  AND item.budget_status_version IS NULL
                  AND item.recommended_action = 'NONE'
                  AND NEW.authority_kind = 'SIGNED_OPERATOR')
          )
          AND (
              NEW.action NOT IN (
                  'RELEASE_BUDGET', 'FAIL_GRAPH_AND_RELEASE_BUDGET'
              )
              OR item.budget_id IS NOT NULL
          )
          AND (
              NEW.action <> 'RELEASE_BUDGET'
              OR NEW.target_graph_status = item.graph_status
          )
    )
    OR (
        NEW.graph_outbox_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM task_runtime_terminal_reconciliation_case AS item
            JOIN task_runtime_outbox AS outbox
              ON outbox.outbox_id = NEW.graph_outbox_id
            WHERE item.case_id = NEW.case_id
              AND outbox.aggregate_type = 'TASK_GRAPH'
              AND outbox.aggregate_id = item.graph_id
              AND outbox.graph_revision = item.graph_revision
              AND outbox.aggregate_version = item.graph_status_version + 1
              AND outbox.event_type = 'TASK_GRAPH_FAILED'
              AND outbox.attempt_id IS NULL
        )
    )
    OR (
        NEW.budget_outbox_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM task_runtime_terminal_reconciliation_case AS item
            JOIN task_runtime_outbox AS outbox
              ON outbox.outbox_id = NEW.budget_outbox_id
            WHERE item.case_id = NEW.case_id
              AND item.budget_id IS NOT NULL
              AND item.budget_status_version IS NOT NULL
              AND outbox.aggregate_type = 'BUDGET_RESERVATION'
              AND outbox.aggregate_id = item.budget_id
              AND outbox.graph_revision IS NULL
              AND outbox.aggregate_version = item.budget_status_version + 1
              AND outbox.event_type = 'BUDGET_RESERVATION_RELEASED'
              AND outbox.attempt_id IS NULL
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation proof binding drifted');
END;

COMMIT;
