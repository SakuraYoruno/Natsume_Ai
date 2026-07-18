from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.service_catalog.models import CatalogMode, load_catalog_mode
from host_brain.service_catalog.rollout_authority import (
    CatalogCoordinatorAuthorityV1,
    CatalogRolloutOperationV1,
    ServiceCatalogRolloutError,
    ServiceCatalogRolloutGuardianObserver,
    ServiceCatalogRolloutStore,
    catalog_operation_identity_digest,
)


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "catalog-rollout.sqlite3"
    result = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-service-catalog-rollout"
    )
    assert result.success, result.detail
    return db_path


def _host_authority(db_path: Path, *, scope: str, now: int) -> dict[str, object]:
    identity = {
        "runtime_scope_id": scope,
        "runtime_owner_id": f"host-runtime-owner::{scope}",
        "runtime_lease_id": f"host-runtime-lease::{scope}",
        "fencing_token": 1,
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO host_embodiment_runtime_lease "
            "(runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, "
            "expires_at_ms, released_at_ms) VALUES (?, ?, ?, 1, ?, ?, NULL)",
            (scope, identity["runtime_owner_id"], identity["runtime_lease_id"], now - 10, now + 100_000),
        )
    return identity


def _operation(operation_id: str, stage: CatalogMode) -> CatalogRolloutOperationV1:
    return CatalogRolloutOperationV1(
        operation_id=operation_id,
        catalog_snapshot_digest="sha256:catalog",
        candidate_owner_id="host.main",
        stage=stage,
        endpoint_ids=("host.main.http",),
        rollback_ref="service-catalog://host.main/previous-stage",
    )


def _terminal_payload(
    operation_id: str,
    stage: CatalogMode,
    runtime_authority: dict[str, object],
) -> dict[str, object]:
    return {
        "catalog_snapshot_digest": "sha256:catalog",
        "candidate_owner_id": "host.main",
        "stage": stage.value,
        "health_identity": {
            "endpoint_id": "host.main.http",
            "owner_id": "host.main",
            "protocol_id": "http",
            "protocol_version": "0.1",
        },
        "runtime_authority": runtime_authority,
        "activation_identity": {
            "operation_id": operation_id,
            "operation_id_digest": catalog_operation_identity_digest(operation_id),
            "catalog_snapshot_digest": "sha256:catalog",
            "candidate_owner_id": "host.main",
            "stage": stage.value,
        },
        "rollback_ref": "service-catalog://host.main/previous-stage",
    }


def _append_starting(
    store: ServiceCatalogRolloutStore,
    operation_id: str,
    *,
    now: int,
    authority: CatalogCoordinatorAuthorityV1 | None = None,
) -> None:
    store.append_receipt(
        operation_id,
        event_type="ROLLOUT_PREPARED",
        status="PREPARED",
        payload={"prepared": True},
        authority=authority,
        now_ms=now,
    )
    store.append_receipt(
        operation_id,
        event_type="ACTIVATION_STARTING",
        status="STARTING",
        payload={"attempt": 1},
        authority=authority,
        now_ms=now + 1,
    )


def test_five_typed_modes_and_invalid_mode_are_explicit() -> None:
    for mode in CatalogMode:
        state = load_catalog_mode({"HOST_SERVICE_CATALOG_MODE": mode.value})
        assert state.valid is True
        assert state.mode is mode
    invalid = load_catalog_mode({"HOST_SERVICE_CATALOG_MODE": "SKIP"})
    assert invalid.valid is False
    assert invalid.reason_code == "SERVICE_CATALOG_MODE_INVALID"


