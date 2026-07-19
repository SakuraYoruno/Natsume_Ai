from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.service_catalog_startup import (
    ServiceCatalogStartupCoordinator,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.service_catalog_rollout_repository import (
    SQLiteServiceCatalogRolloutRepository as ServiceCatalogRolloutStore,
)
from host_brain.service_catalog.host_startup import (
    HostCatalogRuntime,
    HostHttpHealthProbe,
    HostUvicornCandidate,
    host_main_health_identity,
    start_host_through_service_catalog,
)
from host_brain.service_catalog.models import (
    AuthKind,
    CatalogMode,
    EndpointKind,
    EndpointSpec,
    Exposure,
    HealthSpec,
    ProtocolSpec,
    Transport,
    build_catalog_snapshot,
)
from host_brain.service_catalog.rollout_authority import (
    CatalogRolloutOperationV1,
    ServiceCatalogRolloutError,
    catalog_operation_identity_digest,
)
from host_brain.service_catalog.rollout_runtime import (
    DurableServiceCatalogActivationRunner,
    IdentityCapturingHealthProbe,
)
from host_brain.service_catalog.socket_reservation import OwnedSocketReservationProbe


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _endpoint(port: int) -> EndpointSpec:
    return EndpointSpec(
        endpoint_id="host.main.http",
        listener_id="host.main.http",
        owner_id="host.main",
        kind=EndpointKind.LISTENER,
        transport=Transport.TCP,
        bind_host="127.0.0.1",
        port=port,
        protocols=(ProtocolSpec("http", "0.1"),),
        health=HealthSpec(
            kind="http",
            path="/health",
            expected_owner_id="host.main",
            expected_protocol="http",
            expected_protocol_version="0.1",
        ),
        exposure=Exposure.LOOPBACK,
        auth_kind=AuthKind.ROUTE_POLICY,
        auth_policy_ref="host.route-policy.v1",
    )


def _runtime_authority(
    db_path: Path,
    *,
    runtime_scope: str,
    now: int,
) -> dict[str, object]:
    runtime_authority = {
        "runtime_scope_id": runtime_scope,
        "runtime_owner_id": "test-host-owner",
        "runtime_lease_id": "test-host-lease",
        "fencing_token": 1,
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO host_embodiment_runtime_lease "
            "(runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, "
            "expires_at_ms, released_at_ms) VALUES (?, ?, ?, 1, ?, ?, NULL)",
            (
                runtime_scope,
                runtime_authority["runtime_owner_id"],
                runtime_authority["runtime_lease_id"],
                now - 1_000,
                now + 600_000,
            ),
        )
    return runtime_authority


def _real_health_app(runtime_authority: dict[str, object]) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "OK",
            "version": "0.1.0",
            "timestamp": int(time.time() * 1000),
            "details": {
                "service_identity": host_main_health_identity(),
                "degradation_state": {
                    "guardian_authority": runtime_authority,
                },
            },
        }

    return app


def _seed_terminal(
    store: ServiceCatalogRolloutStore,
    authority: object,
    *,
    snapshot_digest: str,
    stage: CatalogMode,
    operation_id: str,
    runtime_authority: dict[str, object],
    now: int,
) -> None:
    operation = CatalogRolloutOperationV1(
        operation_id=operation_id,
        catalog_snapshot_digest=snapshot_digest,
        candidate_owner_id="host.main",
        stage=stage,
        endpoint_ids=("host.main.http",),
        rollback_ref="service-catalog://host.main/previous-stage",
    )
    store.register_operation(operation, authority, now_ms=now)
    store.append_receipt(
        operation_id,
        event_type="ROLLOUT_PREPARED",
        status="PREPARED",
        payload={"prepared": True},
        now_ms=now + 1,
    )
    store.append_receipt(
        operation_id,
        event_type="ACTIVATION_STARTING",
        status="STARTING",
        payload={"attempt": 1},
        now_ms=now + 2,
    )
    store.append_receipt(
        operation_id,
        event_type="ACTIVATION_STARTED",
        status="STARTED",
        payload={
            "catalog_snapshot_digest": snapshot_digest,
            "candidate_owner_id": "host.main",
            "stage": stage.value,
            "health_identity": host_main_health_identity(),
            "runtime_authority": runtime_authority,
            "activation_identity": {
                "operation_id": operation_id,
                "operation_id_digest": catalog_operation_identity_digest(
                    operation_id
                ),
                "catalog_snapshot_digest": snapshot_digest,
                "candidate_owner_id": "host.main",
                "stage": stage.value,
            },
            "rollback_ref": "service-catalog://host.main/previous-stage",
        },
        now_ms=now + 3,
    )


