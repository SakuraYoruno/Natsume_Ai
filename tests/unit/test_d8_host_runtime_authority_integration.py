from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    EdgeEmbodimentSessionConfig,
    HostEmbodimentRuntime,
    build_durable_host_embodiment_session_config,
    build_host_embodiment_runtime_with_takeover_wait,
    build_host_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from natsume_contracts.edge.transport_v1 import EdgeSessionLease, stable_lease_id
from tests.action_preview_test_support import (
    build_scheduler_action_preview,
    issue_embodiment_action_preview,
    terminal_preview_kwargs,
)


class _Result:
    def __init__(
        self,
        *,
        success: bool = True,
        reason_code: str | None = None,
        detail: str = "ok",
        event: dict | None = None,
    ) -> None:
        self.success = success
        self.status = "ACCEPTED" if success else "REJECTED"
        self.reason_code = reason_code
        self.detail = detail
        self.event = dict(event or {})


class _NoopCommandPort:
    async def submit(self, command):
        return EmbodimentReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="test",
            received_at_ms=command.issued_at_ms,
            completed_at_ms=command.issued_at_ms,
        )


class _DelayedAcquireRepository:
    def __init__(
        self,
        delegate: EmbodimentOutboxRepository,
        *,
        unavailable_attempts: int,
    ) -> None:
        self._delegate = delegate
        self._remaining = int(unavailable_attempts)
        self.acquire_calls = 0

    def acquire_runtime_lease(self, **kwargs):  # noqa: ANN003
        self.acquire_calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            return None
        return self._delegate.acquire_runtime_lease(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class _ClockedAcquireRepository:
    def __init__(
        self,
        delegate: EmbodimentOutboxRepository,
        *,
        acquire_times_ms: tuple[int, ...],
    ) -> None:
        self._delegate = delegate
        self._acquire_times = iter(acquire_times_ms)
        self.acquire_calls = 0

    def acquire_runtime_lease(self, **kwargs):  # noqa: ANN003
        self.acquire_calls += 1
        return self._delegate.acquire_runtime_lease(
            **kwargs,
            now_ms=next(self._acquire_times),
        )

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


def _repository(tmp_path: Path, name: str) -> tuple[EmbodimentOutboxRepository, Path]:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"d8-host-runtime-authority-{name}",
    )
    assert migrated.success, migrated.as_dict()
    return EmbodimentOutboxRepository(db_path), db_path


def _command(name: str, *, grant_ref: str):
    return build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "status", "request": name}},
        request_id=f"req_d8_runtime_{name}",
        authority_ref=f"authority://task-node/{name}",
        grant_ref=grant_ref,
        idempotency_key=f"d8-runtime-key-{name}",
    )


def _command_with_preview(
    *,
    service,
    scheduler_context: dict,
    name: str,
):
    command = _command(
        name,
        grant_ref=str(
            scheduler_context["origin_boundary"]["node_grant_ref"]
        ),
    )
    preview = issue_embodiment_action_preview(
        service=service,
        scheduler_context=scheduler_context,
        command=command,
    )
    return command, terminal_preview_kwargs(service, preview)


def _admit_and_dispatch(
    runtime: HostEmbodimentRuntime,
    *,
    service,
    scheduler_context: dict,
    name: str,
) -> None:
    command, proof = _command_with_preview(
        service=service,
        scheduler_context=scheduler_context,
        name=name,
    )
    ack = runtime.enqueue(command, **proof)
    assert ack.status == "PENDING"
    batch = asyncio.run(runtime.dispatch_pending_async(limit=1))
    assert batch.applied == 1


def _edge_lease(
    config: EdgeEmbodimentSessionConfig,
    *,
    issued_at_ms: int,
    expires_at_ms: int,
    fencing_token: int = 7,
) -> EdgeSessionLease:
    return EdgeSessionLease(
        session_id=config.session_id,
        lease_id=stable_lease_id(
            session_id=config.session_id,
            owner_id=config.owner_id,
            fencing_token=fencing_token,
        ),
        owner_id=config.owner_id,
        fencing_token=fencing_token,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
    )


def _runtime_lease_row(db_path: Path) -> sqlite3.Row:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM host_embodiment_runtime_lease",
        ).fetchone()
    assert row is not None
    return row


