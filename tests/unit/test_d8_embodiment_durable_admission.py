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


def test_legacy_reply_performance_is_retired_without_durable_admission() -> None:
    runtime = _Runtime()

    result = asyncio.run(
        trigger_reply_performance(
            request_id="req-d8-performance",
            reply_envelope=_reply(),
            embodiment_runtime=runtime,
        )
    )

    assert runtime.commands == []
    assert result["status"] == "RETIRED"
    assert result["reason_code"] == "LEGACY_REPLY_PERFORMANCE_RETIRED"
    assert result["delivery_state"] == "RETIRED"
    assert result["replacement_authority"] == "accepted_expression_plan_life_delivery_transport"
    assert result["affected_output"] is False


def test_retired_reply_performance_has_no_process_global_runtime_fallback() -> None:
    result = asyncio.run(
        trigger_reply_performance(
            request_id="req-d8-no-runtime",
            reply_envelope=_reply(),
            embodiment_runtime=None,
        )
    )

    assert result["status"] == "RETIRED"
    assert result["reason_code"] == "LEGACY_REPLY_PERFORMANCE_RETIRED"
    assert result["affected_output"] is False
