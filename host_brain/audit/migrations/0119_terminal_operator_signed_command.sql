-- NE-01: immutable signed Operator command intake for terminal reconciliation.
--
-- The generic WorkOrder/TaskGraph input deliberately rejects signatures and
-- other credential-shaped fields.  Secure Ingress therefore stages the exact
-- signed command here and the governed graph carries only an immutable ref and
-- digest.  Staging is an ingress receipt; it never mutates terminal task state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_terminal_operator_signed_command (
    bundle_ref TEXT PRIMARY KEY
        CHECK(
            bundle_ref GLOB 'terminal-command://sha256/*'
            AND length(bundle_ref) = length('terminal-command://sha256/') + 64
            AND substr(bundle_ref, length('terminal-command://sha256/') + 1)
                NOT GLOB '*[^0-9a-f]*'
        ),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.terminal_operator_command.v1'),
    request_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(request_id)) BETWEEN 1 AND 255),
    principal_id TEXT NOT NULL
        CHECK(length(trim(principal_id)) BETWEEN 1 AND 255),
    case_id TEXT NOT NULL
        CHECK(length(trim(case_id)) BETWEEN 1 AND 512),
    command_json TEXT NOT NULL
        CHECK(json_valid(command_json) AND json_type(command_json) = 'object'),
    command_sha256 TEXT NOT NULL
        CHECK(
            command_sha256 GLOB 'sha256:*'
            AND length(command_sha256) = 71
            AND substr(command_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    ingress_claim_ref TEXT NOT NULL
        CHECK(length(trim(ingress_claim_ref)) BETWEEN 1 AND 512),
    ingress_audit_ref TEXT NOT NULL
        CHECK(length(trim(ingress_audit_ref)) BETWEEN 1 AND 512),
    ingress_body_sha256 TEXT NOT NULL
        CHECK(
            ingress_body_sha256 GLOB 'sha256:*'
            AND length(ingress_body_sha256) = 71
            AND substr(ingress_body_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    staged_at_ms INTEGER NOT NULL CHECK(staged_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_task_terminal_operator_command_case
    ON task_terminal_operator_signed_command(case_id, staged_at_ms, bundle_ref);

CREATE INDEX IF NOT EXISTS idx_task_terminal_operator_command_audit
    ON task_terminal_operator_signed_command(ingress_audit_ref, request_id);

CREATE TRIGGER IF NOT EXISTS task_terminal_operator_signed_command_no_update
BEFORE UPDATE ON task_terminal_operator_signed_command
BEGIN
    SELECT RAISE(ABORT, 'terminal operator signed commands are immutable');
END;

CREATE TRIGGER IF NOT EXISTS task_terminal_operator_signed_command_no_delete
BEFORE DELETE ON task_terminal_operator_signed_command
BEGIN
    SELECT RAISE(ABORT, 'terminal operator signed commands are immutable');
END;

COMMIT;
