"""Graph 配置构建模块。

从 fastapi_app.py 拆分，负责 Chat 图的配置构建和场景路由。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from host_brain.app.config import load_host_config
from host_brain.integrations.llm_gateway import (
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
)
from host_brain.app.prompt_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_RENDER_NODE_ID,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.app.provider_factory import (
    _GEMINI_PRIMARY_RETRY_NODE_ID,
    _GEMINI_PRIMARY_RETRY_MODEL_IDS,
    build_host_provider_fn,
    build_host_provider_fn_for_node,
    resolve_gemini_primary_fallback_model_id,
    resolve_host_llm_model_id,
)

CHAT_GRAPH_NODE_ORDER: list[str] = [
    CHAT_DIRECT_NODE_ID,
    "core_reasoner_primary",
    "core_reasoner_secondary",
    "planner_constraints",
    "tool_planner",
    "safety_verifier",
    "consistency_verifier",
    "renderer_ja_c_style",
    "workbench_translate",
    "workbench_code_assist",
]
CHAT_INTENT_SOURCE_NODE_ID = "core_reasoner_primary"

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
# 文化娱乐关键词白名单：命中时强制返回 SCENE_CHAT，避免被 WORK_CODE 误判
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


def pre_intent_classifier(user_input: str) -> str:
    """轻量规则预分类器，优先级从高到低：VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT。

    修复：文化娱乐关键词（动漫、书、电影等）默认归为 SCENE_CHAT，
    仅当同时命中代码关键词时才升级为 SCENE_WORK_CODE（代码意图优先级更高）。
    """
    text = str(user_input or "").strip().lower()
    if not text:
        return SCENE_CHAT
    for kw in _PIC_VISION_UI_KEYWORDS:
        if kw in text:
            return SCENE_VISION_UI
    # 检测是否命中娱乐关键词
    has_entertainment = any(kw in text for kw in _PIC_ENTERTAINMENT_KEYWORDS)
    has_code = any(kw in text for kw in _PIC_WORK_CODE_KEYWORDS)
    if has_code:
        # 代码意图优先级高于娱乐，即使同时命中娱乐关键词也判定为 WORK_CODE
        return SCENE_WORK_CODE
    if has_entertainment:
        # 纯娱乐话题，强制归为 CHAT，不触发多节点链路
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
        return [
            "core_reasoner_primary",
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == SCENE_WORK_TRANSLATE:
        return [
            "core_reasoner_primary",
            "workbench_translate",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == SCENE_WORK_CODE:
        return [
            "core_reasoner_primary",
            "workbench_code_assist",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    return [CHAT_DIRECT_NODE_ID]


def resolve_chat_scene_type(*, request_body: Any) -> tuple[str, str]:
    def _parse_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    explicit_scene_type = str(getattr(request_body, "scene_type", None) or "").strip().upper()
    if explicit_scene_type:
        return explicit_scene_type, "explicit"
    if _parse_bool(os.environ.get("HOST_CHAT_ENABLE_PRECLASSIFIER"), default=True):
        return pre_intent_classifier(request_body.message), "pre_classifier"
    return SCENE_CHAT, "default_chat"


def resolve_chat_retrieval_gate(*, scene_type: str) -> tuple[bool, str | None]:
    def _parse_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    if not _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True):
        return False, "HOST_RETRIEVAL_DISABLED"
    if (
        str(scene_type or "").strip().upper() == SCENE_CHAT
        and _parse_bool(os.environ.get("HOST_CHAT_DISABLE_RETRIEVAL"), default=False)
    ):
        return False, "HOST_CHAT_RETRIEVAL_DISABLED"
    return True, None


def resolve_host_llm_runtime() -> dict[str, Any]:
    config_result = load_host_config()
    provider_name = str(os.environ.get("HOST_LLM_PROVIDER") or PROVIDER_OPENAI).strip().lower() or PROVIDER_OPENAI
    timeout_ms = 15_000
    llm_api_key: str | None = None
    available = True
    if config_result.success and config_result.config is not None:
        provider_name = str(config_result.config.llm_provider or provider_name).strip().lower() or provider_name
        timeout_ms = int(config_result.config.llm_timeout_ms)
        llm_api_key = config_result.config.llm_api_key
        available = (provider_name != PROVIDER_DISABLED)
    model_id = resolve_host_llm_model_id(provider_name)
    provider_fn = build_host_provider_fn(
        provider_name=provider_name,
        model_id=model_id,
        api_key=llm_api_key,
        timeout_ms=timeout_ms,
    )
    return {
        "provider_name": provider_name,
        "model_id": model_id,
        "timeout_ms": timeout_ms,
        "llm_api_key": llm_api_key,
        "available": available,
        "provider_fn": provider_fn,
    }


def build_chat_graph_config(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    api_key: str | None,
    provider_fn: Callable[[dict[str, Any]], Any],
    scene_type: str = SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    _ = provider_fn
    _ = model_id
    resolved_scene_type = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    is_chat_fast_path = resolved_scene_type == SCENE_CHAT
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = "ja"
    base_timeout_ms = max(800, int(timeout_ms))
    resolved_provider = PROVIDER_GEMINI
    node_model_ids: dict[str, str] = {
        "core_reasoner_primary": "gemini-2.5-pro",
        "core_reasoner_secondary": "gemini-2.5-flash",
        "planner_constraints": "gemini-2.5-pro",
        "tool_planner": "gemini-2.5-flash",
        "safety_verifier": "gemini-2.5-flash-lite",
        "consistency_verifier": "gemini-2.5-flash-lite",
        "renderer_ja_c_style": "gemini-2.5-flash",
        "workbench_translate": "gemini-2.5-pro",
        "workbench_code_assist": "gemini-2.5-pro",
    }
    node_timeouts: dict[str, int] = {
        "core_reasoner_primary": min(base_timeout_ms, 12000),
        "core_reasoner_secondary": min(base_timeout_ms, 9000),
        "planner_constraints": min(base_timeout_ms, 8000),
        "tool_planner": min(base_timeout_ms, 8000),
        "safety_verifier": min(base_timeout_ms, 7000),
        "consistency_verifier": min(base_timeout_ms, 7000),
        "renderer_ja_c_style": min(base_timeout_ms, 9000),
        "workbench_translate": min(base_timeout_ms, 10000),
        "workbench_code_assist": min(base_timeout_ms, 10000),
    }
    if is_chat_fast_path:
        node_model_ids[CHAT_DIRECT_NODE_ID] = "gemini-2.5-flash"
        # 修复：使用 max 而非 min，确保超时不低于 10,000ms（需求 1.1、1.2）
        node_timeouts[CHAT_DIRECT_NODE_ID] = max(base_timeout_ms, 10_000)
        node_model_ids["core_reasoner_primary"] = "gemini-2.5-flash"
        node_timeouts["core_reasoner_primary"] = min(base_timeout_ms, 5000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 3000)
        node_timeouts["renderer_ja_c_style"] = min(base_timeout_ms, 4000)

    model_node_overrides: dict[str, dict[str, Any]] = {}
    for node_id in CHAT_GRAPH_NODE_ORDER:
        node_model_id = str(node_model_ids.get(node_id) or model_id).strip() or model_id
        node_timeout = int(node_timeouts.get(node_id, base_timeout_ms))
        node_override: dict[str, Any] = {
            "provider": resolved_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "api_key": str(api_key or "").strip() or None,
            "available": bool(available),
            "provider_fn": build_host_provider_fn_for_node(
                provider_name=resolved_provider,
                model_id=node_model_id,
                api_key=api_key,
                timeout_ms=node_timeout,
                node_id=node_id,
            ),
        }
        # 需求 2.1：CHAT 快速路径下，chat_direct_ja 节点限制 max_output_tokens=200
        # 日常对话 2-3 句即可，减少生成时间约 30%
        if is_chat_fast_path and node_id == CHAT_DIRECT_NODE_ID:
            node_override["max_output_tokens"] = 200
        model_node_overrides[node_id] = node_override

    scene_node_sequence = resolve_scene_node_sequence(scene_type)
    fallback_only_nodes = [] if is_chat_fast_path else ["core_reasoner_secondary"]
    all_registered_nodes = scene_node_sequence + [
        n for n in fallback_only_nodes if n not in scene_node_sequence
    ]
    node_bindings = [
        {
            "node_id": node_id,
            "model_node_id": node_id,
            "task": node_id,
            "require_approval_token": False,
        }
        for node_id in all_registered_nodes
    ]
    edges = [
        {"from": scene_node_sequence[i], "to": scene_node_sequence[i + 1]}
        for i in range(len(scene_node_sequence) - 1)
    ]
    entry_node_id = scene_node_sequence[0]
    fallback_target_node = CHAT_RENDER_NODE_ID if is_chat_fast_path else (
        "consistency_verifier"
        if "consistency_verifier" in scene_node_sequence
        else CHAT_RENDER_NODE_ID
    )
    chain_budget_ms = (
        sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence)
        + (
            int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
            if not is_chat_fast_path
            else 0
        )
    )
    primary_timeout_ms = int(node_timeouts.get(entry_node_id, base_timeout_ms))
    try:
        configured_attempts = int(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS") or "2").strip())
    except ValueError:
        configured_attempts = 2
    retry_attempts = max(1, min(configured_attempts, 3))
    try:
        configured_retry_delay_ms = int(float(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS") or "150").strip()))
    except ValueError:
        configured_retry_delay_ms = 150
    retry_delay_ms = max(0, min(configured_retry_delay_ms, 2_000))
    retry_budget_ms = 0
    fallback_budget_ms = 0
    if entry_node_id == _GEMINI_PRIMARY_RETRY_NODE_ID:
        retry_budget_ms = max(0, retry_attempts - 1) * (primary_timeout_ms + retry_delay_ms)
        fallback_budget_ms = (
            primary_timeout_ms
            if resolve_gemini_primary_fallback_model_id(str(node_model_ids.get(entry_node_id) or ""))
            else 0
        )
    policy_timeout_ms = chain_budget_ms + retry_budget_ms + fallback_budget_ms + 5_000
    if is_chat_fast_path:
        policy_timeout_ms = max(8_000, min(int(policy_timeout_ms), 12_000))
    else:
        policy_timeout_ms = max(90_000, min(int(policy_timeout_ms), 240_000))

    return {
        "graph_id": "host.chat.realtime",
        "version": "v1",
        "policy_timeout_ms": policy_timeout_ms,
        "entry_node": entry_node_id,
        "model_profile_id": "chat_ja_24x7_v1",
        "node_bindings": node_bindings,
        "edges": edges,
        "fallback_edges": (
            []
            if is_chat_fast_path
            else [
                {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
                {"from": "core_reasoner_secondary", "to": fallback_target_node},
            ]
        ),
        "model_node_overrides": model_node_overrides,
        "graph_meta": {
            "online_chain": (
                "chat_direct_single_node + scene_routed_langgraph"
                if is_chat_fast_path
                else "six_recall + scene_routed_langgraph + targeted_renderer"
            ),
            "provider": provider_name,
            "scene_type": resolved_scene_type,
            "scene_type_source": scene_type_source,
            "render_target_lang": resolved_render_lang,
            "active_node_count": len(scene_node_sequence),
            "active_nodes": list(scene_node_sequence),
        },
    }
