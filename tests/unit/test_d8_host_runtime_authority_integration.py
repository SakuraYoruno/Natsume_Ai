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
    build_host_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from natsume_contracts.edge.transport_v1 import EdgeSessionLease, stable_lease_id


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


def _repository(tmp_path: Path, name: str) -> tuple[EmbodimentOutboxRepository, Path]:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"d8-host-runtime-authority-{name}",
    )
    assert migrated.success, migrated.as_dict()
    return EmbodimentOutboxRepository(db_path), db_path


def _command(name: str):
    return build_host_embodiment_command(
        kind="voice",
        action="speak",
        payload={"text": name},
        request_id=f"req_d8_runtime_{name}",
        idempotency_key=f"d8-runtime-key-{name}",
    )


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
    assert asyncio.run(first.submit(_command("before-restart"))).success
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

    assert asyncio.run(restarted.submit(_command("after-restart"))).success
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
    assert asyncio.run(first.submit(_command("seed-renew"))).success
    first.close()

    clock["value"] = 27_000
    restarted = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="host-renewed-after-restart",
        post_to_edge=fake_post_to_edge,
        session_config=config,
        authority_lease_ms=30_000,
    )
    assert asyncio.run(restarted.submit(_command("renew-after-restart"))).success
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

    stale = HostEmbodimentRuntime(
        repository=repository,
        worker_id="stale-host",
        post_to_edge=fake_post_to_edge,
        session_config=build_durable_host_embodiment_session_config(db_path),
        authority_lease_ms=1_000,
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
        stale.enqueue(_command("stale-enqueue"))
    with pytest.raises(RuntimeError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        asyncio.run(stale.submit(_command("stale-submit")))
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
