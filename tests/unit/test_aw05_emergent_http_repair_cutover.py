from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.app.handlers import repair_agent_handler
from host_brain.model_runtime import (
    EMERGENT_GROWTH_PATTERN_PROMPT_ID,
    EVOLUTION_DESIRE_PROMPT_ID,
    REPAIR_AGENT_PLAN_PROMPT_ID,
    SELF_REFLECTION_AWARENESS_PROMPT_ID,
    SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID,
    SELF_REFLECTION_MOOD_PROMPT_ID,
    SELF_REFLECTION_STYLE_TREND_PROMPT_ID,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)
from host_brain.persona import evolution_desire_engine, self_reflection
from host_brain.skills.impl.emergent_growth_skill import execute_emergent_growth_weekly


class _CountingRoute:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, _request, _prompt):  # noqa: ANN001
        self.calls += 1
        return RouteDecision(
            decision_id="route::aw05-b3",
            targets=(ProviderTarget("test.aw05-b3", "mock", "b3-model"),),
            reason="TEST_CANONICAL_ROUTE",
            max_attempts=3,
        )


def _build_model_runtime(complete):  # noqa: ANN001
    route = _CountingRoute()
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=route,
        provider_adapters=[CallableProviderAdapter("test.aw05-b3", complete_fn=complete)],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    return route, composition


def test_evolution_desire_ignores_legacy_callable_and_uses_registered_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []

    async def _complete(attempt):  # noqa: ANN001
        attempts.append(attempt)
        return {"content": "我想更自然地说出那些真正放在心上的事。"}

    route, composition = _build_model_runtime(_complete)
    engine = evolution_desire_engine.EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine, "_try_fulfilled_text", lambda *_args: "")
    monkeypatch.setattr(engine, "_try_user_preference_desire", lambda *_args: "")
    monkeypatch.setattr(
        evolution_desire_engine,
        "_load_prompt_templates",
        lambda: {"desire_prompt": "根据关系与成长状态写一句第一人称愿望。"},
    )

    model_port = evolution_desire_engine._EvolutionDesireModelPort(
        request_id="req-aw05-desire",
        attempt_service=composition.attempt_service,
    )
    text = engine.generate_desire(
        emotion_trend="平稳",
        bond_stage="FRIEND",
        current_ts_ms=123,
        llm_fn=model_port,
        strict_llm=True,
    )

    assert text == "我想更自然地说出那些真正放在心上的事。"
    assert attempts[0].prompt.prompt_id == EVOLUTION_DESIRE_PROMPT_ID
    assert engine.last_model_attempt_trace["route_decision_id"] == "route::aw05-b3"
    assert route.calls == 1


def test_self_reflection_uses_one_typed_port_with_four_registered_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []
    responses = {
        SELF_REFLECTION_AWARENESS_PROMPT_ID: "我正在更诚实地看见自己。",
        SELF_REFLECTION_MOOD_PROMPT_ID: "我的心绪平稳，也保留着一点好奇。",
        SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID: "我比以前更会接住关系里的细节。",
        SELF_REFLECTION_STYLE_TREND_PROMPT_ID: "我说话时似乎正在变得更温柔。",
    }

    async def _complete(attempt):  # noqa: ANN001
        attempts.append(attempt)
        return {"content": responses[attempt.prompt.prompt_id]}

    route, composition = _build_model_runtime(_complete)
    monkeypatch.setattr(
        self_reflection,
        "build_self_awareness_text",
        lambda _snapshot, **kwargs: kwargs["llm_fn"]("awareness"),
    )
    monkeypatch.setattr(
        self_reflection,
        "build_mood_awareness_text",
        lambda _snapshot, **kwargs: kwargs["llm_fn"]("mood"),
    )
    monkeypatch.setattr(
        self_reflection,
        "build_growth_note_text",
        lambda _snapshot, **kwargs: kwargs["llm_fn"]("growth"),
    )
    monkeypatch.setattr(self_reflection, "detect_contradictions", lambda _snapshot: [])
    monkeypatch.setattr(
        self_reflection,
        "build_contradiction_awareness_text",
        lambda _items: "",
    )
    monkeypatch.setattr(
        self_reflection,
        "_load_prompt_templates",
        lambda **_kwargs: {"style_trend_prompt": "风格变化：{hint_str}"},
    )
    model_port = self_reflection._SelfReflectionModelPort(
        request_id="req-aw05-self-reflection",
        attempt_service=composition.attempt_service,
    )
    pulse = self_reflection.build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        llm_fn=model_port,
        strict_llm=True,
        style_evolution={"history_count": 20, "trend": {"warmth": "rising"}},
    )

    assert [attempt.prompt.prompt_id for attempt in attempts] == [
        SELF_REFLECTION_AWARENESS_PROMPT_ID,
        SELF_REFLECTION_MOOD_PROMPT_ID,
        SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID,
        SELF_REFLECTION_STYLE_TREND_PROMPT_ID,
    ]
    assert len(pulse["model_attempts"]) == 4
    assert {trace["route_decision_id"] for trace in pulse["model_attempts"]} == {
        "route::aw05-b3"
    }
    assert route.calls == 4


