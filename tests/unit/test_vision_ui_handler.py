"""
test_vision_ui_handler.py —— 任务4 单元测试

验证 SCENE_VISION_UI 专用视觉场景处理：
1. _vision_ui_sse_generator 正常流程（文本模式）
2. _vision_ui_sse_generator 图片模式（base64）
3. LLM 不可用时降级处理
4. ChatRequest.vision_context 字段解析
5. _build_vision_context_section 辅助函数
6. _has_image_input 辅助函数
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.chat_handler_vision import (
    _build_vision_context_section,
    _call_vision_llm,
    _has_image_input,
    _vision_ui_sse_generator,
    VISION_UI_NODE_SEQUENCE,
)
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


# ---------------------------------------------------------------------------
# 辅助：构造最小 ChatRequest mock
# ---------------------------------------------------------------------------

def _make_request_body(
    message: str = "帮我看看这个界面",
    vision_context: dict | None = None,
    emotion_context: dict | None = None,
) -> MagicMock:
    req = MagicMock()
    req.message = message
    req.session_id = "session-vision-test"
    req.thread_id = "thread-vision-test"
    req.mood_state = None
    req.emotion_tag = None
    req.scene_type = "SCENE_VISION_UI"
    req.emotion_context = emotion_context
    req.vision_context = vision_context
    req.hmac_signature = ""
    req.origin_ip = ""
    req.image_upload_consent = False
    req.response_format = "text"
    req.person_id = None
    return req


def _make_metrics() -> MagicMock:
    m = MagicMock()
    m.inc_chat = MagicMock()
    m.inc_rejection = MagicMock()
    m.inc_error = MagicMock()
    return m


def _make_governor_result() -> dict:
    return {
        "decision": "APPROVE",
        "reason_code": None,
        "intent_id": "intent-vision-test",
        "stage": "S_APPROVED",
        "tokens": {},
        "committed_route_token": None,
    }


def _parse_sse_events(events: list[str]) -> list[dict]:
    """解析 SSE 事件字符串为 dict 列表。"""
    result = []
    for event in events:
        if event.startswith("data: "):
            try:
                result.append(json.loads(event[6:].strip()))
            except json.JSONDecodeError:
                pass
    return result


# ---------------------------------------------------------------------------
# 测试：辅助函数
# ---------------------------------------------------------------------------

class TestVisionHelpers:
    """视觉辅助函数测试。"""

    def test_has_image_input_with_base64(self):
        """验证 base64 图片被识别为有图片输入。"""
        ctx = {"image_base64": "abc123=="}
        assert _has_image_input(ctx) is True

    def test_has_image_input_with_url(self):
        """验证 URL 图片被识别为有图片输入。"""
        ctx = {"image_url": "https://example.com/screenshot.png"}
        assert _has_image_input(ctx) is True

    def test_has_image_input_text_only(self):
        """验证纯文字描述不被识别为有图片输入。"""
        ctx = {"screen_description": "这是一个登录界面"}
        assert _has_image_input(ctx) is False

    def test_has_image_input_none(self):
        """验证 None 返回 False。"""
        assert _has_image_input(None) is False

    def test_has_image_input_empty(self):
        """验证空字典返回 False。"""
        assert _has_image_input({}) is False

    def test_build_vision_context_section_with_description(self):
        """验证有界面描述时生成正确的上下文段落。"""
        ctx = {
            "screen_description": "这是一个登录界面，有用户名和密码输入框",
            "app_name": "Chrome",
            "os_info": "Windows 11",
        }
        section = _build_vision_context_section(ctx)
        assert "登录界面" in section
        assert "Chrome" in section
        assert "Windows 11" in section

    def test_build_vision_context_section_none(self):
        """验证 None 返回降级提示。"""
        section = _build_vision_context_section(None)
        assert "未提供截图" in section or "为空" in section

    def test_build_vision_context_section_with_error(self):
        """验证错误信息被包含在上下文段落中。"""
        ctx = {"error_text": "Error: Access denied (0x80070005)"}
        section = _build_vision_context_section(ctx)
        assert "Access denied" in section


# ---------------------------------------------------------------------------
# 测试：_vision_ui_sse_generator 正常流程
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vision_templates_override_context_and_unavailable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_handler_vision.analysis_prompt.templates",
        content=json.dumps(
            {
                "no_screenshot_context": "（这里使用自定义截图缺失提示）",
                "screen_description_label": "画面：{value}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )
    store.upsert_template(
        "chat_handler_vision.fallback_texts",
        content=json.dumps(
            {
                "llm_unavailable": "这是自定义视觉不可用文案。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _build_vision_context_section(None) == "（这里使用自定义截图缺失提示）"
    assert "画面：登录页" in _build_vision_context_section({"screen_description": "登录页"})

    result = await _call_vision_llm(
        request_id="req-vision-template-override",
        user_message="帮我看看这个界面",
        vision_context=None,
        llm_runtime={
            "cloud_provider_name": "gemini",
            "cloud_model_id": "gemini-2.5-flash",
            "cloud_llm_api_key": "",
            "cloud_timeout_ms": 15000,
            "cloud_available": False,
        },
        ts_ms=1000,
    )

    assert result["narrative"] == "这是自定义视觉不可用文案。"


@pytest.mark.asyncio
async def test_vision_templates_override_hmac_rejected_narrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_handler_vision.fallback_texts",
        content=json.dumps(
            {
                "hmac_rejected": "这是自定义 HMAC 拒绝文案。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setenv("HOST_VISION_HMAC_SECRET", "unit-test-secret")

    request_body = _make_request_body()
    request_body.hmac_signature = "bad-signature"
    metrics = _make_metrics()

    events = []
    async for event in _vision_ui_sse_generator(
        request_body=request_body,
        request_id="req-vision-hmac-reject",
        session_id="session-vision-hmac-reject",
        ts_ms=1000,
        intent_draft={},
        scene_type="SCENE_VISION_UI",
        scene_type_source="pre_classifier",
        governor_result=_make_governor_result(),
        emotion_context={},
        emotion_snapshot={},
        intent_id="intent-vision-hmac-reject",
        reason_code=None,
        metrics=metrics,
    ):
        events.append(event)

    parsed = _parse_sse_events(events)
    commit_event = next(e for e in parsed if e.get("phase") == "COMMIT")
    assert commit_event.get("reason_code") == "VISION_HMAC_INVALID"
    assert commit_event.get("narrative") == "这是自定义 HMAC 拒绝文案。"


@pytest.mark.asyncio
async def test_vision_templates_override_prompts_and_empty_narrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_handler_vision.analysis_prompt.templates",
        content=json.dumps(
            {
                "system_prompt": "这是自定义 system prompt。",
                "user_prompt_template": "请求内容：{user_message}\n{vision_context_section}",
                "no_screenshot_context": "（这里使用自定义截图缺失提示）",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )
    store.upsert_template(
        "chat_handler_vision.fallback_texts",
        content=json.dumps(
            {
                "empty_narrative": "这是自定义空回复兜底。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, object] = {}

    def _fake_provider(payload: dict) -> dict:
        captured["payload"] = payload
        return {}

    result = await _call_vision_llm(
        request_id="req-vision-prompt-override",
        user_message="帮我看看这个界面",
        vision_context=None,
        llm_runtime={
            "cloud_provider_name": "gemini",
            "cloud_model_id": "gemini-2.5-flash",
            "cloud_llm_api_key": "test-key",
            "cloud_timeout_ms": 15000,
            "cloud_available": True,
            "cloud_provider_fn": _fake_provider,
        },
        ts_ms=1000,
    )

    payload = dict(captured["payload"] or {})
    messages = list(payload.get("messages") or [])
    assert messages[0]["content"] == "这是自定义 system prompt。"
    assert messages[1]["content"] == "请求内容：帮我看看这个界面\n（这里使用自定义截图缺失提示）"
    assert result["narrative"] == "这是自定义空回复兜底。"


def test_builtin_vision_templates_are_clean() -> None:
    analysis_templates = resolve_prompt_template_json_merged("chat_handler_vision.analysis_prompt.templates")
    fallback_templates = resolve_prompt_template_json_merged("chat_handler_vision.fallback_texts")

    assert analysis_templates["context_title"] == "视觉上下文："
    assert analysis_templates["screen_description_label"] == "界面描述：{value}"
    assert fallback_templates["hmac_rejected"] == "啊啦，请求签名验证失败呢，请检查一下鉴权配置。"
    assert fallback_templates["origin_not_allowed"] == "啊啦，这个请求来源不在允许范围内呢。"


class TestVisionUiSseGenerator:
    """_vision_ui_sse_generator 测试。"""

    @pytest.mark.asyncio
    async def test_text_only_mode_yields_commit(self):
        """验证纯文字模式（无图片）能正常产出 COMMIT 事件。"""
        request_body = _make_request_body(message="帮我看看这个登录界面怎么操作")
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        fake_llm_result = {
            "narrative": "这是一个标准的登录界面，你需要在用户名框输入账号，密码框输入密码，然后点击登录按钮。",
            "model_used": "gemini-2.5-flash",
            "provider": "gemini",
            "status": "OK",
            "reason_code": None,
        }

        with patch(
            "host_brain.app.chat_handler_vision._call_vision_llm",
            new_callable=AsyncMock,
            return_value=fake_llm_result,
        ), patch(
            "host_brain.app.chat_handler_vision.resolve_host_llm_runtime_async",
            new_callable=AsyncMock,
            return_value={"cloud_provider_name": "gemini", "cloud_model_id": "gemini-2.5-flash",
                          "cloud_llm_api_key": "test-key", "cloud_timeout_ms": 15000,
                          "cloud_available": True, "cloud_provider_fn": MagicMock()},
        ), patch(
            "host_brain.app.chat_handler_vision.persist_chat_episodic_memory",
            return_value={"status": "OK", "memory_id": "mem-001"},
        ), patch(
            "host_brain.app.chat_handler_vision.get_episodic_store",
            return_value=MagicMock(),
        ):
            collected = []
            async for event in _vision_ui_sse_generator(
                request_body=request_body,
                request_id="req-vision-001",
                session_id="session-vision-001",
                ts_ms=1000000,
                intent_draft={"id": "intent-vision-001", "action": "narrative_only"},
                scene_type="SCENE_VISION_UI",
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-vision-001",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        parsed = _parse_sse_events(collected)
        phases = [e.get("phase") for e in parsed]

        # 验证节点级进度通知
        assert "GRAPH_START" in phases
        assert "NODE_START" in phases
        assert "NODE_DONE" in phases
        assert "GRAPH_DONE" in phases
        assert "COMMIT" in phases

        # 验证 COMMIT 事件内容
        commit_event = next(e for e in parsed if e.get("phase") == "COMMIT")
        assert commit_event.get("decision") == "APPROVE"
        assert "登录界面" in str(commit_event.get("narrative") or "")

    @pytest.mark.asyncio
    async def test_graph_start_contains_vision_node_sequence(self):
        """验证 GRAPH_START 事件包含正确的视觉节点序列。"""
        request_body = _make_request_body()
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        fake_llm_result = {
            "narrative": "这是一个界面。",
            "model_used": "gemini-2.5-flash",
            "provider": "gemini",
            "status": "OK",
            "reason_code": None,
        }

        with patch(
            "host_brain.app.chat_handler_vision._call_vision_llm",
            new_callable=AsyncMock,
            return_value=fake_llm_result,
        ), patch(
            "host_brain.app.chat_handler_vision.resolve_host_llm_runtime_async",
            new_callable=AsyncMock,
            return_value={"cloud_provider_name": "gemini", "cloud_model_id": "gemini-2.5-flash",
                          "cloud_llm_api_key": "test-key", "cloud_timeout_ms": 15000,
                          "cloud_available": True, "cloud_provider_fn": MagicMock()},
        ), patch(
            "host_brain.app.chat_handler_vision.persist_chat_episodic_memory",
            return_value={"status": "OK", "memory_id": "mem-002"},
        ), patch(
            "host_brain.app.chat_handler_vision.get_episodic_store",
            return_value=MagicMock(),
        ):
            collected = []
            async for event in _vision_ui_sse_generator(
                request_body=request_body,
                request_id="req-vision-002",
                session_id="session-vision-002",
                ts_ms=1000000,
                intent_draft={"id": "intent-vision-002", "action": "narrative_only"},
                scene_type="SCENE_VISION_UI",
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-vision-002",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        parsed = _parse_sse_events(collected)
        graph_start = next(e for e in parsed if e.get("phase") == "GRAPH_START")
        assert graph_start.get("scene_type") == "VISION_UI"  # SCENE_VISION_UI = "VISION_UI"
        assert graph_start.get("node_sequence") == VISION_UI_NODE_SEQUENCE
        assert graph_start.get("node_count") == len(VISION_UI_NODE_SEQUENCE)

    @pytest.mark.asyncio
    async def test_vision_context_field_used_when_present(self):
        """验证 request_body.vision_context 字段被正确使用。"""
        vision_ctx = {
            "screen_description": "这是一个错误对话框",
            "error_text": "Error: File not found",
            "app_name": "Notepad",
        }
        request_body = _make_request_body(
            message="这个错误是什么意思？",
            vision_context=vision_ctx,
        )
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        captured_kwargs = {}

        async def _capture_llm_call(**kwargs):
            captured_kwargs.update(kwargs)
            return {
                "narrative": "这是一个文件未找到的错误。",
                "model_used": "gemini-2.5-flash",
                "provider": "gemini",
                "status": "OK",
                "reason_code": None,
            }

        with patch(
            "host_brain.app.chat_handler_vision._call_vision_llm",
            side_effect=_capture_llm_call,
        ), patch(
            "host_brain.app.chat_handler_vision.resolve_host_llm_runtime_async",
            new_callable=AsyncMock,
            return_value={"cloud_provider_name": "gemini", "cloud_model_id": "gemini-2.5-flash",
                          "cloud_llm_api_key": "test-key", "cloud_timeout_ms": 15000,
                          "cloud_available": True, "cloud_provider_fn": MagicMock()},
        ), patch(
            "host_brain.app.chat_handler_vision.persist_chat_episodic_memory",
            return_value={"status": "OK", "memory_id": "mem-003"},
        ), patch(
            "host_brain.app.chat_handler_vision.get_episodic_store",
            return_value=MagicMock(),
        ):
            async for _ in _vision_ui_sse_generator(
                request_body=request_body,
                request_id="req-vision-003",
                session_id="session-vision-003",
                ts_ms=1000000,
                intent_draft={"id": "intent-vision-003", "action": "narrative_only"},
                scene_type="SCENE_VISION_UI",
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-vision-003",
                reason_code=None,
                metrics=metrics,
            ):
                pass

        # 验证 vision_context 被正确传递给 LLM 调用
        assert captured_kwargs.get("vision_context") == vision_ctx

    @pytest.mark.asyncio
    async def test_llm_unavailable_yields_degraded_commit(self):
        """验证 LLM 不可用时产出降级 COMMIT 事件（decision=APPROVE，含降级提示）。"""
        request_body = _make_request_body()
        metrics = _make_metrics()
        governor_result = _make_governor_result()

        with patch(
            "host_brain.app.chat_handler_vision._call_vision_llm",
            new_callable=AsyncMock,
            return_value={
                "narrative": "啊啦，视觉分析功能现在暂时不可用呢。",
                "model_used": "gemini-2.5-flash",
                "provider": "gemini",
                "status": "DEGRADED",
                "reason_code": "VISION_LLM_UNAVAILABLE",
            },
        ), patch(
            "host_brain.app.chat_handler_vision.resolve_host_llm_runtime_async",
            new_callable=AsyncMock,
            return_value={"cloud_provider_name": "gemini", "cloud_model_id": "gemini-2.5-flash",
                          "cloud_llm_api_key": "", "cloud_timeout_ms": 15000,
                          "cloud_available": False, "cloud_provider_fn": MagicMock()},
        ), patch(
            "host_brain.app.chat_handler_vision.persist_chat_episodic_memory",
            return_value={"status": "SKIPPED"},
        ), patch(
            "host_brain.app.chat_handler_vision.get_episodic_store",
            return_value=MagicMock(),
        ):
            collected = []
            async for event in _vision_ui_sse_generator(
                request_body=request_body,
                request_id="req-vision-004",
                session_id="session-vision-004",
                ts_ms=1000000,
                intent_draft={"id": "intent-vision-004", "action": "narrative_only"},
                scene_type="SCENE_VISION_UI",
                scene_type_source="pre_classifier",
                governor_result=governor_result,
                emotion_context={},
                emotion_snapshot={},
                intent_id="intent-vision-004",
                reason_code=None,
                metrics=metrics,
            ):
                collected.append(event)

        parsed = _parse_sse_events(collected)
        commit_event = next(e for e in parsed if e.get("phase") == "COMMIT")
        # 降级时仍然 APPROVE（不拒绝用户），但 narrative 包含降级提示
        assert commit_event.get("decision") == "APPROVE"
        assert commit_event.get("narrative") is not None


# ---------------------------------------------------------------------------
# 测试：ChatRequest.vision_context 字段
# ---------------------------------------------------------------------------

class TestChatRequestVisionContext:
    """ChatRequest.vision_context 字段测试。"""

    def test_vision_context_field_exists(self):
        """验证 ChatRequest 有 vision_context 字段。"""
        from host_brain.app.schemas import ChatRequest
        req = ChatRequest(message="test")
        assert hasattr(req, "vision_context")
        assert req.vision_context is None

    def test_vision_context_accepts_dict(self):
        """验证 vision_context 接受字典值。"""
        from host_brain.app.schemas import ChatRequest
        ctx = {
            "image_base64": "abc123==",
            "image_mime": "image/png",
            "screen_description": "测试界面",
        }
        req = ChatRequest(message="test", vision_context=ctx)
        assert req.vision_context == ctx

    def test_vision_context_with_scene_type(self):
        """验证 vision_context 和 scene_type 可以同时设置。"""
        from host_brain.app.schemas import ChatRequest
        req = ChatRequest(
            message="帮我看看这个界面",
            scene_type="SCENE_VISION_UI",
            vision_context={"screen_description": "登录界面"},
        )
        assert req.scene_type == "SCENE_VISION_UI"
        assert req.vision_context is not None