def test_receipt_state_machine_rejects_terminal_first_invalid_pair_and_second_terminal(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    now = 5_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-state-machine",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    host_authority = _host_authority(
        db_path,
        scope="runtime-state-machine",
        now=now,
    )
    operation = _operation("operation-state-machine", CatalogMode.AUDIT)
    store.register_operation(operation, authority, now_ms=now + 1)

    with pytest.raises(ServiceCatalogRolloutError, match="transition is invalid"):
        store.append_receipt(
            operation.operation_id,
            event_type="ACTIVATION_STARTED",
            status="STARTED",
            payload=_terminal_payload(operation.operation_id, CatalogMode.AUDIT, host_authority),
            now_ms=now + 2,
        )
    with pytest.raises(ServiceCatalogRolloutError, match="event/status pair is invalid"):
        store.append_receipt(
            operation.operation_id,
            event_type="ROLLOUT_PREPARED",
            status="STARTED",
            payload={"invalid": True},
            now_ms=now + 2,
        )

    _append_starting(store, operation.operation_id, now=now + 2)
    store.append_receipt(
        operation.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(operation.operation_id, CatalogMode.AUDIT, host_authority),
        now_ms=now + 4,
    )
    with pytest.raises(ServiceCatalogRolloutError, match="transition is invalid"):
        store.append_receipt(
            operation.operation_id,
            event_type="ACTIVATION_FAILED",
            status="FAILED",
            payload={"reason_code": "must-not-rewrite-terminal"},
            now_ms=now + 5,
        )