def test_canary_transfers_exact_socket_and_restart_reconciles_without_duplicate_start(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog-host-socket.sqlite3"
    migration = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-catalog-host-socket"
    )
    assert migration.success, migration.detail
    now = int(time.time() * 1000)
    runtime_scope = "test-host-runtime"
    runtime_authority = {
        "runtime_scope_id": runtime_scope,
        "runtime_owner_id": "test-host-owner",
        "runtime_lease_id": "test-host-lease",
        "fencing_token": 1,
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO host_embodiment_runtime_lease "
            "(runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, "
            "expires_at_ms, released_at_ms) VALUES (?, ?, ?, 1, ?, ?, NULL)",
            (
                runtime_scope,
                runtime_authority["runtime_owner_id"],
                runtime_authority["runtime_lease_id"],
                now - 1_000,
                now + 120_000,
            ),
        )

    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id=runtime_scope,
        owner_id="test-catalog-owner",
        lease_ms=120_000,
        now_ms=now,
    )
    assert authority is not None
    endpoint = _endpoint(_free_port())
    snapshot = build_catalog_snapshot((endpoint,))
    for index, stage in enumerate(
        (CatalogMode.AUDIT, CatalogMode.SHADOW, CatalogMode.PREFLIGHT)
    ):
        _seed_terminal(
            store,
            authority,
            snapshot_digest=snapshot.digest,
            stage=stage,
            operation_id=f"seed-{stage.value.lower()}",
            runtime_authority=runtime_authority,
            now=now + 10 + index * 2,
        )

    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "OK",
            "version": "0.1.0",
            "timestamp": int(time.time() * 1000),
            "details": {
                "service_identity": host_main_health_identity(),
                "degradation_state": {
                    "guardian_authority": runtime_authority,
                },
            },
        }

    env = {
        "HOST_SERVICE_CATALOG_MODE": "CANARY",
        "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000",
    }
    reservation = OwnedSocketReservationProbe()
    raw_health = HostHttpHealthProbe(timeout_s=1.0)
    health_probe = IdentityCapturingHealthProbe(raw_health)
    coordinator = ServiceCatalogStartupCoordinator(
        snapshot=snapshot,
        env=env,
        bind_probe=reservation,
        health_probe=health_probe,
    )
    candidate = HostUvicornCandidate(
        endpoint=endpoint,
        reservation_probe=reservation,
        snapshot_digest=snapshot.digest,
        operation_id="canary-real-socket",
        stage=CatalogMode.CANARY,
        use_reserved_sockets=True,
        log_level="ERROR",
        access_log=False,
        app=app,
        startup_timeout_s=10.0,
    )
    runner = DurableServiceCatalogActivationRunner(
        snapshot=snapshot,
        coordinator=coordinator,
        store=store,
        authority=authority,
        health_probe=health_probe,
        candidate_owner_id="host.main",
        start_candidate=candidate,
        stop_candidate=lambda handle: handle.stop(),
        bind_probe=reservation,
        rollback_ref="service-catalog://host.main/previous-stage",
    )
    try:
        # Simulate response loss/crash after the immutable operation insert but
        # before the first receipt. Reusing the same operation id must continue
        # that operation without creating a second listener effect.
        registered_only = CatalogRolloutOperationV1(
            operation_id="canary-real-socket",
            catalog_snapshot_digest=snapshot.digest,
            candidate_owner_id="host.main",
            stage=CatalogMode.CANARY,
            endpoint_ids=(endpoint.endpoint_id,),
            rollback_ref="service-catalog://host.main/previous-stage",
        )
        store.register_operation(
            registered_only,
            authority,
            now_ms=int(time.time() * 1000),
        )
        assert store.operation_state(registered_only.operation_id) == "REGISTERED"
        outcome = runner.activate(operation_id="canary-real-socket")
        assert outcome.status == "STARTED"
        assert outcome.exact_replay is True
        assert outcome.recovered is True
        assert outcome.candidate_handle is candidate
        assert raw_health.inspect(endpoint).healthy is True

        replay = runner.activate(operation_id="canary-real-socket")
        assert replay.exact_replay is True
        assert replay.status == "STARTED"
        rollback_drift_runner = DurableServiceCatalogActivationRunner(
            snapshot=snapshot,
            coordinator=coordinator,
            store=ServiceCatalogRolloutStore(db_path),
            authority=authority,
            health_probe=health_probe,
            candidate_owner_id="host.main",
            start_candidate=candidate,
            stop_candidate=lambda handle: handle.stop(),
            bind_probe=reservation,
            rollback_ref="service-catalog://host.main/different-rollback",
        )
        with pytest.raises(ServiceCatalogRolloutError, match="identity drift"):
            rollback_drift_runner.activate(operation_id="canary-real-socket")

        pending = CatalogRolloutOperationV1(
            operation_id="canary-pending-restart",
            catalog_snapshot_digest=snapshot.digest,
            candidate_owner_id="host.main",
            stage=CatalogMode.CANARY,
            endpoint_ids=(endpoint.endpoint_id,),
            rollback_ref="service-catalog://host.main/previous-stage",
        )
        store.register_operation(pending, authority, now_ms=int(time.time() * 1000))
        store.append_receipt(
            pending.operation_id,
            event_type="ROLLOUT_PREPARED",
            status="PREPARED",
            payload={"prepared": True},
            now_ms=int(time.time() * 1000),
        )
        store.append_receipt(
            pending.operation_id,
            event_type="ACTIVATION_STARTING",
            status="STARTING",
            payload={"crash_boundary": "after-intent-before-terminal"},
            now_ms=int(time.time() * 1000),
        )

        class MustNotStart:
            candidate_handle = None

            def __call__(self) -> object:
                raise AssertionError("pending reconciliation duplicated candidate start")

        restart_runner = DurableServiceCatalogActivationRunner(
            snapshot=snapshot,
            coordinator=ServiceCatalogStartupCoordinator(
                snapshot=snapshot,
                env=env,
                bind_probe=reservation,
                health_probe=health_probe,
            ),
            store=ServiceCatalogRolloutStore(db_path),
            authority=authority,
            health_probe=health_probe,
            candidate_owner_id="host.main",
            start_candidate=MustNotStart(),
            stop_candidate=lambda _handle: None,
            bind_probe=reservation,
            rollback_ref="service-catalog://host.main/previous-stage",
        )
        with pytest.raises(
            ServiceCatalogRolloutError,
            match="listener identity is unknown",
        ):
            restart_runner.activate(operation_id=pending.operation_id)
        assert (
            ServiceCatalogRolloutStore(db_path).operation_state(pending.operation_id)
            == "STARTING"
        )
        assert (
            ServiceCatalogRolloutStore(db_path).rollout_head(
                runtime_scope_id=runtime_scope,
                candidate_owner_id="host.main",
            )["activation_operation_id"]
            == "canary-real-socket"
        )

        # Only a listener carrying the exact pending operation identity may
        # close the recovery boundary and advance the durable head.
        candidate.stop()
        recovery_reservation = OwnedSocketReservationProbe()
        recovery_health = IdentityCapturingHealthProbe(
            HostHttpHealthProbe(timeout_s=1.0)
        )
        exact_pending_candidate = HostUvicornCandidate(
            endpoint=endpoint,
            reservation_probe=recovery_reservation,
            snapshot_digest=snapshot.digest,
            operation_id=pending.operation_id,
            stage=CatalogMode.CANARY,
            use_reserved_sockets=False,
            log_level="ERROR",
            access_log=False,
            app=app,
            startup_timeout_s=10.0,
        )
        exact_pending_candidate()
        try:
            exact_restart_runner = DurableServiceCatalogActivationRunner(
                snapshot=snapshot,
                coordinator=ServiceCatalogStartupCoordinator(
                    snapshot=snapshot,
                    env=env,
                    bind_probe=recovery_reservation,
                    health_probe=recovery_health,
                ),
                store=ServiceCatalogRolloutStore(db_path),
                authority=authority,
                health_probe=recovery_health,
                candidate_owner_id="host.main",
                start_candidate=MustNotStart(),
                stop_candidate=lambda _handle: None,
                bind_probe=recovery_reservation,
                rollback_ref="service-catalog://host.main/previous-stage",
            )
            recovered = exact_restart_runner.activate(
                operation_id=pending.operation_id
            )
            assert recovered.recovered is True
            assert recovered.status == "STARTED"
            assert (
                ServiceCatalogRolloutStore(db_path).operation_state(
                    pending.operation_id
                )
                == "STARTED"
            )
        finally:
            exact_pending_candidate.stop()
            recovery_reservation.close()
        with pytest.raises(
            ServiceCatalogRolloutError,
            match="no live matching candidate",
        ):
            runner.activate(operation_id="canary-real-socket")
    finally:
        candidate.stop()
        reservation.close()
        store.release_authority(authority)


