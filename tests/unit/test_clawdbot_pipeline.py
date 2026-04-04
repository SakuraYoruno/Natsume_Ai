"""ClawdBot 协议、指令生成器、Agent Loop 单元测试。"""

from __future__ import annotations

import json
import pytest
import anyio

from host_brain.app.vision.clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionReport,
    ActionResult,
    ActionStatus,
    ActionType,
    ResponseFormat,
)
from host_brain.app.vision.action_planner import (
    _extract_json_block,
    _make_fallback_plan,
    _parse_llm_response,
)
from host_brain.app.vision.agent_loop import ClawdbotClient, MockClawdbotClient, run_agent_loop


# ---------------------------------------------------------------------------
# 协议数据结构测试
# ---------------------------------------------------------------------------

class TestClawdbotProtocol:
    def test_action_type_values(self):
        assert ActionType.CLICK.value == "click"
        assert ActionType.TYPE.value == "type"
        assert ActionType.DONE.value == "done"

    def test_response_format_values(self):
        assert ResponseFormat.TEXT.value == "text"
        assert ResponseFormat.ACTIONS.value == "actions"

    def test_action_model(self):
        a = Action(type=ActionType.CLICK, target_desc="登录按钮", x=100, y=200)
        assert a.type == ActionType.CLICK
        assert a.x == 100
        assert a.y == 200

    def test_action_plan_model(self):
        plan = ActionPlan(
            request_id="req-001",
            session_id="sess-001",
            goal="点击登录按钮",
            actions=[
                Action(type=ActionType.CLICK, target_desc="登录按钮", x=320, y=480),
                Action(type=ActionType.DONE, target_desc="完成"),
            ],
        )
        assert len(plan.actions) == 2
        assert plan.protocol_version == "v1"
        assert plan.stop_on_error is True

    def test_action_report_model(self):
        report = ActionReport(
            request_id="req-001",
            session_id="sess-001",
            status=ActionStatus.SUCCESS,
            goal_achieved=True,
            total_actions=2,
            success_count=2,
        )
        assert report.goal_achieved is True
        assert report.failed_count == 0

    def test_action_plan_serialization(self):
        plan = ActionPlan(
            request_id="req-001",
            session_id="sess-001",
            goal="测试",
            actions=[Action(type=ActionType.SCREENSHOT, screenshot_label="before")],
        )
        data = plan.model_dump()
        assert data["protocol_version"] == "v1"
        assert data["actions"][0]["type"] == "screenshot"


# ---------------------------------------------------------------------------
# 指令生成器测试
# ---------------------------------------------------------------------------

class TestActionPlanner:
    def test_extract_json_block_direct(self):
        text = '{"actions": [], "goal_understood": "test"}'
        result = _extract_json_block(text)
        assert result.startswith("{")

    def test_extract_json_block_markdown(self):
        text = '```json\n{"actions": []}\n```'
        result = _extract_json_block(text)
        assert result == '{"actions": []}'

    def test_extract_json_block_embedded(self):
        text = '这是说明文字\n{"actions": [{"type": "click"}]}\n结束'
        result = _extract_json_block(text)
        assert '"actions"' in result

    def test_extract_json_block_empty(self):
        result = _extract_json_block("没有 JSON 内容")
        assert result == ""

    def test_make_fallback_plan(self):
        plan = _make_fallback_plan("req-001", "sess-001", "测试目标", reason="TEST")
        assert len(plan.actions) == 1
        assert plan.actions[0].type == ActionType.DONE
        assert plan.extra["fallback"] is True
        assert plan.extra["reason"] == "TEST"

    def test_parse_llm_response_valid(self):
        raw = json.dumps({
            "goal_understood": "点击登录按钮",
            "actions": [
                {"type": "click", "target_desc": "登录按钮", "x": 320, "y": 480},
                {"type": "type", "target_desc": "用户名框", "text": "admin"},
                {"type": "done", "target_desc": "完成"},
            ],
            "confidence": 0.9,
            "notes": "测试备注",
        })
        plan = _parse_llm_response(
            raw_text=raw,
            request_id="req-001",
            session_id="sess-001",
            goal="登录",
            stop_on_error=True,
            need_screenshot_feedback=False,
        )
        assert len(plan.actions) == 3
        assert plan.actions[0].type == ActionType.CLICK
        assert plan.actions[0].x == 320
        assert plan.actions[1].text == "admin"
        assert plan.extra["confidence"] == 0.9

    def test_parse_llm_response_empty(self):
        plan = _parse_llm_response(
            raw_text="",
            request_id="req-001",
            session_id="sess-001",
            goal="测试",
            stop_on_error=True,
            need_screenshot_feedback=False,
        )
        assert plan.extra["fallback"] is True
        assert plan.extra["reason"] == "EMPTY_RESPONSE"

    def test_parse_llm_response_invalid_json(self):
        plan = _parse_llm_response(
            raw_text="这不是 JSON",
            request_id="req-001",
            session_id="sess-001",
            goal="测试",
            stop_on_error=True,
            need_screenshot_feedback=False,
        )
        assert plan.extra["fallback"] is True

    def test_parse_llm_response_unknown_action_type(self):
        """未知操作类型应被跳过，不影响其他指令。"""
        raw = json.dumps({
            "actions": [
                {"type": "unknown_op", "target_desc": "未知"},
                {"type": "click", "target_desc": "按钮", "x": 100, "y": 100},
                {"type": "done", "target_desc": "完成"},
            ],
        })
        plan = _parse_llm_response(
            raw_text=raw,
            request_id="req-001",
            session_id="sess-001",
            goal="测试",
            stop_on_error=True,
            need_screenshot_feedback=False,
        )
        # unknown_op 被跳过，剩余 2 条
        assert len(plan.actions) == 2
        assert plan.actions[0].type == ActionType.CLICK