def test_sqlite_trigger_rejects_direct_terminal_first_write(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    now = 7_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-physical-state-machine",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    operation = _operation("operation-direct-terminal", CatalogMode.AUDIT)
    store.register_operation(operation, authority, now_ms=now + 1)
    with sqlite3.connect(db_path) as connection, pytest.raises(
        sqlite3.IntegrityError,
        match="receipt transition is invalid",
    ):
        connection.execute(
            "INSERT INTO service_catalog_rollout_receipt "
            "(receipt_id, operation_id, event_key, event_type, status, "
            "runtime_scope_id, coordinator_owner_id, coordinator_lease_id, "
            "coordinator_fencing_token, payload_json, previous_digest, receipt_digest, "
            "created_at_ms) VALUES (?, ?, ?, 'ACTIVATION_FAILED', 'FAILED', ?, ?, ?, ?, "
            "'{}', NULL, ?, ?)",
            (
                "direct-terminal-receipt",
                operation.operation_id,
                operation.operation_id + ":ACTIVATION_FAILED",
                authority.runtime_scope_id,
                authority.owner_id,
                authority.lease_id,
                authority.fencing_token,
                "sha256:direct-terminal",
                now + 2,
            ),
        )


def test_sqlite_rejects_stage_rank_alias_that_would_skip_audit(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-stage-rank",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=8_000,
    )
    assert authority is not None
    with sqlite3.connect(db_path) as connection, pytest.raises(
        sqlite3.IntegrityError,
        match="CHECK constraint failed",
    ):
        connection.execute(
            "INSERT INTO service_catalog_rollout_operation "
            "(operation_id, operation_digest, runtime_scope_id, coordinator_owner_id, "
            "coordinator_lease_id, coordinator_fencing_token, catalog_snapshot_digest, "
            "candidate_owner_id, stage, stage_rank, endpoint_ids_json, rollback_ref, "
            "created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ENFORCE', 0, ?, ?, ?)",
            (
                "operation-stage-rank-alias",
                "sha256:stage-rank-alias",
                authority.runtime_scope_id,
                authority.owner_id,
                authority.lease_id,
                authority.fencing_token,
                "sha256:catalog",
                "host.main",
                '["host.main.http"]',
                "service-catalog://host.main/previous-stage",
                8_001,
            ),
        )


def test_terminal_rejects_other_runtime_scope_and_operation_digest_tamper(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    now = 9_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-terminal-a",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    other_host_authority = _host_authority(
        db_path,
        scope="runtime-terminal-b",
        now=now,
    )
    operation = _operation("operation-terminal-scope", CatalogMode.AUDIT)
    store.register_operation(operation, authority, now_ms=now + 1)
    _append_starting(store, operation.operation_id, now=now + 2)
    with pytest.raises(ServiceCatalogRolloutError, match="identity drift"):
        store.append_receipt(
            operation.operation_id,
            event_type="ACTIVATION_STARTED",
            status="STARTED",
            payload=_terminal_payload(
                operation.operation_id,
                CatalogMode.AUDIT,
                other_host_authority,
            ),
            now_ms=now + 4,
        )
    previous_digest = store.list_receipts(operation.operation_id)[-1][
        "receipt_digest"
    ]
    with sqlite3.connect(db_path) as connection, pytest.raises(
        sqlite3.IntegrityError,
        match="activation identity drift",
    ):
        connection.execute(
            "INSERT INTO service_catalog_rollout_receipt "
            "(receipt_id, operation_id, event_key, event_type, status, "
            "runtime_scope_id, coordinator_owner_id, coordinator_lease_id, "
            "coordinator_fencing_token, payload_json, previous_digest, receipt_digest, "
            "created_at_ms) VALUES (?, ?, ?, 'ACTIVATION_STARTED', 'STARTED', "
            "?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "direct-cross-runtime-terminal",
                operation.operation_id,
                operation.operation_id + ":ACTIVATION_STARTED",
                authority.runtime_scope_id,
                authority.owner_id,
                authority.lease_id,
                authority.fencing_token,
                json.dumps(
                    _terminal_payload(
                        operation.operation_id,
                        CatalogMode.AUDIT,
                        other_host_authority,
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                previous_digest,
                "sha256:direct-cross-runtime-terminal",
                now + 4,
            ),
        )

    valid_host_authority = _host_authority(
        db_path,
        scope="runtime-terminal-a",
        now=now,
    )
    tampered = _terminal_payload(
        operation.operation_id,
        CatalogMode.AUDIT,
        valid_host_authority,
    )
    tampered["activation_identity"] = {
        **tampered["activation_identity"],
        "operation_id_digest": "sha256:tampered",
    }
    with pytest.raises(ServiceCatalogRolloutError, match="identity drift"):
        store.append_receipt(
            operation.operation_id,
            event_type="ACTIVATION_STARTED",
            status="STARTED",
            payload=tampered,
            now_ms=now + 5,
        )
    assert store.operation_state(operation.operation_id) == "STARTING"


def test_sqlite_rollout_is_monotonic_exact_replay_and_restart_safe(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    now = 10_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-a",
        owner_id="catalog-owner-a",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    host_authority = _host_authority(db_path, scope="runtime-a", now=now)

    audit = _operation("operation-audit", CatalogMode.AUDIT)
    registered = store.register_operation(audit, authority, now_ms=now + 1)
    replay = ServiceCatalogRolloutStore(db_path).register_operation(
        audit, authority, now_ms=now + 2
    )
    assert replay["operation_digest"] == registered["operation_digest"]
    _append_starting(store, audit.operation_id, now=now + 3)
    terminal = store.append_receipt(
        audit.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(audit.operation_id, CatalogMode.AUDIT, host_authority),
        now_ms=now + 5,
    )
    exact_terminal = ServiceCatalogRolloutStore(db_path).append_receipt(
        audit.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(audit.operation_id, CatalogMode.AUDIT, host_authority),
        now_ms=now + 99,
    )
    assert exact_terminal["receipt_digest"] == terminal["receipt_digest"]
    assert ServiceCatalogRolloutStore(db_path).operation_state(audit.operation_id) == "STARTED"

    shadow = _operation("operation-shadow", CatalogMode.SHADOW)
    store.register_operation(shadow, authority, now_ms=now + 6)
    _append_starting(store, shadow.operation_id, now=now + 7)
    store.append_receipt(
        shadow.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(shadow.operation_id, CatalogMode.SHADOW, host_authority),
        now_ms=now + 9,
    )
    with pytest.raises(ServiceCatalogRolloutError, match="regression or skip"):
        store.register_operation(
            _operation("operation-canary-skip", CatalogMode.CANARY),
            authority,
            now_ms=now + 7,
        )
    with pytest.raises(ServiceCatalogRolloutError, match="regression or skip"):
        store.register_operation(
            _operation("operation-audit-regression", CatalogMode.AUDIT),
            authority,
            now_ms=now + 7,
        )
    with pytest.raises(ServiceCatalogRolloutError, match="identity drift"):
        store.append_receipt(
            shadow.operation_id,
            event_type="ACTIVATION_STARTED",
            status="STARTED",
            payload={**_terminal_payload(shadow.operation_id, CatalogMode.SHADOW, host_authority), "rollback_ref": "drift"},
            now_ms=now + 8,
        )


def test_digest_tamper_is_detected_after_sqlite_restart(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-tamper",
        owner_id="catalog-owner",
        lease_ms=10_000,
        now_ms=1_000,
    )
    assert authority is not None
    store.register_operation(_operation("operation-tamper", CatalogMode.AUDIT), authority, now_ms=1_001)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER service_catalog_rollout_operation_no_update")
        connection.execute(
            "UPDATE service_catalog_rollout_operation SET rollback_ref = 'tampered' "
            "WHERE operation_id = 'operation-tamper'"
        )
    with pytest.raises(ServiceCatalogRolloutError, match="digest mismatch"):
        ServiceCatalogRolloutStore(db_path).get_operation("operation-tamper")


def test_rollout_head_reopens_terminal_health_authority(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    now = 20_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-head",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    host_authority = _host_authority(db_path, scope="runtime-head", now=now)
    operation = _operation("operation-head", CatalogMode.AUDIT)
    store.register_operation(operation, authority, now_ms=now + 1)
    _append_starting(store, operation.operation_id, now=now + 2)
    store.append_receipt(
        operation.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(operation.operation_id, CatalogMode.AUDIT, host_authority),
        now_ms=now + 4,
    )
    head = store.rollout_head(
        runtime_scope_id="runtime-head",
        candidate_owner_id="host.main",
    )
    assert head is not None
    assert head["operation"]["operation_id"] == operation.operation_id
    assert head["terminal_receipt"]["event_type"] == "ACTIVATION_STARTED"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE service_catalog_rollout_head SET health_identity_digest = 'tampered' "
            "WHERE runtime_scope_id = 'runtime-head' AND candidate_owner_id = 'host.main'"
        )
    with pytest.raises(ServiceCatalogRolloutError, match="projection identity drift"):
        ServiceCatalogRolloutStore(db_path).rollout_head(
            runtime_scope_id="runtime-head",
            candidate_owner_id="host.main",
        )


def test_pending_operation_can_be_reconciled_by_new_coordinator_fence(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    store = ServiceCatalogRolloutStore(db_path)
    old = store.acquire_authority(
        runtime_scope_id="runtime-takeover",
        owner_id="catalog-old",
        lease_ms=10_000,
        now_ms=2_000,
    )
    assert old is not None
    host_authority = _host_authority(db_path, scope="runtime-takeover", now=2_000)
    operation = _operation("operation-takeover", CatalogMode.AUDIT)
    store.register_operation(operation, old, now_ms=2_001)
    _append_starting(store, operation.operation_id, now=2_002, authority=old)
    assert store.release_authority(old, now_ms=2_004) is True
    new = store.acquire_authority(
        runtime_scope_id="runtime-takeover",
        owner_id="catalog-new",
        lease_ms=10_000,
        now_ms=2_005,
    )
    assert new is not None
    terminal = ServiceCatalogRolloutStore(db_path).append_receipt(
        operation.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(operation.operation_id, CatalogMode.AUDIT, host_authority),
        authority=new,
        now_ms=2_006,
    )
    assert terminal["coordinator_authority"]["fencing_token"] == 2
    assert ServiceCatalogRolloutStore(db_path).operation_state(operation.operation_id) == "STARTED"


def test_guardian_observer_reads_durable_terminal_receipt(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    now = 50_000
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id="runtime-guardian",
        owner_id="catalog-owner",
        lease_ms=100_000,
        now_ms=now,
    )
    assert authority is not None
    host_authority = _host_authority(db_path, scope="runtime-guardian", now=now)
    operation = _operation("operation-guardian", CatalogMode.AUDIT)
    store.register_operation(operation, authority, now_ms=now + 1)
    _append_starting(store, operation.operation_id, now=now + 2)
    terminal = store.append_receipt(
        operation.operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload=_terminal_payload(operation.operation_id, CatalogMode.AUDIT, host_authority),
        now_ms=now + 4,
    )

    class Guardian:
        def __init__(self) -> None:
            self.observation: dict[str, object] | None = None

        def observe_dependency(self, **kwargs: object) -> dict[str, object]:
            self.observation = dict(kwargs)
            return self.observation

    guardian = Guardian()
    observer = ServiceCatalogRolloutGuardianObserver(store=store, guardian=guardian)
    observer.observe_latest(runtime_scope_id="runtime-guardian", candidate_owner_id="host.main")
    assert guardian.observation is not None
    assert guardian.observation["status"] == "UP"
    assert terminal["receipt_digest"] in guardian.observation["source_ref"]
