"""Visual action contracts remain pure after raw ClawdBot execution retirement."""

from __future__ import annotations

import json

import pytest

from host_brain.app.vision.action_planner import (
    VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
    _extract_json_block,
    _make_fallback_plan,
    _parse_llm_response,
    generate_action_plan,
)
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"actions": []}', '{"actions": []}'),
        ('```json\n{"actions": []}\n```', '{"actions": []}'),
        ('说明\n{"actions": [{"type": "done"}]}\n结束', '{"actions": [{"type": "done"}]}'),
        ("没有 JSON", ""),
    ],
)
def test_extract_json_block_is_pure(raw: str, expected: str) -> None:
    assert _extract_json_block(raw) == expected


def test_parse_action_proposal_has_no_execution_authority() -> None:
    raw = json.dumps(
        {
            "goal_understood": "点击登录",
            "actions": [
                {"type": "click", "target_desc": "登录按钮", "x": 320, "y": 480},
                {"type": "done", "target_desc": "完成"},
            ],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )
    plan = _parse_llm_response(
        raw_text=raw,
        request_id="req-parse",
        session_id="sess-parse",
        goal="登录",
        stop_on_error=True,
        need_screenshot_feedback=False,
    )

    assert [action.type for action in plan.actions] == [
        ActionType.CLICK,
        ActionType.DONE,
    ]
    assert plan.extra["execution_authority"] == "NONE_PROPOSAL_ONLY"


def test_fallback_action_proposal_has_no_execution_authority() -> None:
    plan = _make_fallback_plan(
        "req-fallback",
        "sess-fallback",
        "测试目标",
        reason="TEST",
    )
    assert plan.actions[0].type is ActionType.DONE
    assert plan.extra["fallback"] is True
    assert plan.extra["execution_authority"] == "NONE_PROPOSAL_ONLY"


@pytest.mark.asyncio
async def test_legacy_action_plan_model_entry_is_sealed() -> None:
    with pytest.raises(
        RuntimeError,
        match=VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
    ):
        await generate_action_plan(
            request_id="req-sealed",
            session_id="sess-sealed",
            goal="点击按钮",
            vision_context={"screen_description": "按钮"},
            llm_runtime={"available": True},
        )
