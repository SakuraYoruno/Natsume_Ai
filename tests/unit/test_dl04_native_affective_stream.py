from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.surface_main_chain_adapter import (
    _final_stream_usage_is_attested,
    surface_main_chain_sse_generator,
)
from host_brain.model_runtime import (
    AttemptStatus,
    ModelAttemptRecord,
    ModelAttemptResult,
    ModelAttemptStreamEvent,
    ModelStreamProtocolError,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
)


SOUL = "你是七海。中文私聊，真诚、有分寸，不操控用户。"


def _parse_sse(raw: str) -> dict[str, Any]:
    return json.loads(raw.removeprefix("data: ").strip())


def _canonical_context() -> dict[str, object]:
    return {
        "_affective_life_internal": True,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-stream",
            "relationship_subject_id": "person-stream",
            "session_id": "session-stream",
            "self_affect": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "person-stream",
                "revision": 9,
                "components": [{"emotion": "CARE", "intensity": 0.66}],
                "last_regulation": {"regulation_id": "reg-stream", "mode": "EXPRESS"},
            },
            "user_observations": [
                {
                    "modality": "TEXT",
                    "emotion_distribution": {"SAD": 0.85},
                    "valence": -0.72,
                    "confidence": 0.7,
                }
            ],
            "conflict": False,
            "relationship_life": {
                "state": {
                    "relationship_subject_id": "person-stream",
                    "revision": 3,
                    "repair_required": False,
                    "proactive_permission": "UNKNOWN",
                },
                "aftereffect": {"outcome": "UNKNOWN"},
            },
        },
    }


def _agent_input() -> SimpleNamespace:
    return SimpleNamespace(
        request_id="request-stream",
        session_id="session-stream",
        person_id="person-stream",
        workspace_id="workspace-stream",
        user_message="我今天有点难受。",
        render_policy={
            "primary_topic": "daily_chat",
            "interaction_move": "reply_current_turn",
            "response_form": "text",
            "max_questions": 0,
        },
        emotion_context=_canonical_context(),
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        trace_stage_meta={},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )


def test_final_stream_usage_status_requires_all_provider_budget_dimensions() -> None:
    complete = SimpleNamespace(
        cached=False,
        attempts=(
            SimpleNamespace(
                metadata={
                    "provider_budget_measurements": {
                        "token_count": 12,
                        "completion_tokens": 7,
                        "model_cost_micros": 31,
                    }
                }
            ),
        ),
    )
    missing_cost = SimpleNamespace(
        cached=False,
        attempts=(
            SimpleNamespace(
                metadata={
                    "provider_budget_measurements": {
                        "token_count": 12,
                        "completion_tokens": 7,
                    }
                }
            ),
        ),
    )

    assert _final_stream_usage_is_attested(complete) is True
    assert _final_stream_usage_is_attested(missing_cost) is False
    assert _final_stream_usage_is_attested(SimpleNamespace(cached=True, attempts=())) is False


@pytest.mark.asyncio
async def test_native_stream_stages_plan_and_each_sentence_before_visibility(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    submitted: list[dict[str, Any]] = []
    requests: list[Any] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class StreamingModel:
        async def stream(self, request):  # noqa: ANN001
            requests.append(request)
            yield "先缓一下。"
            yield "我在这儿，慢慢说也行。"

    def _submit(**kwargs: Any) -> dict[str, Any]:
        submitted.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "event_id": f"event-{len(submitted)}",
            "state_version": len(submitted),
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _submit)
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: StreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(_agent_input(), ports=ports)
    ]

    sentences = [event for event in events if event.get("phase") == "SENTENCE"]
    deliveries = [event for event in events if event.get("phase") == "DELIVERY"]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    command_types = [str(item["command_type"]) for item in submitted]

    assert requests
    messages = requests[0].prompt.variables["messages"]
    assert messages[-1]["role"] == "system"
    assert "只输出一条最终可发送的中文正文" in messages[-1]["content"]
    assert command_types == [
        "affective_turn_planned",
        "surface_sentence_delivery_intent",
        "surface_sentence_delivery_intent",
        "foreground_completed",
    ]
    assert [event["sentence_index"] for event in sentences] == [0, 1]
    assert [event["delivery_state"] for event in deliveries] == [
        "HOST_PROTOCOL_STAGED",
        "HOST_PROTOCOL_STAGED",
        "HOST_RELEASE_STAGED",
    ]
    assert all(event["channels"] == ["VOICE", "AVATAR"] for event in deliveries)
    assert all("sentence" not in event for event in deliveries)
    assert {event["synchronization_id"] for event in sentences} == {
        submitted[0]["payload"]["affective_turn_plan"]["expression_plan"]["synchronization_id"]
    }
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "surface_reply:native_affective_stream"
    assert commit["audit_refs"]["surface_main_chain"]["streaming"] is True
    foreground = submitted[-1]["payload"]
    first_sentence_protocol = submitted[1]["payload"]["expression_delivery_intents"]
    assert [item["channel"] for item in first_sentence_protocol] == ["VOICE", "AVATAR"]
    assert {item["synchronization_id"] for item in first_sentence_protocol} == {
        submitted[0]["payload"]["affective_turn_plan"]["expression_plan"]["synchronization_id"]
    }
    assert foreground["expression_delivery_controls"]
    assert foreground["emotion_context"]["emotion_tag"] == "CALM"
    assert foreground["emotion_context"]["emotion_source"] == "affective_turn_plan"
    assert foreground["surface_result"]["plan_id"] == submitted[0]["payload"]["affective_turn_plan"]["plan_id"]


