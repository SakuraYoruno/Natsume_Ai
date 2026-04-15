"""graph_config_router.py — 场景路由与预分类器。

拆分自 graph_config_builder.py（P2-11）：
  - pre_intent_classifier: 轻量规则预分类器
  - resolve_scene_node_sequence: 按场景返回节点序列
  - resolve_graph_entry_scene_type: 图入口场景类型解析
  - graph_scene_router_entry_handler / graph_scene_router_post_core_handler
  - resolve_chat_scene_type / resolve_chat_retrieval_gate
  - build_unified_route_decision
  - 场景路由相关常量
"""
from __future__ import annotations

import os
from typing import Any

from host_brain.app.prompt_builder import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_GATEWAY,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)

# ---------------------------------------------------------------------------
# 场景路由常量
# ---------------------------------------------------------------------------
CHAT_INTENT_SOURCE_NODE_ID = "core_reasoner_primary"
SCENE_ROUTER_ENTRY_NODE_ID = "scene_router_entry"
SCENE_ROUTER_POST_CORE_NODE_ID = "scene_router_post_core"
SCENE_ROUTER_ENTRY_HANDLER_ID = "scene_router_entry"
SCENE_ROUTER_POST_CORE_HANDLER_ID = "scene_router_post_core"
SCENE_TYPE_AUTO = "AUTO"

SUPERVISOR_DISPATCH_NODE_ID = "supervisor_dispatch"
SUPERVISOR_DISPATCH_HANDLER_ID = "supervisor_dispatch"

HOST_CHAT_ROUTE_HOST_14B = "host_14b"
HOST_CHAT_ROUTE_CLOUD = "cloud"
HOST_CHAT_ROUTE_SKILLS = "skills"

ROUTE_DECISION_VERSION = "route.v1"
ROUTE_TARGET_HOST = "host"
ROUTE_TARGET_EDGE = "edge"
ROUTE_TARGET_CLOUD = "cloud"
ROUTE_TARGET_SKILLS = "skills"

ALLOWED_EXPLICIT_SCENE_TYPES: frozenset[str] = frozenset({
    SCENE_CHAT, SCENE_WORK_CODE, SCENE_WORK_TRANSLATE,
    SCENE_EXEC_INTENT, SCENE_VISION_UI, SCENE_TYPE_AUTO,
})

_PIC_VISION_UI_KEYWORDS = frozenset([
    "screenshot", "screen", "ui", "window", "interface", "capture",
    "screen shot", "desktop", "look at the screen",
    "截图", "屏幕", "界面", "画面", "看一下屏幕", "看看这个界面",
])
_PIC_WORK_CODE_KEYWORDS = frozenset([
    "code", "coding", "programming", "function", "script", "program",
    "python", "javascript", "typescript", "java", "golang", "rust", "c++",
    "debug", "bug", "class", "algorithm", "implement", "develop",
    "代码", "编程", "写代码", "写个函数", "函数", "脚本", "调试", "类", "实现", "开发",
])
_PIC_ENTERTAINMENT_KEYWORDS = frozenset([
    "动漫", "漫画", "番剧", "动画", "アニメ", "マンガ", "漫",
    "电影", "电视剧", "剧集", "综艺", "映画", "ドラマ",
    "音乐", "歌曲", "歌手", "专辑", "音楽", "歌",
    "小说", "书", "读书", "本", "読書",
    "游戏", "ゲーム", "玩",
    "爱好", "兴趣", "趣味",
])
_PIC_WORK_TRANSLATE_KEYWORDS = frozenset([
    "translate", "translation", "translate to", "translate into",
    "翻译", "翻成", "译成", "译为", "翻訳", "訳して",
])
_PIC_EXEC_INTENT_KEYWORDS = frozenset([
    "execute", "run", "setup", "configure", "start", "stop", "install",
    "执行", "运行", "设置", "配置", "启动", "关闭", "打开",
    "帮我做", "部署", "创建", "新建", "修改", "更新", "安装",
    "deploy", "build", "submit", "save",
])


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_scene_type(scene_type: str | None) -> str:
    scene = str(scene_type or "").strip().upper()
    if scene in {SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE, SCENE_TYPE_AUTO, SCENE_VISION_UI}:
        return scene
    return SCENE_CHAT


def _extract_scene_router_message(payload: dict[str, Any]) -> str:
    request_payload = payload.get("request")
    if isinstance(request_payload, dict):
        request_message = str(request_payload.get("user_message") or "").strip()
        if request_message:
            return request_message
    top_level_message = str(payload.get("user_message") or "").strip()
    if top_level_message:
        return top_level_message
    input_payload = payload.get("input")
    if isinstance(input_payload, dict):
        nested = str(input_payload.get("user_message") or "").strip()
        if nested:
            return nested
    return str(payload.get("message") or "").strip()


