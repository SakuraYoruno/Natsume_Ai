from __future__ import annotations

import asyncio

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import (
    EmbodimentOutboxAck,
    HostEmbodimentRuntime,
    build_host_embodiment_command,
)
from host_brain.input_runtime.gateway_delivery import _send_gateway_voice_reply
from host_brain.persistence.embodiment_outbox_repository import EmbodimentOutboxRepository
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt


def test_duplicate_terminal_outbox_ack_remains_a_successful_durable_admission(tmp_path) -> None:
    """A lost caller response must not turn an already-applied command into failure."""

    db_path = tmp_path / "d8-outbox-duplicate.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d8-outbox-duplicate"
    )
    assert migration.success, migration.as_dict()

    class _AppliedPort:
        async def submit(self, command):  # noqa: ANN001, ANN201
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                status="APPLIED",
                reason_code=None,
                detail="applied",
                received_at_ms=command.issued_at_ms,
                completed_at_ms=command.issued_at_ms + 1,
            )

    runtime = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="d8-outbox-duplicate",
        command_port=_AppliedPort(),
    )
    try:
        command = build_host_embodiment_command(
            kind="voice",
            action="speak",
            payload={"text": "duplicate safe"},
            request_id="d8-outbox-duplicate",
        )
        first = runtime.enqueue(command)
        assert first.status == "PENDING"
        assert asyncio.run(runtime.dispatch_pending_async(limit=1)).applied == 1

        repeated = runtime.enqueue(command)
        assert repeated.outbox_id == first.outbox_id
        assert repeated.status == "APPLIED"
        assert repeated.durable_admitted is True
        assert repeated.success is True
        assert repeated.effect_verified is True
    finally:
        runtime.close()


def test_gateway_voice_replay_reports_applied_receipt_not_a_new_pending_send() -> None:
    """Gateway output distinguishes a durable replay from an actual new enqueue."""

    class _AppliedRuntime:
        def enqueue(self, command):  # noqa: ANN001, ANN201
            return EmbodimentOutboxAck(
                outbox_id="outbox-existing-applied",
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                status="APPLIED",
                detail="existing Edge receipt is applied",
            )

    result = asyncio.run(
        _send_gateway_voice_reply(
            platform="qq",
            conversation_id="qq-d8-replay",
            tts_text="已经播放过了。",
            request_id="d8-gateway-voice-replay",
            send_gateway_voice_message=True,
            embodiment_runtime=_AppliedRuntime(),
        )
    )

    assert result["voice_status"] == "APPLIED"
    assert result["voice_delivery_state"] == "APPLIED"
    assert result["voice_durable_admitted"] is True
    assert result["voice_success"] is True
    assert result["voice_physical_effect_verified"] is True
    assert result["voice_gateway_sent"] is False
    assert result["voice_gateway_status"] == "RECEIPT_AVAILABLE_PENDING_DELIVERY"
    assert result["voice_event"]["event_type"] == "HOST_EMBODIMENT_DURABLE_STATUS_REPLAYED"


def test_rejected_terminal_ack_is_not_a_successful_durable_admission() -> None:
    ack = EmbodimentOutboxAck(
        outbox_id="outbox-rejected",
        command_id="command-rejected",
        idempotency_key="key-rejected",
        status="REJECTED",
        reason_code="EDGE_EMBODIMENT_REJECTED",
    )

    assert ack.durable_admitted is False
    assert ack.success is False
    assert ack.effect_verified is False
