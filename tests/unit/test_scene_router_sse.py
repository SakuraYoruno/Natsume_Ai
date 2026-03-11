"""
test_scene_router_sse.py —— 任务3 单元测试

验证 SceneRouter.route_sse() 节点化路由：
1. SCENE_CHAT → _sse_event_generator
2. SCENE_WORK_CODE / SCENE_WORK_TRANSLATE / SCENE_EXEC_INTENT → _complex_scene_sse_generator
3. SCENE_VISION_UI → _vision_ui_sse_generator
4. 自定义注册生成器优先级高于内置路由
5. _unified_chat_sse_generator 通过 SceneRouter 分流（集成验证）
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.graph.main_chain import SceneRouter


# ---------------------------------------------------------------------------
# 辅助：构造最小 ChatRequest mock
# ---------------------------------------------------------------------------

def _make_request_body(message: str = "你好", scene_type: str | None = None) -> MagicMock:
    req = MagicMock()
    req.message = message
    req.session_id = "session-test"
    req.thread_id = "thread-test"
    req.mood_state = None
    req.emotion_tag = None
    req.scene_type = scene_type
    req.emotion_context = None
    req.vision_context = None
    return req


def _make_metrics() -> MagicMock:
    m = MagicMock()
    m.inc_chat = MagicMock()
    m.inc_rejection = MagicMock()
    m.inc_error = MagicMock()
    return m


def _make_governor_result(decision: str = "APPROVE") -> dict:
    return {
        "decision": decision,
        "reason_code": None,
        "intent_id": "intent-test",
        "stage": "S_APPROVED",
        "tokens": {},
        "committed_route_token": None,
    }


async def _fake_sse_generator(**kwargs):
    """通用假 SSE 生成器，产出一个 COMMIT 事件。"""
    yield 'data: {"phase": "COMMIT", "done": true}\n\n'


# ---------------------------------------------------------------------------
# 测试：SceneRouter.route_sse 场景分流
# ---------------------------------------------------------------------------

class TestSceneRouterSse:
    """SceneRouter.route_sse() 场景分流测试。"""

    @pytest.mark.asyncio
    async def test_scene_chat_routes_to_sse_event_generator(self):
        """验证 SCENE_CHAT 路由到 _sse_event_generator。"""
        request_body = _make_request_body()
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_stream_handler._sse_event_generator",
            side_effect=_fake_sse_generator,
        ) as mock_gen:
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="CHAT",  # SCENE_CHAT = "CHAT"
                request_body=request_body,
                request_id="req-001",
                session_id="session-001",
                ts_ms=1000000,
                intent_draft={"id": "intent-001", "action": "narrative_only"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-001",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1
        assert '"phase": "COMMIT"' in collected[0]

    @pytest.mark.asyncio
    async def test_scene_work_code_routes_to_complex_generator(self):
        """验证 SCENE_WORK_CODE 路由到 _complex_scene_sse_generator。"""
        request_body = _make_request_body(message="帮我写个 Python 函数")
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_complex._complex_scene_sse_generator",
            side_effect=_fake_sse_generator,
        ) as mock_gen:
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="WORK_CODE",  # SCENE_WORK_CODE = "WORK_CODE"
                request_body=request_body,
                request_id="req-002",
                session_id="session-002",
                ts_ms=1000000,
                intent_draft={"id": "intent-002", "action": "work_code"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-002",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_scene_work_translate_routes_to_complex_generator(self):
        """验证 SCENE_WORK_TRANSLATE 路由到 _complex_scene_sse_generator。"""
        request_body = _make_request_body(message="翻译这段文字")
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_complex._complex_scene_sse_generator",
            side_effect=_fake_sse_generator,
        ):
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="WORK_TRANSLATE",  # SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
                request_body=request_body,
                request_id="req-003",
                session_id="session-003",
                ts_ms=1000000,
                intent_draft={"id": "intent-003", "action": "work_translate"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-003",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_scene_exec_intent_routes_to_complex_generator(self):
        """验证 SCENE_EXEC_INTENT 路由到 _complex_scene_sse_generator。"""
        request_body = _make_request_body(message="帮我执行这个脚本")
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_complex._complex_scene_sse_generator",
            side_effect=_fake_sse_generator,
        ):
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="EXEC_INTENT",  # SCENE_EXEC_INTENT = "EXEC_INTENT"
                request_body=request_body,
                request_id="req-004",
                session_id="session-004",
                ts_ms=1000000,
                intent_draft={"id": "intent-004", "action": "exec_intent"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-004",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_scene_vision_ui_routes_to_vision_generator(self):
        """验证 SCENE_VISION_UI 路由到 _vision_ui_sse_generator。"""
        request_body = _make_request_body(message="帮我看看这个界面")
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_vision._vision_ui_sse_generator",
            side_effect=_fake_sse_generator,
        ):
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="VISION_UI",  # SCENE_VISION_UI = "VISION_UI"
                request_body=request_body,
                request_id="req-005",
                session_id="session-005",
                ts_ms=1000000,
                intent_draft={"id": "intent-005", "action": "narrative_only"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-005",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_custom_registered_generator_takes_priority(self):
        """验证自定义注册生成器优先级高于内置路由。"""
        custom_events = ['data: {"phase": "CUSTOM", "done": true}\n\n']

        async def _custom_gen(**kwargs):
            for e in custom_events:
                yield e

        # 注册自定义生成器
        SceneRouter.register_sse_generator("SCENE_CUSTOM_TEST", _custom_gen)

        request_body = _make_request_body()
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        try:
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="SCENE_CUSTOM_TEST",
                request_body=request_body,
                request_id="req-006",
                session_id="session-006",
                ts_ms=1000000,
                intent_draft={"id": "intent-006", "action": "narrative_only"},
                scene_type_source="explicit",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-006",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

            assert collected == custom_events
        finally:
            # 清理注册表，避免影响其他测试
            SceneRouter._sse_generators.pop("SCENE_CUSTOM_TEST", None)

    @pytest.mark.asyncio
    async def test_unknown_scene_falls_back_to_complex_generator(self):
        """验证未知场景类型兜底到 _complex_scene_sse_generator。"""
        request_body = _make_request_body()
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_complex._complex_scene_sse_generator",
            side_effect=_fake_sse_generator,
        ):
            collected = []
            async for event in SceneRouter.route_sse(
                scene_type="SCENE_UNKNOWN_XYZ",
                request_body=request_body,
                request_id="req-007",
                session_id="session-007",
                ts_ms=1000000,
                intent_draft={"id": "intent-007", "action": "narrative_only"},
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-007",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        assert len(collected) == 1
