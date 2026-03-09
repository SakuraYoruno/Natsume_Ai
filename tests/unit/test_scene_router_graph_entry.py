from __future__ import annotations

from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_INTENT_SOURCE_NODE_ID,
    SCENE_EXEC_INTENT,
    SCENE_ROUTER_ENTRY_HANDLER_ID,
    SCENE_ROUTER_ENTRY_NODE_ID,
    SCENE_ROUTER_POST_CORE_HANDLER_ID,
    SCENE_ROUTER_POST_CORE_NODE_ID,
    SCENE_TYPE_AUTO,
    build_chat_graph_config,
    graph_scene_router_entry_handler,
    graph_scene_router_post_core_handler,
)


def test_scene_router_entry_handler_defaults_to_chat(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CHAT_ENABLE_PRECLASSIFIER", raising=False)
    payload = {"input": {"user_message": "你好呀"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": SCENE_ROUTER_ENTRY_NODE_ID})

    assert out["scene_type"] == "CHAT"
    assert out["__next_node__"] == CHAT_DIRECT_NODE_ID
    assert out["scene_type_source"] in {"graph_pre_classifier", "graph_default_chat"}
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("target") == "host"
    assert route_decision.get("route_target") == "host_8b"
    assert dict(route_decision.get("targets") or {}).get("host") is True


def test_scene_router_entry_handler_routes_code_request(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {"input": {"user_message": "请帮我写一段 python 代码"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": SCENE_ROUTER_ENTRY_NODE_ID})

    assert out["scene_type"] == "WORK_CODE"
    assert out["__next_node__"] == CHAT_INTENT_SOURCE_NODE_ID
    assert out["scene_type_source"] == "graph_pre_classifier"
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


def test_build_chat_graph_config_auto_includes_router_handlers() -> None:
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

    assert cfg["entry_node"] == SCENE_ROUTER_ENTRY_NODE_ID
    bindings = {row["node_id"]: row for row in cfg["node_bindings"]}
    assert bindings[SCENE_ROUTER_ENTRY_NODE_ID]["handler_id"] == SCENE_ROUTER_ENTRY_HANDLER_ID
    assert bindings[SCENE_ROUTER_POST_CORE_NODE_ID]["handler_id"] == SCENE_ROUTER_POST_CORE_HANDLER_ID
    assert cfg["graph_meta"]["scene_type"] == SCENE_TYPE_AUTO
