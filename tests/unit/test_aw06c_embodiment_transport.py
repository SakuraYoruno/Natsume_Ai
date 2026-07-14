from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from edge_client.gateway.embodiment_runtime import (
    DurableEmbodimentRuntime,
    EdgeEmbodimentTransportError,
    SQLiteEdgeEmbodimentStore,
)
from edge_client.gateway.migration_runner import apply_edge_gateway_migrations
from edge_client.app.fastapi_app import (
    _handle_embodiment_command,
    _handle_embodiment_session,
)
from edge_client.app.schemas import EdgeRpcEnvelope
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_CONFLICT,
    EMBODIMENT_COMMAND_EXPIRED,
    EMBODIMENT_COMMAND_IN_DOUBT,
    EMBODIMENT_FENCE_STALE,
    build_embodiment_command,
)
from natsume_contracts.edge.transport_v1 import (
    EDGE_SESSION_BUSY,
    EDGE_SESSION_RECONNECT_INVALID,
    bind_command_to_lease,
    parse_edge_session_lease,
)


class _Clock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _store(tmp_path, clock: _Clock) -> SQLiteEdgeEmbodimentStore:
    return _migrated_store(tmp_path / "edge-transport.sqlite3", clock=clock)


def _migrated_store(db_path, *, clock: _Clock) -> SQLiteEdgeEmbodimentStore:
    apply_edge_gateway_migrations(db_path)
    return SQLiteEdgeEmbodimentStore(db_path, clock=clock)


def _command(lease, *, key: str = "idem-1", deadline_ms: int = 50_000, payload=None):
    raw = build_embodiment_command(
        kind="mateengine",
        action="command",
        payload=payload or {"command": {"type": "reset"}},
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/1",
        deadline_ms=deadline_ms,
        idempotency_key=key,
        request_id=f"request-{key}",
        issued_at_ms=900,
    )
    return bind_command_to_lease(raw, lease)


def test_session_reconnect_requires_exact_lease_and_rotates_fence_after_expiry(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    first = store.open_session(session_id="host-session", owner_id="host", ttl_ms=2_000)

    reconnected = store.open_session(
        session_id="host-session",
        owner_id="host",
        ttl_ms=3_000,
        reconnect_lease_id=first.lease_id,
        reconnect_fencing_token=first.fencing_token,
    )
    assert reconnected.lease_id == first.lease_id
    assert reconnected.fencing_token == first.fencing_token == 1

    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_BUSY):
        store.open_session(session_id="host-session", owner_id="other", ttl_ms=2_000)

    stale_command = _command(first)
    clock.value = reconnected.expires_at_ms + 1
    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_RECONNECT_INVALID):
        store.open_session(
            session_id="host-session",
            owner_id="host",
            ttl_ms=2_000,
            reconnect_lease_id=first.lease_id,
            reconnect_fencing_token=first.fencing_token,
        )

    second = store.open_session(session_id="host-session", owner_id="host", ttl_ms=2_000)
    assert second.fencing_token == 2
    assert second.lease_id != first.lease_id
    with pytest.raises(EdgeEmbodimentTransportError, match=EMBODIMENT_FENCE_STALE):
        store.claim_command(stale_command)