def _receipt_for(payload: dict, *, at_ms: int) -> EmbodimentReceipt:
    return EmbodimentReceipt(
        command_id=str(payload["command_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        accepted=True,
        status="COMPLETED",
        reason_code=None,
        detail="done",
        received_at_ms=at_ms,
        completed_at_ms=at_ms,
    )


def test_same_canonical_store_has_one_runtime_authority_and_close_releases_exact_fence(
    monkeypatch,
    tmp_path,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    first_repository, db_path = _repository(tmp_path, "exclusive")
    second_repository = EmbodimentOutboxRepository(db_path)

    first = HostEmbodimentRuntime(
        repository=first_repository,
        worker_id="host-app-a",
        command_port=_NoopCommandPort(),
        authority_lease_ms=10_000,
    )
    first_row = _runtime_lease_row(db_path)
    assert first_row["owner_id"] == "host-app-a"
    assert first_row["fencing_token"] == 1
    assert first_row["released_at_ms"] is None

    with pytest.raises(
        RuntimeError,
        match="EMBODIMENT_RUNTIME_AUTHORITY_UNAVAILABLE",
    ):
        HostEmbodimentRuntime(
            repository=second_repository,
            worker_id="host-app-b",
            command_port=_NoopCommandPort(),
            authority_lease_ms=10_000,
        )

    first.close()
    released = _runtime_lease_row(db_path)
    assert released["lease_id"] == first_row["lease_id"]
    assert released["fencing_token"] == first_row["fencing_token"]
    assert released["released_at_ms"] is not None
    assert released["released_at_ms"] >= released["issued_at_ms"]

    second = HostEmbodimentRuntime(
        repository=second_repository,
        worker_id="host-app-b",
        command_port=_NoopCommandPort(),
        authority_lease_ms=10_000,
    )
    replacement = _runtime_lease_row(db_path)
    assert replacement["owner_id"] == "host-app-b"
    assert replacement["lease_id"] != first_row["lease_id"]
    assert replacement["fencing_token"] == first_row["fencing_token"] + 1
    second.close()


def test_process_takeover_wait_retries_only_until_authority_can_be_fenced(
    tmp_path,
) -> None:  # noqa: ANN001
    repository, db_path = _repository(tmp_path, "bounded-takeover-wait")
    delayed = _DelayedAcquireRepository(repository, unavailable_attempts=2)

    runtime = asyncio.run(
        build_host_embodiment_runtime_with_takeover_wait(
            repository=delayed,  # type: ignore[arg-type]
            worker_id="host-after-crash",
            command_port=_NoopCommandPort(),
            takeover_wait_ms=100,
            retry_interval_ms=1,
        )
    )

    assert delayed.acquire_calls == 3
    assert _runtime_lease_row(db_path)["owner_id"] == "host-after-crash"
    runtime.close()


def test_process_takeover_wait_acquires_higher_fence_after_crashed_lease_expires(
    tmp_path,
) -> None:  # noqa: ANN001
    repository, db_path = _repository(tmp_path, "expired-crash-takeover")
    crashed = repository.acquire_runtime_lease(
        worker_id="host-crashed",
        lease_ms=1_000,
        now_ms=1_000,
    )
    assert crashed is not None
    clocked = _ClockedAcquireRepository(
        repository,
        acquire_times_ms=(1_999, 2_000),
    )

    runtime = asyncio.run(
        build_host_embodiment_runtime_with_takeover_wait(
            repository=clocked,  # type: ignore[arg-type]
            worker_id="host-replacement",
            command_port=_NoopCommandPort(),
            authority_lease_ms=1_000,
            takeover_wait_ms=100,
            retry_interval_ms=1,
        )
    )

    replacement = _runtime_lease_row(db_path)
    assert clocked.acquire_calls == 2
    assert replacement["owner_id"] == "host-replacement"
    assert replacement["lease_id"] != crashed["lease_id"]
    assert replacement["fencing_token"] == int(crashed["fencing_token"]) + 1
    runtime.close()


def test_process_takeover_wait_does_not_steal_a_live_authority(
    tmp_path,
) -> None:  # noqa: ANN001
    first_repository, db_path = _repository(tmp_path, "bounded-live-owner")
    first = HostEmbodimentRuntime(
        repository=first_repository,
        worker_id="host-live",
        command_port=_NoopCommandPort(),
        authority_lease_ms=30_000,
    )
    original = dict(_runtime_lease_row(db_path))

    with pytest.raises(
        RuntimeError,
        match="EMBODIMENT_RUNTIME_AUTHORITY_UNAVAILABLE",
    ):
        asyncio.run(
            build_host_embodiment_runtime_with_takeover_wait(
                repository=EmbodimentOutboxRepository(db_path),
                worker_id="host-must-not-steal",
                command_port=_NoopCommandPort(),
                takeover_wait_ms=5,
                retry_interval_ms=1,
            )
        )

    current = dict(_runtime_lease_row(db_path))
    assert current["owner_id"] == original["owner_id"] == "host-live"
    assert current["lease_id"] == original["lease_id"]
    assert current["fencing_token"] == original["fencing_token"]
    first.close()


def test_restart_loads_live_edge_lease_without_busy_and_rebinds_it_to_new_runtime_claim(
    monkeypatch,
    tmp_path,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    repository, db_path = _repository(tmp_path, "restart-live-edge-lease")
    config = build_durable_host_embodiment_session_config(db_path)
    edge_lease = _edge_lease(config, issued_at_ms=1_000, expires_at_ms=61_000)
    rotated_edge_lease = _edge_lease(
        config,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
        fencing_token=edge_lease.fencing_token + 1,
    )
    calls: list[tuple[str, dict]] = []
    session_leases = iter((edge_lease, rotated_edge_lease))

    async def fake_post_to_edge(*, path, payload, **_kwargs):
        calls.append((str(path), dict(payload)))
        if path.endswith("/session"):
            return _Result(event={"lease": next(session_leases).as_dict()})
        return _Result(
            event={"receipt": _receipt_for(dict(payload), at_ms=clock["value"]).as_dict()}
        )

    first = HostEmbodimentRuntime(
        repository=repository,
        worker_id="host-before-restart",
        post_to_edge=fake_post_to_edge,
        session_config=config,
        authority_lease_ms=20_000,
    )
    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="d8-runtime-restart",
    )
    _admit_and_dispatch(
        first,
        service=preview_service,
        scheduler_context=scheduler_context,
        name="before-restart",
    )
    assert [path for path, _payload in calls] == [
        "/v1/edge/embodiment/session",
        "/v1/edge/embodiment",
    ]
    persisted_before = repository.load_edge_session_lease(
        config.session_id,
        config.owner_id,
    )
    assert persisted_before is not None
    assert persisted_before["lease_id"] == edge_lease.lease_id
    assert persisted_before["runtime_fencing_token"] == 1
    first.close()

    calls.clear()
    restarted_repository = EmbodimentOutboxRepository(db_path)
    restarted = HostEmbodimentRuntime(
        repository=restarted_repository,
        worker_id="host-after-restart",
        post_to_edge=fake_post_to_edge,
        session_config=config,
        authority_lease_ms=20_000,
    )
    rebound = restarted_repository.load_edge_session_lease(
        config.session_id,
        config.owner_id,
    )
    current_runtime_claim = _runtime_lease_row(db_path)
    assert rebound is not None
    assert rebound["lease_id"] == edge_lease.lease_id
    assert rebound["fencing_token"] == edge_lease.fencing_token
    assert rebound["runtime_lease_id"] == current_runtime_claim["lease_id"]
    assert rebound["runtime_fencing_token"] == 2

    _admit_and_dispatch(
        restarted,
        service=preview_service,
        scheduler_context=scheduler_context,
        name="after-restart",
    )
    assert [path for path, _payload in calls] == [
        "/v1/edge/embodiment/session",
        "/v1/edge/embodiment",
    ]
    rotation_payload = calls[0][1]
    assert rotation_payload["reconnect_lease_id"] == edge_lease.lease_id
    assert rotation_payload["reconnect_fencing_token"] == edge_lease.fencing_token
    assert rotation_payload["rotate_fencing_token"] is True
    rotated = restarted_repository.load_edge_session_lease(
        config.session_id,
        config.owner_id,
    )
    assert rotated is not None
    assert rotated["lease_id"] == rotated_edge_lease.lease_id
    assert rotated["fencing_token"] == edge_lease.fencing_token + 1
    assert rotated["runtime_fencing_token"] == 2
    restarted.close()


def test_restart_near_edge_expiry_renews_with_exact_reconnect_credentials(
    monkeypatch,
    tmp_path,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    repository, db_path = _repository(tmp_path, "restart-renew-edge-lease")
    config = build_durable_host_embodiment_session_config(db_path)
    initial_lease = _edge_lease(config, issued_at_ms=1_000, expires_at_ms=31_000)
    renewed_lease = _edge_lease(
        config,
        issued_at_ms=27_000,
        expires_at_ms=57_000,
        fencing_token=initial_lease.fencing_token + 1,
    )
    session_payloads: list[dict] = []

    async def fake_post_to_edge(*, path, payload, **_kwargs):
        if path.endswith("/session"):
            session_payloads.append(dict(payload))
            lease = initial_lease if len(session_payloads) == 1 else renewed_lease
            return _Result(event={"lease": lease.as_dict()})
        return _Result(
            event={"receipt": _receipt_for(dict(payload), at_ms=clock["value"]).as_dict()}
        )

    first = HostEmbodimentRuntime(
        repository=repository,
        worker_id="host-before-renew-restart",
        post_to_edge=fake_post_to_edge,
        session_config=config,
        authority_lease_ms=30_000,
    )
    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="d8-runtime-renew",
    )
    _admit_and_dispatch(
        first,
        service=preview_service,
        scheduler_context=scheduler_context,
        name="seed-renew",
    )
    first.close()

    clock["value"] = 27_000
    restarted = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="host-renewed-after-restart",
        post_to_edge=fake_post_to_edge,
        session_config=config,
        authority_lease_ms=30_000,
    )
    _admit_and_dispatch(
        restarted,
        service=preview_service,
        scheduler_context=scheduler_context,
        name="renew-after-restart",
    )
    assert len(session_payloads) == 2
    assert session_payloads[1]["session_id"] == config.session_id
    assert session_payloads[1]["owner_id"] == config.owner_id
    assert session_payloads[1]["reconnect_lease_id"] == initial_lease.lease_id
    assert (
        session_payloads[1]["reconnect_fencing_token"]
        == initial_lease.fencing_token
    )
    assert session_payloads[1]["rotate_fencing_token"] is True
    restarted.close()


def test_expired_and_replaced_runtime_fence_blocks_old_enqueue_and_edge_submit(
    monkeypatch,
    tmp_path,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    repository, db_path = _repository(tmp_path, "authority-takeover")
    edge_calls: list[str] = []

    async def fake_post_to_edge(*, path, **_kwargs):
        edge_calls.append(str(path))
        raise AssertionError("失去 Host authority 后不得跨越 Edge 边界")

    preview_service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="d8-runtime-stale-fence",
    )
    stale = HostEmbodimentRuntime(
        repository=repository,
        worker_id="stale-host",
        post_to_edge=fake_post_to_edge,
        session_config=build_durable_host_embodiment_session_config(db_path),
        authority_lease_ms=1_000,
    )
    pending_command, pending_proof = _command_with_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        name="stale-dispatch",
    )
    assert stale.enqueue(pending_command, **pending_proof).status == "PENDING"
    rejected_command, rejected_proof = _command_with_preview(
        service=preview_service,
        scheduler_context=scheduler_context,
        name="stale-enqueue",
    )
    original = _runtime_lease_row(db_path)

    takeover_at_ms = int(original["expires_at_ms"]) + 1
    clock["value"] = takeover_at_ms
    replacement_repository = EmbodimentOutboxRepository(db_path)
    replacement = replacement_repository.acquire_runtime_lease(
        worker_id="replacement-host",
        lease_ms=10_000,
        now_ms=takeover_at_ms,
    )
    assert replacement is not None
    assert replacement["fencing_token"] == original["fencing_token"] + 1

    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        stale.enqueue(rejected_command, **rejected_proof)
    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        asyncio.run(stale.dispatch_pending_async(limit=1))
    assert edge_calls == []
    stale.close()


def test_lifespan_heartbeat_keeps_authority_live_when_scheduler_is_disabled(
    tmp_path,
) -> None:
    repository, db_path = _repository(tmp_path, "authority-heartbeat")
    runtime = HostEmbodimentRuntime(
        repository=repository,
        worker_id="heartbeat-host",
        command_port=_NoopCommandPort(),
        authority_lease_ms=1_000,
    )

    async def scenario() -> None:
        heartbeat = asyncio.create_task(runtime.run_authority_heartbeat())
        try:
            # 超过初始 lease TTL；heartbeat 应已至少续租三次，且不依赖
            # default-off governed Scheduler clock。
            await asyncio.sleep(1.35)
            competitor = EmbodimentOutboxRepository(db_path)
            assert competitor.acquire_runtime_lease(
                worker_id="competing-host",
                lease_ms=1_000,
            ) is None
        finally:
            heartbeat.cancel()
            with pytest.raises(asyncio.CancelledError):
                await heartbeat

    asyncio.run(scenario())
    runtime.close()
