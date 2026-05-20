from __future__ import annotations

from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    INTENT_AGENT_ENTRY_NODE_ID,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_TYPE_AUTO,
    SUPERVISOR_DISPATCH_NODE_ID,
    build_chat_graph_config,
)
from host_brain.app.prompt_builder import (
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.tribe.thalamus_agent import (
    classify_scene_type_by_rules,
    classify_task_intent_by_rules,
    resolve_scene_type_from_intent_decision,
)


def test_thalamus_rules_classify_core_scene_types() -> None:
    assert classify_scene_type_by_rules("你好") == SCENE_CHAT
    assert classify_scene_type_by_rules("帮我翻译这段文字") == SCENE_WORK_TRANSLATE
    assert classify_scene_type_by_rules("请帮我写一段 python 代码") == SCENE_WORK_CODE
    assert classify_scene_type_by_rules("帮我执行这个任务") == SCENE_EXEC_INTENT
    assert classify_scene_type_by_rules("帮我做这件事") == SCENE_EXEC_INTENT
    assert classify_scene_type_by_rules("开始吧") == SCENE_EXEC_INTENT
    assert classify_scene_type_by_rules("打开设置") == SCENE_EXEC_INTENT
    assert classify_scene_type_by_rules("setup the environment") == SCENE_EXEC_INTENT
    assert classify_scene_type_by_rules("看一下我的屏幕截图") == SCENE_VISION_UI
    assert classify_scene_type_by_rules("查看完整主链") == SCENE_WORK_CODE
    assert classify_scene_type_by_rules("使用无头浏览器验证前端效果") == SCENE_WORK_CODE


def test_thalamus_rules_keep_non_action_chat_in_chat_scene() -> None:
    assert classify_scene_type_by_rules("帮我安装一下这首歌") == SCENE_CHAT
    assert classify_scene_type_by_rules("有没有更好的方案") == SCENE_CHAT
    assert classify_scene_type_by_rules("给我一个完整方案，不要改代码") == SCENE_EXEC_INTENT


def test_thalamus_scene_mapping_uses_canonical_intent_decision() -> None:
    assert (
        resolve_scene_type_from_intent_decision({"route": "skill", "target_id": "codex_worker"})
        == SCENE_WORK_CODE
    )
    assert (
        resolve_scene_type_from_intent_decision({"route": "delegate", "target_id": "translate_agent"})
        == SCENE_WORK_TRANSLATE
    )
    assert (
        resolve_scene_type_from_intent_decision({"route": "delegate", "target_id": "vision_agent"})
        == SCENE_VISION_UI
    )
    assert (
        resolve_scene_type_from_intent_decision({"route": "delegate", "target_id": "intent_agent"})
        == SCENE_EXEC_INTENT
    )
    assert resolve_scene_type_from_intent_decision({"route": "chat"}) == SCENE_CHAT


def test_thalamus_route_and_scene_confusion_matrix_for_recent_workspace_phrases() -> None:
    cases = [
        ("开始吧", "delegate", "intent_agent", SCENE_EXEC_INTENT),
        ("查看完整主链", "skill", "codex_inspect", SCENE_WORK_CODE),
        ("使用无头浏览器验证前端效果", "skill", "codex_worker", SCENE_WORK_CODE),
        ("打开设置", "skill", "open_app", SCENE_EXEC_INTENT),
        ("有没有更好的方案", None, None, SCENE_CHAT),
        ("帮我安装一下这首歌", None, None, SCENE_CHAT),
    ]

    for text, expected_route, expected_target, expected_scene in cases:
        decision = classify_task_intent_by_rules(text)
        if expected_route is None:
            assert decision is None or decision["route"] == "chat"
        else:
            assert decision is not None
            assert decision["route"] == expected_route
            assert decision["target_id"] == expected_target
        assert classify_scene_type_by_rules(text) == expected_scene


def test_build_chat_graph_config_by_execution_profile() -> None:
    cfg_chat = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_CHAT,
        scene_type_source="explicit",
    )
    assert cfg_chat["entry_node"] == CHAT_DIRECT_NODE_ID
    assert cfg_chat["graph_meta"]["execution_profile"] == PROFILE_CHAT_FAST
    assert cfg_chat["graph_meta"]["scene_type"] == SCENE_CHAT

    cfg_auto = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_TYPE_AUTO,
        scene_type_source="explicit",
    )
    assert cfg_auto["entry_node"] == SUPERVISOR_DISPATCH_NODE_ID
    assert cfg_auto["graph_meta"]["execution_profile"] == PROFILE_WORKFLOW_SUPERVISED
    assert cfg_auto["graph_meta"]["scene_type"] == SCENE_TYPE_AUTO

    cfg_intent = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_EXEC_INTENT,
        scene_type_source="explicit",
    )
    assert cfg_intent["entry_node"] == INTENT_AGENT_ENTRY_NODE_ID
    assert cfg_intent["graph_meta"]["execution_profile"] == PROFILE_INTENT_EXEC
    assert cfg_intent["graph_meta"]["scene_type"] == SCENE_EXEC_INTENT


def test_build_chat_graph_config_auto_meta_preserved() -> None:
    cfg = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_TYPE_AUTO,
        scene_type_source="thalamus_agent",
    )
    assert cfg["graph_meta"]["scene_type"] == SCENE_TYPE_AUTO
