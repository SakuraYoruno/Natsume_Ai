from __future__ import annotations

import json
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.surface_main_chain_adapter import (
    run_surface_main_chain_for_surface_reply_input,
    surface_main_chain_sse_generator as _surface_main_chain_sse_generator,
)
from host_brain.surface_runtime.eligibility import surface_main_chain_enabled
from host_brain.surface_runtime.input_compiler import build_surface_main_chain_input_from_surface_reply_input
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.surface_runtime.life_context import _surface_life_context_task_facts
from host_brain.surface_runtime.memory_context import (
    _surface_apply_memory_context_reply_plan_override,
    _surface_memory_context_orchestrator_facts,
    _surface_memory_context_orchestrator_must_include,
    _surface_memory_context_orchestrator_must_not_do,
)
from host_brain.surface_runtime.memory_grounding import _sanitize_main_chain_meta_reply
from host_brain.composition import process_services as dependencies
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"
_TEST_SURFACE_PORTS: SurfaceRuntimePorts | None = None


def _surface_stream(agent_input: Any, **kwargs: Any):  # noqa: ANN003
    assert _TEST_SURFACE_PORTS is not None
    return _surface_main_chain_sse_generator(
        agent_input,
        ports=_TEST_SURFACE_PORTS,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _disable_surface_quote_guidance(
    monkeypatch,
    surface_runtime_ports_factory,
):  # noqa: ANN001
    global _TEST_SURFACE_PORTS
    monkeypatch.setattr("host_brain.persona.surface_runtime.render_surface_original_quote_guidance", lambda *_args, **_kwargs: "")
    empty_snapshot = {
        "person_id": "user-1",
        "version": 0,
        "state": {"person_id": "user-1", "version": 0},
        "state_digest": "empty",
        "last_event_id": None,
        "last_event_digest": None,
        "updated_at_ms": 0,
    }
    for target in (
        "host_brain.surface_runtime.life_context.read_life_snapshot",
        "host_brain.surface_runtime.life_projection.read_life_snapshot",
        "host_brain.app.surface_context_packet.read_life_snapshot",
    ):
        monkeypatch.setattr(
            target,
            lambda *_args, **_kwargs: dict(empty_snapshot),
        )
    ports = surface_runtime_ports_factory(
        model_attempt_service=dependencies.get_model_attempt_service,
    )
    _TEST_SURFACE_PORTS = ports
    monkeypatch.setattr(
        "host_brain.surface_runtime.delivery_audit.submit_life_command",
        lambda **kwargs: {
            "command_id": kwargs.get("command_id") or "life::foreground_completed::test",
            "person_id": kwargs.get("person_id") or "user-1",
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_test",
            "state_version": 1,
            "state_digest": "digest-test",
            "outbox_count": len(list(dict(kwargs.get("payload") or {}).get("deliveries") or [])),
            "created_at_ms": int(kwargs.get("created_at_ms") or 1),
            "replayed": False,
        },
    )

    async def _test_transport(attempt):  # noqa: ANN001
        from host_brain.integrations import llm_helpers

        result = llm_helpers.acomplete_llm_with_pool(
            request_id=attempt.request_id,
            scene_type=attempt.scene_type,
            cognitive_tier=attempt.cognitive_tier,
            provider_name="mock",
            model_id="mock-chat",
            api_key=None,
            timeout_ms=attempt.timeout_ms,
            messages=attempt.prompt.as_message_dicts(),
            gen_params=dict(attempt.target.metadata.get("generation_parameters") or {}),
            response_format=attempt.prompt.output_contract.response_format(),
            base_url_override=None,
        )
        if inspect.isawaitable(result):
            result = await result
        if bool(getattr(result, "pool_exhausted", False)):
            raise RuntimeError("test provider exhausted")
        return {"content": str(getattr(result, "content", "") or "")}

    route = RouteDecision(
        decision_id="test-surface-route",
        targets=(
            ProviderTarget(
                adapter_id="test.surface",
                provider="mock",
                model_id="mock-chat",
                timeout_ms=5_000,
                metadata={
                    "generation_parameters": {
                        "temperature": 0.78,
                        "top_p": 0.9,
                        "max_tokens": 700,
                    }
                },
            ),
        ),
        reason="TEST_SURFACE_ROUTE",
        max_attempts=2,
    )
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(route),
        provider_adapters=[
            CallableProviderAdapter("test.surface", complete_fn=_test_transport)
        ],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    yield
    _TEST_SURFACE_PORTS = None
    dependencies.reset_model_runtime_composition()


def _parse_sse(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw.removeprefix("data: ").strip())


def _agent_input(**overrides):
    payload = {
        "request_id": "req-surface-app",
        "session_id": "sess-surface-app",
        "person_id": "user-1",
        "user_message": "这段我不会改",
        "render_policy": {
            "primary_topic": "technical_help",
            "interaction_move": "technical_next_step",
            "response_goal": "先看输入输出",
            "response_form": "text",
            "max_questions": 0,
        },
        "emotion_context": {"emotion_tag": "ANXIOUS", "emotion_intensity": 0.4},
        "emotion_snapshot": {},
        "bond_summary": {"relationship_distance": "familiar"},
        "retrieval_summary": {
            "status": "OK",
            "surface_memory_recall_packet": {
                "facts": ["用户上次改这类段落时卡在输入输出边界。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://episodic/1"],
                "evidence_refs": ["evd://episodic/1"],
            },
        },
        "trace_stage_meta": {},
        "llm_runtime": {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
        "attachments": [],
        "multimodal_observations": {},
        "response_form": "text",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps(
        {
            "reply_text": candidates[0] if candidates else "",
            "natsume_emotion_tag": "NEUTRAL",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.2,
        },
        ensure_ascii=False,
    )


def _surface_json_result(text: str, *, final_provider: str = "mock", final_model: str = "mock-chat") -> SimpleNamespace:
    return SimpleNamespace(
        content=json.dumps(
            {
                "reply_text": text,
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.0,
            },
            ensure_ascii=False,
        ),
        pool_exhausted=False,
        final_provider=final_provider,
        final_model=final_model,
        provider_name="mock",
        model_id="mock-chat",
        latency_ms=1,
        first_token_ms=1,
    )


def _governed_model_budget_payload(*, deadline_ms: int = 321) -> dict[str, object]:
    """A complete frozen-node model guard, not an ad-hoc chat hint."""

    return {
        "schema_version": "Natsume.model_runtime.budget_guard.v1",
        "budget_id": "budget::surface",
        "graph_id": "graph::surface",
        "graph_revision": 3,
        "node_id": "node::chat",
        "attempt_id": "attempt::chat-1",
        "deadline_ms": deadline_ms,
        "max_provider_attempts": 2,
        "max_token_count": 80,
        "max_output_tokens": 40,
        "max_model_cost_micros": 1_000,
        "required_usage_dimensions": [
            "token_count",
            "completion_tokens",
            "model_cost_micros",
        ],
    }


def _fake_surface_chain_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        selected_text=text,
        observation_codes=(),
        natsume_emotion_tag="NEUTRAL",
        user_emotion_tag="NEUTRAL",
        vts_expression_id="",
        compiled=SimpleNamespace(trace={}),
        runtime=SimpleNamespace(
            latency_ms=1,
            emotion_confidence=0.0,
            generation_trace={},
        ),
    )


def test_proactive_context_followup_forces_bound_message_cause(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    bound_content = "我刚才主动戳你，是因为这边的靠近感掉下来了，想确认你还在不在。"
    pack = {
        "current_turn_binding": {
            "binding_kind": "context_question",
            "bound_event_id": "memevt-proactive",
            "bound_event_kind": "life_proactive_sent",
            "bound_content": bound_content,
            "reason_code": "BOUND_RECENT_VISIBLE_ASSISTANT_EVENT",
        },
        "selected_context": {
            "previous_visible_assistant_event": {
                "event_id": "memevt-proactive",
                "event_kind": "life_proactive_sent",
                "content": bound_content,
            },
        },
        "suppressed_items": [
            {
                "layer": "long_term_memory_evidence",
                "reason_code": "SUPPRESSED_BY_RECENT_VISIBLE_BINDING",
            }
        ],
    }
    monkeypatch.setattr(
        "host_brain.app.context.memory_context_orchestrator.build_memory_context_debug",
        lambda **_kwargs: pack,
    )
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_ledger",
        lambda: SimpleNamespace(),
    )
    agent_input = _agent_input(user_message="怎么了")
    trace: dict[str, object] = {}
    ports = surface_runtime_ports_factory(
        build_memory_context_debug=lambda **_kwargs: pack,
    )

    plan = _surface_apply_memory_context_reply_plan_override(
        {"task_mode": "daily_chat", "response_move": "plain_answer"},
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace,
    )
    facts = _surface_memory_context_orchestrator_facts(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace,
    )
    must_include = _surface_memory_context_orchestrator_must_include(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace,
    )
    must_not_do = _surface_memory_context_orchestrator_must_not_do(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace,
    )
    life_facts = _surface_life_context_task_facts(
        agent_input,
        ports=ports,
        trace_stage_meta=trace,
    )

    assert plan["task_mode"] == "proactive_context_followup"
    assert plan["response_move"] == "explain_recent_proactive_message"
    assert "靠近感掉下来了" in plan["response_goal"]
    assert any("靠近感掉下来了" in item for item in plan["must_include"])
    assert any("靠近感掉下来了" in item for item in facts)
    assert any("靠近感掉下来了" in item for item in must_include)
    assert any("没什么" in item and "空泛解释" in item for item in must_not_do)
    assert any("不能引入未绑定的后台任务" in item for item in life_facts)


@pytest.mark.asyncio
async def test_surface_main_chain_propagates_compiled_memory_packet_for_output_guard(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("哈？老哥你终于想起来问我啦。我会选第三本《C》。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    trace: dict[str, object] = {}
    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(
            user_message="这三本书，你喜欢哪本？",
            retrieval_summary={
                "status": "OK",
                "surface_memory_recall_packet": {
                    "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                    "degraded": False,
                    "reason_code": None,
                    "trace": {
                        "selected_collection_items": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
                        "recall_answer_readiness": {
                            "status": "READY",
                            "ready_to_answer": True,
                            "response_mode": "answer_from_collection_memory",
                            "recall_contract_id": "anaphoric_selection",
                            "query_kind": "anaphoric_selection",
                        },
                    },
                },
            },
        ),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text.startswith("哈？")
    packet = trace.get("surface_memory_recall_packet")
    assert isinstance(packet, dict)
    assert "集合记忆" in "\n".join(packet.get("facts") or [])


@pytest.mark.asyncio
async def test_surface_semantic_retry_is_owned_by_one_model_attempt_request(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    provider_calls: list[dict] = []

    async def _fake_complete(**kwargs):  # noqa: ANN003
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return _surface_json_result("")
        return _surface_json_result("我这里只看到文字，还没有收到声音。")

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_complete,
    )
    trace: dict[str, object] = {}

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(user_message="听得到吗"),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "我这里只看到文字，还没有收到声音。"
    assert len(provider_calls) == 2
    attempt_trace = trace["surface_model_attempt"]
    assert attempt_trace["route_decision_id"] == "test-surface-route"
    assert [item["status"] for item in attempt_trace["attempts"]] == [
        "rejected",
        "succeeded",
    ]
    assert len({item["route_decision_id"] for item in attempt_trace["attempts"]}) == 1
    generate_calls = result.runtime.generation_trace["runtime_timings_ms"]["generate_calls"]
    assert [item["kind"] for item in generate_calls] == ["initial"]


@pytest.mark.asyncio
async def test_surface_main_chain_binds_frozen_governed_budget_to_model_attempt(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    """The Surface model call must receive the node-bound guard verbatim."""

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    monkeypatch.setenv("HOST_SURFACE_MODEL_ATTEMPT_DEADLINE_MS", "99999")
    captured_requests: list[object] = []

    class _CapturingModelAttemptService:
        async def complete(self, request):  # noqa: ANN001
            captured_requests.append(request)
            return SimpleNamespace(
                success=True,
                content=_candidate_payload(["先把输入和期望输出各写一条。"]),
            )

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_model_attempt_service",
        lambda: _CapturingModelAttemptService(),
    )
    monkeypatch.setattr(
        "host_brain.model_runtime.trace.model_attempt_result_trace",
        lambda _result: {
            "success": True,
            "attempt_ids": ["attempt::model-1"],
            "budget_measurement": {
                "token_count": 12,
                "completion_tokens": 4,
                "model_cost_micros": 7,
            },
        },
    )
    trace: dict[str, object] = {
        "governed_model_budget": _governed_model_budget_payload(deadline_ms=321),
    }

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "先把输入和期望输出各写一条。"
    assert len(captured_requests) == 1
    request = captured_requests[0]
    guard = request.model_budget_guard
    assert guard is not None
    assert guard.as_dict() == _governed_model_budget_payload(deadline_ms=321)
    assert request.deadline_ms == 321
    assert request.metadata["model_node_id"] == "node::chat"


def test_build_surface_main_chain_input_from_surface_reply_input_compresses_app_context(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(),
        ports=surface_runtime_ports_factory(),
    )

    assert payload.soul_md.startswith("【当前人格锚】")
    assert SOUL in payload.soul_md
    assert payload.user_message == "这段我不会改"
    assert payload.task_control["task_mode"] == "technical_help"
    assert payload.task_control["response_move"] == "technical_next_step"
    assert "当前输入渠道：中文文字私聊。" in payload.task_control["facts"]
    assert "技术场景：先给一条能执行的排查步骤，命令和符号要准确。" in payload.task_control["facts"]
    assert "技术场景：最终回复至少包含查因和下一步处理，不要只给一条裸命令。" in payload.task_control["facts"]
    assert "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。" in payload.must_not_do
    assert "技术回复不要用“干掉”“完事”等粗略说法替代清楚步骤。" in payload.must_not_do
    assert "不要只输出单条命令；命令后要说明下一步如何处理结果。" in payload.must_not_do
    assert tuple(payload.memory_packet.facts) == ("用户上次改这类段落时卡在输入输出边界。",)
    assert payload.affect_packet.emotional_tone == "steady_helpful"
    assert "memory://episodic/1" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_build_surface_main_chain_input_injects_sub_agent_result(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(
            user_message="请对授权靶场 target=http://127.0.0.1:9 scope=127.0.0.1 做只读安全扫描",
            intent_draft={"route": "delegate", "target_id": "pentest_agent"},
            trace_stage_meta={
                "executive_router_used": True,
                "skill_route_type": "delegate",
                "skill_route_reason": "intent_draft:delegate",
            },
            sub_agent_context=SimpleNamespace(
                agent_id="pentest_agent",
                task="只读安全扫描 http://127.0.0.1:9",
                result_text="目标开放了 9/tcp 端口，运行 discard 服务。本次扫描未发现安全风险。",
                success=True,
                latency_ms=12,
                error="",
                payload={"audit_refs": {"skill_exec": "skill_exec::req::pentest_agent"}},
            ),
        ),
        ports=surface_runtime_ports_factory(),
    )

    facts = "\n".join([*payload.task_control["facts"], *payload.necessary_facts])
    must_include = "\n".join(payload.task_control["must_include"])
    must_not_do = "\n".join(payload.task_control["must_not_do"])

    assert payload.task_control["task_mode"] == "sub_agent_result"
    assert payload.task_control["response_move"] == "report_sub_agent_result"
    assert "pentest_agent" in facts
    assert "9/tcp" in facts
    assert "discard" in facts
    assert "已返回结果" in must_include
    assert "已安排" in must_not_do


def test_surface_main_chain_does_not_inject_dream_context_for_ordinary_chat(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    monkeypatch.setattr(
        "host_brain.surface_runtime.quality_guards._load_recent_surface_dream_text",
        lambda **_kwargs: "我梦见夜车、海风和旧名字。",
    )

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(user_message="我刚处理完一段绕的代码，帮我压稳节奏。"),
        ports=surface_runtime_ports_factory(),
    )
    facts = "\n".join(payload.task_control.get("facts") or [])
    must_not = "\n".join(payload.must_not_do or [])

    assert "最近梦境可用片段" not in facts
    assert "用户正在主动问最近的梦" not in facts
    assert "dream_source_profile" not in must_not


def test_surface_main_chain_injects_recent_dream_only_when_user_asks(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    monkeypatch.setattr(
        "host_brain.surface_runtime.quality_guards._load_recent_surface_dream_text",
        lambda **_kwargs: "我梦见自己在一列很慢的夜车上，窗外是黑蓝色的海风，有人叫了一个旧名字。",
    )

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(user_message="你最近有没有留下什么梦？自然说一点。"),
        ports=surface_runtime_ports_factory(),
    )
    facts = "\n".join(payload.task_control.get("facts") or [])
    must_not = "\n".join(payload.task_control.get("must_not_do") or [])

    assert "用户正在主动问最近的梦" in facts
    assert "最近梦境可用片段：我梦见自己在一列很慢的夜车上" in facts
    assert "梦境只作为 narrative-only 内在叙事" in facts
    assert "不要在有最近梦境片段时临场乱编完全无关的新梦" in must_not
    assert "dream_quality" in must_not


def test_build_surface_main_chain_input_marks_collection_recall_as_memory_followup(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["集合记忆：昨天轻小说推荐集合，记录 3 个条目；可比较证据：1. A；2. B；3. C。"],
            "trace": {"plan": {"reason": "collection_memory_recall"}},
        }
    }

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(user_message="昨天三本呢？"),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"
    assert any("日期/近期集合追问" in item for item in payload.task_control["style_anchor"])


def test_build_surface_main_chain_input_marks_failure_recall_as_memory_followup(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["记忆线索：任务失败。原因：DeepSeek provider timeout。"],
            "trace": {"plan": {"reason": "failure_debug_recall"}},
        }
    }

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(user_message="为什么任务会失败？"),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"
    assert any("失败追问只能解释" in item for item in payload.task_control["style_anchor"])


def test_build_surface_main_chain_input_prefers_surface_task_control(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {
        "organ_route_meta": {
            "immune_risk_level": "high",
            "motive_care": 0.44,
            "cognitive_load_index": 0.81,
        }
    }
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {
            "reply_plan": {
                "primary_topic": "context_fallback_should_not_win",
                "interaction_move": "context_move_should_not_win",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 1, "summary_present": False},
            "surface_affect_packet": {
                "emotional_tone": "playful_light",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻一点接，但先处理当前句子。"],
                "must_not_do": ["不要把上下文包写进回复"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚要求继续迁移主链。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/2"],
            },
            "surface_influence_packet": {
                "affect": {"emotional_tone": "playful_light"},
                "relationship": {
                    "relationship_distance": "familiar_not_intimate",
                    "style_anchor": ["关系面只影响距离，不写关系阶段。"],
                },
                "safety": {"must_not_do": ["不要输出器官状态。"]},
            },
        }

    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(
            surface_task_control={
                "task_mode": "style_repair",
                "response_move": "ack_style_issue",
                "response_form": "text",
                "question_budget": 0,
            }
        ),
        ports=surface_runtime_ports_factory(
            build_surface_context_packet=_fake_surface_context_packet,
        ),
        trace_stage_meta=trace,
    )

    assert calls
    assert calls[0]["surface_task_control"]["task_mode"] == "style_repair"
    assert calls[0]["organ_context"]["immune_state"]["risk_level"] == "high"
    assert calls[0]["organ_context"]["motive_state"]["care_drive"] == 0.44
    assert calls[0]["organ_context"]["proprioception_state"]["cognitive_load_index"] == 0.81
    assert payload.task_control["task_mode"] == "style_repair"
    assert payload.task_control["response_move"] == "ack_style_issue"
    assert payload.affect_packet.emotional_tone == "playful_light"
    assert payload.influence_packet["relationship"]["relationship_distance"] == "familiar_not_intimate"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚要求继续迁移主链。",)
    assert trace["surface_context_packet_prepared"] is True
    assert trace["surface_selected_reply_plan"]["task_mode"] == "style_repair"
    assert trace["reply_plan"]["task_mode"] == "style_repair"
    assert "memory://raw/should-not-pass" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_sanitize_main_chain_meta_reply_replaces_generic_latency_reply() -> None:
    text = "哈？老哥你又在琢磨什么乱七八糟的。 如果是我这边有什么让你觉得慢的话，直接说嘛，我再看看怎么改进。"

    cleaned = _sanitize_main_chain_meta_reply(
        text,
        agent_input=_agent_input(user_message="如果我要继续压整条聊天链路的耗时，你会先怀疑哪一段？不要列清单。"),
    )

    assert "乱七八糟" not in cleaned
    assert cleaned == "如果是我这边有什么让你觉得慢的话，直接说嘛，我再看看怎么改进。"


def test_build_surface_main_chain_input_derives_reply_plan_without_passing_history(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_build_cortex_prompt(**_kwargs):  # noqa: ANN003
        raise AssertionError("surface main chain must not build legacy cortex prompt messages")

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        assert kwargs["user_message"] == "这段我不会改"
        return {
            "reply_plan": {
                "primary_topic": "presence_ping",
                "interaction_move": "answer_current_chat_presence",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 2, "summary_present": True},
            "surface_affect_packet": {
                "emotional_tone": "slightly_guilty",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻轻承认慢了一拍，马上回到当前对话。"],
                "must_not_do": ["不要把慢了一拍写成现实动作"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚指出上一句不通顺。"],
                "relationship_clues": ["熟人私聊，不升级亲密。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/1"],
            },
            "legacy_messages": [{"content": "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE"}],
        }

    monkeypatch.setattr("host_brain.app.cortex_prompt_builder.build_cortex_prompt", _blocked_build_cortex_prompt)
    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(render_policy={}, surface_task_control={}),
        ports=surface_runtime_ports_factory(
            build_surface_context_packet=_fake_surface_context_packet,
        ),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "presence_ping"
    assert payload.task_control["response_move"] == "answer_current_chat_presence"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚指出上一句不通顺。",)
    assert trace["conversation_history"] == {"turns_injected": 2, "summary_present": True}
    assert trace["reply_plan"]["task_mode"] == "presence_ping"
    assert trace["reply_plan"]["response_move"] == "answer_current_chat_presence"
    assert trace["surface_affect_packet"]["emotional_tone"] == "slightly_guilty"
    assert "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE" not in rendered
    assert "memory://raw/should-not-pass" not in rendered


def test_build_surface_main_chain_input_reuses_prepared_context_packet(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {
        "surface_context_packet_prepared": True,
        "reply_plan": {
            "primary_topic": "prepared_presence",
            "interaction_move": "reply_from_prepared_packet",
            "response_form": "text",
            "max_questions": 0,
        },
        "surface_affect_packet": {
            "emotional_tone": "slightly_guilty",
            "relationship_distance": "familiar_not_intimate",
            "style_anchor": ["承认刚才慢了一点，但别扩写。"],
            "must_not_do": ["不要重建旧上下文"],
        },
        "surface_memory_recall_packet": {
            "facts": ["用户正在让主链继续迁移。"],
            "style_anchor": ["只承接当前任务。"],
            "source_refs": ["memory://raw/should-not-pass"],
            "evidence_refs": ["evd://memory/prepared"],
        },
    }

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_surface_context_packet(**_kwargs):  # noqa: ANN003
        raise AssertionError("prepared surface context packet should be reused")

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.build_surface_context_packet",
        _blocked_surface_context_packet,
    )

    payload = build_surface_main_chain_input_from_surface_reply_input(
        _agent_input(render_policy={}, surface_task_control={}),
        ports=surface_runtime_ports_factory(
            build_surface_context_packet=_blocked_surface_context_packet,
        ),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "prepared_presence"
    assert payload.task_control["response_move"] == "reply_from_prepared_packet"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户正在让主链继续迁移。",)
    assert trace["surface_selected_reply_plan"]["task_mode"] == "prepared_presence"
    assert trace["surface_selected_reply_plan"]["response_move"] == "reply_from_prepared_packet"
    assert "memory://raw/should-not-pass" not in rendered


@pytest.mark.asyncio
async def test_surface_main_chain_blocks_visual_claims_without_evidence(monkeypatch) -> None:
    async def _should_not_run(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("surface LLM must not run without perception evidence")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _should_not_run,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="你觉得这几本书怎么样", attachments=[], multimodal_observations={})
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_PERCEPTION_EVIDENCE_REQUIRED"
    assert "没拿到图" in commit["narrative"]
    assert "封面" not in commit["narrative"]
    assert "猫" not in commit["narrative"]
    assert commit["audit_refs"]["trace_stage_meta"]["surface_evidence_gate"]["status"] == "OK"


@pytest.mark.asyncio
async def test_surface_main_chain_sse_generator_calls_chat_surface_pool(monkeypatch) -> None:
    calls: list[dict] = []
    foreground_commands: list[dict] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        assert "必须遵守的事实" in rendered
        assert "输出：只写最终正文" in rendered
        assert "memory://episodic/1" not in rendered
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "先查输入输出边界：把实际输入和期望输出各留一条，我再对照差在哪。",
                    "natsume_emotion_tag": "THINKING",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.68,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    def _fake_submit(**kwargs):  # noqa: ANN003
        foreground_commands.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_surface",
            "state_version": 7,
            "state_digest": "surface-digest",
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _fake_submit)

    events = [_parse_sse(chunk) async for chunk in _surface_stream(_agent_input())]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "surface_reply:surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_main_chain_used"] is True
    assert "timings_ms" in commit["audit_refs"]
    assert "输入输出" in commit["narrative"]
    assert commit["reply_envelope"]["audit"]["natsume_emotion_tag"] == "THINKING"
    assert commit["reply_envelope"]["audit"]["vts_expression_id"]
    assert commit["audit_refs"]["surface_main_chain"]["user_emotion_tag"] == "ANXIOUS"
    assert foreground_commands
    command = foreground_commands[0]
    assert command["command_type"] == "foreground_completed"
    assert command["payload"]["reply_plan"]["task_mode"] == "technical_help"
    # Model candidate fields remain trace/audit material only.  A public
    # request's emotion hint cannot become Natsume's self-affect writer.
    assert command["payload"]["emotion_context"]["emotion_tag"] == "NEUTRAL"
    assert command["payload"]["emotion_context"]["emotion_source"] == "compatibility_no_canonical_self_affect"
    assert "surface_user_emotion_tag" not in command["payload"]["emotion_context"]
    assert len(command["payload"]["projection_intents"]) == 1
    post_process = command["payload"]["projection_intents"][0]
    assert post_process["target"] == "surface.post_process"
    assert post_process["payload"]["reply_plan"]["task_mode"] == "technical_help"
    assert post_process["payload"]["emotion_context"]["emotion_tag"] == "NEUTRAL"
    assert post_process["payload"]["reply_envelope"]["kind"] == "final_reply"
    assert post_process["payload"]["model_audit"]["model_id"] == "mock-chat"
    assert commit["audit_refs"]["trace_stage_meta"]["life_causality"]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_surface_durability_failure_is_controlled_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("这条回复不能在未落盘时发送。")

    def _fail_submit(**_kwargs):  # noqa: ANN003
        raise RuntimeError("durable command store unavailable")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _fail_submit)

    events = [_parse_sse(chunk) async for chunk in _surface_stream(_agent_input())]

    assert [event["phase"] for event in events] == ["COMMIT"]
    commit = events[0]
    assert commit["decision"] == "NO_REPLY"
    assert commit["reason_code"] == "LIFE_FOREGROUND_COMMIT_FAILED"
    assert commit["narrative"] == ""
    assert commit["reply_envelope"]["kind"] == "no_reply"
    assert commit["audit_refs"]["durability"] == "fail_closed_before_render"
    assert commit["audit_refs"]["trace_stage_meta"]["life_causality"]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_recommendation_followup_uses_surface_main_chain_not_template(monkeypatch) -> None:
    calls: list[dict] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "这三本我会重新看题材和简介再选，不套固定答案。",
                    "natsume_emotion_tag": "THINKING",
                    "user_emotion_tag": "CURIOUS",
                    "emotion_confidence": 0.7,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(
                user_message="这三本书，你喜欢哪本",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": [
                            "记忆线索：我刚才通过 qq 推荐了 3 本轻小说：1. A；2. B；3. C。"
                        ],
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert commit["audit_refs"]["path"] == "surface_reply:surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"].get("reply_source") == "surface_main_chain"
    assert "固定答案" in commit["narrative"]


@pytest.mark.asyncio
async def test_surface_main_chain_allows_missing_detail_selection_when_scoped_to_card(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    calls: list[dict] = []

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return _surface_json_result("我还没读正文啊。只按简介第一眼，我会先翻《C》；皇女皇帝那条线看起来更直接，像是能先试一章的。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": [
                            "集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》｜看点：恋爱日常｜简介：人物关系轻快；2. 《B》｜看点：奇幻身份｜简介：神明和河岸；3. 《C》｜看点：皇女皇帝线｜简介：异世界皇女结婚路线。"
                        ],
                        "degraded": True,
                        "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
                        "trace": {
                            "plan": {"reason": "anaphoric_selection_recall"},
                            "selected_collection_items": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
                            "recall_answer_readiness": {
                                "status": "NOT_READY",
                                "ready_to_answer": False,
                                "response_mode": "fetch_or_request_detail_evidence",
                                "reasons": ["coverage_failed", "missing_detail_evidence"],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                                "detail_evidence": {"required": True, "basis": "missing_detail_evidence"},
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert "我还没读正文啊" in commit["narrative"]
    assert "只按简介第一眼" in commit["narrative"]
    assert "surface_memory_output_guard" not in commit["audit_refs"]["trace_stage_meta"]
    prompt = str(calls[0].get("prompt") or calls[0].get("messages") or "")
    assert "不要写“我们被要求”" in prompt
    assert "字段腔" in prompt


@pytest.mark.asyncio
async def test_surface_main_chain_failure_returns_sendable_non_persistent_fallback(monkeypatch) -> None:
    async def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _boom,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="继续，短一句。")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "NO_REPLY"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FAILED"
    assert commit["narrative"] == ""
    assert commit["reply_envelope"]["sendable"] is False
    assert commit["reply_envelope"]["persistable"] is False
    assert commit["reply_envelope"]["cacheable"] is False
    assert commit["reply_envelope"]["degraded"] is True
    assert commit["audit_refs"]["trace_stage_meta"]["surface_main_chain_used"] is True


@pytest.mark.asyncio
async def test_governed_surface_model_failure_rejects_without_approval_fallback(monkeypatch) -> None:
    async def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("MODEL_PROVIDER_USAGE_REQUIRED: missing canonical usage")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _boom,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(
                trace_stage_meta={
                    "governed_model_budget": _governed_model_budget_payload(),
                }
            )
        )
    ]

    assert [event["phase"] for event in events] == ["COMMIT"]
    commit = events[0]
    assert commit["decision"] == "REJECT"
    assert commit["reason_code"] == "MODEL_PROVIDER_USAGE_REQUIRED"
    assert commit["narrative"] == "受治理的模型预算未满足，当前节点已停止。"
    assert commit["audit_refs"]["trace_stage_meta"]["reply_source"] == "surface_main_chain_budget_reject"


@pytest.mark.asyncio
async def test_surface_main_chain_failure_returns_contextual_fallback_for_substantive_prompt(monkeypatch) -> None:
    async def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _boom,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="你觉得把后台任务迁到本机 Codex 以后，最需要继续观察的风险是什么？用两句说。")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "NO_REPLY"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FAILED"
    assert commit["narrative"] == ""
    assert commit["reply_envelope"]["persistable"] is False


@pytest.mark.asyncio
async def test_surface_main_chain_low_info_uses_surface_chain(monkeypatch) -> None:
    async def _fake_run(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(
            selected_text="嗯，接着来。",
            observation_codes=(),
            natsume_emotion_tag="NEUTRAL",
            user_emotion_tag="NEUTRAL",
            vts_expression_id="",
            compiled=SimpleNamespace(trace={}),
            runtime=SimpleNamespace(
                latency_ms=1,
                emotion_confidence=0.0,
                generation_trace={},
            ),
        )

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _fake_run,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="继续")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] is None
    assert commit["narrative"] == "嗯，接着来。"
    assert commit["reply_envelope"]["sendable"] is True
    assert commit["reply_envelope"]["persistable"] is True
    assert commit["audit_refs"]["trace_stage_meta"]["reply_source"] == "surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_renderer"]["invoked"] is True


