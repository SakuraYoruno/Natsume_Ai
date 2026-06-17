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
    assert classify_scene_type_by_rules("帮我对 target=example.com 做授权安全扫描，只做侦察") == SCENE_EXEC_INTENT


def test_thalamus_rules_keep_non_action_chat_in_chat_scene() -> None:
    assert classify_scene_type_by_rules("帮我安装一下这首歌") == SCENE_CHAT
    assert classify_scene_type_by_rules("有没有更好的方案") == SCENE_CHAT
    assert classify_scene_type_by_rules("听得到吗") == SCENE_CHAT
    assert classify_scene_type_by_rules("继续") == SCENE_CHAT
    assert classify_scene_type_by_rules("别讲测试指标，直接说你现在对这个主链体感的判断，像正常聊天那样。") == SCENE_CHAT
    assert classify_scene_type_by_rules("如果我要继续压整条聊天链路的耗时，你会先怀疑哪一段？不要列清单。") == SCENE_CHAT
    assert classify_scene_type_by_rules("你刚刚说了什么") == SCENE_CHAT
    assert classify_scene_type_by_rules("做个统计 token 消耗的插件") == SCENE_WORK_CODE
    assert classify_scene_type_by_rules("给我一个完整方案，不要改代码") == SCENE_EXEC_INTENT


def test_thalamus_scene_mapping_uses_canonical_intent_decision() -> None:
    assert (
        resolve_scene_type_from_intent_decision({"route": "skill", "target_id": "agent_worker"})
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
    assert (
        resolve_scene_type_from_intent_decision({"route": "delegate", "target_id": "pentest_agent"})
        == SCENE_EXEC_INTENT
    )
    assert resolve_scene_type_from_intent_decision({"route": "recent_turn"}) == SCENE_CHAT
    assert resolve_scene_type_from_intent_decision({"route": "task_progress"}) == SCENE_CHAT
    assert resolve_scene_type_from_intent_decision({"route": "chat"}) == SCENE_CHAT


def test_thalamus_route_and_scene_confusion_matrix_for_recent_workspace_phrases() -> None:
    cases = [
        ("开始吧", "delegate", "intent_agent", SCENE_EXEC_INTENT),
        ("继续", "chat", "", SCENE_CHAT),
        ("查看完整主链", "skill", "agent_inspect", SCENE_WORK_CODE),
        ("使用无头浏览器验证前端效果", "skill", "agent_frontend_acceptance", SCENE_WORK_CODE),
        ("打开设置", "skill", "open_app", SCENE_EXEC_INTENT),
        ("有没有更好的方案", None, None, SCENE_CHAT),
        ("帮我安装一下这首歌", None, None, SCENE_CHAT),
        ("听得到吗", "chat", "", SCENE_CHAT),
        ("刚才说到哪了", "recent_turn", "", SCENE_CHAT),
        ("你刚刚说了什么", "recent_turn", "", SCENE_CHAT),
        ("这次主链效果怎么样，短句回答", "task_progress", "", SCENE_CHAT),
        ("你觉得主链现在最该观察的风险是什么？用两句说。", "chat", "", SCENE_CHAT),
        ("做个统计 token 消耗的插件", "skill", "agent_worker", SCENE_WORK_CODE),
        ("帮我对 target=example.com 做授权安全扫描，只做侦察", "delegate", "pentest_agent", SCENE_EXEC_INTENT),
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


def test_chat_graph_can_route_direct_node_to_codex_spark(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SURFACE_REPLY_CODEX_EXPERIMENT_ENABLED", "true")
    monkeypatch.setenv("HOST_SURFACE_REPLY_CODEX_MODEL_ID", "gpt-5.3-codex-spark")
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.codex_cli_lm_available",
        lambda: True,
    )

    cfg = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_CHAT,
        scene_type_source="explicit",
    )

    direct_node = cfg["model_node_overrides"][CHAT_DIRECT_NODE_ID]
    assert cfg["graph_meta"]["chat_provider"] == "codex"
    assert cfg["graph_meta"]["chat_model_id"] == "gpt-5.3-codex-spark"
    assert direct_node["provider"] == "codex"
    assert direct_node["model_id"] == "gpt-5.3-codex-spark"
    assert direct_node["api_key"] is None
    assert direct_node["timeout_ms"] == 45000


def test_non_chat_graph_model_nodes_can_migrate_to_codex(monkeypatch) -> None:
    monkeypatch.setenv("HOST_GRAPH_CODEX_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-5.4-mini")
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.codex_cli_lm_available",
        lambda: True,
    )

    cfg = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_WORK_CODE,
        scene_type_source="explicit",
        cloud_provider_name="gemini",
        cloud_model_id="gemini-2.5-pro",
        cloud_api_key="test-key",
        cloud_available=True,
    )

    nodes = cfg["model_node_overrides"]
    assert nodes["core_reasoner_primary"]["provider"] == "codex"
    assert nodes["core_reasoner_primary"]["model_id"] == "gpt-5.4-mini"
    assert nodes["core_reasoner_primary"]["api_key"] is None
    assert nodes[CHAT_DIRECT_NODE_ID]["provider"] == "gemini"