def test_receipt_survives_process_reopen_and_replay_never_reexecutes(tmp_path) -> None:
    clock = _Clock()
    db_path = tmp_path / "edge-transport.sqlite3"
    store = _migrated_store(db_path, clock=clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _command(lease)
    calls: list[str] = []

    first = DurableEmbodimentRuntime(store).execute(
        command,
        lambda item: calls.append(item.command_id) or {
            "success": True,
            "status": "COMPLETED",
            "detail": "applied",
        },
    )
    reopened = DurableEmbodimentRuntime(_migrated_store(db_path, clock=clock))
    duplicate = reopened.execute(
        command,
        lambda item: calls.append(f"duplicate:{item.command_id}") or {"success": True},
    )

    assert calls == [command.command_id]
    assert duplicate.as_dict() == first.as_dict()
    assert first.receipt_ref and first.receipt_ref == duplicate.receipt_ref


def test_same_idempotency_key_with_different_payload_fails_closed(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    runtime = DurableEmbodimentRuntime(store)
    runtime.execute(_command(lease), lambda _: {"success": True, "status": "COMPLETED"})

    conflicting = _command(
        lease,
        payload={"command": {"type": "shutdown"}},
    )
    with pytest.raises(EdgeEmbodimentTransportError, match=EMBODIMENT_COMMAND_CONFLICT):
        runtime.execute(conflicting, lambda _: {"success": True})


def test_dispatching_command_is_in_doubt_after_reconnect_and_never_replayed(tmp_path) -> None:
    clock = _Clock()
    db_path = tmp_path / "edge-transport.sqlite3"
    store = _migrated_store(db_path, clock=clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _command(lease)
    assert store.claim_command(command).execute is True

    calls: list[str] = []
    first = DurableEmbodimentRuntime(_migrated_store(db_path, clock=clock)).execute(
        command, lambda item: calls.append(item.command_id) or {"success": True}
    )
    clock.value += 50
    second = DurableEmbodimentRuntime(_migrated_store(db_path, clock=clock)).execute(
        command, lambda item: calls.append(item.command_id) or {"success": True}
    )

    assert calls == []
    assert first.status == second.status == "IN_DOUBT"
    assert first.reason_code == second.reason_code == EMBODIMENT_COMMAND_IN_DOUBT
    assert first.receipt_ref == second.receipt_ref
    assert first.completed_at_ms == second.completed_at_ms


def test_executor_error_is_durable_in_doubt_and_not_retried(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _command(lease)
    calls = 0

    def uncertain(_):
        nonlocal calls
        calls += 1
        raise OSError("device disconnected after write")

    runtime = DurableEmbodimentRuntime(store)
    first = runtime.execute(command, uncertain)
    second = runtime.execute(command, uncertain)
    assert calls == 1
    assert first.reason_code == second.reason_code == EMBODIMENT_COMMAND_IN_DOUBT
    assert first.as_dict() == second.as_dict()


def test_expired_command_is_durably_rejected_before_device_effect(tmp_path) -> None:
    clock = _Clock(5_000)
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _command(lease, deadline_ms=4_999)
    calls: list[str] = []
    runtime = DurableEmbodimentRuntime(store)

    first = runtime.execute(command, lambda item: calls.append(item.command_id))
    second = runtime.execute(command, lambda item: calls.append(item.command_id))
    assert calls == []
    assert first.reason_code == second.reason_code == EMBODIMENT_COMMAND_EXPIRED
    assert first.as_dict() == second.as_dict()


def test_concurrent_duplicate_cannot_run_effect_twice(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _command(lease)
    runtime = DurableEmbodimentRuntime(store)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow(_):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        return {"success": True, "status": "COMPLETED"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(runtime.execute, command, slow)
        assert entered.wait(2)
        duplicate = pool.submit(runtime.execute, command, slow).result(timeout=2)
        release.set()
        first = first_future.result(timeout=2)

    assert calls == 1
    assert duplicate.status == "IN_DOUBT"
    assert first.status == "COMPLETED"
    assert runtime.execute(command, slow).as_dict() == first.as_dict()


def test_session_endpoint_and_command_handler_share_durable_authority(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    session_envelope = EdgeRpcEnvelope(
        request_id="session-request",
        ts_ms=clock.value,
        nonce="nonce-session",
        signature="signature",
        payload={"session_id": "host-session", "owner_id": "host", "ttl_ms": 10_000},
    )
    session_result = _handle_embodiment_session(session_envelope, store=store)
    assert session_result.success is True
    lease = parse_edge_session_lease(session_result.event["lease"])

    command = _command(lease)
    calls: list[str] = []
    monkeypatch.setattr(
        "edge_client.actuators.mateengine_driver.execute_mateengine_effect",
        lambda **kwargs: calls.append(str(kwargs["action"])) or {
            "success": True,
            "status": "COMPLETED",
            "detail": "done",
        },
    )
    envelope = EdgeRpcEnvelope(
        request_id=command.request_id,
        ts_ms=command.issued_at_ms,
        nonce="nonce-command",
        signature="signature",
        payload=command.as_dict(),
    )
    runtime = DurableEmbodimentRuntime(store)
    first = _handle_embodiment_command(
        object(), envelope, transport_runtime=runtime
    )
    second = _handle_embodiment_command(
        object(), envelope, transport_runtime=runtime
    )
    assert calls == ["command"]
    assert first.event["receipt"] == second.event["receipt"]


def test_command_handler_rejects_unleased_wire_command_before_effect(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    unleased = build_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "reset"}},
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/1",
        deadline_ms=20_000,
        idempotency_key="unleased",
        request_id="unleased-request",
        issued_at_ms=900,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "edge_client.actuators.mateengine_driver.execute_mateengine_effect",
        lambda **kwargs: calls.append("effect") or {"success": True},
    )
    result = _handle_embodiment_command(
        object(),
        EdgeRpcEnvelope(
            request_id=unleased.request_id,
            ts_ms=unleased.issued_at_ms,
            nonce="nonce",
            signature="signature",
            payload=unleased.as_dict(),
        ),
        transport_runtime=DurableEmbodimentRuntime(store),
    )
    assert result.success is False
    assert result.reason_code == "EMBODIMENT_LEASE_REQUIRED"
    assert calls == []


def test_command_handler_dispatches_clipboard_only_after_durable_claim(tmp_path, monkeypatch) -> None:
    from edge_client.actuators.desktop_clipboard import DesktopClipboardEffect

    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = bind_command_to_lease(
        build_embodiment_command(
            kind="desktop",
            action="clipboard_write",
            payload={"text": "edge-only"},
            authority_ref="authority://host/device",
            grant_ref="grant://host/device/clipboard",
            deadline_ms=20_000,
            idempotency_key="clipboard-write",
            request_id="clipboard-request",
            issued_at_ms=900,
        ),
        lease,
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        "edge_client.actuators.desktop_clipboard.execute_desktop_clipboard_effect",
        lambda **kwargs: calls.append(dict(kwargs)) or DesktopClipboardEffect(
            success=True,
            status="COMPLETED",
            reason_code=None,
            detail="done",
            event={"text_length": 9},
        ),
    )
    envelope = EdgeRpcEnvelope(
        request_id=command.request_id,
        ts_ms=command.issued_at_ms,
        nonce="nonce",
        signature="signature",
        payload=command.as_dict(),
    )
    runtime = DurableEmbodimentRuntime(store)
    first = _handle_embodiment_command(
        object(), envelope, transport_runtime=runtime
    )
    second = _handle_embodiment_command(
        object(), envelope, transport_runtime=runtime
    )
    assert first.success and second.success
    assert calls == [{"action": "clipboard_write", "payload": {"text": "edge-only"}}]
