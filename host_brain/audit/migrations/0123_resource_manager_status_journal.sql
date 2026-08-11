-- NE-01: runtime-scope Resource Manager status revisions with Host lease fencing.

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX IF NOT EXISTS ux_resource_manager_status_revision
    ON runtime_event_timeline(aggregate_type, aggregate_id, aggregate_version)
    WHERE aggregate_type = 'RESOURCE_MANAGER_STATUS';

CREATE TRIGGER IF NOT EXISTS resource_manager_status_requires_active_host_lease
BEFORE INSERT ON runtime_event_timeline
WHEN NEW.aggregate_type = 'RESOURCE_MANAGER_STATUS'
BEGIN
    SELECT CASE
        WHEN NEW.event_type <> 'RESOURCE_MANAGER_STATUS_UPDATED' THEN
            RAISE(ABORT, 'Resource Manager status event type is invalid')
        WHEN json_valid(NEW.payload_json) = 0 THEN
            RAISE(ABORT, 'Resource Manager status payload must be valid JSON')
        WHEN NEW.aggregate_version < 1 THEN
            RAISE(ABORT, 'Resource Manager status revision must be positive')
        WHEN json_type(
            NEW.payload_json,
            '$.runtime_scope_id'
        ) IS NOT 'text' THEN
            RAISE(ABORT, 'Resource Manager runtime scope is required')
        WHEN json_type(
            NEW.payload_json,
            '$.status_revision'
        ) IS NOT 'integer' THEN
            RAISE(ABORT, 'Resource Manager status revision is required')
        WHEN json_type(
            NEW.payload_json,
            '$.runtime_owner_id'
        ) IS NOT 'text' THEN
            RAISE(ABORT, 'Resource Manager runtime owner is required')
        WHEN json_type(
            NEW.payload_json,
            '$.runtime_lease_id'
        ) IS NOT 'text' THEN
            RAISE(ABORT, 'Resource Manager runtime lease is required')
        WHEN json_type(
            NEW.payload_json,
            '$.fencing_token'
        ) IS NOT 'integer' THEN
            RAISE(ABORT, 'Resource Manager fencing token is required')
        WHEN json_type(
            NEW.payload_json,
            '$.runtime_issued_at_ms'
        ) IS NOT 'integer' THEN
            RAISE(ABORT, 'Resource Manager runtime issue time is required')
        WHEN json_type(
            NEW.payload_json,
            '$.runtime_expires_at_ms'
        ) IS NOT 'integer' THEN
            RAISE(ABORT, 'Resource Manager runtime expiry is required')
        WHEN json_type(
            NEW.payload_json,
            '$.observed_at_ms'
        ) IS NOT 'integer' THEN
            RAISE(ABORT, 'Resource Manager observation time is required')
        WHEN json_type(
            NEW.payload_json,
            '$.reason'
        ) IS NOT 'text' THEN
            RAISE(ABORT, 'Resource Manager status reason is required')
        WHEN json_type(
            NEW.payload_json,
            '$.status'
        ) IS NOT 'object' THEN
            RAISE(ABORT, 'Resource Manager status object is required')
        WHEN json_extract(NEW.payload_json, '$.runtime_scope_id') <> NEW.aggregate_id THEN
            RAISE(ABORT, 'Resource Manager status scope does not match aggregate')
        WHEN CAST(json_extract(
            NEW.payload_json,
            '$.status_revision'
        ) AS INTEGER) <> NEW.aggregate_version THEN
            RAISE(ABORT, 'Resource Manager status revision does not match aggregate')
        WHEN CAST(json_extract(
            NEW.payload_json,
            '$.observed_at_ms'
        ) AS INTEGER) <> NEW.ts_ms THEN
            RAISE(ABORT, 'Resource Manager observation time does not match event')
        WHEN NOT EXISTS (
            SELECT 1
            FROM host_embodiment_runtime_lease AS lease
            WHERE lease.runtime_scope_id = json_extract(
                NEW.payload_json,
                '$.runtime_scope_id'
            )
              AND lease.owner_id = json_extract(
                NEW.payload_json,
                '$.runtime_owner_id'
            )
              AND lease.lease_id = json_extract(
                NEW.payload_json,
                '$.runtime_lease_id'
            )
              AND lease.fencing_token = CAST(json_extract(
                NEW.payload_json,
                '$.fencing_token'
              ) AS INTEGER)
              AND lease.released_at_ms IS NULL
              AND lease.issued_at_ms = CAST(json_extract(
                  NEW.payload_json,
                  '$.runtime_issued_at_ms'
              ) AS INTEGER)
              AND lease.expires_at_ms >= CAST(json_extract(
                  NEW.payload_json,
                  '$.runtime_expires_at_ms'
              ) AS INTEGER)
              AND lease.issued_at_ms <= NEW.ts_ms
              AND lease.expires_at_ms > NEW.ts_ms
              AND lease.issued_at_ms <= NEW.created_ts_ms
              AND lease.expires_at_ms > NEW.created_ts_ms
        ) THEN RAISE(ABORT, 'Resource Manager runtime authority is not active')
    END;
END;

COMMIT;
