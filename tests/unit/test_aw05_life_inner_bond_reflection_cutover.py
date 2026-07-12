from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.app import dependencies
from host_brain.model_runtime import (
    INNER_WORLD_MONOLOGUE_PROMPT_ID,
    LIFE_PROACTIVE_COMPOSE_PROMPT_ID,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)
from host_brain.persona import inner_world
from host_brain.runtime.life_continuity import _compose_life_proactive_content


class _CountingRoute:
    def __init__(self, *, adapter_id: str, model_id: str, timeout_ms: int = 12_000) -> None:
        self.calls = 0
        self._decision = RouteDecision(
            decision_id=f"route::{adapter_id}",
            targets=(ProviderTarget(adapter_id, "mock", model_id, timeout_ms=timeout_ms),),
            reason="TEST_CANONICAL_ROUTE",
            max_attempts=2,
        )

    async def resolve(self, _request, _prompt):  # noqa: ANN001
        self.calls += 1
        return self._decision


def _install_composition(*, adapter_id: str, model_id: str, complete):  # noqa: ANN001
    route = _CountingRoute(adapter_id=adapter_id, model_id=model_id)
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=route,
        provider_adapters=[CallableProviderAdapter(adapter_id, complete_fn=complete)],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    return route


@pytest.mark.asyncio
async def test_life_proactive_semantic_repair_reuses_one_request_and_route() -> None:
    outputs = [
        json.dumps({"narrative": "我就在这儿"}, ensure_ascii=False),
        json.dumps(
            {"narrative": "刚才那点心绪又轻轻绷了一下，想听你说说今天；忙的话晚点回也行。"},
            ensure_ascii=False,
        ),
    ]
    attempts = []

    async def _complete(attempt):  # noqa: ANN001
        attempts.append(attempt)
        return {"content": outputs.pop(0)}

    route = _install_composition(
        adapter_id="test.life-proactive",
        model_id="life-model",
        complete=_complete,
    )
    try:
        content, meta = await _compose_life_proactive_content(
            director_pack={
                "scene": "idle",
                "style_goal": "轻短自然",
                "life_pressure": {"care": 0.6},
                "suppress_work_context": True,
            },
            state={},
            motive="care",
            pressure=0.6,
            state_binding={"trigger": "care_pressure", "anchors": ["心绪略绷紧"]},
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert "晚点回也行" in content
    assert route.calls == 1
    assert len(attempts) == 2
    assert len({attempt.request_id for attempt in attempts}) == 1
    assert attempts[0].prompt.prompt_id == LIFE_PROACTIVE_COMPOSE_PROMPT_ID
    trace = meta["model_attempt"]
    assert [item["status"] for item in trace["attempts"]] == ["rejected", "succeeded"]
    assert len({item["route_decision_id"] for item in trace["attempts"]}) == 1


def test_inner_world_fallback_uses_registered_model_attempt(monkeypatch) -> None:
    captured = {}

    async def _complete(attempt):  # noqa: ANN001
        captured["attempt"] = attempt
        return {
            "content": json.dumps(
                {
                    "note": "雨声停了一瞬，我忽然想起那本还没读完的书。",
                    "focus": "memory",
                    "core_need": "continuity",
                    "tension": "unfinished_thread",
                    "memory_anchor": "未读完的书",
                    "next_tendency": "observe_more",
                    "thought_form": "memory_flash",
                    "salience_hint": 0.7,
                },
                ensure_ascii=False,
            )
        }

    route = _install_composition(
        adapter_id="test.inner-world",
        model_id="inner-model",
        complete=_complete,
    )
    inner_world._reset_inner_world_llm_circuit()
    monkeypatch.setenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": False},
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **_kwargs: ("", "", "PRIMARY_EMPTY", {}),
    )
    monkeypatch.setattr(
        inner_world,
        "_score_inner_world_note",
        lambda **_kwargs: {"score": 1.0, "flags": []},
    )
    try:
        note, focus, reason, meta = inner_world._generate_inner_stream_note_from_llm(
            state=inner_world.InnerWorldState(user_id="user-1"),
            absence_band="recent",
            offline_hours=0.5,
            now_ms=123_456,
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert note == "雨声停了一瞬，我忽然想起那本还没读完的书。"
    assert focus == "memory"
    assert reason == ""
    assert route.calls == 1
    assert captured["attempt"].prompt.prompt_id == INNER_WORLD_MONOLOGUE_PROMPT_ID
    assert meta["model_attempt"]["route_decision_id"] == "route::test.inner-world"


def test_four_domain_files_have_no_private_model_route_or_pool_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = (
        "host_brain/runtime/life_continuity.py",
        "host_brain/persona/inner_world.py",
        "host_brain/bond/bond_narrative.py",
        "host_brain/reasoning/reflection_chain.py",
    )
    sources = {path: (root / path).read_text(encoding="utf-8") for path in paths}
    forbidden = (
        "complete_llm_with_pool_sync",
        "acomplete_llm_with_pool",
        "resolve_host_llm_runtime",
        "resolve_surface_reply_llm_runtime",
        "llm_api_key",
        "base_url_override",
    )
    for path, source in sources.items():
        for token in forbidden:
            assert token not in source, f"{path} still contains {token}"
        assert "complete_compiled_prompt" in source
    assert sources[paths[0]].count("complete_compiled_prompt(") == 1
