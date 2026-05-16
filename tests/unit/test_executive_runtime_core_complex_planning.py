from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.capability_gap_detector import CapabilityGapDecision
from host_brain.app.executive_runtime_core import (
    _RouteSelection,
    _ExecutionOutcome,
    _build_plan_chat_outcome,
    _hard_rule_route,
    _maybe_build_multi_step_plan,
    _should_force_complex_plan,
)
from host_brain.app.skill_dag_planner import SkillDagNode, SkillDagPlan


class _StubSkillRegistry:
    def __init__(self) -> None:
        self._items = [
            SimpleNamespace(skill_name="memory_add"),
            SimpleNamespace(skill_name="skill_translate"),
            SimpleNamespace(skill_name="codex_worker"),
        ]

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


def test_maybe_build_multi_step_plan_requires_explicit_steps_in_intent_draft(monkeypatch) -> None:
    monkeypatch.setenv("HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_ENABLED", "true")
    plan = _maybe_build_multi_step_plan(
        request_id="req-plan-001",
        sanitized_message="请先分析当前问题并整理关键日志，再执行修复并验证输出是否正确",
        intent_draft={},
        skill_registry=_StubSkillRegistry(),
    )

    assert plan is None


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


def test_hard_rule_route_respects_explicit_plan_before_keyword_rule() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "plan",
            "target_id": "skill_dag",
            "planned_steps": [
                {"route": "skill", "target_id": "session_summary"},
                {"route": "skill", "target_id": "emotion_query"},
            ],
        },
        sanitized_message="先帮我总结一下这段会话，然后再查一下当前情绪状态。",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "plan"
    assert route.target_id == "skill_dag"
    assert route.reason_code == "intent_draft:plan"


def test_hard_rule_route_forces_relational_followup_back_to_chat() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "delegate",
            "target_id": "intent_agent",
        },
        sanitized_message="那你还会帮我吗",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "chat"
    assert route.target_id == ""
    assert route.reason_code == "RELATIONAL_CHAT:help_commitment"


def test_hard_rule_route_keeps_simple_technical_advice_on_surface_chat() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "skill",
            "target_id": "codex_worker",
        },
        sanitized_message="项目启动报错，端口18080被占用了，我该怎么排查？",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "chat"
    assert route.target_id == ""
    assert route.reason_code == "hard_rule:surface_technical_advice"


def test_hard_rule_route_keeps_project_service_probe_advice_on_surface_chat() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "skill",
            "target_id": "codex_worker",
        },
        sanitized_message="Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "chat"
    assert route.target_id == ""
    assert route.reason_code == "hard_rule:surface_technical_advice"


def test_hard_rule_route_keeps_surface_memory_recall_on_chat() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "skill",
            "target_id": "memory_recall",
        },
        sanitized_message="你还记得我上次说喜欢芒果吗？",
        skill_registry=_StubSkillRegistry(),
        surface_task_control={
            "task_mode": "memory_recall",
            "response_move": "recall_memory",
        },
    )

    assert route.route_type == "chat"
    assert route.target_id == ""
    assert route.reason_code == "hard_rule:surface_memory_recall"


def test_hard_rule_route_keeps_actionable_code_request_on_skill_route() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "skill",
            "target_id": "codex_worker",
        },
        sanitized_message="开始处理，直接修改代码修复端口问题并跑测试。",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "skill"
    assert route.target_id == "codex_worker"
    assert route.reason_code == "intent_draft:skill"


def test_hard_rule_route_keeps_simple_task_split_on_surface_chat() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "delegate",
            "target_id": "intent_agent",
        },
        sanitized_message="帮我把下一步拆成三件事。",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "chat"
    assert route.target_id == ""
    assert route.reason_code == "hard_rule:surface_task_split"


def test_hard_rule_route_keeps_actionable_task_split_on_delegate() -> None:
    route = _hard_rule_route(
        intent_draft={
            "route": "delegate",
            "target_id": "intent_agent",
        },
        sanitized_message="开始处理，把修复任务拆成三件事然后执行。",
        skill_registry=_StubSkillRegistry(),
    )

    assert route.route_type == "delegate"
    assert route.target_id == "intent_agent"
    assert route.reason_code == "intent_draft:delegate"


def test_build_plan_chat_outcome_preserves_structured_steps() -> None:
    plan = SkillDagPlan(
        plan_id="plan::req-structured",
        nodes=[
            SkillDagNode(
                node_id="step-1",
                step_index=1,
                description="summarize session",
                task_description="summarize current session",
                route="skill",
                target_id="session_summary",
                route_reason="test:plan:session_summary",
                allow_parallel=True,
                skill_family="social",
                risk_level=1,
            ),
            SkillDagNode(
                node_id="step-2",
                step_index=2,
                description="read emotion snapshot",
                task_description="read current emotion snapshot",
                route="skill",
                target_id="emotion_query",
                route_reason="test:plan:emotion_query",
                allow_parallel=True,
                skill_family="social",
                risk_level=1,
            ),
        ],
        execution_batches=[["step-1", "step-2"]],
    )
    outcome = _ExecutionOutcome(
        route_type="plan",
        target_id="plan::req-structured",
        skill_family="executive",
        success=True,
        result_text="[1] session_summary: done\n[2] emotion_query: done",
        error_text="",
        latency_ms=42,
        payload={"plan_trace": []},
    )

    enriched = _build_plan_chat_outcome(
        plan=plan,
        outcome=outcome,
        plan_meta={
            "parallel_enabled": True,
            "plan_trace": [
                {"node_id": "step-1", "success": True, "result_text": "summary ok", "error_text": ""},
                {"node_id": "step-2", "success": True, "result_text": "emotion ok", "error_text": ""},
            ],
        },
    )

    assert enriched.payload["total_steps"] == 2
    assert enriched.payload["success_count"] == 2
    assert enriched.payload["parallel_enabled"] is True
    assert enriched.payload["steps"][0]["target_id"] == "session_summary"
    assert enriched.payload["steps"][1]["target_id"] == "emotion_query"
