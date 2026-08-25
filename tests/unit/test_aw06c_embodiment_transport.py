from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from edge_client.gateway.embodiment_runtime import (
    DurableEmbodimentRuntime,
    EdgeEmbodimentTransportError,
    SQLiteEdgeEmbodimentStore,
)
from edge_client.actuators.speech import SpeechQueueResult
from edge_client.gateway.migration_runner import apply_edge_gateway_migrations
from edge_client.app.fastapi_app import (
    _execute_embodiment_effect,
    _handle_embodiment_command,
    _handle_embodiment_session,
)
from edge_client.app.schemas import EdgeRpcEnvelope
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_INVALID,
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


def _voice_command(lease, *, key: str = "voice-idem"):
    raw = build_embodiment_command(
        kind="voice",
        action="speak",
        payload={
            "session_id": "voice-session",
            "utterance_id": key,
            "text": "short voice",
            "lang_hint": "en",
        },
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/voice",
        deadline_ms=50_000,
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


def test_live_exact_reconnect_can_rotate_fence_and_reject_late_old_fence_command(tmp_path) -> None:
    """A replacement Host fences an old still-live Host before dispatch."""

    clock = _Clock()
    store = _store(tmp_path, clock)
    first = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    old_fence_command = _command(first, key="old-edge-fence")

    # Ordinary renewal is deliberately not a replacement and retains the
    # fence.  This avoids invalidating healthy in-flight work on each tick.
    renewed = store.open_session(
        session_id="host-session",
        owner_id="host",
        ttl_ms=10_000,
        reconnect_lease_id=first.lease_id,
        reconnect_fencing_token=first.fencing_token,
    )
    assert renewed.lease_id == first.lease_id
    assert renewed.fencing_token == first.fencing_token

    replacement = store.open_session(
        session_id="host-session",
        owner_id="host",
        ttl_ms=10_000,
        reconnect_lease_id=renewed.lease_id,
        reconnect_fencing_token=renewed.fencing_token,
        rotate_fencing_token=True,
    )
    assert replacement.fencing_token == first.fencing_token + 1
    assert replacement.lease_id != first.lease_id
    assert replacement.issued_at_ms == clock.value

    with pytest.raises(EdgeEmbodimentTransportError, match=EMBODIMENT_FENCE_STALE):
        store.claim_command(old_fence_command)
    assert store.claim_command(
        _command(replacement, key="replacement-edge-fence")
    ).execute is True


def test_live_fence_rotation_requires_exact_same_owner_reconnect_credential(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    first = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)

    # A competing owner and a caller without the precise current credential
    # cannot turn an active session into a replacement session.
    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_BUSY):
        store.open_session(
            session_id="host-session",
            owner_id="other-host",
            ttl_ms=10_000,
            reconnect_lease_id=first.lease_id,
            reconnect_fencing_token=first.fencing_token,
            rotate_fencing_token=True,
        )
    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_BUSY):
        store.open_session(
            session_id="host-session",
            owner_id="host",
            ttl_ms=10_000,
            rotate_fencing_token=True,
        )
    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_BUSY):
        store.open_session(
            session_id="host-session",
            owner_id="host",
            ttl_ms=10_000,
            reconnect_lease_id="forged-lease",
            reconnect_fencing_token=first.fencing_token,
            rotate_fencing_token=True,
        )
    with pytest.raises(EdgeEmbodimentTransportError, match=EDGE_SESSION_RECONNECT_INVALID):
        store.open_session(
            session_id="host-session",
            owner_id="host",
            ttl_ms=10_000,
            reconnect_lease_id=first.lease_id,
            reconnect_fencing_token=first.fencing_token,
            rotate_fencing_token="true",  # type: ignore[arg-type]
        )


def test_session_handler_forwards_exact_live_fence_rotation_request(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    initial = _handle_embodiment_session(
        EdgeRpcEnvelope(
            request_id="open-session",
            ts_ms=clock.value,
            nonce="nonce-open",
            signature="signature",
            payload={"session_id": "host-session", "owner_id": "host", "ttl_ms": 10_000},
        ),
        store=store,
    )
    first = parse_edge_session_lease(initial.event["lease"])
    old_command = _command(first, key="handler-old-fence")

    rotated = _handle_embodiment_session(
        EdgeRpcEnvelope(
            request_id="replace-session",
            ts_ms=clock.value,
            nonce="nonce-replace",
            signature="signature",
            payload={
                "session_id": first.session_id,
                "owner_id": first.owner_id,
                "ttl_ms": 10_000,
                "reconnect_lease_id": first.lease_id,
                "reconnect_fencing_token": first.fencing_token,
                "rotate_fencing_token": True,
            },
        ),
        store=store,
    )
    replacement = parse_edge_session_lease(rotated.event["lease"])
    assert replacement.fencing_token == first.fencing_token + 1

    rejected = _handle_embodiment_command(
        object(),
        EdgeRpcEnvelope(
            request_id=old_command.request_id,
            ts_ms=clock.value,
            nonce="nonce-old-command",
            signature="signature",
            payload=old_command.as_dict(),
        ),
        transport_runtime=DurableEmbodimentRuntime(store),
    )
    assert rejected.success is False
    assert rejected.reason_code == EMBODIMENT_FENCE_STALE


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


def test_queued_voice_receipt_settles_from_exact_playback_terminal_without_replay(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _voice_command(lease)
    runtime = DurableEmbodimentRuntime(store)
    calls: list[str] = []

    queued = runtime.execute(
        command,
        lambda item: calls.append(item.command_id) or {
            "success": True,
            "status": "ACCEPTED",
            "detail": "tts queued",
            "event": {"external_side_effects": "PENDING"},
        },
    )

    assert queued.status == "ACCEPTED"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM edge_embodiment_command WHERE command_id = ?",
            (command.command_id,),
        ).fetchone()[0] == "IN_DOUBT"

    clock.value += 50
    settled = runtime.complete_voice_playback(
        SpeechQueueResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            stage="S_VOICE_TTS_PLAYBACK",
            detail="tts playback finished",
            event={
                "request_id": command.command_id,
                "event_type": "TTS_PLAYBACK_FINISHED",
                "ts_ms": clock.value,
            },
        )
    )
    duplicate = runtime.execute(
        command,
        lambda item: calls.append(f"duplicate:{item.command_id}") or {"success": True},
    )

    assert settled is not None
    assert settled.status == "APPLIED"
    assert settled.event["physical_terminal"] is True
    assert duplicate.as_dict() == settled.as_dict()
    assert calls == [command.command_id]


