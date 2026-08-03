-- DL-07: policy payload digests are content identities, not global row ids.
--
-- 0142 made version_digest globally UNIQUE.  Two workspace/subject scopes may
-- legitimately start from the same immutable baseline payload, so the global
-- constraint rejected the second scope before its active projection existed.
-- Keep the append-only primary identity scoped by workspace/subject/kind/version
-- and add a scoped digest lookup index used by exact rollback resolution.

BEGIN IMMEDIATE;

DROP TRIGGER IF EXISTS evolution_policy_version_v2_no_update;
DROP TRIGGER IF EXISTS evolution_policy_version_v2_no_delete;

ALTER TABLE evolution_policy_version_v2
    RENAME TO evolution_policy_version_v2_0142;

CREATE TABLE evolution_policy_version_v2 (
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    policy_kind TEXT NOT NULL CHECK(length(trim(policy_kind)) BETWEEN 1 AND 64),
    version TEXT NOT NULL CHECK(length(trim(version)) BETWEEN 1 AND 128),
    version_digest TEXT NOT NULL CHECK(length(version_digest) = 64),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    provenance_ref TEXT NOT NULL CHECK(length(trim(provenance_ref)) BETWEEN 1 AND 1024),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(workspace_id, subject_id, policy_kind, version)
);

INSERT INTO evolution_policy_version_v2(
    workspace_id,
    subject_id,
    policy_kind,
    version,
    version_digest,
    payload_json,
    provenance_ref,
    created_at_ms
)
SELECT
    workspace_id,
    subject_id,
    policy_kind,
    version,
    version_digest,
    payload_json,
    provenance_ref,
    created_at_ms
FROM evolution_policy_version_v2_0142;

DROP TABLE evolution_policy_version_v2_0142;

CREATE INDEX idx_evolution_policy_version_v2_scope_digest
    ON evolution_policy_version_v2(
        workspace_id,
        subject_id,
        policy_kind,
        version_digest
    );

CREATE TRIGGER evolution_policy_version_v2_no_update
BEFORE UPDATE ON evolution_policy_version_v2
BEGIN SELECT RAISE(ABORT, 'Evolution policy version is immutable'); END;

CREATE TRIGGER evolution_policy_version_v2_no_delete
BEFORE DELETE ON evolution_policy_version_v2
BEGIN SELECT RAISE(ABORT, 'Evolution policy version is append-only'); END;

COMMIT;
