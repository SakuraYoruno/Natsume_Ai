from __future__ import annotations

import json

import anyio

from host_brain.app.vision.agent_loop import ClawdbotClient, run_agent_loop
from host_brain.app.vision.clawdbot_protocol import Action, ActionPlan, ActionReport, ActionStatus, ActionType
from host_brain.core.errors import SCHED_CONDITION_UNMET
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_MORNING_GREET, SCENE_TOPIC_FOLLOWUP
from host_brain.skills.impl.proactive_talk import STATUS_EXECUTED, STATUS_REJECTED, execute_proactive_talk


def _make_ctx(**kwargs) -> dict:
    return {
        "sys_mode": "normal",
        "last_interaction_ts": 0,
        "request_id": "req-scene-aware",
        "intent_id": "intent-scene-aware",
        "time_period": "evening",
        "user_name": "主人",
        **kwargs,
    }


def _make_ui_pusher() -> tuple[callable, list[dict]]:
    pushed: list[dict] = []

    def _pusher(msg: dict) -> dict:
        pushed.append(msg)
        return {"status": "OK"}

    return _pusher, pushed


class TestSceneAwareAgentLoop:
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
                    total_latency_ms=1,
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
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-scene-001",
                session_id="sess-scene-001",
                goal="check scene awareness",
                llm_runtime={"available": True, "llm_api_key": "test"},
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


class TestSceneAwareProactiveTalk:
    def test_scene_awareness_blocks_when_focus_work(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_make_ctx(
                llm_generator=lambda _: {"greeting": "我来看看你在忙什么"},
                ui_pusher=pusher,
                scene_awareness={
                    "scene_tags": ["focus_work"],
                    "current_activity": "coding",
                    "companion_suitable": False,
                    "has_screenshot": True,
                },
            ),
        )

        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "scene_awareness blocked proactive_talk" in result["detail"]
        assert pushed == []

    def test_scene_awareness_flows_to_llm_and_message_keeps_only_structured_fields(self) -> None:
        pusher, pushed = _make_ui_pusher()
        captured: dict[str, object] = {}

        def _capturing_llm(inp: dict) -> dict:
            captured.update(inp)
            return {"greeting": "我先轻轻陪你打完这一段。"}

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_make_ctx(
                llm_generator=_capturing_llm,
                ui_pusher=pusher,
                scene_awareness={
                    "scene_tags": ["battle"],
                    "current_activity": "gaming",
                    "app_name": "Genshin Impact",
                    "companion_suitable": True,
                    "has_screenshot": True,
                    "screen_description": "boss fight in progress",
                },
            ),
        )

        assert result["status"] == STATUS_EXECUTED
        assert result["scene_driven"] is True
        assert captured["scene_awareness"]["scene_tags"][:2] == ["gaming", "battle"]
        assert "陪伴场景" in str(captured["scene_hint"])
        assert pushed[0]["scene_tags"][:2] == ["gaming", "battle"]
        assert pushed[0]["scene_activity"] == "gaming"
        assert "screen_description" not in str(captured["scene_awareness"])
        assert "screen_description" not in str(pushed[0])


class TestSceneAwareProactiveVoice:
    def test_focus_work_scene_blocks_generic_morning_greet(self) -> None:
        voice = PersonaProactiveVoice()
        result = voice.evaluate_scene(
            user_id="scene-focus-block",
            context={
                "current_ts_ms": _make_ts_at_hour(7, 30),
                "scene_awareness": {
                    "scene_tags": ["focus_work"],
                    "current_activity": "coding",
                    "companion_suitable": False,
                },
            },
        )
        assert result is None

    def test_gaming_scene_keeps_companion_followup_enabled(self) -> None:
        voice = PersonaProactiveVoice()
        result = voice.evaluate_scene(
            user_id="scene-gaming-followup",
            context={
                "current_ts_ms": _make_ts_at_hour(15, 30),
                "pending_followup_topics": [{"topic_label": "深渊战斗"}],
                "scene_awareness": {
                    "scene_tags": ["battle"],
                    "current_activity": "gaming",
                    "companion_suitable": True,
                },
                "motive_state": {
                    "reach_out_drive": 0.62,
                    "care_drive": 0.48,
                    "withdraw_drive": 0.12,
                    "continuity_drive": 0.82,
                    "self_protect_drive": 0.08,
                    "dominant_motive": "continuity",
                },
            },
        )

        assert result is not None
        assert result.scene_type == SCENE_TOPIC_FOLLOWUP
        assert "scene=gaming" in result.motive_influence


def _make_ts_at_hour(hour: int, minute: int = 0) -> int:
    import datetime

    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(target.timestamp() * 1000)


def test_gaming_activity_prefers_topic_followup_over_long_absent_reconnect() -> None:
    voice = PersonaProactiveVoice()
    ts = _make_ts_at_hour(15, 30)
    result = voice.evaluate_scene(
        user_id="scene-gaming-priority",
        context={
            "current_ts_ms": ts,
            "last_interaction_ts_ms": ts - 5 * 24 * 60 * 60 * 1000,
            "pending_followup_topics": [{"topic_label": "副本"}],
            "activity_monitor": {
                "is_gaming": True,
                "recent_event_type": "gaming_detected",
                "recent_event_age_s": 30.0,
            },
            "scene_awareness": {
                "scene_tags": ["battle"],
                "current_activity": "gaming",
                "companion_suitable": True,
            },
            "motive_state": {
                "reach_out_drive": 0.64,
                "care_drive": 0.52,
                "withdraw_drive": 0.12,
                "continuity_drive": 0.84,
                "self_protect_drive": 0.08,
                "dominant_motive": "continuity",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "你先专心打" in result.content
    assert "activity:gaming_detected" in result.motive_influence
    assert "activity:gaming_companion_followup" in result.motive_influence


def test_recent_wakeup_prefers_gentle_morning_greet_over_followup() -> None:
    voice = PersonaProactiveVoice()
    ts = _make_ts_at_hour(7, 30)
    result = voice.evaluate_scene(
        user_id="scene-wakeup-morning",
        context={
            "current_ts_ms": ts,
            "pending_followup_topics": [{"topic_label": "项目"}],
            "activity_monitor": {
                "recent_event_type": "wakeup",
                "recent_event_age_s": 20.0,
            },
            "motive_state": {
                "reach_out_drive": 0.42,
                "care_drive": 0.66,
                "withdraw_drive": 0.08,
                "continuity_drive": 0.74,
                "self_protect_drive": 0.08,
                "dominant_motive": "continuity",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_MORNING_GREET
    assert result.content == "醒啦？先慢慢清醒一下，我在。"
    assert "activity:wakeup" in result.motive_influence
    assert "activity:wakeup_gentle_morning" in result.motive_influence
