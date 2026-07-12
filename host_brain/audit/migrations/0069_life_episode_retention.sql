-- AW-03: move Life episode retention columns out of SQLiteLedger inline DDL.
--
-- Empty databases reach this migration after 0045 creates life_episode.
-- Historical databases may already contain every column because the legacy
-- SQLiteLedger constructor altered the table at runtime; the migration runner
-- accepts that case only through checksum-bound, full-schema postconditions.

BEGIN IMMEDIATE;

ALTER TABLE life_episode ADD COLUMN actor_id TEXT;
ALTER TABLE life_episode ADD COLUMN intent TEXT;
ALTER TABLE life_episode ADD COLUMN action TEXT;
ALTER TABLE life_episode ADD COLUMN result TEXT;
ALTER TABLE life_episode ADD COLUMN base_salience REAL NOT NULL DEFAULT 0.0;
ALTER TABLE life_episode ADD COLUMN memory_strength REAL NOT NULL DEFAULT 0.0;
ALTER TABLE life_episode ADD COLUMN decay_profile_id TEXT NOT NULL DEFAULT 'NORMAL';
ALTER TABLE life_episode ADD COLUMN retention_state TEXT NOT NULL DEFAULT 'COLD';
ALTER TABLE life_episode ADD COLUMN last_recalled_ts_ms INTEGER;
ALTER TABLE life_episode ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE life_episode ADD COLUMN reinforcement_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE life_episode ADD COLUMN compressed_into_episode_id TEXT;
ALTER TABLE life_episode ADD COLUMN retention_until_ts_ms INTEGER;
ALTER TABLE life_episode ADD COLUMN forget_reason TEXT;

UPDATE life_episode
SET
    base_salience = COALESCE(NULLIF(base_salience, 0.0), COALESCE(salience, 0.0)),
    memory_strength = CASE
        WHEN COALESCE(memory_strength, 0.0) > 0.0 THEN memory_strength
        ELSE COALESCE(salience, 0.0)
    END,
    retention_state = CASE
        WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.75 THEN 'HOT'
        WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.35 THEN 'WARM'
        WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.10 THEN 'COLD'
        ELSE 'GONE'
    END;

CREATE INDEX IF NOT EXISTS idx_life_episode_retention
    ON life_episode(retention_state, memory_strength, last_touched_ts_ms);

COMMIT;
