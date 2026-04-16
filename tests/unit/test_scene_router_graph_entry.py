from __future__ import annotations

from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_INTENT_SOURCE_NODE_ID,
    INTENT_AGENT_ENTRY_NODE_ID,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_ROUTER_ENTRY_HANDLER_ID,
    SCENE_ROUTER_ENTRY_NODE_ID,
    SCENE_ROUTER_POST_CORE_HANDLER_ID,
    SCENE_ROUTER_POST_CORE_NODE_ID,
    SCENE_TYPE_AUTO,
    SUPERVISOR_DISPATCH_NODE_ID,
    build_chat_graph_config,
    graph_scene_router_entry_handler,
    graph_scene_router_post_core_handler,
)


def test_scene_router_entry_handler_defaults_to_chat(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CHAT_ENABLE_PRECLASSIFIER", raising=False)
    payload = {"input": {"user_message": "你好呀"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": SCENE_ROUTER_ENTRY_NODE_ID})

    # Phase XV 后，默认返回 AUTO，由 cortex_tribe_decider 决策
    assert out["scene_type"] in {"CHAT", "AUTO"}
    assert out["scene_type_source"] in {"graph_pre_classifier", "graph_default_chat", "cortex_tribe_decider"}
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("target") == "cloud"
    assert route_decision.get("route_target") == "cloud"


def test_scene_router_entry_handler_routes_code_request(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {"input": {"user_message": "请帮我写一段 python 代码"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": SCENE_ROUTER_ENTRY_NODE_ID})

    # Phase XV 后，即使开启 preclassifier，也可能返回 AUTO
    assert out["scene_type"] in {"WORK_CODE", "AUTO"}
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("target") == "cloud"
    assert route_decision.get("route_target") == "cloud"


def test_scene_router_post_core_uses_scene_to_pick_branch() -> None:
    payload = {"scene_type": "WORK_TRANSLATE", "scene_type_source": "explicit"}
    out = graph_scene_router_post_core_handler(payload, {"node_id": SCENE_ROUTER_POST_CORE_NODE_ID})

    assert out["scene_type"] == "WORK_TRANSLATE"
    assert out["__next_node__"] == "workbench_translate"
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("target") == "cloud"
    assert route_decision.get("route_target") == "cloud"


def test_scene_router_post_core_exec_intent_targets_skills() -> None:
    payload = {"scene_type": SCENE_EXEC_INTENT, "scene_type_source": "explicit"}
    out = graph_scene_router_post_core_handler(payload, {"node_id": SCENE_ROUTER_POST_CORE_NODE_ID})

    assert out["__next_node__"] == "planner_constraints"
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("target") == "skills"
    assert route_decision.get("route_target") == "skills"


def test_build_chat_graph_config_by_execution_profile() -> None:
    """P1 先选图再跑图：按 scene_type 解析 execution_profile，CHAT→chat 图、AUTO→supervised 图、EXEC_INTENT→intent 图。"""
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
    """显式 AUTO 时 graph_meta.scene_type 仍为 AUTO。"""
    cfg = build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=lambda _: {"status": "OK"},
        scene_type=SCENE_TYPE_AUTO,
        scene_type_source="graph_entry_router",
    )
    assert cfg["graph_meta"]["scene_type"] == SCENE_TYPE_AUTO


def test_scene_router_entry_auto_routes_to_supervisor_dispatch() -> None:
    """AUTO 时 scene_router_entry 下一跳为 supervisor_dispatch（多 agent 实测闭环）。"""
    payload = {"graph": {"scene_type": "AUTO", "scene_type_source": "explicit"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": SCENE_ROUTER_ENTRY_NODE_ID})
    assert out["scene_type"] == SCENE_TYPE_AUTO
    assert out["scene_type_source"] == "explicit"
    assert out["__next_node__"] == SUPERVISOR_DISPATCH_NODE_ID
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("next_node") == SUPERVISOR_DISPATCH_NODE_ID
