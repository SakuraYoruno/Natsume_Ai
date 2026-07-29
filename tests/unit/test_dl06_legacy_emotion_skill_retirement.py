from __future__ import annotations

import pytest

from host_brain.skills import provider_catalog
from host_brain.skills.builtin_execution_policy import builtin_skill_policies
from host_brain.skills.executor_registry import build_builtin_tool_executor_registry
from host_brain.skills.scheduler_registry import _normalize_scheduler_add


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
