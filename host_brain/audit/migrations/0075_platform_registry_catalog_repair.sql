-- Repair the one checksum-bound 0072 draft that was applied before the
-- platform-registry migration asset was sealed.  Fresh databases already
-- carry the final checksum; the same transaction is therefore a verified
-- no-op apart from ensuring the missing seal trigger exists.

BEGIN IMMEDIATE;

CREATE TEMP TABLE aw03_platform_registry_repair_guard (
    accepted INTEGER NOT NULL CHECK(accepted = 1)
);

INSERT INTO aw03_platform_registry_repair_guard(accepted)
SELECT CASE
    WHEN EXISTS (
        SELECT 1
        FROM schema_migrations
        WHERE migration_id = 72
          AND name = 'platform_registry_persistence'
          AND checksum IN (
              '01fea4a36c2fcbad3f0e1b33ead86e9f0bf7fa0293176c34408fd28020f0fb0f',
              '21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5'
          )
    )
    AND EXISTS (
        SELECT 1
        FROM schema_migration_assets
        WHERE asset_key = '0072#platform_registry_persistence'
          AND migration_id = 72
          AND name = 'platform_registry_persistence'
          AND checksum IN (
              '01fea4a36c2fcbad3f0e1b33ead86e9f0bf7fa0293176c34408fd28020f0fb0f',
              '21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5'
          )
          AND disposition IN ('APPLIED', 'APPLIED_WITH_COMPAT', 'LEGACY_BACKFILL')
    )
    THEN 1
    ELSE 0
END;

CREATE TRIGGER IF NOT EXISTS trg_vts_expression_mapping_no_insert_after_journal
BEFORE INSERT ON vts_expression_mapping
WHEN EXISTS (
    SELECT 1 FROM vts_mapping_change_journal WHERE version = NEW.version
)
BEGIN
    SELECT RAISE(ABORT, 'VTS_EXPRESSION_MAPPING_VERSION_SEALED');
END;

UPDATE schema_migrations
SET checksum = '21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5'
WHERE migration_id = 72
  AND name = 'platform_registry_persistence'
  AND checksum = '01fea4a36c2fcbad3f0e1b33ead86e9f0bf7fa0293176c34408fd28020f0fb0f';

UPDATE schema_migration_assets
SET checksum = '21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5'
WHERE asset_key = '0072#platform_registry_persistence'
  AND migration_id = 72
  AND name = 'platform_registry_persistence'
  AND checksum = '01fea4a36c2fcbad3f0e1b33ead86e9f0bf7fa0293176c34408fd28020f0fb0f';

DROP TABLE aw03_platform_registry_repair_guard;

COMMIT;
