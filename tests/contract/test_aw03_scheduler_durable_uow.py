from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.scheduler_repository import (
    SCHEDULER_ADMIN_OPERATION_CONFLICT,
    SCHEDULER_TERMINAL_OUTCOME_INVALID,
    SCHEDULER_UOW_MIGRATION_ASSET_KEY,
    SCHEDULER_UOW_MIGRATION_SHA256,
    SchedulerDefinitionRepository,
    SchedulerRepositoryError,
)
from host_brain.secure_ingress import (
    ClaimStatus,
    IngressAuditEvent,
    SQLiteSecureIngressStore,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "scheduler-uow.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-scheduler-durable-uow",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _authority(
    db_path: Path,
    *,
    request_id: str,
    suffix: str,
    body_material: str,
) -> tuple[SQLiteSecureIngressStore, dict[str, object]]:
    body_sha256 = _sha256(body_material)
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    decision = store.try_claim(
        principal_id="scheduler-admin-principal",
        surface="scheduler.admin",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        idempotency_key=f"scheduler-uow-{suffix}",
        nonce=f"scheduler-uow-nonce-{suffix}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    assert decision.status is ClaimStatus.NEW
    assert decision.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=decision.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"scheduler-uow-{suffix}",
            surface="scheduler.admin",
            action="scheduler:admin",
            method="POST",
            normalized_path="/api/scheduler/tasks",
            principal_id="scheduler-admin-principal",
            tenant_id="scheduler-uow",
            key_id="scheduler-uow-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + _sha256("credential-" + suffix),
            requested_scopes=("scheduler:admin",),
            required_scopes=("scheduler:admin",),
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + _sha256("idempotency-" + suffix),
            nonce_digest="sha256:" + _sha256("nonce-" + suffix),
            transport_key_digest="sha256:" + _sha256("transport-" + suffix),
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=decision.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=NOW,
        ),
    )
    return store, {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": "scheduler-admin-principal",
        "claim_ref": decision.claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha256,
        "existing_result_ref": None,
    }


def _definition() -> dict[str, object]:
    return {
        "task_id": "scheduler.uow.task",
        "trigger_mode": "interval",
        "trigger_config": {"seconds": 60},
        "intent_template": {"intent_type": "uow_contract"},
        "worker_key": "intent_dispatch",
        "priority": "MEDIUM",
        "enabled": True,
    }


def test_admin_mutation_and_immutable_result_are_one_sqlite_transaction(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store, authority = _authority(
        db_path,
        request_id="request-admin-atomic",
        suffix="atomic",
        body_material="publish-atomic",
    )
    try:
        def crash(stage: str) -> None:
            if stage == "after_admin_mutation_before_journal":
                raise RuntimeError("simulated process crash")

        crashing = SchedulerDefinitionRepository(db_path, fault_injector=crash)
        with pytest.raises(RuntimeError, match="simulated process crash"):
            crashing.publish_definition_operation(
                task_id="scheduler.uow.task",
                definition=_definition(),
                expected_head_version=0,
                next_scheduled_at_ms=NOW + 60_000,
                authority=authority,
            )

        restarted = SchedulerDefinitionRepository(db_path)
        assert restarted.get("scheduler.uow.task") is None
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM scheduler_admin_operation_result"
            ).fetchone()
        assert count == (0,)

        committed = restarted.publish_definition_operation(
            task_id="scheduler.uow.task",
            definition=_definition(),
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
            authority=authority,
        )
        replay = SchedulerDefinitionRepository(db_path).publish_definition_operation(
            task_id="scheduler.uow.task",
            definition=_definition(),
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
            authority={**authority, "allowed": False, "duplicate": True},
        )
        assert replay["receipt_ref"] == committed["receipt_ref"]
        assert replay["result"] == committed["result"]
        assert restarted.get("scheduler.uow.task")["head_version"] == 1

        with sqlite3.connect(str(db_path)) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
                conn.execute(
                    "UPDATE scheduler_admin_operation_result SET task_id = 'drift'"
                )
    finally:
        store.close()


def test_publish_enable_delete_each_replay_their_exact_journaled_result(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path)
    stores: list[SQLiteSecureIngressStore] = []
    try:
        publish_store, publish_auth = _authority(
            db_path,
            request_id="request-admin-publish",
            suffix="publish",
            body_material="publish",
        )
        stores.append(publish_store)
        published = repository.publish_definition_operation(
            task_id="scheduler.uow.task",
            definition=_definition(),
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
            authority=publish_auth,
        )

        enable_store, enable_auth = _authority(
            db_path,
            request_id="request-admin-enable",
            suffix="enable",
            body_material="enable",
        )
        stores.append(enable_store)
        disabled = repository.set_enabled_operation(
            task_id="scheduler.uow.task",
            enabled=False,
            expected_head_version=published["result"]["head_version"],
            authority=enable_auth,
        )
        disabled_replay = repository.set_enabled_operation(
            task_id="scheduler.uow.task",
            enabled=False,
            expected_head_version=published["result"]["head_version"],
            authority={**enable_auth, "allowed": False, "duplicate": True},
        )
        assert disabled_replay["receipt_ref"] == disabled["receipt_ref"]
        assert disabled_replay["result"] == disabled["result"]

        delete_store, delete_auth = _authority(
            db_path,
            request_id="request-admin-delete",
            suffix="delete",
            body_material="delete",
        )
        stores.append(delete_store)
        deleted = repository.delete_definition_operation(
            task_id="scheduler.uow.task",
            expected_head_version=disabled["result"]["head_version"],
            authority=delete_auth,
        )
        deleted_replay = repository.delete_definition_operation(
            task_id="scheduler.uow.task",
            expected_head_version=disabled["result"]["head_version"],
            authority={**delete_auth, "allowed": False, "duplicate": True},
        )
        assert deleted_replay["receipt_ref"] == deleted["receipt_ref"]
        assert deleted_replay["result"] == deleted["result"]

        with pytest.raises(SchedulerRepositoryError) as drift:
            repository.delete_definition_operation(
                task_id="scheduler.uow.task",
                expected_head_version=999,
                authority={**delete_auth, "allowed": False, "duplicate": True},
            )
        assert drift.value.reason_code == SCHEDULER_ADMIN_OPERATION_CONFLICT
    finally:
        for store in stores:
            store.close()


def test_uow_schema_checksum_immutability_and_secret_free_outcome(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        asset = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (SCHEDULER_UOW_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (SCHEDULER_UOW_MIGRATION_SHA256,)

    with pytest.raises(SchedulerRepositoryError) as secret:
        repository.append_terminal_outcome(
            occurrence_id="scheduleroccurrence://sha256/" + "a" * 64,
            outcome_kind="GOVERNOR_REJECTED",
            reason_code="POLICY_DENIED",
            outcome={
                "decision": "REJECT",
                "reason_code": "POLICY_DENIED",
                "secret": "must-not-persist",
            },
        )
    assert secret.value.reason_code == SCHEDULER_TERMINAL_OUTCOME_INVALID

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, SCHEDULER_UOW_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(SchedulerRepositoryError):
        SchedulerDefinitionRepository(db_path)
