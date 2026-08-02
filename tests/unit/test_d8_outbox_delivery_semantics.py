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
from tests.action_preview_test_support import (
    build_scheduler_action_preview,
    issue_embodiment_action_preview,
    terminal_preview_kwargs,
)


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
            kind="mateengine",
            action="command",
            payload={"command": {"type": "reset"}},
            request_id="d8-outbox-duplicate",
            authority_ref="authority://task-node/d8-outbox-duplicate",
            grant_ref="governor-grant://preview-child/d8-outbox-duplicate",
        )
        service, scheduler_context = build_scheduler_action_preview(
            tmp_path,
            name="d8-outbox-duplicate",
        )
        preview = issue_embodiment_action_preview(
            service=service,
            scheduler_context=scheduler_context,
            command=command,
        )
        proof = terminal_preview_kwargs(service, preview)
        first = runtime.enqueue(command, **proof)
        assert first.status == "PENDING"
        assert asyncio.run(runtime.dispatch_pending_async(limit=1)).applied == 1

        repeated = runtime.enqueue(command, **proof)
        assert repeated.outbox_id == first.outbox_id
        assert repeated.status == "APPLIED"
        assert repeated.durable_admitted is True
        assert repeated.success is True
        assert repeated.effect_verified is True
    finally:
        runtime.close()


def test_legacy_gateway_voice_is_retired_without_durable_admission() -> None:
    """The legacy Gateway voice route cannot bypass the accepted expression path."""

    class _AppliedRuntime:
        def __init__(self) -> None:
            self.calls = 0

        def enqueue(self, command, **_kwargs):  # noqa: ANN001, ANN201
            self.calls += 1
            return EmbodimentOutboxAck(
                outbox_id="outbox-existing-applied",
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                status="APPLIED",
                detail="existing Edge receipt is applied",
            )

    runtime = _AppliedRuntime()
    result = asyncio.run(
        _send_gateway_voice_reply(
            platform="qq",
            conversation_id="qq-d8-replay",
            tts_text="已经播放过了。",
            request_id="d8-gateway-voice-replay",
            send_gateway_voice_message=True,
            embodiment_runtime=runtime,
        )
    )

    assert result["voice_status"] == "RETIRED"
    assert result["voice_reason_code"] == "LEGACY_GATEWAY_VOICE_RETIRED"
    assert result["voice_delivery_state"] == "RETIRED"
    assert result["voice_durable_admitted"] is False
    assert result["voice_success"] is False
    assert result["voice_physical_effect_verified"] is False
    assert result["voice_gateway_sent"] is False
    assert result["voice_gateway_status"] == "RETIRED"
    assert result["replacement_authority"] == "accepted_expression_plan_life_delivery_transport"
    assert result["voice_event"] == {}
    assert runtime.calls == 0


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
