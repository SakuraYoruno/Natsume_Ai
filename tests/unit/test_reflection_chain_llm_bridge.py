from __future__ import annotations

import json

from host_brain.integrations.llm_gateway_schemas import ModelAttempt, PoolCompleteResult
from host_brain.reasoning import reflection_chain as rc


def _pool_result(content: str) -> PoolCompleteResult:
    return PoolCompleteResult(
        content=content,
        final_model="test-model",
        final_provider="test-provider",
        attempts=[ModelAttempt(model="test-model", provider="test-provider", status="OK", latency_ms=12)],
        pool_exhausted=False,
        total_latency_ms=12,
        cached=False,
        cognitive_tier="B",
    )


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

    def fake_complete(**kwargs):
        scene_type = str(kwargs.get("scene_type") or "")
        calls.append(scene_type)
        if scene_type == "REFLECTION":
            if calls.count("REFLECTION") == 1:
                return _pool_result(
                    json.dumps(
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
                )
            return _pool_result(
                json.dumps(
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
            )
        raise AssertionError(scene_type)

    import host_brain.integrations.llm_helpers as helpers

    monkeypatch.setattr(helpers, "complete_llm_with_pool_sync", fake_complete)

    chain = rc.ReflectionChain(use_llm=True)
    result = chain.revise(
        user_message="我今天真的有点撑不住了",
        ai_response="嗯。",
        emotion_tag="sadness",
        bond_stage="FRIEND",
    )

    assert result.revised_text == ""
    assert "drop_too_short_reply" in result.operations
    assert result.reflection_before is not None
    assert result.reflection_before.should_revise is True
    assert result.reflection_after is not None
    assert calls.count("REFLECTION") >= 2
    assert set(calls) == {"REFLECTION"}