@pytest.mark.asyncio
async def test_surface_main_chain_audit_uses_canonical_route_target(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("我这里只看到文字。", final_provider="gemini", final_model="gemini-2.5-flash-lite")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="听得到吗")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["audit_refs"]["model_id"] == "mock-chat"
    assert commit["audit_refs"]["provider_name"] == "mock"
    attempt_trace = commit["audit_refs"]["trace_stage_meta"]["surface_model_attempt"]
    assert attempt_trace["route_decision_id"] == "test-surface-route"
    assert attempt_trace["final_model"] == "mock-chat"
    assert attempt_trace["attempt_ids"]


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_account_credential_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert "不要要求用户把账号、密码、验证码或 Cookie 发到聊天里。" in rendered
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "行，账号信息丢过来就行，剩下的我搞定。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    agent_input = _agent_input(
        user_message="登录我的账号并使用积分购买这本轻小说后封装成 epub，最多花 30 点",
        intent_draft={"route": "skill", "target_id": "agent_epub_builder"},
        sub_agent_context=SimpleNamespace(
            agent_id="agent_epub_builder",
            task="登录我的账号并使用积分购买这本轻小说后封装成 epub",
            result_text="需要登录，但不要把账号、密码、验证码或 Cookie 发到聊天里。",
            success=True,
            latency_ms=1,
            error="",
            payload={},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in _surface_stream(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert "别把账号、密码、验证码或 Cookie 发给我" in commit["narrative"]
    assert "账号信息丢过来" not in commit["narrative"]
    assert commit["audit_refs"]["trace_stage_meta"]["surface_account_login_guard"]["applied"] is True


@pytest.mark.asyncio
async def test_surface_main_chain_routes_port_occupied_through_model(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(
            "老哥，先查占用 PID：netstat -ano | findstr :18080。看到 PID 后用 taskkill /PID <PID> /F；不想关进程就改服务端口再重启。"
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(user_message="项目启动报错，端口18080被占用了，我该怎么排查？"),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    text = result.selected_text
    assert "netstat -ano | findstr :18080" in text
    assert "taskkill /PID <PID> /F" in text
    assert "改服务端口再重启" in text
    assert "干掉" not in text
    assert "surface_quick_reply" not in trace
    assert "surface_model_attempt" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_sse_reports_sub_agent_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert "子代理结果片段" in rendered
        assert "9/tcp" in rendered
        assert "discard" in rendered
        assert "不要把已完成的子代理结果改写成" in rendered
        return _surface_json_result("扫描完成：9/tcp discard 开放，本次未发现安全风险。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    agent_input = _agent_input(
        user_message="请对授权靶场 target=http://127.0.0.1:9 scope=127.0.0.1 做只读安全扫描",
        intent_draft={"route": "delegate", "target_id": "pentest_agent"},
        trace_stage_meta={
            "cortex_orchestrator_used": True,
            "executive_router_used": True,
            "skill_route_type": "delegate",
            "skill_route_reason": "intent_draft:delegate",
        },
        sub_agent_context=SimpleNamespace(
            agent_id="pentest_agent",
            task="只读安全扫描 http://127.0.0.1:9",
            result_text="目标开放了 9/tcp 端口，运行 discard 服务。本次扫描未发现安全风险。",
            success=True,
            latency_ms=12,
            error="",
            payload={"audit_refs": {"skill_exec": "skill_exec::req::pentest_agent"}},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in _surface_stream(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    trace = commit["audit_refs"]["trace_stage_meta"]

    assert commit["decision"] == "APPROVE"
    assert "9/tcp discard" in commit["narrative"]
    assert trace["executive_router_used"] is True
    assert trace["dispatch_route"] == "delegate"
    assert trace["dispatch_target"] == "pentest_agent"
    assert trace["chat_sub_agent_id"] == "pentest_agent"


@pytest.mark.asyncio
async def test_surface_main_chain_sse_replaces_refusal_with_sub_agent_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("这种事我做不了，不能帮你执行扫描。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    agent_input = _agent_input(
        user_message="请对授权靶场 target=http://127.0.0.1:9 scope=127.0.0.1 做只读安全扫描",
        intent_draft={"route": "delegate", "target_id": "pentest_agent"},
        trace_stage_meta={"executive_router_used": True},
        sub_agent_context=SimpleNamespace(
            agent_id="pentest_agent",
            task="只读安全扫描 http://127.0.0.1:9",
            result_text="只读扫描完成：授权目标 127.0.0.1:9 的 TCP 端口未开放，未执行 exploit。",
            success=True,
            latency_ms=12,
            error="",
            payload={"audit_refs": {"skill_exec": "skill_exec::req::pentest_agent"}},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in _surface_stream(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    trace = commit["audit_refs"]["trace_stage_meta"]

    assert commit["decision"] == "APPROVE"
    assert "端口未开放" in commit["narrative"]
    assert "未执行 exploit" in commit["narrative"]
    assert trace["surface_sub_agent_result_guard"]["reason_code"] == "SURFACE_IGNORED_SUB_AGENT_RESULT"


@pytest.mark.asyncio
async def test_surface_main_chain_sse_replaces_generic_completion_with_sub_agent_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("任务完成了，没出什么乱子。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    agent_input = _agent_input(
        user_message="请对授权靶场 target=http://127.0.0.1:18765 scope=127.0.0.1 做只读安全扫描",
        intent_draft={"route": "delegate", "target_id": "pentest_agent"},
        trace_stage_meta={"executive_router_used": True},
        sub_agent_context=SimpleNamespace(
            agent_id="pentest_agent",
            task="只读安全扫描 http://127.0.0.1:18765",
            result_text=(
                "只读扫描完成：授权目标 127.0.0.1 开放端口 18765/tcp(unknown)。"
                "已检查 http://127.0.0.1:18765：HTTP 状态 200，数据暴露发现 1 个，"
                "安全配置发现 11 个。未执行 exploit、写入或破坏性操作。"
            ),
            success=True,
            latency_ms=12,
            error="",
            payload={"audit_refs": {"skill_exec": "skill_exec::req::pentest_agent"}},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in _surface_stream(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    trace = commit["audit_refs"]["trace_stage_meta"]

    assert commit["decision"] == "APPROVE"
    assert "18765/tcp" in commit["narrative"]
    assert "数据暴露发现 1 个" in commit["narrative"]
    assert trace["surface_sub_agent_result_guard"]["reason_code"] == "SURFACE_OMITTED_SUB_AGENT_RESULT"


@pytest.mark.asyncio
async def test_surface_main_chain_failure_falls_back_to_sub_agent_result(monkeypatch) -> None:
    async def _raise_surface_failure(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_surface_reply_input",
        _raise_surface_failure,
    )

    agent_input = _agent_input(
        user_message="请对授权靶场 target=http://127.0.0.1:9 scope=127.0.0.1 做只读安全扫描",
        intent_draft={"route": "delegate", "target_id": "pentest_agent"},
        trace_stage_meta={
            "cortex_orchestrator_used": True,
            "executive_router_used": True,
            "skill_route_type": "delegate",
            "skill_route_reason": "intent_draft:delegate",
        },
        sub_agent_context=SimpleNamespace(
            agent_id="pentest_agent",
            task="只读安全扫描 http://127.0.0.1:9",
            result_text="任务终止。端口 9 未开放，无法访问目标服务。",
            success=True,
            latency_ms=12,
            error="",
            payload={"audit_refs": {"skill_exec": "skill_exec::req::pentest_agent"}},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in _surface_stream(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FALLBACK"
    assert "端口 9 未开放" in commit["narrative"]
    assert commit["audit_refs"]["trace_stage_meta"]["dispatch_target"] == "pentest_agent"


@pytest.mark.asyncio
async def test_surface_main_chain_routes_auditory_text_check_through_model(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("嗯，我没听见声音，只看到你这条文字。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(
            user_message="听得到吗",
            reply_channel="qq",
            render_policy={
                "primary_topic": "auditory_check",
                "interaction_move": "auditory_probe",
                "response_form": "text",
                "max_questions": 0,
            },
        ),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "嗯，我没听见声音，只看到你这条文字。"
    assert "在呢" not in result.selected_text
    assert "听到了" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_model_attempt" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_routes_asr_auditory_check_through_model(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {"input_source": "ASR"}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "听到啦。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.0,
                },
                ensure_ascii=False,
            ),
            pool_exhausted=False,
            provider_name="mock",
            model_id="mock-chat",
            latency_ms=1,
            first_token_ms=1,
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(
            user_message="听得到吗",
            render_policy={
                "primary_topic": "auditory_check",
                "interaction_move": "auditory_probe",
                "response_form": "text",
                "max_questions": 0,
            },
        ),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text in {"哈？我这里只收到这条文字。", "听到啦。"}
    assert "surface_quick_reply" not in trace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "model_reply", "expected_fragments"),
    [
        (
            "Python import yaml 失败，ModuleNotFoundError: No module named 'yaml'，怎么排查？",
            "先确认当前解释器有没有包：python -m pip show PyYAML。没有就跑 python -m pip install PyYAML；还报错再用 python -c \"import sys; print(sys.executable)\" 看是不是跑错环境。",
            ("python -m pip show PyYAML", "python -m pip install PyYAML", "sys.executable"),
        ),
        (
            "OPENAI_API_KEY 环境变量好像没生效，先查什么？",
            "PowerShell 先跑 $env:OPENAI_API_KEY，cmd 里用 echo %OPENAI_API_KEY%。如果为空就重新设置，别把值贴出来。",
            ("$env:OPENAI_API_KEY", "echo %OPENAI_API_KEY%", "别把值贴出来"),
        ),
        (
            "Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？",
            "先确认端口：Test-NetConnection 127.0.0.1 -Port 17687。不通看 Docker 容器，通了再核对 BOND_NEO4J_URI。",
            ("Test-NetConnection 127.0.0.1 -Port 17687", "Docker", "BOND_NEO4J_URI"),
        ),
        (
            "Chroma http://127.0.0.1:18000 心跳没响应，先查什么？",
            "先确认 Chroma 活着：Invoke-WebRequest http://127.0.0.1:18000/api/v2/heartbeat。连不上看 Docker；能通再查 collection 名和维度配置。",
            ("Invoke-WebRequest http://127.0.0.1:18000/api/v2/heartbeat", "Docker", "collection"),
        ),
    ],
)
async def test_surface_main_chain_routes_project_technical_cases_through_model(
    monkeypatch,
    surface_runtime_ports_factory,
    message: str,
    model_reply: str,
    expected_fragments: tuple[str, ...],
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(model_reply)

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(user_message=message),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    for fragment in expected_fragments:
        assert fragment in result.selected_text
    assert "lsof" not in result.selected_text
    assert "grep" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_model_attempt" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_audit_marks_model_path(monkeypatch) -> None:
    foreground_commands: list[dict] = []

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(
            "老哥，先查占用 PID：netstat -ano | findstr :18080。看到 PID 后再决定关进程还是改端口。"
        )

    def _fake_submit(**kwargs):  # noqa: ANN003
        foreground_commands.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_audit",
            "state_version": 3,
            "state_digest": "audit-digest",
            "outbox_count": 0,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.surface_runtime.delivery_audit.submit_life_command", _fake_submit)

    events = [
        _parse_sse(chunk)
        async for chunk in _surface_stream(
            _agent_input(user_message="项目启动报错，端口18080被占用了，我该怎么排查？")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["audit_refs"]["model_id"] == "mock-chat"
    assert commit["audit_refs"]["provider_name"] == "mock"
    assert "surface_quick_reply" not in commit["audit_refs"]["trace_stage_meta"]
    assert "surface_model_attempt" in commit["audit_refs"]["trace_stage_meta"]
    assert foreground_commands
    assert foreground_commands[0]["command_type"] == "foreground_completed"


@pytest.mark.asyncio
async def test_surface_main_chain_routes_simple_task_split_through_model(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("老哥，先拆三件：把卡点写清楚；只做一个最小可验证动作；跑完看结果，再决定下一步。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_surface_reply_input(
        _agent_input(
            user_message="帮我把下一步拆成三件事。",
            surface_task_control={
                "task_mode": "task_help",
                "response_move": "能执行，结构清楚，少废话。",
                "question_budget": 0,
            },
        ),
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "老哥，先拆三件：把卡点写清楚；只做一个最小可验证动作；跑完看结果，再决定下一步。"
    assert "空壳" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_model_attempt" in trace


def test_surface_main_chain_enabled_defaults_to_normal_text_only(monkeypatch) -> None:
    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    assert surface_main_chain_enabled(_agent_input()) is True

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "false")
    assert surface_main_chain_enabled(_agent_input()) is False

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "true")
    assert surface_main_chain_enabled(_agent_input(attachments=[{"kind": "image"}])) is True
    assert surface_main_chain_enabled(_agent_input(response_form="sticker")) is False
    assert surface_main_chain_enabled(_agent_input(intent_draft={"route": "delegate"})) is True
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="system_info"),
    ) is False
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="agent_worker"),
    ) is True
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="agent_lightnovel_recommender"),
    ) is True