def test_fresh_host_rollout_reaches_default_enforce_and_restart_reactivates_head(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog-host-full-rollout.sqlite3"
    migration = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-catalog-host-full-rollout"
    )
    assert migration.success, migration.detail
    runtime_scope = EmbodimentOutboxRepository(str(db_path)).runtime_scope_id
    runtime_authority = _runtime_authority(
        db_path,
        runtime_scope=runtime_scope,
        now=int(time.time() * 1000),
    )
    app = _real_health_app(runtime_authority)
    port = _free_port()
    env = {
        # Legacy callers may still supply a low-level stage input.  The Host
        # target defaults to ENFORCE but must nevertheless begin at AUDIT.
        "HOST_SERVICE_CATALOG_MODE": "ENFORCE",
        "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000",
    }

    first = start_host_through_service_catalog(
        db_path=str(db_path),
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        access_log=False,
        env=env,
        operation_id="host-full-rollout-first",
        app=app,
    )
    try:
        assert first.target_stage is CatalogMode.ENFORCE
        assert [item.stage for item in first.stage_outcomes] == list(CatalogMode)
        assert HostHttpHealthProbe(timeout_s=1.0).inspect(_endpoint(port)).healthy is True
        head = first.store.rollout_head(
            runtime_scope_id=runtime_scope,
            candidate_owner_id="host.main",
        )
        assert head is not None
        assert head["stage"] == "ENFORCE"
        with sqlite3.connect(db_path) as connection:
            stage_rows = connection.execute(
                "SELECT stage, activation_operation_id FROM service_catalog_rollout_head "
                "WHERE runtime_scope_id = ? AND candidate_owner_id = 'host.main'",
                (runtime_scope,),
            ).fetchall()
            terminal_stages = connection.execute(
                "SELECT operation.stage FROM service_catalog_rollout_operation AS operation "
                "JOIN service_catalog_rollout_receipt AS receipt "
                "ON receipt.operation_id = operation.operation_id "
                "WHERE operation.runtime_scope_id = ? "
                "AND operation.candidate_owner_id = 'host.main' "
                "AND receipt.event_type = 'ACTIVATION_STARTED' ORDER BY operation.stage_rank",
                (runtime_scope,),
            ).fetchall()
        assert stage_rows == [("ENFORCE", first.outcome.operation_id)]
        assert [row[0] for row in terminal_stages] == [stage.value for stage in CatalogMode]
    finally:
        first.close()

    free_probe = OwnedSocketReservationProbe()
    try:
        assert free_probe.inspect(_endpoint(port)).available is True
    finally:
        free_probe.close()

    restarted = start_host_through_service_catalog(
        db_path=str(db_path),
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        access_log=False,
        env=env,
        operation_id="host-full-rollout-restart",
        app=app,
    )
    try:
        assert [item.stage for item in restarted.stage_outcomes] == [CatalogMode.ENFORCE]
        assert restarted.store.rollout_head(
            runtime_scope_id=runtime_scope,
            candidate_owner_id="host.main",
        )["activation_operation_id"] == restarted.outcome.operation_id
        assert HostHttpHealthProbe(timeout_s=1.0).inspect(_endpoint(port)).healthy is True
    finally:
        restarted.close()


