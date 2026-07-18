from __future__ import annotations

import asyncio

from host_brain.channels.embodiment import EmbodimentOutboxAck
from host_brain.input_runtime.outbound_delivery import trigger_reply_performance
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.input_runtime.reply_performance import ReplyPerformance


class _Runtime:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def enqueue(self, command):  # noqa: ANN001, ANN201
        self.commands.append(command)
        return EmbodimentOutboxAck(
            outbox_id="outbox-performance-1",
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
        )


def _reply() -> ReplyEnvelope:
    return ReplyEnvelope.final_reply(
        text="我在。",
        source="d8-test",
        performance=ReplyPerformance(
            emotion_tag="HAPPY",
            emotion_intensity=0.7,
            source="d8-test",
        ),
    )


def test_reply_performance_only_claims_durable_admission_before_edge_receipt() -> None:
    runtime = _Runtime()

    result = asyncio.run(
        trigger_reply_performance(
            request_id="req-d8-performance",
            reply_envelope=_reply(),
            embodiment_runtime=runtime,
        )
    )

    assert len(runtime.commands) == 1
    command = runtime.commands[0]
    assert command.kind == "avatar"
    assert command.action == "set_expression"
    assert result["status"] == "PENDING"
    assert result["success"] is True
    assert result["delivery_state"] == "DURABLY_QUEUED"
    assert result["affected_output"] is False
    assert result["expression_id"] is None
    assert result["outbox_id"] == "outbox-performance-1"


def test_reply_performance_has_no_process_global_runtime_fallback() -> None:
    result = asyncio.run(
        trigger_reply_performance(
            request_id="req-d8-no-runtime",
            reply_envelope=_reply(),
            embodiment_runtime=None,
        )
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "PERFORMANCE_TRIGGER_EXCEPTION"
    assert result["affected_output"] is False