# ---------------------------------------------------------------------------
# Agent Loop 测试（使用 Mock 客户端）
# ---------------------------------------------------------------------------

class TestAgentLoop:
    def test_mock_client_screenshot(self):
        async def _run():
            client = MockClawdbotClient()
            result = await client.capture_screenshot("sess-001")
            assert "image_base64" in result
            assert result.get("mock") is True
        anyio.run(_run)


    def test_agent_loop_builds_structured_scene_awareness_from_screenshot_payload(self, monkeypatch):
        captured: dict[str, object] = {}

        class SceneAwareClient(ClawdbotClient):
            async def capture_screenshot(self, session_id: str):
                return {
                    "image_base64": "abc123==",
                    "image_mime": "image/png",
                    "width": 2560,
                    "height": 1440,
                    "scene_tags": ["battle"],
                    "current_activity": "gaming",
                    "app_name": "Genshin Impact",
                    "window_title": "Abyss Battle",
                    "screen_description": "boss is almost defeated",
                }

            async def execute_plan(self, plan: ActionPlan):
                return ActionReport(
                    request_id=plan.request_id,
                    session_id=plan.session_id,
                    status=ActionStatus.SUCCESS,
                    goal_achieved=True,
                    total_actions=len(plan.actions),
                    success_count=len(plan.actions),
                )

        async def _fake_generate_action_plan(**kwargs):
            captured["vision_context"] = kwargs["vision_context"]
            return ActionPlan(
                request_id=kwargs["request_id"],
                session_id=kwargs["session_id"],
                goal=kwargs["goal"],
                actions=[Action(type=ActionType.DONE, target_desc="done")],
                extra={"goal_understood": "ok", "confidence": 0.88},
            )

        import host_brain.app.vision.action_planner as action_planner

        monkeypatch.setattr(action_planner, "generate_action_plan", _fake_generate_action_plan)

        async def _run():
            llm_runtime = {"available": True, "llm_api_key": "test"}
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-scene-001",
                session_id="sess-scene-001",
                goal="check scene awareness",
                llm_runtime=llm_runtime,
                clawdbot_client=SceneAwareClient(),
                max_rounds=1,
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            screenshot_done = next(e for e in events if e["phase"] == "SCREENSHOT_DONE")
            scene_awareness = screenshot_done["scene_awareness"]
            assert scene_awareness["scene_tags"][:2] == ["gaming", "battle"]
            assert scene_awareness["app_name"] == "Genshin Impact"
            assert scene_awareness["companion_suitable"] is True
            assert "screen_description" not in json.dumps(screenshot_done, ensure_ascii=False)

            vision_context = captured["vision_context"]
            assert vision_context["scene_awareness"]["scene_tags"][:2] == ["gaming", "battle"]
            assert vision_context["scene_awareness"]["has_screenshot"] is True
            assert "screen_description" not in json.dumps(vision_context, ensure_ascii=False)

        anyio.run(_run)

    def test_mock_client_execute(self):
        async def _run():
            client = MockClawdbotClient()
            plan = ActionPlan(
                request_id="req-001",
                session_id="sess-001",
                goal="测试",
                actions=[
                    Action(type=ActionType.CLICK, target_desc="按钮", x=100, y=100),
                    Action(type=ActionType.DONE, target_desc="完成"),
                ],
            )
            report = await client.execute_plan(plan)
            assert report.status == ActionStatus.SUCCESS
            assert report.goal_achieved is True
            assert report.success_count == 2
        anyio.run(_run)

    def test_agent_loop_sse_events(self):
        """验证 Agent Loop 推送的 SSE 事件序列。"""
        async def _run():
            llm_runtime = {"available": False, "llm_api_key": ""}
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-001",
                session_id="sess-001",
                goal="点击登录按钮",
                llm_runtime=llm_runtime,
                use_mock=True,
                max_rounds=2,
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            phases = [e["phase"] for e in events]
            assert "LOOP_START" in phases
            assert "LOOP_DONE" in phases

            loop_done = next(e for e in events if e["phase"] == "LOOP_DONE")
            assert "goal_achieved" in loop_done
            assert "final_status" in loop_done
            assert "total_latency_ms" in loop_done
        anyio.run(_run)

    def test_agent_loop_max_rounds(self):
        """验证 max_rounds 限制生效。"""
        async def _run():
            llm_runtime = {"available": False, "llm_api_key": ""}
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-002",
                session_id="sess-002",
                goal="测试最大轮数",
                llm_runtime=llm_runtime,
                use_mock=True,
                max_rounds=3,
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            loop_done = next(e for e in events if e["phase"] == "LOOP_DONE")
            # 因为 LLM 不可用，每轮都会 fallback → 提前终止
            assert loop_done["total_rounds"] <= 3
        anyio.run(_run)
