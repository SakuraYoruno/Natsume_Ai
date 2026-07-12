from __future__ import annotations

import asyncio
from edge_client.app.fastapi_app import _handle_embodiment_command
from edge_client.gateway.embodiment_runtime import (
    DurableEmbodimentRuntime,
    SQLiteEdgeEmbodimentStore,
)
from edge_client.app.schemas import EdgeRpcEnvelope
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_EXPIRED,
    EMBODIMENT_KIND_MATEENGINE,
    EmbodimentReceipt,
    build_embodiment_command,
)
from natsume_contracts.edge.transport_v1 import bind_command_to_lease
from host_brain.channels.embodiment import (
    configure_embodiment_command_port,
    submit_voice_command,
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
    store = SQLiteEdgeEmbodimentStore(tmp_path / "edge.sqlite3", clock=lambda: 1_100)
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
    store = SQLiteEdgeEmbodimentStore(tmp_path / "edge.sqlite3", clock=lambda: 2_001)
    lease = store.open_session(session_id="host", owner_id="host", ttl_ms=10_000)
    command = bind_command_to_lease(command, lease)
    result = _handle_embodiment_command(
        object(), _envelope(command),
        transport_runtime=DurableEmbodimentRuntime(store),
    )

    assert result.success is False
    assert result.reason_code == EMBODIMENT_COMMAND_EXPIRED
    assert calls == []


def test_host_authors_metadata_and_only_submits_to_port() -> None:
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

    configure_embodiment_command_port(_Port())
    try:
        receipt = asyncio.run(
            submit_voice_command(
                action="speak",
                payload={"text": "hello"},
                request_id="req-host-port",
                grant_ref="grant://test/voice",
                idempotency_key="voice-command-1",
            )
        )
    finally:
        configure_embodiment_command_port(None)

    assert receipt.success is True
    assert len(captured) == 1
    command = captured[0]
    assert command.command_id.startswith("emb_")
    assert command.authority_ref == "authority://host/device-command-author"
    assert command.grant_ref == "grant://test/voice"
    assert command.deadline_ms > command.issued_at_ms
    assert command.idempotency_key == "voice-command-1"