def test_target_boundary_stops_at_preflight_then_resumes_without_stage_skip(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog-host-target-boundary.sqlite3"
    migration = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-catalog-host-target-boundary"
    )
    assert migration.success, migration.detail
    runtime_scope = EmbodimentOutboxRepository(str(db_path)).runtime_scope_id
    runtime_authority = _runtime_authority(
        db_path,
        runtime_scope=runtime_scope,
        now=int(time.time() * 1000),
    )
    app = _real_health_app(runtime_authority)
    port = _free_port()
    preflight_env = {
        "HOST_SERVICE_CATALOG_TARGET_MODE": "PREFLIGHT",
        "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000",
        "HOST_SERVICE_CATALOG_ROLLBACK_REF": "release://known-good-host",
    }
    preflight = start_host_through_service_catalog(
        db_path=str(db_path),
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        access_log=False,
        env=preflight_env,
        operation_id="host-preflight-boundary",
        app=app,
    )
    try:
        assert [item.stage for item in preflight.stage_outcomes] == [
            CatalogMode.AUDIT,
            CatalogMode.SHADOW,
            CatalogMode.PREFLIGHT,
        ]
        operation = preflight.store.get_operation(preflight.outcome.operation_id)
        assert operation is not None
        assert operation["rollback_ref"] == "release://known-good-host"
    finally:
        preflight.close()

    with pytest.raises(
        ServiceCatalogRolloutError,
        match="target regresses the durable head",
    ):
        start_host_through_service_catalog(
            db_path=str(db_path),
            host="127.0.0.1",
            port=port,
            log_level="ERROR",
            access_log=False,
            env={"HOST_SERVICE_CATALOG_TARGET_MODE": "AUDIT"},
            operation_id="host-illegal-regression",
            app=app,
        )

    enforce = start_host_through_service_catalog(
        db_path=str(db_path),
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        access_log=False,
        env={
            "HOST_SERVICE_CATALOG_TARGET_MODE": "ENFORCE",
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000",
        },
        operation_id="host-resume-enforce",
        app=app,
    )
    try:
        assert [item.stage for item in enforce.stage_outcomes] == [
            CatalogMode.CANARY,
            CatalogMode.ENFORCE,
        ]
    finally:
        enforce.close()