def _scene_router_entry_next_node(scene_type: str) -> str:
    if scene_type == SCENE_TYPE_AUTO:
        return SUPERVISOR_DISPATCH_NODE_ID
    if scene_type in {SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE}:
        return CHAT_INTENT_SOURCE_NODE_ID
    return "chat_direct_ja"


def _scene_router_post_core_next_node(scene_type: str) -> str:
    if scene_type == SCENE_WORK_TRANSLATE:
        return "workbench_translate"
    if scene_type == SCENE_WORK_CODE:
        return "workbench_code_assist"
    if scene_type == SCENE_EXEC_INTENT:
        return "planner_constraints"
    return "consistency_verifier"


def _resolve_scene_router_route_target(*, scene_type: str, next_node: str) -> str:
    resolved_scene = _normalize_scene_type(scene_type)
    resolved_next_node = str(next_node or "").strip()
    if resolved_scene == SCENE_EXEC_INTENT or resolved_next_node in {"planner_constraints", "tool_planner"}:
        return HOST_CHAT_ROUTE_SKILLS
    return HOST_CHAT_ROUTE_CLOUD


def _resolve_route_target_family(*, route_target: str, scene_type: str, next_node: str | None) -> str:
    normalized_target = str(route_target or "").strip().lower()
    resolved_scene = _normalize_scene_type(scene_type)
    resolved_next_node = str(next_node or "").strip()
    if normalized_target == HOST_CHAT_ROUTE_SKILLS:
        return ROUTE_TARGET_SKILLS
    if normalized_target == HOST_CHAT_ROUTE_HOST_14B:
        return ROUTE_TARGET_HOST
    if normalized_target == HOST_CHAT_ROUTE_CLOUD:
        return ROUTE_TARGET_CLOUD
    if resolved_scene == SCENE_EXEC_INTENT or resolved_next_node in {"planner_constraints", "tool_planner"}:
        return ROUTE_TARGET_SKILLS
    return ROUTE_TARGET_CLOUD


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------
def pre_intent_classifier(user_input: str) -> str:
    """[已废弃] 轻量规则预分类器。

    Phase XV 架构重设计后，意图识别统一由 cortex_route_decider.classify_task_intent() 完成。
    此函数仅保留以兼容 graph_config_builder 的导入，不应在新代码中调用。
    """
    text = str(user_input or "").strip().lower()
    if not text:
        return SCENE_CHAT
    for kw in _PIC_VISION_UI_KEYWORDS:
        if kw in text:
            return SCENE_VISION_UI
    has_entertainment = any(kw in text for kw in _PIC_ENTERTAINMENT_KEYWORDS)
    has_code = any(kw in text for kw in _PIC_WORK_CODE_KEYWORDS)
    if has_code:
        return SCENE_WORK_CODE
    if has_entertainment:
        return SCENE_CHAT
    for kw in _PIC_WORK_TRANSLATE_KEYWORDS:
        if kw in text:
            return SCENE_WORK_TRANSLATE
    for kw in _PIC_EXEC_INTENT_KEYWORDS:
        if kw in text:
            return SCENE_EXEC_INTENT
    return SCENE_CHAT


def resolve_scene_node_sequence(scene_type: str) -> list[str]:
    """按场景返回应执行的节点序列。"""
    scene = str(scene_type or SCENE_CHAT).strip().upper()
    if scene == SCENE_EXEC_INTENT:
        return ["core_reasoner_primary", "planner_constraints", "tool_planner", "safety_verifier", "consistency_verifier", "renderer_ja_c_style"]
    if scene == SCENE_WORK_TRANSLATE:
        return ["core_reasoner_primary", "workbench_translate", "consistency_verifier", "renderer_ja_c_style"]
    if scene == SCENE_WORK_CODE:
        return ["core_reasoner_primary", "workbench_code_assist", "consistency_verifier", "renderer_ja_c_style"]
    return ["chat_direct_ja"]


def build_unified_route_decision(
    *,
    scene_type: str | None,
    scene_type_source: str | None,
    route_target: str | None,
    route_reason_code: str | None = None,
    next_node: str | None = None,
    router_node: str | None = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    sys_mode: str | None = None,
) -> dict[str, Any]:
    resolved_scene = _normalize_scene_type(scene_type)
    resolved_next_node = str(next_node or "").strip() or None
    resolved_route_target = str(route_target or "").strip().lower()
    if not resolved_route_target:
        resolved_route_target = _resolve_scene_router_route_target(
            scene_type=resolved_scene,
            next_node=(resolved_next_node or _scene_router_entry_next_node(resolved_scene)),
        )
    resolved_target = _resolve_route_target_family(route_target=resolved_route_target, scene_type=resolved_scene, next_node=resolved_next_node)
    return {
        "version": ROUTE_DECISION_VERSION,
        "target": resolved_target,
        "targets": {ROUTE_TARGET_HOST: resolved_target == ROUTE_TARGET_HOST, ROUTE_TARGET_EDGE: resolved_target == ROUTE_TARGET_EDGE, ROUTE_TARGET_CLOUD: resolved_target == ROUTE_TARGET_CLOUD, ROUTE_TARGET_SKILLS: resolved_target == ROUTE_TARGET_SKILLS},
        "route_target": resolved_route_target,
        "route_reason_code": (str(route_reason_code or "").strip() or None),
        "scene_type": resolved_scene,
        "scene_type_source": (str(scene_type_source or "").strip() or "graph_entry_router"),
        "next_node": resolved_next_node,
        "router_node": (str(router_node or "").strip() or None),
        "provider": (str(provider_name or "").strip() or None),
        "model_id": (str(model_id or "").strip() or None),
        "sys_mode": (str(sys_mode or "").strip() or None),
    }


