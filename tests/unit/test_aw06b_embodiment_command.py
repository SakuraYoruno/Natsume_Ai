from __future__ import annotations

import asyncio

import pytest

from edge_client.app.fastapi_app import _handle_embodiment_command
from edge_client.gateway.embodiment_runtime import (
    DurableEmbodimentRuntime,
    SQLiteEdgeEmbodimentStore,
)
from edge_client.gateway.migration_runner import apply_edge_gateway_migrations
from edge_client.app.schemas import EdgeRpcEnvelope
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_EXPIRED,
    EMBODIMENT_KIND_MATEENGINE,
    EmbodimentReceipt,
    build_embodiment_command,
)
from natsume_contracts.edge.transport_v1 import bind_command_to_lease
from host_brain.channels.embodiment import (
    build_host_embodiment_command,
    submit_avatar_command,
    submit_voice_command,
)
from tests.action_preview_test_support import (
    build_scheduler_action_preview,
    issue_embodiment_action_preview,
    terminal_preview_kwargs,
)


def _envelope(command) -> EdgeRpcEnvelope:
    return EdgeRpcEnvelope(
        request_id=command.request_id,
        ts_ms=command.issued_at_ms,
        nonce="nonce",
        signature="signature",
        payload=command.as_dict(),
    )


def test_edge_executes_idempotent_physical_command_once(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def _execute(*, action, payload):
        calls.append({"action": action, "payload": dict(payload)})
        return {
            "success": True, "status": "COMPLETED", "reason_code": None,
            "detail": "done", "target": "mateengine",
        }

    monkeypatch.setattr(
        "edge_client.actuators.mateengine_driver.execute_mateengine_effect", _execute
    )
    command = build_embodiment_command(
        kind=EMBODIMENT_KIND_MATEENGINE,
        action="command",
        payload={"command": {"type": "reset"}},
        authority_ref="authority://host/test",
        grant_ref="grant://test/1",
        deadline_ms=2_000,
        idempotency_key="same-command",
        request_id="req-1",
        issued_at_ms=1_000,
    )
    db_path = tmp_path / "edge.sqlite3"
    apply_edge_gateway_migrations(db_path)
    store = SQLiteEdgeEmbodimentStore(db_path, clock=lambda: 1_100)
    lease = store.open_session(session_id="host", owner_id="host", ttl_ms=10_000)
    command = bind_command_to_lease(command, lease)
    transport = DurableEmbodimentRuntime(store)

    first = _handle_embodiment_command(
        object(), _envelope(command), transport_runtime=transport,
    )
    second = _handle_embodiment_command(
        object(), _envelope(command), transport_runtime=transport,
    )

    assert first.success is True
    assert first.event["receipt"]["command_id"] == command.command_id
    assert second.success is True
    assert second.event["receipt"] == first.event["receipt"]
    assert len(calls) == 1


def test_edge_rejects_expired_command_without_device_effect(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "edge_client.actuators.mateengine_driver.execute_mateengine_effect",
        lambda **kwargs: calls.append(kwargs),
    )
    command = build_embodiment_command(
        kind=EMBODIMENT_KIND_MATEENGINE,
        action="command",
        payload={"command": {"type": "reset"}},
        authority_ref="authority://host/test",
        grant_ref="grant://test/1",
        deadline_ms=2_000,
        idempotency_key="expired-on-arrival",
        request_id="req-expired",
        issued_at_ms=1_000,
    )
    db_path = tmp_path / "edge.sqlite3"
    apply_edge_gateway_migrations(db_path)
    store = SQLiteEdgeEmbodimentStore(db_path, clock=lambda: 2_001)
    lease = store.open_session(session_id="host", owner_id="host", ttl_ms=10_000)
    command = bind_command_to_lease(command, lease)
    result = _handle_embodiment_command(
        object(), _envelope(command),
        transport_runtime=DurableEmbodimentRuntime(store),
    )

    assert result.success is False
    assert result.reason_code == EMBODIMENT_COMMAND_EXPIRED
    assert calls == []


def test_host_authors_metadata_and_only_submits_to_port(
    monkeypatch,
    tmp_path,
) -> None:
    captured = []

    class _Port:
        async def submit(self, command):
            captured.append(command)
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="ACCEPTED",
                reason_code=None,
                detail="accepted",
                received_at_ms=command.issued_at_ms,
            )

    port = _Port()
    with pytest.raises(RuntimeError, match="ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"):
        asyncio.run(
            submit_voice_command(
                action="speak",
                payload={"text": "hello"},
                request_id="req-host-port-voice",
                grant_ref="grant://test/voice",
                idempotency_key="voice-command-without-preview",
                command_port=port,
            )
        )
    assert captured == []

    issued_at_ms = 1_800_000_000_000
    monkeypatch.setattr(
        "host_brain.channels.embodiment.now_ms",
        lambda: issued_at_ms,
    )
    service, scheduler_context = build_scheduler_action_preview(
        tmp_path,
        name="aw06b-host-metadata",
    )
    meta = {
        "request_id": "req-host-port",
        "grant_ref": "governor-grant://preview-child/aw06b-host-metadata",
        "idempotency_key": "avatar-command-1",
        "deadline_ms": issued_at_ms + 30_000,
    }
    expected = build_host_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload={"emotion_tag": "HAPPY"},
        **meta,
    )
    preview = issue_embodiment_action_preview(
        service=service,
        scheduler_context=scheduler_context,
        command=expected,
    )
    receipt = asyncio.run(
        submit_avatar_command(
            action="set_expression",
            payload={"emotion_tag": "HAPPY"},
            command_port=port,
            **meta,
            **terminal_preview_kwargs(service, preview),
        )
    )

    assert receipt.success is True
    assert len(captured) == 1
    command = captured[0]
    assert command.command_id.startswith("emb_")
    assert command.authority_ref == "authority://host/device-command-author"
    assert command.grant_ref == "governor-grant://preview-child/aw06b-host-metadata"
    assert command.deadline_ms > command.issued_at_ms
    assert command.idempotency_key == "avatar-command-1"
