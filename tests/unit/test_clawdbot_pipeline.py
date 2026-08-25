"""Visual action contracts remain pure after raw ClawdBot execution retirement."""

from __future__ import annotations

from host_brain.app.vision.clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionReport,
    ActionStatus,
    ActionType,
    ResponseFormat,
)


def test_visual_action_protocol_models_remain_serializable() -> None:
    action = Action(type=ActionType.CLICK, target_desc="登录按钮", x=100, y=200)
    plan = ActionPlan(
        request_id="req-001",
        session_id="sess-001",
        goal="点击登录按钮",
        actions=[action, Action(type=ActionType.DONE, target_desc="完成")],
    )
    report = ActionReport(
        request_id=plan.request_id,
        session_id=plan.session_id,
        status=ActionStatus.SUCCESS,
        goal_achieved=True,
        total_actions=2,
        success_count=2,
    )

    assert ResponseFormat.ACTIONS.value == "actions"
    assert plan.model_dump()["actions"][0]["type"] == "click"
    assert report.goal_achieved is True
