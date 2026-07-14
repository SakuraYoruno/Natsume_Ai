from __future__ import annotations

import json

from host_brain.composition import process_services as dependencies
from host_brain.model_runtime import (
    REFLECTION_EVALUATE_PROMPT_ID,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)
from host_brain.reasoning import reflection_chain as rc


def test_reflection_chain_revise_no_longer_uses_llm_authoring_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        rc,
        "_load_reflection_llm_templates",
        lambda: {
            "reflect": {
                "system_prompt": "judge",
                "user_prompt_template": "reply={ai_response}",
            },
            "revise": {
                "system_prompt": "revise",
                "user_prompt_template": "reply={ai_response} hints={revision_hints_json}",
            },
        },
    )

    calls: list[str] = []

    async def fake_complete(attempt):  # noqa: ANN001
        scene_type = str(attempt.scene_type or "")
        calls.append(scene_type)
        if scene_type == "REFLECTION":
            if calls.count("REFLECTION") == 1:
                return {
                    "content": json.dumps(
                        {
                            "overall_score": 0.32,
                            "passed": False,
                            "should_revise": True,
                            "issues": ["需要更直接地回应情绪"],
                            "revision_hints": ["先接住对方，再给一句自然回应"],
                            "dimensions": {
                                "emotion_fit": {"score": 0.2, "issues": ["缺少共情"]},
                                "canon_compliance": {"score": 1.0, "issues": []},
                                "style_consistency": {"score": 0.8, "issues": []},
                                "info_accuracy": {"score": 0.6, "issues": []},
                                "length_appropriate": {"score": 0.8, "issues": []},
                            },
                        },
                        ensure_ascii=False,
                    )
                }
            return {
                "content": json.dumps(
                    {
                        "overall_score": 0.91,
                        "passed": True,
                        "should_revise": False,
                        "issues": [],
                        "revision_hints": [],
                        "dimensions": {
                            "emotion_fit": {"score": 0.95, "issues": []},
                            "canon_compliance": {"score": 1.0, "issues": []},
                            "style_consistency": {"score": 0.9, "issues": []},
                            "info_accuracy": {"score": 0.9, "issues": []},
                            "length_appropriate": {"score": 0.9, "issues": []},
                        },
                    },
                    ensure_ascii=False,
                )
            }
        raise AssertionError(scene_type)

    route = RouteDecision(
        decision_id="reflection-route",
        targets=(
            ProviderTarget("test.reflection", "mock", "reflection-model", timeout_ms=2_800),
        ),
        reason="TEST_REFLECTION_ROUTE",
        max_attempts=2,
    )
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(route),
        provider_adapters=[
            CallableProviderAdapter("test.reflection", complete_fn=fake_complete)
        ],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    try:
        chain = rc.ReflectionChain(
            use_llm=True,
            model_attempt_service=dependencies.get_model_attempt_service(),
        )
        result = chain.revise(
            user_message="我今天真的有点撑不住了",
            ai_response="嗯。",
            emotion_tag="sadness",
            bond_stage="FRIEND",
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert result.revised_text == ""
    assert "drop_too_short_reply" in result.operations
    assert result.reflection_before is not None
    assert result.reflection_before.should_revise is True
    assert result.reflection_before.model_attempt_trace["route_decision_id"] == "reflection-route"
    assert result.reflection_before.model_attempt_trace["attempts"][0]["prompt_id"] == REFLECTION_EVALUATE_PROMPT_ID
    assert result.reflection_after is not None
    assert calls.count("REFLECTION") >= 2
    assert set(calls) == {"REFLECTION"}