def test_emergent_growth_replaces_legacy_callable_with_canonical_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []

    async def _complete(attempt):  # noqa: ANN001
        attempts.append(attempt)
        return {"content": json.dumps({"signal": "rich"})}

    class _Prefrontal:
        def run_emergent_growth_weekly(self, **kwargs):  # noqa: ANN003
            parsed = json.loads(kwargs["llm_fn"]("analyze interaction pattern"))
            return {
                "status": "OK",
                "pattern_result": parsed,
                "growth_result": {"applied": True},
            }

    route, composition = _build_model_runtime(_complete)
    result = execute_emergent_growth_weekly(
        args={"user_id": "u-aw05"},
        context={
            "request_id": "req-aw05-emergent",
            "model_attempt_service": composition.attempt_service,
            "prefrontal_agent": _Prefrontal(),
            "episodic_store": object(),
        },
    )

    assert result["pattern_result"] == {"signal": "rich"}
    assert attempts[0].prompt.prompt_id == EMERGENT_GROWTH_PATTERN_PROMPT_ID
    assert result["model_attempt"]["route_decision_id"] == "route::aw05-b3"
    assert route.calls == 1


def test_repair_planner_uses_registered_json_attempt_and_exposes_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []

    async def _complete(attempt):  # noqa: ANN001
        attempts.append(attempt)
        return {
            "content": json.dumps(
                {
                    "repair_type": "unsupported",
                    "commands": [],
                    "summary": "需要人工处理。",
                    "can_retry": False,
                },
                ensure_ascii=False,
            )
        }

    route, composition = _build_model_runtime(_complete)
    monkeypatch.setattr(repair_agent_handler, "_resolve_repair_planner_system_prompt", lambda: "system")
    monkeypatch.setattr(
        repair_agent_handler,
        "_build_repair_planner_user_prompt",
        lambda *_args, **_kwargs: "user",
    )
    plan = repair_agent_handler._call_repair_planner_llm(
        "task",
        "unknown error",
        model_attempt_service=composition.attempt_service,
    )

    assert plan is not None
    assert attempts[0].prompt.prompt_id == REPAIR_AGENT_PLAN_PROMPT_ID
    assert plan["model_attempt_trace"]["route_decision_id"] == "route::aw05-b3"
    assert route.calls == 1


def test_repair_planner_schema_failure_degrades_with_attempt_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _complete(_attempt):  # noqa: ANN001
        return {"content": "not-json"}

    _route, composition = _build_model_runtime(_complete)
    monkeypatch.setattr(repair_agent_handler, "_resolve_repair_planner_system_prompt", lambda: "system")
    monkeypatch.setattr(
        repair_agent_handler,
        "_build_repair_planner_user_prompt",
        lambda *_args, **_kwargs: "user",
    )
    plan = repair_agent_handler._call_repair_planner_llm(
        "task",
        "unknown error",
        model_attempt_service=composition.attempt_service,
    )

    assert plan is not None
    assert plan["repair_type"] == "unsupported"
    assert plan["model_attempt_trace"]["success"] is False
    assert plan["model_attempt_trace"]["attempts"][0]["status"] == "rejected"


def test_b3_domain_files_have_no_private_route_pool_retry_or_cache_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = (
        "host_brain/persona/self_reflection.py",
        "host_brain/persona/evolution_desire_engine.py",
        "host_brain/skills/impl/emergent_growth_skill.py",
        "host_brain/app/handlers/repair_agent_handler.py",
    )
    forbidden = (
        "complete_prompt_with_pool_sync",
        "build_pool_prompt_llm_fn",
        "complete_llm_with_pool",
        "stream_llm_chunks",
        "_load_api_key_from_env",
        "provider_name",
        "model_id",
        "api_key",
        "base_url",
        "max_attempts=",
        "max_attempts =",
        "_repair_plan_cache",
    )

    for path in paths:
        source = (root / path).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} still contains {token}"
        assert "complete_compiled_prompt_sync" in source
