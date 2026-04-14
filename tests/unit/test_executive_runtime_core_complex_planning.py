from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.capability_gap_detector import CapabilityGapDecision
from host_brain.app.executive_runtime_core import (
    _RouteSelection,
    _maybe_build_multi_step_plan,
    _should_force_complex_plan,
)


class _StubSkillRegistry:
    def __init__(self) -> None:
        self._items = [SimpleNamespace(skill_name="memory_add"), SimpleNamespace(skill_name="skill_translate")]

    def all(self):
        return list(self._items)

    def get(self, skill_name: str):
        for item in self._items:
            if str(item.skill_name) == str(skill_name):
                return item
        return None


class _StubPlanningAgent:
    def __init__(self, *, needs_planning: bool = True) -> None:
        self._needs_planning = needs_planning

    def needs_planning(self, request: str) -> bool:
        return bool(self._needs_planning and str(request or "").strip())

    def create_plan(self, *, task_id: str, request: str, available_skills=None):
        del task_id, request, available_skills
        steps = [
            SimpleNamespace(step_id=1, action_type="reason", description="analyze", depends_on=[], skill_name=None),
            SimpleNamespace(step_id=2, action_type="act", description="先收集环境和日志", depends_on=[1], skill_name=None),
            SimpleNamespace(step_id=3, action_type="act", description="再执行修复并验证", depends_on=[2], skill_name=None),
            SimpleNamespace(step_id=4, action_type="synthesize", description="总结结果", depends_on=[2, 3], skill_name=None),
        ]
        return SimpleNamespace(steps=steps)


def test_maybe_build_multi_step_plan_uses_planning_agent_without_split_markers(monkeypatch) -> None:
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED", "true")
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_MIN_CHARS", "10")
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_PARALLEL_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_planning_agent",
        lambda: _StubPlanningAgent(needs_planning=True),
    )

    plan = _maybe_build_multi_step_plan(
        request_id="req-plan-001",
        sanitized_message="请先分析当前问题并整理关键日志，再执行修复并验证输出是否正确",
        intent_draft={},
        skill_registry=_StubSkillRegistry(),
    )

    assert plan is not None
    assert plan.plan_id == "plan::req-plan-001"
    assert len(plan.nodes) >= 2
    assert plan.nodes[0].step_index == 1
    assert any(len(batch) >= 1 for batch in plan.execution_batches)


def test_should_force_complex_plan_returns_false_for_chat_without_action_gap(monkeypatch) -> None:
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_planning_agent",
        lambda: _StubPlanningAgent(needs_planning=True),
    )
    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.detect_capability_gap",
        lambda **_: CapabilityGapDecision(
            detected=False,
            reason_code="NO_ACTION_INTENT",
            confidence=0.0,
            missing_capability_tags=(),
            suggested_family=None,
            details={},
        ),
    )

    forced = _should_force_complex_plan(
        route=_RouteSelection("chat", "", "default:chat"),
        user_message="今天心情怎么样，聊聊天吧",
        intent_draft={"route_source": "llm"},
        llm_route_used=True,
    )
    assert forced is False


def test_should_force_complex_plan_returns_true_for_chat_action_gap(monkeypatch) -> None:
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_planning_agent",
        lambda: _StubPlanningAgent(needs_planning=True),
    )
    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.detect_capability_gap",
        lambda **_: CapabilityGapDecision(
            detected=True,
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            confidence=0.9,
            missing_capability_tags=("coding",),
            suggested_family="code",
            details={},
        ),
    )

    forced = _should_force_complex_plan(
        route=_RouteSelection("chat", "", "default:chat"),
        user_message="请先整理现状并拆分任务，然后分步执行代码修复和验证",
        intent_draft={"route_source": "llm"},
        llm_route_used=True,
    )
    assert forced is True