@pytest.mark.asyncio
async def test_native_stream_records_terminal_usage_attestation_when_available(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    submitted: list[dict[str, Any]] = []
    requests: list[Any] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class UsageAttestedStreamingModel:
        async def stream_with_result(self, request):  # noqa: ANN001
            requests.append(request)
            yield ModelAttemptStreamEvent.delta("先缓一下。")
            yield ModelAttemptStreamEvent.delta("我在这儿，慢慢说也行。")
            target = ProviderTarget("surface-attested", "provider-a", "model-a")
            response = ProviderResponse(
                content="先缓一下。我在这儿，慢慢说也行。",
                usage={
                    "total_tokens": 12,
                    "completion_tokens": 7,
                    "model_cost_micros": 31,
                },
            )
            result = ModelAttemptResult(
                request_id=request.request_id,
                success=True,
                route_decision=RouteDecision(
                    "route-surface-attested",
                    (target,),
                    "surface-test",
                    max_attempts=1,
                ),
                attempts=(
                    ModelAttemptRecord(
                        attempt_id="attempt-surface-attested",
                        request_id=request.request_id,
                        route_decision_id="route-surface-attested",
                        attempt_index=1,
                        status=AttemptStatus.SUCCEEDED,
                        retry_reason="initial",
                        adapter_id="surface-attested",
                        provider="provider-a",
                        model_id="model-a",
                        prompt_id="surface.main.text.v1",
                        prompt_version="1",
                        prompt_digest="a" * 64,
                        started_at_ms=1,
                        ended_at_ms=2,
                        latency_ms=1,
                        metadata={
                            "provider_budget_measurements": {
                                "token_count": 12,
                                "completion_tokens": 7,
                                "model_cost_micros": 31,
                            }
                        },
                    ),
                ),
                response=response,
                final_target=target,
            )
            yield ModelAttemptStreamEvent.final(result)

        async def stream(self, _request):  # noqa: ANN001
            raise AssertionError("attested stream must not fall back to legacy stream")
            yield ""  # pragma: no cover - preserves async-generator shape

    def _submit(**kwargs: Any) -> dict[str, Any]:
        submitted.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "event_id": f"event-{len(submitted)}",
            "state_version": len(submitted),
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _submit)
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: UsageAttestedStreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(_agent_input(), ports=ports)
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    trace = commit["audit_refs"]["trace_stage_meta"]
    model_trace = trace["surface_model_attempt"]

    assert requests
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["model_id"] == "model-a"
    assert commit["audit_refs"]["provider_name"] == "provider-a"
    assert commit["audit_refs"]["surface_main_chain"]["provider_usage_status"] == (
        "STREAM_FINAL_USAGE_ATTESTED"
    )
    assert model_trace["provider_usage_status"] == "STREAM_FINAL_USAGE_ATTESTED"
    assert model_trace["budget_measurement"]["token_count"] == 12
    assert model_trace["budget_measurement"]["model_cost_micros"] == 31
    assert trace["native_sentence_stream"]["model_stream_protocol"] == "FINAL_RESULT_WITH_USAGE"


@pytest.mark.asyncio
async def test_native_stream_falls_back_only_for_explicit_pre_delta_unsupported_protocol(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    submitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class ExplicitLegacyStreamingModel:
        async def stream_with_result(self, _request):  # noqa: ANN001
            raise ModelStreamProtocolError(
                "provider has no final usage protocol",
                code="MODEL_STREAM_RESULT_UNSUPPORTED",
            )
            yield ModelAttemptStreamEvent.delta("unreachable")  # pragma: no cover

        async def stream(self, _request):  # noqa: ANN001
            yield "先缓一下。"
            yield "我在这儿。"

    def _submit(**kwargs: Any) -> dict[str, Any]:
        submitted.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "event_id": f"event-{len(submitted)}",
            "state_version": len(submitted),
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _submit)
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: ExplicitLegacyStreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(_agent_input(), ports=ports)
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    trace = commit["audit_refs"]["trace_stage_meta"]

    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["surface_main_chain"]["provider_usage_status"] == (
        "STREAM_ATTEMPT_AUDITED_NO_FINAL_USAGE"
    )
    assert trace["native_sentence_stream"]["model_stream_protocol"] == "LEGACY_NO_FINAL_USAGE"
    assert trace["native_sentence_stream"]["legacy_stream_fallback_reason"] == (
        "MODEL_STREAM_RESULT_UNSUPPORTED"
    )


@pytest.mark.asyncio
async def test_native_stream_blocks_manipulative_sentence_before_it_is_visible(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    submitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class StreamingModel:
        async def stream(self, _request):  # noqa: ANN001
            yield "你不回我我就一直难过。"

    def _submit(**kwargs: Any) -> dict[str, Any]:
        submitted.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "event_id": f"event-{len(submitted)}",
            "state_version": len(submitted),
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _submit)
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: StreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(_agent_input(), ports=ports)
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert [event for event in events if event.get("phase") == "SENTENCE"] == []
    assert commit["decision"] == "NO_REPLY"
    assert commit["reason_code"] == "SURFACE_STREAM_INTERRUPTED"
    assert [item["command_type"] for item in submitted] == ["affective_turn_planned"]
