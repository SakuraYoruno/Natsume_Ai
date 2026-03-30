from __future__ import annotations

import pytest

from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_INTENT_SOURCE_NODE_ID,
    HOST_CHAT_ROUTE_CLOUD,
    HOST_CHAT_ROUTE_HOST_14B,
    HOST_CHAT_ROUTE_SKILLS,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    graph_scene_router_entry_handler,
    graph_scene_router_post_core_handler,
)


@pytest.mark.parametrize(
    "message,expected_scene,expected_next,expected_route_target",
    [
        ("请帮我写一段 python 代码", SCENE_WORK_CODE, CHAT_INTENT_SOURCE_NODE_ID, HOST_CHAT_ROUTE_CLOUD),
        ("帮我翻译这段话成日语", SCENE_WORK_TRANSLATE, CHAT_INTENT_SOURCE_NODE_ID, HOST_CHAT_ROUTE_CLOUD),
        ("帮我执行这个部署任务", SCENE_EXEC_INTENT, CHAT_INTENT_SOURCE_NODE_ID, HOST_CHAT_ROUTE_SKILLS),
        ("今天聊聊动漫推荐吧", SCENE_CHAT, CHAT_DIRECT_NODE_ID, HOST_CHAT_ROUTE_HOST_14B),
        # pre_intent_classifier 识别为 VISION_UI 时保留，下一跳为 chat_direct，route_target 由 _resolve 给出为 cloud
        ("截图并分析界面", SCENE_VISION_UI, CHAT_DIRECT_NODE_ID, HOST_CHAT_ROUTE_CLOUD),
        # 代码意图优先于娱乐关键词。
        ("我在看动漫，但请先写个脚本", SCENE_WORK_CODE, CHAT_INTENT_SOURCE_NODE_ID, HOST_CHAT_ROUTE_CLOUD),
        # 翻译优先于执行（pre_intent_classifier 顺序策略）。
        ("翻译并执行这段话", SCENE_WORK_TRANSLATE, CHAT_INTENT_SOURCE_NODE_ID, HOST_CHAT_ROUTE_CLOUD),
        ("", SCENE_CHAT, CHAT_DIRECT_NODE_ID, HOST_CHAT_ROUTE_HOST_14B),
    ],
)
def test_scene_router_entry_classification_dataset(
    monkeypatch,
    message: str,
    expected_scene: str,
    expected_next: str,
    expected_route_target: str,
) -> None:
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {"input": {"user_message": message}}
    out = graph_scene_router_entry_handler(payload, {"node_id": "scene_router_entry"})

    assert out["scene_type"] == expected_scene
    assert out["__next_node__"] == expected_next
    assert out["scene_type_source"] == "graph_pre_classifier"
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("route_target") == expected_route_target


def test_scene_router_entry_explicit_scene_type_overrides_message(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {
        "scene_type": SCENE_WORK_CODE,
        "input": {"user_message": "你好，今天天气怎么样"},
    }
    out = graph_scene_router_entry_handler(payload, {"node_id": "scene_router_entry"})

    assert out["scene_type"] == SCENE_WORK_CODE
    assert out["scene_type_source"] == "explicit"
    assert out["__next_node__"] == CHAT_INTENT_SOURCE_NODE_ID
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("route_target") == HOST_CHAT_ROUTE_CLOUD


def test_scene_router_entry_nested_explicit_scene_type_is_supported(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {
        "input": {
            "scene_type": SCENE_EXEC_INTENT,
            "user_message": "只是闲聊",
        }
    }
    out = graph_scene_router_entry_handler(payload, {"node_id": "scene_router_entry"})

    assert out["scene_type"] == SCENE_EXEC_INTENT
    assert out["scene_type_source"] == "explicit"
    assert out["__next_node__"] == CHAT_INTENT_SOURCE_NODE_ID
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("route_target") == HOST_CHAT_ROUTE_SKILLS


def test_scene_router_entry_invalid_explicit_scene_type_falls_back_to_chat(monkeypatch) -> None:
    """未在 _normalize_scene_type 白名单中的显式 scene_type 归一化为 CHAT。"""
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "1")
    payload = {"scene_type": "INVALID_TYPE_XYZ", "input": {"user_message": "帮我截图"}}
    out = graph_scene_router_entry_handler(payload, {"node_id": "scene_router_entry"})

    assert out["scene_type"] == SCENE_CHAT
    assert out["scene_type_source"] == "explicit"
    assert out["__next_node__"] == CHAT_DIRECT_NODE_ID
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("route_target") == HOST_CHAT_ROUTE_HOST_14B


@pytest.mark.parametrize(
    "scene_type,expected_next,expected_route_target",
    [
        (SCENE_CHAT, "consistency_verifier", HOST_CHAT_ROUTE_CLOUD),
        (SCENE_WORK_TRANSLATE, "workbench_translate", HOST_CHAT_ROUTE_CLOUD),
        (SCENE_WORK_CODE, "workbench_code_assist", HOST_CHAT_ROUTE_CLOUD),
        (SCENE_EXEC_INTENT, "planner_constraints", HOST_CHAT_ROUTE_SKILLS),
    ],
)
def test_scene_router_post_core_dataset(
    scene_type: str,
    expected_next: str,
    expected_route_target: str,
) -> None:
    payload = {"scene_type": scene_type, "scene_type_source": "dataset"}
    out = graph_scene_router_post_core_handler(payload, {"node_id": "scene_router_post_core"})

    assert out["scene_type"] == scene_type
    assert out["__next_node__"] == expected_next
    route_decision = dict(out.get("route_decision") or {})
    assert route_decision.get("route_target") == expected_route_target
