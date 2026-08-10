from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.surface_affective_native_stream import (
    _native_affective_stream_preparation,
)
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
from host_brain.model_runtime.contracts import ModelBudgetGuard
from host_brain.task_runtime.chat_delivery_authority import (
    TaskBoundChatDeliveryAuthority,
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


def test_native_stream_transport_switch_falls_back_before_compilation(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SURFACE_NATIVE_SENTENCE_STREAM_ENABLED", "false")

    assert (
        _native_affective_stream_preparation(
            _agent_input(),
            ports=None,  # type: ignore[arg-type]
            trace_stage_meta={},
        )
        is None
    )


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

    agent_input = _agent_input()
    agent_input.trace_stage_meta = {"runtime_path": "realtime_companion"}
    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(agent_input, ports=ports)
    ]

    sentences = [event for event in events if event.get("phase") == "SENTENCE"]
    deliveries = [event for event in events if event.get("phase") == "DELIVERY"]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    command_types = [str(item["command_type"]) for item in submitted]

    assert requests
    assert requests[0].scene_type == "CHAT_SURFACE_REALTIME"
    assert requests[0].cognitive_tier == "B"
    assert requests[0].metadata["route_lane"] == "realtime_companion"
    messages = requests[0].prompt.variables["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    rendered_prompt = "\n".join(message["content"] for message in messages)
    assert "只输出一条可发送的自然中文私聊" in rendered_prompt
    assert '"candidates"' not in rendered_prompt
    assert "输出必须是 JSON" not in rendered_prompt
    assert "用户情绪只是观察，不是七海自己的情绪" in rendered_prompt
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
async def test_governed_stream_uses_task_bound_sentence_receipts_not_life_writes(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    """The real governed branch must persist text before SSE but defer effects."""

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class RecordingTaskDeliveryAuthority(TaskBoundChatDeliveryAuthority):
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def stage_plan(self, plan):  # noqa: ANN001
            assert plan["self_id"] == "natsume_ai"
            assert plan["relationship_subject_id"] == "person-stream"
            assert plan["workspace_id"] == "workspace-stream"
            self.calls.append(("PLAN_FROZEN", -1))
            return {
                "event_id": "taskchat://" + "a" * 64,
                "delivery_status": "TASK_NODE_STREAM_RECORDED",
                "replayed": False,
            }

        def stage_sentence(self, *, plan, sentence, sentence_index):  # noqa: ANN001
            assert plan["self_id"] == "natsume_ai"
            self.calls.append(("SENTENCE_STAGED", int(sentence_index)))
            return {
                "phase": "SENTENCE",
                "sentence": sentence,
                "sentence_index": sentence_index,
                "delivery_id": "taskchat://" + f"{sentence_index + 1:064x}",
                "delivery_status": "TASK_NODE_SENTENCE_STAGED",
                "replayed": False,
                "request_id": "request-stream",
                "graph_id": "graph-stream",
                "graph_revision": 1,
                "node_id": "node-stream",
                "attempt_id": "attempt-stream",
                "lease_id": "lease-stream",
                "lease_owner_id": "owner-stream",
                "fencing_token": 1,
                "node_grant_ref": "governor-grant://child/stream",
                "self_id": "natsume_ai",
                "relationship_subject_id": "person-stream",
                "workspace_id": "workspace-stream",
                "plan_id": str(plan["plan_id"]),
                "synchronization_id": str(plan["expression_plan"]["synchronization_id"]),
                "self_affect_revision": int(plan["self_affect_revision"]),
            }

        def record_stream_control(self, *, plan, event_kind, sent_sentence_count, reason_code=""):  # noqa: ANN001
            self.calls.append((str(event_kind), int(sent_sentence_count)))
            return {
                "event_id": "taskchat://" + "f" * 64,
                "event_kind": event_kind,
                "delivery_status": "TASK_NODE_STREAM_RECORDED",
            }

    requests: list[Any] = []

    class GuardedStreamingModel:
        async def stream_with_result(self, request):  # noqa: ANN001
            requests.append(request)
            yield ModelAttemptStreamEvent.delta("我在。")
            target = ProviderTarget("surface-guarded", "provider", "model")
            yield ModelAttemptStreamEvent.final(
                ModelAttemptResult(
                    request_id=request.request_id,
                    success=True,
                    route_decision=RouteDecision(
                        "route-surface-guarded", (target,), "test", max_attempts=1
                    ),
                    attempts=(
                        ModelAttemptRecord(
                            attempt_id="attempt-model-guarded",
                            request_id=request.request_id,
                            route_decision_id="route-surface-guarded",
                            attempt_index=1,
                            status=AttemptStatus.SUCCEEDED,
                            retry_reason="initial",
                            adapter_id="surface-guarded",
                            provider="provider",
                            model_id="model",
                            prompt_id="surface.main.text.v1",
                            prompt_version="1",
                            prompt_digest="b" * 64,
                            started_at_ms=1,
                            ended_at_ms=2,
                            latency_ms=1,
                            metadata={
                                "provider_budget_measurements": {
                                    "token_count": 8,
                                    "completion_tokens": 4,
                                    "model_cost_micros": 16,
                                }
                            },
                        ),
                    ),
                    response=ProviderResponse(
                        content="我在。",
                        usage={
                            "total_tokens": 8,
                            "completion_tokens": 4,
                            "model_cost_micros": 16,
                        },
                    ),
                    final_target=target,
                )
            )

    def unexpected_life_write(**_kwargs: Any) -> dict[str, Any]:
        pytest.fail("governed streaming must not write Life before Task Acceptance")

    monkeypatch.setattr(
        "host_brain.surface_runtime.delivery_audit.submit_life_command",
        unexpected_life_write,
    )
    authority = RecordingTaskDeliveryAuthority()
    guard = ModelBudgetGuard(
        budget_id="budget://stream",
        graph_id="graph-stream",
        graph_revision=1,
        node_id="node-stream",
        attempt_id="attempt-stream",
        deadline_ms=4_000,
        max_provider_attempts=1,
        max_token_count=64,
        max_output_tokens=64,
        max_model_cost_micros=1_000,
    )
    agent_input = _agent_input()
    agent_input.defer_effects_to_task_runtime = True
    agent_input.governed_delivery_authority = authority
    agent_input.trace_stage_meta = {"governed_model_budget": guard.as_dict()}
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: GuardedStreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(agent_input, ports=ports)
    ]

    sentences = [event for event in events if event.get("phase") == "SENTENCE"]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert [event["delivery_status"] for event in sentences] == [
        "TASK_NODE_SENTENCE_STAGED"
    ]
    assert not [event for event in events if event.get("phase") == "DELIVERY"]
    assert authority.calls == [
        ("PLAN_FROZEN", -1),
        ("SENTENCE_STAGED", 0),
        ("STREAM_COMPLETED", 1),
    ]
    assert commit["decision"] == "APPROVE"
    assert commit["acceptance_effect_intents"][0]["effect_type"] == (
        "life.foreground_completed"
    )
    assert commit["audit_refs"]["surface_main_chain"]["mode"] == (
        "governed_node_sentence_stream"
    )
    assert requests[0].metadata["model_node_id"] == "node-stream"


@pytest.mark.asyncio
async def test_governed_unseen_stream_failure_is_a_durable_clean_reject(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    """An unexposed provider failure must not become an unknown-effect replay."""

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class RecordingTaskDeliveryAuthority(TaskBoundChatDeliveryAuthority):
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def stage_plan(self, _plan):  # noqa: ANN001
            self.calls.append(("PLAN_FROZEN", -1))
            return {
                "event_id": "taskchat://" + "a" * 64,
                "delivery_status": "TASK_NODE_STREAM_RECORDED",
                "replayed": False,
            }

        def record_stream_control(
            self,
            *,
            plan,
            event_kind,
            sent_sentence_count,
            reason_code="",
        ):  # noqa: ANN001
            del plan, reason_code
            self.calls.append((str(event_kind), int(sent_sentence_count)))
            return {
                "event_id": "taskchat://" + "f" * 64,
                "event_kind": event_kind,
                "delivery_status": "TASK_NODE_STREAM_RECORDED",
            }

    class FailingStreamingModel:
        async def stream_with_result(self, _request):  # noqa: ANN001
            raise ModelStreamProtocolError(
                "provider final result is malformed",
                code="MODEL_STREAM_RESULT_INVALID",
            )
            yield ModelAttemptStreamEvent.delta("unreachable")  # pragma: no cover

    def unexpected_life_write(**_kwargs: Any) -> dict[str, Any]:
        pytest.fail("governed streaming must not write Life before Task Acceptance")

    monkeypatch.setattr(
        "host_brain.surface_runtime.delivery_audit.submit_life_command",
        unexpected_life_write,
    )
    authority = RecordingTaskDeliveryAuthority()
    guard = ModelBudgetGuard(
        budget_id="budget://unseen-stream-failure",
        graph_id="graph-stream",
        graph_revision=1,
        node_id="node-stream",
        attempt_id="attempt-stream",
        deadline_ms=4_000,
        max_provider_attempts=1,
        max_token_count=64,
        max_output_tokens=64,
        max_model_cost_micros=1_000,
    )
    agent_input = _agent_input()
    agent_input.defer_effects_to_task_runtime = True
    agent_input.governed_delivery_authority = authority
    agent_input.trace_stage_meta = {"governed_model_budget": guard.as_dict()}
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: FailingStreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(agent_input, ports=ports)
    ]

    assert not [event for event in events if event.get("phase") == "SENTENCE"]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert commit["decision"] == "REJECT"
    assert commit["reason_code"] == "MODEL_STREAM_RESULT_INVALID"
    assert commit["audit_refs"]["durability"] == "fail_closed_before_visibility"
    assert authority.calls == [
        ("PLAN_FROZEN", -1),
        ("STREAM_INTERRUPTED", 0),
    ]


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


@pytest.mark.asyncio
async def test_native_stream_repairs_unprovided_work_state_before_visibility(
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
            yield "行，我这边盯着给你最短修复路径。"

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
    agent_input = _agent_input()
    agent_input.user_message = "先帮我看堆栈。"
    agent_input.render_policy = {
        "primary_topic": "technical_task",
        "interaction_move": "solve",
        "response_goal": "根据堆栈给出最短修复",
        "response_form": "text",
        "max_questions": 0,
    }
    ports = surface_runtime_ports_factory(
        model_attempt_service=lambda: StreamingModel(),
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(agent_input, ports=ports)
    ]
    sentences = [
        str(event.get("sentence") or "")
        for event in events
        if event.get("phase") == "SENTENCE"
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert sentences
    assert all("盯着" not in sentence and "后台" not in sentence for sentence in sentences)
    assert any("堆栈" in sentence or "报错" in sentence for sentence in sentences)
    assert commit["decision"] == "APPROVE"
    repairs = commit["audit_refs"]["trace_stage_meta"][
        "native_sentence_hard_gate_repairs"
    ]
    assert repairs[0]["reason_codes"] == ["UNPROVIDED_WORK_STATE_CLAIM"]


@pytest.mark.asyncio
async def test_native_stream_does_not_treat_reply_level_question_budget_as_sentence_gate(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    submitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    monkeypatch.setattr(
        "host_brain.persona.surface_runtime._surface_hard_gate_violations",
        lambda _request, _text: ("QUESTION_BUDGET_PROMPTING_USER",),
    )

    class StreamingModel:
        async def stream(self, _request):  # noqa: ANN001
            yield "要不要先缓一下？"

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
    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(
            _agent_input(),
            ports=surface_runtime_ports_factory(
                model_attempt_service=lambda: StreamingModel(),
            ),
        )
    ]

    sentences = [
        event for event in events if event.get("phase") == "SENTENCE"
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert [event["sentence"] for event in sentences] == ["要不要先缓一下？"]
    assert commit["decision"] == "APPROVE"
    assert "native_sentence_hard_gate_repairs" not in commit["audit_refs"][
        "trace_stage_meta"
    ]


@pytest.mark.asyncio
async def test_native_stream_repairs_low_info_meeting_guess_before_visibility(
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
            yield "开完了啊，那正好。下午的会最磨人，出来透口气吧。"

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

    monkeypatch.setattr(
        "host_brain.surface_runtime.delivery_audit.submit_life_command",
        _submit,
    )
    agent_input = _agent_input()
    agent_input.user_message = "刚刚开会结束了。"
    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(
            agent_input,
            ports=surface_runtime_ports_factory(
                model_attempt_service=lambda: StreamingModel(),
            ),
        )
    ]

    sentences = [
        event["sentence"] for event in events if event.get("phase") == "SENTENCE"
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert sentences == ["开完了啊，那正好。", "开完会了啊。那先歇口气。"]
    assert commit["decision"] == "APPROVE"
    repairs = commit["audit_refs"]["trace_stage_meta"][
        "native_sentence_hard_gate_repairs"
    ]
    assert repairs[0]["reason_codes"] == ["LOW_INFO_SCENE_STATE_GUESS"]


@pytest.mark.asyncio
async def test_native_stream_repairs_repair_restatement_pressure_before_visibility(
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
            yield "行，那我不装。你说，我听着。"

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

    monkeypatch.setattr(
        "host_brain.surface_runtime.delivery_audit.submit_life_command",
        _submit,
    )
    agent_input = _agent_input()
    agent_input.user_message = "别装懂。"
    agent_input.render_policy = {
        "primary_topic": "relationship_repair",
        "interaction_move": "repair",
        "response_goal": "承认没有接住，不要求用户重新说明",
        "response_form": "text",
        "max_questions": 0,
    }
    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(
            agent_input,
            ports=surface_runtime_ports_factory(
                model_attempt_service=lambda: StreamingModel(),
            ),
        )
    ]

    sentences = [
        event["sentence"] for event in events if event.get("phase") == "SENTENCE"
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert sentences
    assert all("你说" not in sentence and "我听着" not in sentence for sentence in sentences)
    assert commit["decision"] == "APPROVE"
    repairs = commit["audit_refs"]["trace_stage_meta"][
        "native_sentence_hard_gate_repairs"
    ]
    assert repairs[0]["reason_codes"] == [
        "RELATIONAL_REPAIR_SOLICITS_RESTATEMENT"
    ]


@pytest.mark.asyncio
async def test_affective_evidence_gate_stages_expression_before_rule_reply_visibility(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    """A perception-boundary reply must not bypass Natsume's frozen plan."""

    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

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
    agent_input = _agent_input()
    agent_input.user_message = "你觉得这几本书怎么样？"

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(
            agent_input,
            ports=surface_runtime_ports_factory(),
        )
    ]

    command_types = [str(item["command_type"]) for item in submitted]
    sentences = [event for event in events if event.get("phase") == "SENTENCE"]
    deliveries = [event for event in events if event.get("phase") == "DELIVERY"]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert command_types[0] == "affective_turn_planned"
    assert command_types[-1] == "foreground_completed"
    assert command_types[1:-1]
    assert set(command_types[1:-1]) == {"surface_sentence_delivery_intent"}
    assert sentences
    assert [event["delivery_state"] for event in deliveries[:-1]] == [
        "HOST_PROTOCOL_STAGED"
    ] * len(sentences)
    assert deliveries[-1]["delivery_state"] == "HOST_RELEASE_STAGED"
    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_PERCEPTION_EVIDENCE_REQUIRED"
    assert commit["audit_refs"]["path"] == "surface_reply:canonical_affective_deterministic"
    assert commit["audit_refs"]["surface_main_chain"]["streaming"] is False
    assert (
        commit["audit_refs"]["surface_main_chain"]["provider_usage_status"]
        == "NO_MODEL_RULE_FALLBACK"
    )
    assert len(command_types[1:-1]) == len(sentences)
    synchronization_id = submitted[0]["payload"]["affective_turn_plan"]["expression_plan"][
        "synchronization_id"
    ]
    assert {event["synchronization_id"] for event in sentences} == {synchronization_id}
    assert submitted[1]["metadata"]["source"] == "surface_affective_deterministic_delivery"
    assert submitted[-1]["payload"]["expression_delivery_intents"] == []
    assert submitted[-1]["payload"]["expression_delivery_controls"]


@pytest.mark.asyncio
async def test_affective_model_failure_fallback_keeps_plan_and_expression_protocol(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    """A safe sub-agent fallback remains an accepted affective response."""

    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    class FailingModel:
        async def complete(self, _request):  # noqa: ANN001
            raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

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
    agent_input = _agent_input()
    agent_input.user_message = "请告诉我授权任务有没有完成。"
    agent_input.emotion_context["affective_life"]["conflict"] = True
    agent_input.sub_agent_context = SimpleNamespace(
        agent_id="pentest_agent",
        task="只读检查授权结果",
        result_text="检查完成：授权目标当前不可访问，没有执行任何写入动作。",
        success=True,
        latency_ms=12,
        error="",
        payload={"audit_refs": {"skill_exec": "skill_exec::request-stream::pentest_agent"}},
    )

    events = [
        _parse_sse(raw)
        async for raw in surface_main_chain_sse_generator(
            agent_input,
            ports=surface_runtime_ports_factory(
                model_attempt_service=lambda: FailingModel(),
            ),
        )
    ]

    command_types = [str(item["command_type"]) for item in submitted]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    sentences = [event for event in events if event.get("phase") == "SENTENCE"]

    assert command_types == [
        "affective_turn_planned",
        "surface_sentence_delivery_intent",
        "foreground_completed",
    ]
    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FALLBACK"
    assert commit["reply_envelope"]["source"] == "surface_main_chain_affective_fallback"
    assert commit["audit_refs"]["surface_main_chain"]["streaming"] is False
    assert "授权目标当前不可访问" in commit["narrative"]
    assert sentences
    assert submitted[-1]["payload"]["surface_result"]["mode"] == "deterministic_affective_delivery"
    assert submitted[-1]["payload"]["expression_delivery_intents"] == []
    assert submitted[-1]["payload"]["expression_delivery_controls"]