def test_segmented_voice_waits_for_last_physical_segment(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _voice_command(lease, key="voice-segmented")
    runtime = DurableEmbodimentRuntime(store)
    runtime.execute(
        command,
        lambda _: {
            "success": True,
            "status": "ACCEPTED",
            "detail": "tts queued",
        },
    )

    first = runtime.complete_voice_playback(
        SpeechQueueResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            stage="S_VOICE_TTS_PLAYBACK",
            detail="segment 1 finished",
            event={
                "request_id": command.command_id,
                "segment_parent_request_id": command.command_id,
                "segment_index": 1,
                "segment_count": 2,
                "event_type": "TTS_PLAYBACK_FINISHED",
            },
        )
    )
    assert first is None
    assert store.get_receipt(command_id=command.command_id).status == "ACCEPTED"

    clock.value += 50
    last = runtime.complete_voice_playback(
        SpeechQueueResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            stage="S_VOICE_TTS_PLAYBACK",
            detail="segment 2 finished",
            event={
                "request_id": f"{command.command_id}::seg2",
                "segment_parent_request_id": command.command_id,
                "segment_index": 2,
                "segment_count": 2,
                "event_type": "TTS_PLAYBACK_FINISHED",
            },
        )
    )

    assert last is not None
    assert last.status == "APPLIED"
    assert last.event["segment_index"] == 2


def test_segmented_voice_synth_failure_settles_rejected_without_physical_effect(tmp_path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    lease = store.open_session(session_id="host-session", owner_id="host", ttl_ms=10_000)
    command = _voice_command(lease, key="voice-segment-failed")
    runtime = DurableEmbodimentRuntime(store)
    runtime.execute(
        command,
        lambda _: {
            "success": True,
            "status": "ACCEPTED",
            "detail": "tts queued",
        },
    )

    rejected = runtime.complete_voice_playback(
        SpeechQueueResult(
            success=False,
            status="REJECTED",
            reason_code="VOICE_SYNTH_FAILED",
            stage="S_VOICE_TTS_SYNTH",
            detail="segment 1 synth failed",
            event={
                "request_id": command.command_id,
                "segment_parent_request_id": command.command_id,
                "segment_index": 1,
                "segment_count": 2,
                "event_type": "TTS_SYNTH_FAILED",
                "external_side_effects": False,
                "cancelled_queued_segments": 1,
            },
        )
    )

    assert rejected is not None
    assert rejected.accepted is False
    assert rejected.status == "REJECTED"
    assert rejected.reason_code == "VOICE_SYNTH_FAILED"
    assert rejected.event["cancelled_queued_segments"] == 1


@pytest.mark.parametrize("kind", ["voice", "avatar"])
def test_expression_release_is_a_terminal_control_not_a_speak_request(kind: str) -> None:
    command = build_embodiment_command(
        kind=kind,
        action="release",
        payload={"session_id": "voice-session"},
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/release",
        deadline_ms=50_000,
        idempotency_key=f"release-{kind}",
        request_id=f"request-release-{kind}",
        issued_at_ms=900,
    )

    result = _execute_embodiment_effect(
        object(),
        EdgeRpcEnvelope(
            request_id=command.request_id,
            ts_ms=900,
            nonce="nonce-release",
            signature="signature",
            payload=command.as_dict(),
        ),
        command,
    )

    assert result.success is True
    assert result.status == "APPLIED"
    assert result.event["effect"]["release_applied"] is True


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


def test_unknown_desktop_embodiment_action_fails_closed() -> None:
    command = build_embodiment_command(
        kind="desktop",
        action="visual_locate",
        payload={},
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/desktop",
        deadline_ms=50_000,
        idempotency_key="desktop-unknown-action",
        request_id="request-desktop-unknown-action",
        issued_at_ms=900,
    )

    result = _execute_embodiment_effect(
        object(),
        EdgeRpcEnvelope(
            request_id=command.request_id,
            ts_ms=900,
            nonce="nonce-desktop-unknown-action",
            signature="signature",
            payload=command.as_dict(),
        ),
        command,
    )

    assert result.success is False
    assert result.status == "REJECTED"
    assert result.reason_code == EMBODIMENT_COMMAND_INVALID
    assert result.stage == "S_EDGE_DESKTOP_SCHEMA"
