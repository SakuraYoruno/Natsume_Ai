from __future__ import annotations

import pytest

from host_brain.agents.planning_agent import PlanningAgent
from host_brain.skills import provider_catalog
from host_brain.skills.builtin_execution_policy import builtin_skill_policies
from host_brain.skills.executor_registry import build_builtin_tool_executor_registry
from host_brain.skills.scheduler_registry import _normalize_scheduler_add
from host_brain.tribe.thalamus_agent import classify_task_intent_by_rules, classify_task_intent_fastpath


@pytest.mark.parametrize(
    ("skill_id", "tool", "action"),
    (
        ("emotion_update", "emotion", "update"),
        ("bond_score_update", "emotion", "bond_update"),
        ("emotion_get", "emotion", "get"),
        ("bond_score_get", "emotion", "bond_get"),
    ),
)
def test_legacy_emotion_and_bond_skill_descriptors_fail_closed(
    skill_id: str,
    tool: str,
    action: str,
) -> None:
    """No declarative descriptor may revive a second affect/Bond authority."""

    with pytest.raises(ValueError, match="BUILTIN_SKILL_EXECUTION_POLICY_MISSING"):
        builtin_skill_policies(skill_id=skill_id, tool=tool, action=action)


def test_legacy_emotion_tool_and_provider_are_absent_from_production_catalog() -> None:
    registry = build_builtin_tool_executor_registry()

    assert "emotion" not in registry._executor_ids_unchecked()
    assert all(
        spec.env_flag != "HOST_SKILL_EMOTION_ENABLED"
        and "emotion_registry" not in spec.provider_path
        for spec in provider_catalog.BUILTIN_SKILL_PROVIDER_SPECS
    )


def test_scheduler_cannot_create_new_legacy_emotion_update_task() -> None:
    normalized = _normalize_scheduler_add(
        {"task_name": "legacy-emotion", "task_type": "emotion_update"},
        None,
    )

    assert normalized["task_type"] == "custom"


@pytest.mark.parametrize(
    ("text", "reason_code", "semantic_type"),
    (
        ("查一下你现在的情绪状态", "THALAMUS_RULE_SELF_AFFECT_REFLECTION", "self_affect_reflection"),
        ("看看我们的关系阶段", "THALAMUS_RULE_RELATIONSHIP_REFLECTION", "relationship_reflection"),
        (
            "你现在是什么心情，我们的关系又意味着什么？",
            "THALAMUS_RULE_AFFECTIVE_RELATIONAL_REFLECTION",
            "affective_relational_reflection",
        ),
    ),
)
def test_affective_and_relationship_reflection_are_canonical_chat_not_legacy_skills(
    text: str,
    reason_code: str,
    semantic_type: str,
) -> None:
    for classifier in (classify_task_intent_by_rules, classify_task_intent_fastpath):
        decision = classifier(text)

        assert decision is not None
        assert decision["route"] == "chat"
        assert decision["target_id"] == ""
        assert decision["reason_code"] == reason_code
        semantic = decision["semantic_intent"]
        assert semantic["context_binding"].startswith("canonical_")
        assert semantic["type"] == semantic_type
        assert "without_" in semantic["answer_strategy"]


@pytest.mark.parametrize(
    "text",
    (
        "你现在心情怎么样",
        "看看我们的关系阶段",
        "你对我有什么感受",
    ),
)
def test_planner_does_not_delegate_affective_reflection_to_legacy_routes(text: str) -> None:
    assert PlanningAgent.rule_route(text) is None


@pytest.mark.parametrize(
    "text",
    (
        "分析这段文本的情绪是什么",
        "这张图片里的人是什么关系",
        "请读取用户当前的情绪快照",
    ),
)
def test_external_affect_or_relationship_analysis_is_not_misread_as_natsume_reflection(
    text: str,
) -> None:
    for classifier in (classify_task_intent_by_rules, classify_task_intent_fastpath):
        decision = classifier(text)
        context_binding = str(
            (decision or {}).get("semantic_intent", {}).get("context_binding")
            if isinstance(decision, dict)
            else ""
        )
        assert context_binding not in {
            "canonical_self_affect",
            "canonical_relationship_state",
            "canonical_self_affect_and_relationship",
        }