def resolve_graph_entry_scene_type(*, payload: dict[str, Any]) -> tuple[str, str]:
    graph_payload = payload.get("graph")
    if isinstance(graph_payload, dict):
        explicit_graph_scene = str(graph_payload.get("scene_type") or "").strip().upper()
        if explicit_graph_scene:
            return _normalize_scene_type(explicit_graph_scene), "explicit"
    explicit_scene = str(payload.get("scene_type") or "").strip().upper()
    if explicit_scene:
        return _normalize_scene_type(explicit_scene), "explicit"
    if isinstance(payload.get("input"), dict):
        explicit_nested = str(payload["input"].get("scene_type") or "").strip().upper()
        if explicit_nested:
            return _normalize_scene_type(explicit_nested), "explicit"
    # Phase XV：意图识别已下沉到 cortex_route_decider，此处不再调用 pre_intent_classifier。
    # graph 入口场景类型默认为 AUTO，由 cortex_orchestrator 在 tribe 层统一决策。
    return SCENE_TYPE_AUTO, "cortex_tribe_decider"


def graph_scene_router_entry_handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    resolved_scene, source = resolve_graph_entry_scene_type(payload=dict(payload or {}))
    next_node = _scene_router_entry_next_node(resolved_scene)
    route_decision = build_unified_route_decision(
        scene_type=resolved_scene, scene_type_source=source,
        route_target=_resolve_scene_router_route_target(scene_type=resolved_scene, next_node=next_node),
        next_node=next_node, router_node=str(context.get("node_id") or SCENE_ROUTER_ENTRY_NODE_ID),
    )
    out = dict(payload or {})
    out["scene_type"] = resolved_scene
    out["scene_type_source"] = source
    out["route_decision"] = route_decision
    out["scene_router_decision"] = {"router_node": str(context.get("node_id") or SCENE_ROUTER_ENTRY_NODE_ID), "scene_type": resolved_scene, "source": source, "next_node": next_node}
    out["__next_node__"] = next_node
    return out


def graph_scene_router_post_core_handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    resolved_scene = _normalize_scene_type(str(payload.get("scene_type") or ""))
    source = str(payload.get("scene_type_source") or "graph_entry_router")
    next_node = _scene_router_post_core_next_node(resolved_scene)
    route_decision = build_unified_route_decision(
        scene_type=resolved_scene, scene_type_source=source,
        route_target=_resolve_scene_router_route_target(scene_type=resolved_scene, next_node=next_node),
        next_node=next_node, router_node=str(context.get("node_id") or SCENE_ROUTER_POST_CORE_NODE_ID),
    )
    out = dict(payload or {})
    out["scene_type"] = resolved_scene
    out["scene_type_source"] = source
    out["route_decision"] = route_decision
    out["scene_router_decision"] = {"router_node": str(context.get("node_id") or SCENE_ROUTER_POST_CORE_NODE_ID), "scene_type": resolved_scene, "source": source, "next_node": next_node}
    out["__next_node__"] = next_node
    return out


def resolve_chat_scene_type(*, request_body: Any) -> tuple[str, str]:
    explicit_scene_type = str(getattr(request_body, "scene_type", None) or "").strip().upper()
    if explicit_scene_type:
        return explicit_scene_type, "explicit"
    # Phase XV：意图识别已下沉到 cortex_route_decider，此处不再调用 pre_intent_classifier。
    # 返回 AUTO，由 cortex_orchestrator 在 tribe 层统一决策。
    return SCENE_TYPE_AUTO, "cortex_tribe_decider"


def resolve_chat_retrieval_gate(*, scene_type: str) -> tuple[bool, str | None]:
    if not _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True):
        return False, "HOST_RETRIEVAL_DISABLED"
    if (str(scene_type or "").strip().upper() == SCENE_CHAT and _parse_bool(os.environ.get("HOST_CHAT_DISABLE_RETRIEVAL"), default=False)):
        return False, "HOST_CHAT_RETRIEVAL_DISABLED"
    return True, None