def test_terminal_append_failure_stops_real_listener_and_restart_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "catalog-host-append-failure.sqlite3"
    migration = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-catalog-host-append-failure"
    )
    assert migration.success, migration.detail
    runtime_scope = EmbodimentOutboxRepository(str(db_path)).runtime_scope_id
    runtime_authority = _runtime_authority(
        db_path,
        runtime_scope=runtime_scope,
        now=int(time.time() * 1000),
    )
    app = _real_health_app(runtime_authority)
    port = _free_port()
    original_append = ServiceCatalogRolloutStore.append_receipt
    injected = {"raised": False}

    def fail_shadow_terminal(
        store: ServiceCatalogRolloutStore,
        operation_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        operation = store.get_operation(operation_id)
        if (
            operation is not None
            and operation["stage"] == "SHADOW"
            and kwargs.get("event_type") == "ACTIVATION_STARTED"
            and not injected["raised"]
        ):
            injected["raised"] = True
            raise sqlite3.OperationalError("injected terminal append failure")
        return original_append(store, operation_id, **kwargs)

    monkeypatch.setattr(ServiceCatalogRolloutStore, "append_receipt", fail_shadow_terminal)
    with pytest.raises(sqlite3.OperationalError, match="injected terminal append failure"):
        start_host_through_service_catalog(
            db_path=str(db_path),
            host="127.0.0.1",
            port=port,
            log_level="ERROR",
            access_log=False,
            env={"HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000"},
            operation_id="host-append-failure",
            app=app,
        )
    probe = OwnedSocketReservationProbe()
    try:
        assert probe.inspect(_endpoint(port)).available is True
    finally:
        probe.close()
    store = ServiceCatalogRolloutStore(db_path)
    assert store.rollout_head(
        runtime_scope_id=runtime_scope,
        candidate_owner_id="host.main",
    )["stage"] == "AUDIT"

    monkeypatch.setattr(ServiceCatalogRolloutStore, "append_receipt", original_append)
    recovered = start_host_through_service_catalog(
        db_path=str(db_path),
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        access_log=False,
        env={"HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000"},
        operation_id="host-append-recovery",
        app=app,
    )
    try:
        assert [item.stage for item in recovered.stage_outcomes] == [
            CatalogMode.SHADOW,
            CatalogMode.PREFLIGHT,
            CatalogMode.CANARY,
            CatalogMode.ENFORCE,
        ]
        pending_rows = [
            row
            for row in store.list_receipts("host-append-failure:shadow")
            if row["event_type"] == "ACTIVATION_ROLLED_BACK"
        ]
        assert len(pending_rows) == 1
    finally:
        recovered.close()


def test_stop_failure_keeps_starting_state_until_listener_reconciliation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog-host-stop-failure.sqlite3"
    migration = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-catalog-host-stop-failure"
    )
    assert migration.success, migration.detail
    now = int(time.time() * 1000)
    runtime_scope = "test-host-stop-failure"
    runtime_authority = _runtime_authority(
        db_path,
        runtime_scope=runtime_scope,
        now=now,
    )
    store = ServiceCatalogRolloutStore(db_path)
    authority = store.acquire_authority(
        runtime_scope_id=runtime_scope,
        owner_id="test-catalog-owner",
        lease_ms=120_000,
        now_ms=now,
    )
    assert authority is not None
    endpoint = _endpoint(_free_port())
    snapshot = build_catalog_snapshot((endpoint,))
    _seed_terminal(
        store,
        authority,
        snapshot_digest=snapshot.digest,
        stage=CatalogMode.AUDIT,
        operation_id="seed-audit-stop-failure",
        runtime_authority=runtime_authority,
        now=now + 10,
    )

    app = FastAPI()

    @app.get("/health")
    async def unhealthy_identity() -> dict[str, object]:
        identity = host_main_health_identity()
        identity["owner_id"] = "wrong-owner"
        return {
            "status": "OK",
            "details": {
                "service_identity": identity,
                "degradation_state": {
                    "guardian_authority": runtime_authority,
                },
            },
        }

    operation_id = "shadow-stop-failure"
    reservation = OwnedSocketReservationProbe()
    health_probe = IdentityCapturingHealthProbe(
        HostHttpHealthProbe(timeout_s=1.0)
    )
    coordinator = ServiceCatalogStartupCoordinator(
        snapshot=snapshot,
        env={
            "HOST_SERVICE_CATALOG_MODE": "SHADOW",
            "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS": "10000",
        },
        bind_probe=reservation,
        health_probe=health_probe,
    )
    candidate = HostUvicornCandidate(
        endpoint=endpoint,
        reservation_probe=reservation,
        snapshot_digest=snapshot.digest,
        operation_id=operation_id,
        stage=CatalogMode.SHADOW,
        use_reserved_sockets=False,
        log_level="ERROR",
        access_log=False,
        app=app,
        startup_timeout_s=10.0,
    )

    def stop_failure(_handle: object) -> None:
        raise RuntimeError("injected stop failure")

    runner = DurableServiceCatalogActivationRunner(
        snapshot=snapshot,
        coordinator=coordinator,
        store=store,
        authority=authority,
        health_probe=health_probe,
        candidate_owner_id="host.main",
        start_candidate=candidate,
        stop_candidate=stop_failure,
        bind_probe=reservation,
        rollback_ref="service-catalog://host.main/previous-stage",
    )
    try:
        with pytest.raises(
            ServiceCatalogRolloutError,
            match="requires reconciliation",
        ):
            runner.activate(operation_id=operation_id)
        assert store.operation_state(operation_id) == "STARTING"
        assert store.terminal_receipt(operation_id) is None
        assert store.rollout_head(
            runtime_scope_id=runtime_scope,
            candidate_owner_id="host.main",
        )["stage"] == "AUDIT"
        assert reservation.inspect(endpoint).available is False
    finally:
        candidate.stop()
        reservation.close()
        store.release_authority(authority)


def test_runtime_close_releases_authority_and_reservation_after_candidate_failure() -> None:
    events: list[str] = []
    authority = object()

    class FailingCandidate:
        def stop(self) -> None:
            events.append("candidate.stop")
            raise RuntimeError("injected candidate stop failure")

    class RecordingStore:
        def release_authority(self, received: object) -> bool:
            assert received is authority
            events.append("store.release_authority")
            return True

    class RecordingReservation:
        def close(self) -> None:
            events.append("reservation.close")

    runtime = HostCatalogRuntime(
        outcome=object(),  # type: ignore[arg-type]
        stage_outcomes=(),
        target_stage=CatalogMode.ENFORCE,
        candidate=FailingCandidate(),  # type: ignore[arg-type]
        store=RecordingStore(),  # type: ignore[arg-type]
        authority=authority,
        reservation_probe=RecordingReservation(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="candidate stop failure"):
        runtime.close()

    assert events == [
        "candidate.stop",
        "store.release_authority",
        "reservation.close",
    ]
