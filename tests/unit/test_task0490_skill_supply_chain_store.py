from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
from pathlib import Path
import sqlite3

import pytest

from host_brain.skills.supply_chain_store import (
    BundleKind,
    SKILL_SUPPLY_CHAIN_MIGRATION_SHA256,
    SQLiteSkillSupplyChainStore,
    SkillSupplyChainStoreError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain/audit/migrations/0060_skill_supply_chain.sql"


class FrozenClock:
    def __init__(self, value: int = 1_950_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, delta_ms: int = 1) -> None:
        self.value += delta_ms


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "skill-supply-chain.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(
            """
            CREATE TABLE schema_migration_assets (
                asset_key TEXT PRIMARY KEY,
                migration_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                disposition TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL,
                UNIQUE(migration_id, name)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migration_assets(
                asset_key, migration_id, name, checksum, disposition, applied_ts_ms
            ) VALUES ('0060#skill_supply_chain', 60, 'skill_supply_chain', ?, 'APPLIED', ?)
            """,
            (SKILL_SUPPLY_CHAIN_MIGRATION_SHA256, 1_950_000_000_000),
        )
    return db_path


def _intake(
    store: SQLiteSkillSupplyChainStore,
    *,
    suffix: str = "v1",
    name: str = "safe-skill",
    version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    return store.intake_bundle(
        bundle_id=f"skill://{name}/{suffix}",
        bundle_kind=BundleKind.SKILL,
        bundle_name=name,
        bundle_version=version or suffix,
        publisher_id="publisher://trusted/test",
        bundle_digest=_sha(f"bundle-{name}-{suffix}"),
        quarantine_audit_ref=f"audit://quarantine/{name}/{suffix}",
        idempotency_key=idempotency_key or f"intake-{name}-{suffix}",
    )


def _stage(
    store: SQLiteSkillSupplyChainStore,
    *,
    suffix: str = "v1",
    name: str = "safe-skill",
    expected_status_version: int = 0,
) -> dict[str, object]:
    return store.stage_bundle(
        bundle_id=f"skill://{name}/{suffix}",
        expected_status_version=expected_status_version,
        signature_key_id="signing-key://release/test-1",
        signature_algorithm="hmac-sha256",
        signature_verification_audit_ref=f"audit://signature/{name}/{suffix}",
        sbom_digest=_sha(f"sbom-{name}-{suffix}"),
        sandbox_profile_id="sandbox-profile://python/isolated-v1",
        sandbox_profile_digest=_sha("sandbox-profile-isolated-v1"),
        audit_ref=f"audit://stage/{name}/{suffix}",
        idempotency_key=f"stage-{name}-{suffix}",
    )


def test_schema_is_checksum_bound_expand_only_and_private_material_free(
    tmp_path: Path,
) -> None:
    source = (
        ROOT / "host_brain/skills/supply_chain_store.py"
    ).read_text(encoding="utf-8").upper()
    assert "CREATE TABLE" not in source
    assert "CREATE INDEX" not in source
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == (
        SKILL_SUPPLY_CHAIN_MIGRATION_SHA256
    )

    db_path = _migrated_db(tmp_path / "good")
    SQLiteSkillSupplyChainStore(db_path).assert_schema_ready()
    with sqlite3.connect(str(db_path)) as conn:
        columns = {
            str(row[1]).lower()
            for table in ("skill_supply_chain_bundle", "skill_supply_chain_operation")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    assert not any(
        forbidden in column
        for column in columns
        for forbidden in (
            "raw_bundle",
            "bundle_body",
            "signature_value",
            "signature_material",
            "private_key",
            "secret",
            "credential",
        )
    )
    api_source = inspect.signature(SQLiteSkillSupplyChainStore.intake_bundle)
    assert "raw_bundle" not in str(api_source)
    assert "signature_value" not in str(api_source)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE schema_migration_assets SET checksum = ?
            WHERE asset_key = '0060#skill_supply_chain'
            """,
            ("0" * 64,),
        )
    with pytest.raises(SkillSupplyChainStoreError, match="MIGRATION_DRIFT"):
        SQLiteSkillSupplyChainStore(db_path)


def test_intake_defaults_to_quarantine_and_is_strictly_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))

    first = _intake(store)
    duplicate = _intake(store)

    assert first["acceptance_status"] == "NEW"
    assert duplicate["acceptance_status"] == "DUPLICATE_SAME"
    assert first["operation"]["operation_id"] == duplicate["operation"]["operation_id"]
    bundle = first["bundle"]
    assert bundle["state"] == "QUARANTINED"
    assert bundle["activation_generation"] == 0
    assert bundle["activation_fencing_token"] == 0
    assert bundle["signature_verified"] is False

    with pytest.raises(SkillSupplyChainStoreError, match="IDEMPOTENCY_CONFLICT"):
        store.intake_bundle(
            bundle_id="skill://safe-skill/v1",
            bundle_kind="SKILL",
            bundle_name="safe-skill",
            bundle_version="v1",
            publisher_id="publisher://different",
            bundle_digest=_sha("bundle-safe-skill-v1"),
            quarantine_audit_ref="audit://quarantine/safe-skill/v1",
            idempotency_key="intake-safe-skill-v1",
        )

    with pytest.raises(SkillSupplyChainStoreError, match="IDENTITY_CONFLICT"):
        store.intake_bundle(
            bundle_id="skill://safe-skill/v1-alias",
            bundle_kind="SKILL",
            bundle_name="safe-skill",
            bundle_version="v1",
            publisher_id="publisher://trusted/test",
            bundle_digest=_sha("different-content"),
            quarantine_audit_ref="audit://quarantine/safe-skill/alias",
            idempotency_key="different-idempotency",
        )


def test_stage_requires_signature_sbom_sandbox_and_compare_and_swap(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))
    _intake(store)

    with pytest.raises(SkillSupplyChainStoreError, match="ALGORITHM_UNTRUSTED"):
        store.stage_bundle(
            bundle_id="skill://safe-skill/v1",
            expected_status_version=0,
            signature_key_id="signing-key://release/test-1",
            signature_algorithm="MD5_WITH_RSA",
            signature_verification_audit_ref="audit://signature/v1",
            sbom_digest=_sha("sbom"),
            sandbox_profile_id="sandbox-profile://isolated/v1",
            sandbox_profile_digest=_sha("sandbox"),
            audit_ref="audit://stage/v1",
            idempotency_key="bad-algorithm",
        )

    staged = _stage(store)
    assert staged["bundle"]["state"] == "STAGED"
    assert staged["bundle"]["status_version"] == 1
    assert staged["bundle"]["signature_verified"] is True
    assert staged["bundle"]["signature_algorithm"] == "HMAC_SHA256"
    assert staged["bundle"]["sbom_digest"].startswith("sha256:")
    assert staged["bundle"]["sandbox_profile_digest"].startswith("sha256:")

    duplicate = _stage(store)
    assert duplicate["acceptance_status"] == "DUPLICATE_SAME"
    with pytest.raises(SkillSupplyChainStoreError, match="STATUS_CONFLICT"):
        store.stage_bundle(
            bundle_id="skill://safe-skill/v1",
            expected_status_version=0,
            signature_key_id="signing-key://release/test-1",
            signature_algorithm="ED25519",
            signature_verification_audit_ref="audit://signature/again",
            sbom_digest=_sha("sbom-again"),
            sandbox_profile_id="sandbox-profile://python/isolated-v1",
            sandbox_profile_digest=_sha("sandbox-again"),
            audit_ref="audit://stage/again",
            idempotency_key="stage-again",
        )


def test_activation_generation_fencing_and_atomic_version_replacement(
    tmp_path: Path,
) -> None:
    clock = FrozenClock()
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path), clock=clock)
    _intake(store, suffix="v1")
    _stage(store, suffix="v1")

    first = store.activate_bundle(
        bundle_id="skill://safe-skill/v1",
        expected_status_version=1,
        expected_activation_generation=0,
        fencing_token=10,
        audit_ref="audit://activation/safe-skill/v1",
        idempotency_key="activate-safe-skill-v1",
    )
    assert first["bundle"]["state"] == "ACTIVE"
    assert first["bundle"]["activation_generation"] == 1
    assert store.get_active_bundle(bundle_kind="SKILL", bundle_name="safe-skill")[
        "bundle_id"
    ] == "skill://safe-skill/v1"

    duplicate = store.activate_bundle(
        bundle_id="skill://safe-skill/v1",
        expected_status_version=1,
        expected_activation_generation=0,
        fencing_token=10,
        audit_ref="audit://activation/safe-skill/v1",
        idempotency_key="activate-safe-skill-v1",
    )
    assert duplicate["acceptance_status"] == "DUPLICATE_SAME"
    with pytest.raises(SkillSupplyChainStoreError, match="IDEMPOTENCY_CONFLICT"):
        store.activate_bundle(
            bundle_id="skill://safe-skill/v1",
            expected_status_version=1,
            expected_activation_generation=0,
            fencing_token=11,
            audit_ref="audit://activation/safe-skill/v1",
            idempotency_key="activate-safe-skill-v1",
        )

    clock.advance()
    _intake(store, suffix="v2")
    _stage(store, suffix="v2")
    second = store.activate_bundle(
        bundle_id="skill://safe-skill/v2",
        expected_status_version=1,
        expected_activation_generation=1,
        fencing_token=11,
        audit_ref="audit://activation/safe-skill/v2",
        idempotency_key="activate-safe-skill-v2",
    )
    assert second["bundle"]["activation_generation"] == 2
    assert second["operation"]["replaced_bundle_id"] == "skill://safe-skill/v1"
    assert store.get_bundle(bundle_id="skill://safe-skill/v1")["state"] == "STAGED"
    assert store.get_active_bundle(bundle_kind="SKILL", bundle_name="safe-skill")[
        "bundle_id"
    ] == "skill://safe-skill/v2"

    with pytest.raises(SkillSupplyChainStoreError, match="FENCING_TOKEN_STALE"):
        store.activate_bundle(
            bundle_id="skill://safe-skill/v1",
            expected_status_version=3,
            expected_activation_generation=2,
            fencing_token=11,
            audit_ref="audit://activation/stale-fence",
            idempotency_key="stale-fence",
        )


def test_concurrent_activation_allows_exactly_one_generation_winner(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))
    _intake(store, suffix="v1")
    _stage(store, suffix="v1")
    store.activate_bundle(
        bundle_id="skill://safe-skill/v1",
        expected_status_version=1,
        expected_activation_generation=0,
        fencing_token=1,
        audit_ref="audit://activation/v1",
        idempotency_key="activate-v1",
    )
    for suffix in ("v2", "v3"):
        _intake(store, suffix=suffix)
        _stage(store, suffix=suffix)

    def activate(suffix: str, fence: int) -> str:
        try:
            store.activate_bundle(
                bundle_id=f"skill://safe-skill/{suffix}",
                expected_status_version=1,
                expected_activation_generation=1,
                fencing_token=fence,
                audit_ref=f"audit://activation/{suffix}",
                idempotency_key=f"activate-{suffix}",
            )
            return "NEW"
        except SkillSupplyChainStoreError as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda args: activate(*args), (("v2", 2), ("v3", 3))))

    assert outcomes.count("NEW") == 1
    assert outcomes.count("SKILL_SUPPLY_CHAIN_ACTIVATION_GENERATION_CONFLICT") == 1
    assert len(store.list_bundles(state="ACTIVE")) == 1


def test_rejection_is_terminal_and_audit_query_includes_replacement(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))
    _intake(store, suffix="unsigned")
    rejected = store.reject_bundle(
        bundle_id="skill://safe-skill/unsigned",
        expected_status_version=0,
        audit_ref="audit://rejection/unsigned",
        idempotency_key="reject-unsigned",
        reason_code="SIGNATURE_MISSING",
    )
    assert rejected["bundle"]["state"] == "REJECTED"
    assert rejected["bundle"]["reason_code"] == "SIGNATURE_MISSING"

    duplicate = store.reject_bundle(
        bundle_id="skill://safe-skill/unsigned",
        expected_status_version=0,
        audit_ref="audit://rejection/unsigned",
        idempotency_key="reject-unsigned",
        reason_code="SIGNATURE_MISSING",
    )
    assert duplicate["acceptance_status"] == "DUPLICATE_SAME"
    with pytest.raises(SkillSupplyChainStoreError, match="STATUS_CONFLICT"):
        store.reject_bundle(
            bundle_id="skill://safe-skill/unsigned",
            expected_status_version=1,
            audit_ref="audit://rejection/again",
            idempotency_key="reject-again",
            reason_code="STILL_REJECTED",
        )

    audit = store.list_audit_operations(bundle_id="skill://safe-skill/unsigned")
    assert [event["operation"] for event in audit] == ["INTAKE", "REJECT"]
    assert all(event["audit_ref"].startswith("audit://") for event in audit)
    assert store.list_audit_operations(audit_ref="audit://rejection/unsigned") == [
        audit[-1]
    ]


def test_busy_database_returns_stable_timeout_reason(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSkillSupplyChainStore(db_path, timeout_ms=10)
    blocker = sqlite3.connect(str(db_path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SkillSupplyChainStoreError, match="STORE_BUSY"):
            _intake(store)
    finally:
        blocker.rollback()
        blocker.close()
