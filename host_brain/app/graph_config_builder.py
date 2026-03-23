"""Graph 配置构建模块。

从 fastapi_app.py 拆分，负责 Chat 图的配置构建和场景路由。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from host_brain.app.config import load_host_config
from host_brain.integrations.llm_gateway import (
    PROVIDER_DEEPSEEK,
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)
from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
    resolve_execution_profile,
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
SCENE_ROUTER_ENTRY_NODE_ID = "scene_router_entry"
SCENE_ROUTER_POST_CORE_NODE_ID = "scene_router_post_core"
SCENE_ROUTER_ENTRY_HANDLER_ID = "scene_router_entry"
SCENE_ROUTER_POST_CORE_HANDLER_ID = "scene_router_post_core"
SCENE_TYPE_AUTO = "AUTO"

# M-MA1：Supervisor 分派节点常量
SUPERVISOR_DISPATCH_NODE_ID = "supervisor_dispatch"
SUPERVISOR_DISPATCH_HANDLER_ID = "supervisor_dispatch"

# M-MA5：各 Agent Handler 节点常量
CHAT_AGENT_NODE_ID = "chat_agent_node"
CHAT_AGENT_HANDLER_ID = "chat_agent"
CODE_AGENT_NODE_ID = "code_agent_node"
CODE_AGENT_HANDLER_ID = "code_agent"
TRANSLATE_AGENT_NODE_ID = "translate_agent_node"
TRANSLATE_AGENT_HANDLER_ID = "translate_agent"
INTENT_AGENT_NODE_ID = "intent_agent_node"
INTENT_AGENT_HANDLER_ID = "intent_agent"
# P1/P4 意图图入口节点：内部实现为 intent_agent_subgraph（透明子图）
INTENT_AGENT_ENTRY_NODE_ID = "intent_agent_entry"
INTENT_AGENT_ENTRY_HANDLER_ID = "intent_agent_entry"
JUDGE_AGENT_NODE_ID = "judge_agent_node"
JUDGE_AGENT_HANDLER_ID = "judge_agent"
RETRIEVAL_AGENT_NODE_ID = "retrieval_agent_node"  # P11 新增
RETRIEVAL_AGENT_HANDLER_ID = "retrieval_agent"    # P11 新增
EMOTION_AGENT_NODE_ID = "emotion_agent_node"       # 方向 G Phase 2 新增
EMOTION_AGENT_HANDLER_ID = "emotion_agent"          # 方向 G Phase 2 新增
# P16：M-MA6 动态路由虚拟节点（graph_executor 直接分派，不再经过 agent_dispatch_handler）
AGENT_DISPATCH_NODE_ID = "agent_dispatch_node"
AGENT_DISPATCH_HANDLER_ID = "agent_dispatch"  # 保留常量供其他模块引用，不再用于 binding

# 客户端允许显式指定的场景类型白名单
# 供 chat_handler.py 校验客户端传入的 scene_type，统一维护避免两处同步
# [选项一] SCENE_VISION_UI 已有完整实现（chat_handler_vision.py），加入白名单
# 注意：VISION_UI 场景需要客户端传入 image_upload_consent=true 才允许上传图片
# AUTO：显式走多 agent 编排（scene_router_entry → supervisor_dispatch）；默认已统一图+动态路由，可不传
ALLOWED_EXPLICIT_SCENE_TYPES: frozenset[str] = frozenset({
    SCENE_CHAT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_TYPE_AUTO,
})

HOST_CHAT_ROUTE_HOST_8B = "host_8b"
HOST_CHAT_ROUTE_CLOUD = "cloud"
HOST_CHAT_ROUTE_EDGE_3B = "edge_3b"
HOST_CHAT_ROUTE_SKILLS = "skills"

ROUTE_DECISION_VERSION = "route.v1"
ROUTE_TARGET_HOST = "host"
ROUTE_TARGET_EDGE = "edge"
ROUTE_TARGET_CLOUD = "cloud"
ROUTE_TARGET_SKILLS = "skills"

_HOST_CHAT_8B_DEFAULT_PROVIDER = PROVIDER_OPENAI
_HOST_CHAT_8B_DEFAULT_MODEL_ID = "qwen3:8b"
_HOST_CHAT_8B_FALLBACK_API_KEY = "host-local-placeholder-key"
_HOST_CHAT_8B_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_HOST_CHAT_CLOUD_DEFAULT_PROVIDER = PROVIDER_GEMINI
_PROVIDERS_REQUIRING_API_KEY = frozenset({PROVIDER_OPENAI, PROVIDER_GEMINI, PROVIDER_DEEPSEEK})

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


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(min_value), min(int(max_value), parsed))


def _parse_csv_tokens(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    tokens: set[str] = set()
    for item in raw.split(","):
        token = str(item or "").strip()
        if token:
            tokens.add(token)
    return tokens


def _stable_bucket_0_99(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % 100


def _evaluate_host_chat_gray_rollout(
    *,
    rollout_key: str | None,
    scene_type: str | None,
) -> dict[str, Any]:
    enabled = _parse_bool(os.environ.get("HOST_CHAT_GRAY_ENABLED"), default=False)
    percent = _parse_int(
        os.environ.get("HOST_CHAT_GRAY_PERCENT"),
        default=100,
        min_value=0,
        max_value=100,
    )
    scope_scenes = {
        str(item).strip().upper()
        for item in _parse_csv_tokens(os.environ.get("HOST_CHAT_GRAY_SCENES") or "CHAT")
        if str(item).strip()
    }
    allowlist = _parse_csv_tokens(os.environ.get("HOST_CHAT_GRAY_USER_ALLOWLIST"))
    normalized_scene = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    key_value = str(rollout_key or "").strip()

    result: dict[str, Any] = {
        "enabled": enabled,
        "percent": percent,
        "scene_type": normalized_scene,
        "scope_scenes": sorted(scope_scenes),
        "rollout_key": (key_value or None),
        "bucket": None,
        "allowlist_hit": False,
        "in_rollout": True,
        "reason_code": "HOST_CHAT_GRAY_DISABLED",
        "detail": "gray rollout disabled",
    }
    if not enabled:
        return result

    if scope_scenes and normalized_scene not in scope_scenes:
        result["in_rollout"] = False
        result["reason_code"] = "HOST_CHAT_GRAY_SCENE_EXCLUDED"
        result["detail"] = f"scene_type={normalized_scene} not in gray scope"
        return result

    if key_value and key_value in allowlist:
        result["allowlist_hit"] = True
        result["reason_code"] = "HOST_CHAT_GRAY_ALLOWLIST_MATCH"
        result["detail"] = "rollout key hit allowlist"
        return result

    if not key_value:
        include_without_key = percent >= 100
        result["in_rollout"] = include_without_key
        result["reason_code"] = (
            "HOST_CHAT_GRAY_NO_KEY_INCLUDED" if include_without_key else "HOST_CHAT_GRAY_NO_KEY_EXCLUDED"
        )
        result["detail"] = "rollout key missing"
        return result

    bucket = _stable_bucket_0_99(key_value)
    in_rollout = bucket < percent
    result["bucket"] = bucket
    result["in_rollout"] = in_rollout
    if in_rollout:
        result["reason_code"] = "HOST_CHAT_GRAY_BUCKET_INCLUDED"
        result["detail"] = f"bucket={bucket} < percent={percent}"
    else:
        result["reason_code"] = "HOST_CHAT_GRAY_BUCKET_EXCLUDED"
        result["detail"] = f"bucket={bucket} >= percent={percent}"
    return result


def _provider_requires_api_key(provider_name: str) -> bool:
    return str(provider_name or "").strip().lower() in _PROVIDERS_REQUIRING_API_KEY


def _provider_api_key_env_name(provider_name: str) -> str | None:
    provider = str(provider_name or "").strip().lower()
    mapping = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    }
    return mapping.get(provider)


def _resolve_provider_api_key(*, provider_name: str, preferred_api_key: str | None = None) -> str | None:
    preferred = str(preferred_api_key or "").strip()
    if preferred:
        return preferred
    env_name = _provider_api_key_env_name(provider_name)
    if not env_name:
        return None
    value = str(os.environ.get(env_name) or "").strip()
    return value or None


def _is_provider_available(*, provider_name: str, api_key: str | None, configured_available: bool) -> bool:
    provider = str(provider_name or "").strip().lower()
    if not configured_available:
        return False
    if provider == PROVIDER_DISABLED:
        return False
    if _provider_requires_api_key(provider) and not str(api_key or "").strip():
        return False
    return True


def _normalize_openai_compat_base_url(value: str | None) -> str | None:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return None
    if base_url.lower().endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _split_api_and_root_base(base_url: str | None) -> tuple[str | None, str | None]:
    normalized = _normalize_openai_compat_base_url(base_url)
    if not normalized:
        return None, None
    root = normalized[:-3].rstrip("/") if normalized.lower().endswith("/v1") else normalized.rstrip("/")
    return normalized, (root or normalized)


def _resolve_host_8b_base_url(*, provider_name: str) -> str | None:
    provider = str(provider_name or "").strip().lower()
    if provider != PROVIDER_OPENAI:
        return None
    raw = str(
        os.environ.get("HOST_CHAT_HOST_8B_BASE_URL")
        or os.environ.get("HOST_OPENAI_BASE_URL")
        or _HOST_CHAT_8B_DEFAULT_BASE_URL
    ).strip()
    return _normalize_openai_compat_base_url(raw)


def _probe_http_endpoint(*, url: str, timeout_ms: int) -> tuple[bool, str]:
    """同步 HTTP 探测（仅供 asyncio.to_thread 包装后调用，不得在事件循环中直接调用）。"""
    endpoint = str(url or "").strip()
    if not endpoint:
        return False, "empty_probe_url"
    timeout_sec = max(0.2, float(max(100, int(timeout_ms))) / 1000.0)
    req = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "NatsumeHost8BHealthProbe/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            code = int(getattr(resp, "status", 200) or 200)
        if 200 <= code < 500:
            return True, f"http_{code}"
        return False, f"http_{code}"
    except urllib.error.URLError as exc:
        return False, f"url_error:{exc}"
    except Exception as exc:  # pragma: no cover - defensive guard
        return False, f"probe_error:{exc}"


async def _probe_http_endpoint_async(*, url: str, timeout_ms: int) -> tuple[bool, str]:
    """异步 HTTP 探测——通过 asyncio.to_thread 包装同步探测，不阻塞事件循环。"""
    return await asyncio.to_thread(_probe_http_endpoint, url=url, timeout_ms=timeout_ms)


def _resolve_host_8b_health_status(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    if not _parse_bool(os.environ.get("HOST_CHAT_HOST_8B_HEALTHCHECK_ENABLED"), default=True):
        return True, "HOST_8B_HEALTHCHECK_DISABLED"

    ready_value = os.environ.get("HOST_CHAT_HOST_8B_READY")
    if ready_value is not None and not _parse_bool(ready_value, default=True):
        return False, "HOST_8B_READY_FLAG_DOWN"

    health_url = str(os.environ.get("HOST_CHAT_HOST_8B_HEALTHCHECK_URL") or "").strip()
    probe_urls: list[str] = []
    if health_url:
        probe_urls.append(health_url)
    else:
        api_base, root_base = _split_api_and_root_base(_resolve_host_8b_base_url(provider_name=provider_name))
        if api_base:
            probe_urls.append(f"{api_base}/models")
        if root_base:
            probe_urls.append(f"{root_base}/health")
            probe_urls.append(f"{root_base}/api/tags")

    if not probe_urls:
        if ready_value is not None:
            return _parse_bool(ready_value, default=True), "HOST_8B_READY_FLAG_ONLY"
        return True, "HOST_8B_NO_PROBE_URL"

    first_failure = ""
    for url in probe_urls:
        ok, detail = _probe_http_endpoint(url=url, timeout_ms=timeout_ms)
        if ok:
            return True, f"HOST_8B_PROBE_OK:{detail}"
        if not first_failure:
            first_failure = detail
    return False, f"HOST_8B_PROBE_FAILED:{first_failure or 'unknown'}"


async def _resolve_host_8b_health_status_async(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    """异步版本的 8B 健康探测——在事件循环中调用此函数，不阻塞事件循环。

    修复审计问题 #1：原 _resolve_host_8b_health_status 使用同步 urllib 在事件循环中
    直接调用，会阻塞事件循环最多 3s。此函数通过 asyncio.to_thread 包装解决该问题。
    """
    return await asyncio.to_thread(
        _resolve_host_8b_health_status,
        provider_name=provider_name,
        timeout_ms=timeout_ms,
    )


def pre_intent_classifier(user_input: str) -> str:
    """轻量规则预分类器，优先级从高到低：VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT。

    修复1：文化娱乐关键词（动漫、书、电影等）默认归为 SCENE_CHAT，
    仅当同时命中代码关键词时才升级为 SCENE_WORK_CODE（代码意图优先级更高）。

    修复2（问题A）：娱乐关键词同时命中 EXEC_INTENT 关键词时，优先归为 SCENE_CHAT。
    例如："帮我安装一下这首歌" → 命中"安装"(EXEC_INTENT) + "歌"(娱乐) → SCENE_CHAT。
    仅当无娱乐关键词时，EXEC_INTENT 关键词才生效。
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
        # 修复问题A：即使同时命中 EXEC_INTENT 关键词（如"安装"），也优先归为 CHAT
        # 避免"帮我安装一下这首歌"被误判为 EXEC_INTENT
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

    if _parse_bool(os.environ.get("HOST_CHAT_ENABLE_PRECLASSIFIER"), default=True):
        scene = pre_intent_classifier(_extract_scene_router_message(payload))
        return _normalize_scene_type(scene), "graph_pre_classifier"
    return SCENE_CHAT, "graph_default_chat"


def _scene_router_entry_next_node(scene_type: str) -> str:
    # AUTO：走多 agent 编排，先经 supervisor_dispatch 再选子 agent
    if scene_type == SCENE_TYPE_AUTO:
        return SUPERVISOR_DISPATCH_NODE_ID
    if scene_type in {SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE}:
        return CHAT_INTENT_SOURCE_NODE_ID
    return CHAT_DIRECT_NODE_ID


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
    if resolved_scene == SCENE_CHAT and resolved_next_node == CHAT_DIRECT_NODE_ID:
        return HOST_CHAT_ROUTE_HOST_8B
    return HOST_CHAT_ROUTE_CLOUD


def _resolve_route_target_family(*, route_target: str, scene_type: str, next_node: str | None) -> str:
    normalized_target = str(route_target or "").strip().lower()
    resolved_scene = _normalize_scene_type(scene_type)
    resolved_next_node = str(next_node or "").strip()
    if normalized_target == HOST_CHAT_ROUTE_EDGE_3B:
        return ROUTE_TARGET_EDGE
    if normalized_target == HOST_CHAT_ROUTE_SKILLS:
        return ROUTE_TARGET_SKILLS
    if normalized_target == HOST_CHAT_ROUTE_HOST_8B:
        return ROUTE_TARGET_HOST
    if normalized_target == HOST_CHAT_ROUTE_CLOUD:
        return ROUTE_TARGET_CLOUD
    if resolved_scene == SCENE_EXEC_INTENT or resolved_next_node in {"planner_constraints", "tool_planner"}:
        return ROUTE_TARGET_SKILLS
    if resolved_scene == SCENE_CHAT and resolved_next_node == CHAT_DIRECT_NODE_ID:
        return ROUTE_TARGET_HOST
    return ROUTE_TARGET_CLOUD


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
    resolved_target = _resolve_route_target_family(
        route_target=resolved_route_target,
        scene_type=resolved_scene,
        next_node=resolved_next_node,
    )
    return {
        "version": ROUTE_DECISION_VERSION,
        "target": resolved_target,
        "targets": {
            ROUTE_TARGET_HOST: resolved_target == ROUTE_TARGET_HOST,
            ROUTE_TARGET_EDGE: resolved_target == ROUTE_TARGET_EDGE,
            ROUTE_TARGET_CLOUD: resolved_target == ROUTE_TARGET_CLOUD,
            ROUTE_TARGET_SKILLS: resolved_target == ROUTE_TARGET_SKILLS,
        },
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


def graph_scene_router_entry_handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    resolved_scene, source = resolve_graph_entry_scene_type(payload=dict(payload or {}))
    next_node = _scene_router_entry_next_node(resolved_scene)
    route_decision = build_unified_route_decision(
        scene_type=resolved_scene,
        scene_type_source=source,
        route_target=_resolve_scene_router_route_target(scene_type=resolved_scene, next_node=next_node),
        next_node=next_node,
        router_node=str(context.get("node_id") or SCENE_ROUTER_ENTRY_NODE_ID),
    )
    out = dict(payload or {})
    out["scene_type"] = resolved_scene
    out["scene_type_source"] = source
    out["route_decision"] = route_decision
    out["scene_router_decision"] = {
        "router_node": str(context.get("node_id") or SCENE_ROUTER_ENTRY_NODE_ID),
        "scene_type": resolved_scene,
        "source": source,
        "next_node": next_node,
    }
    out["__next_node__"] = next_node
    return out


def graph_scene_router_post_core_handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    resolved_scene = _normalize_scene_type(str(payload.get("scene_type") or ""))
    source = str(payload.get("scene_type_source") or "graph_entry_router")
    next_node = _scene_router_post_core_next_node(resolved_scene)
    route_decision = build_unified_route_decision(
        scene_type=resolved_scene,
        scene_type_source=source,
        route_target=_resolve_scene_router_route_target(scene_type=resolved_scene, next_node=next_node),
        next_node=next_node,
        router_node=str(context.get("node_id") or SCENE_ROUTER_POST_CORE_NODE_ID),
    )
    out = dict(payload or {})
    out["scene_type"] = resolved_scene
    out["scene_type_source"] = source
    out["route_decision"] = route_decision
    out["scene_router_decision"] = {
        "router_node": str(context.get("node_id") or SCENE_ROUTER_POST_CORE_NODE_ID),
        "scene_type": resolved_scene,
        "source": source,
        "next_node": next_node,
    }
    out["__next_node__"] = next_node
    return out


def resolve_chat_scene_type(*, request_body: Any) -> tuple[str, str]:
    explicit_scene_type = str(getattr(request_body, "scene_type", None) or "").strip().upper()
    if explicit_scene_type:
        return explicit_scene_type, "explicit"
    if _parse_bool(os.environ.get("HOST_CHAT_ENABLE_PRECLASSIFIER"), default=True):
        return pre_intent_classifier(request_body.message), "pre_classifier"
    return SCENE_CHAT, "default_chat"


def resolve_chat_retrieval_gate(*, scene_type: str) -> tuple[bool, str | None]:
    if not _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True):
        return False, "HOST_RETRIEVAL_DISABLED"
    if (
        str(scene_type or "").strip().upper() == SCENE_CHAT
        and _parse_bool(os.environ.get("HOST_CHAT_DISABLE_RETRIEVAL"), default=False)
    ):
        return False, "HOST_CHAT_RETRIEVAL_DISABLED"
    return True, None


async def resolve_host_llm_runtime_async(
    *,
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """异步版本的 LLM 运行时解析（修复审计问题 #1：使用异步健康探测）。

    在事件循环中调用此函数，不阻塞事件循环。
    """
    return await _resolve_host_llm_runtime_core(
        rollout_key=rollout_key,
        scene_type=scene_type,
        health_probe_async=True,
    )


def resolve_host_llm_runtime(
    *,
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """同步版本的 LLM 运行时解析（向后兼容，供测试和非异步上下文使用）。

    修复审计问题 #1：在事件循环外调用时使用同步健康探测（不阻塞事件循环）。
    在事件循环内请使用 resolve_host_llm_runtime_async。
    """
    import asyncio as _asyncio
    coro = _resolve_host_llm_runtime_core(
        rollout_key=rollout_key,
        scene_type=scene_type,
        health_probe_async=False,
    )
    # 同步版本：在新事件循环中运行（仅供测试/非异步上下文）
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            # 已在事件循环中：降级为同步健康探测（不通过 asyncio.to_thread）
            # 此路径仅在测试 mock 场景下触发
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                future = _pool.submit(_asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return _asyncio.run(coro)


async def _resolve_host_llm_runtime_core(
    *,
    rollout_key: str | None = None,
    scene_type: str | None = None,
    health_probe_async: bool = True,
) -> dict[str, Any]:
    """LLM 运行时解析公共核心逻辑。

    health_probe_async=True  → 使用 _resolve_host_8b_health_status_async（不阻塞事件循环）
    health_probe_async=False → 使用同步 _resolve_host_8b_health_status（供非异步上下文）
    """
    config_result = load_host_config()
    cloud_provider_name = str(os.environ.get("HOST_LLM_PROVIDER") or PROVIDER_OPENAI).strip().lower() or PROVIDER_OPENAI
    cloud_timeout_ms = 15_000
    cloud_llm_api_key: str | None = None
    cloud_available = True
    if config_result.success and config_result.config is not None:
        cloud_provider_name = (
            str(config_result.config.llm_provider or cloud_provider_name).strip().lower() or cloud_provider_name
        )
        cloud_timeout_ms = int(config_result.config.llm_timeout_ms)
        cloud_llm_api_key = config_result.config.llm_api_key
        cloud_available = (cloud_provider_name != PROVIDER_DISABLED)

    cloud_model_id = resolve_host_llm_model_id(cloud_provider_name)
    cloud_available = _is_provider_available(
        provider_name=cloud_provider_name,
        api_key=cloud_llm_api_key,
        configured_available=cloud_available,
    )
    cloud_base_url_override = (
        _normalize_openai_compat_base_url(str(os.environ.get("HOST_OPENAI_BASE_URL") or "").strip())
        if cloud_provider_name == PROVIDER_OPENAI
        else None
    )
    cloud_provider_fn = build_host_provider_fn(
        provider_name=cloud_provider_name,
        model_id=cloud_model_id,
        api_key=cloud_llm_api_key,
        timeout_ms=cloud_timeout_ms,
        base_url_override=cloud_base_url_override,
    )

    route_target = HOST_CHAT_ROUTE_CLOUD
    route_reason_code = "HOST_CHAT_ROUTE_CLOUD_DEFAULT"
    route_detail = "using configured cloud runtime"
    sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower() or "normal"

    chat_provider_name = cloud_provider_name
    chat_model_id = cloud_model_id
    chat_timeout_ms = cloud_timeout_ms
    chat_llm_api_key = cloud_llm_api_key
    chat_available = cloud_available
    chat_provider_fn = cloud_provider_fn
    chat_base_url_override = cloud_base_url_override

    host_8b_candidate_runtime: dict[str, Any] = {}
    host_8b_ready = False
    host_8b_unavailable_reason = "HOST_8B_NOT_EVALUATED"

    host_8b_enabled = _parse_bool(os.environ.get("HOST_CHAT_HOST_8B_ENABLED"), default=True)
    force_cloud = _parse_bool(os.environ.get("HOST_CHAT_FORCE_CLOUD"), default=False)
    gray_rollout = _evaluate_host_chat_gray_rollout(
        rollout_key=rollout_key,
        scene_type=scene_type,
    )

    if force_cloud:
        route_reason_code = "HOST_CHAT_FORCE_CLOUD"
        route_detail = "HOST_CHAT_FORCE_CLOUD=true"
        host_8b_unavailable_reason = "HOST_CHAT_FORCE_CLOUD"
    elif not host_8b_enabled:
        route_reason_code = "HOST_CHAT_HOST_8B_DISABLED"
        route_detail = "HOST_CHAT_HOST_8B_ENABLED=false"
        host_8b_unavailable_reason = "HOST_CHAT_HOST_8B_DISABLED"
    elif sys_mode == "gaming":
        route_reason_code = "HOST_CHAT_HOST_8B_DISABLED_GAMING_MODE"
        route_detail = "sys_mode=gaming"
        host_8b_unavailable_reason = "HOST_CHAT_HOST_8B_DISABLED_GAMING_MODE"
    elif bool(gray_rollout.get("enabled")) and not bool(gray_rollout.get("in_rollout")):
        route_reason_code = str(gray_rollout.get("reason_code") or "HOST_CHAT_GRAY_EXCLUDED")
        route_detail = str(gray_rollout.get("detail") or "gray rollout excluded")
        host_8b_unavailable_reason = str(route_reason_code or "HOST_CHAT_GRAY_EXCLUDED")
    else:
        host_8b_provider = (
            str(os.environ.get("HOST_CHAT_HOST_8B_PROVIDER") or _HOST_CHAT_8B_DEFAULT_PROVIDER).strip().lower()
            or _HOST_CHAT_8B_DEFAULT_PROVIDER
        )
        host_8b_model_id = (
            str(os.environ.get("HOST_CHAT_HOST_8B_MODEL_ID") or _HOST_CHAT_8B_DEFAULT_MODEL_ID).strip()
            or _HOST_CHAT_8B_DEFAULT_MODEL_ID
        )
        host_8b_timeout_ms = _parse_int(
            os.environ.get("HOST_CHAT_HOST_8B_TIMEOUT_MS"),
            default=cloud_timeout_ms,
            min_value=800,
            max_value=120_000,
        )
        host_8b_api_key = _resolve_provider_api_key(
            provider_name=host_8b_provider,
            preferred_api_key=str(os.environ.get("HOST_CHAT_HOST_8B_API_KEY") or "").strip() or None,
        )
        if _provider_requires_api_key(host_8b_provider) and not host_8b_api_key:
            if _parse_bool(os.environ.get("HOST_CHAT_HOST_8B_ALLOW_EMPTY_KEY"), default=True):
                host_8b_api_key = _HOST_CHAT_8B_FALLBACK_API_KEY
            else:
                route_reason_code = "HOST_CHAT_HOST_8B_KEY_MISSING"
                route_detail = f"{_provider_api_key_env_name(host_8b_provider) or 'api_key'} missing"
                host_8b_unavailable_reason = "HOST_CHAT_HOST_8B_KEY_MISSING"

        host_8b_available = _is_provider_available(
            provider_name=host_8b_provider,
            api_key=host_8b_api_key,
            configured_available=True,
        )
        host_8b_base_url_override = _resolve_host_8b_base_url(provider_name=host_8b_provider)
        host_8b_candidate_runtime = {
            "provider_name": host_8b_provider,
            "model_id": host_8b_model_id,
            "timeout_ms": host_8b_timeout_ms,
            "llm_api_key": host_8b_api_key,
            "available": bool(host_8b_available),
            "base_url_override": host_8b_base_url_override,
            "provider_fn": (
                build_host_provider_fn(
                    provider_name=host_8b_provider,
                    model_id=host_8b_model_id,
                    api_key=host_8b_api_key,
                    timeout_ms=host_8b_timeout_ms,
                    base_url_override=host_8b_base_url_override,
                )
                if host_8b_available
                else None
            ),
        }
        if host_8b_available:
            if health_probe_async:
                healthy, health_detail = await _resolve_host_8b_health_status_async(
                    provider_name=host_8b_provider,
                    timeout_ms=min(host_8b_timeout_ms, 3_000),
                )
            else:
                healthy, health_detail = _resolve_host_8b_health_status(
                    provider_name=host_8b_provider,
                    timeout_ms=min(host_8b_timeout_ms, 3_000),
                )
            if healthy:
                host_8b_ready = True
                host_8b_unavailable_reason = ""
                chat_provider_name = host_8b_provider
                chat_model_id = host_8b_model_id
                chat_timeout_ms = host_8b_timeout_ms
                chat_llm_api_key = host_8b_api_key
                chat_available = True
                chat_base_url_override = host_8b_base_url_override
                chat_provider_fn = (
                    host_8b_candidate_runtime.get("provider_fn")
                    or build_host_provider_fn(
                        provider_name=chat_provider_name,
                        model_id=chat_model_id,
                        api_key=chat_llm_api_key,
                        timeout_ms=chat_timeout_ms,
                        base_url_override=chat_base_url_override,
                    )
                )
                route_target = HOST_CHAT_ROUTE_HOST_8B
                route_reason_code = "HOST_CHAT_HOST_8B_ACTIVE"
                route_detail = "non-gaming mode defaults to host 8b runtime"
            else:
                route_reason_code = "HOST_CHAT_HOST_8B_UNHEALTHY"
                route_detail = health_detail
                host_8b_unavailable_reason = str(health_detail or "HOST_CHAT_HOST_8B_UNHEALTHY")
        elif route_reason_code == "HOST_CHAT_ROUTE_CLOUD_DEFAULT":
            route_reason_code = "HOST_CHAT_HOST_8B_UNAVAILABLE"
            route_detail = "host 8b provider unavailable"
            host_8b_unavailable_reason = "HOST_CHAT_HOST_8B_UNAVAILABLE"
        elif not host_8b_available and not host_8b_unavailable_reason:
            host_8b_unavailable_reason = "HOST_CHAT_HOST_8B_UNAVAILABLE"

    # [选项二] 接入 RoutingMatrix：将场景类型映射为 TaskType，记录路由矩阵决策
    routing_matrix_decision: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_routing_matrix  # noqa: PLC0415
        from host_brain.app.schemas.routing_types import TaskType as _TaskType, LatencyBudget as _LatencyBudget  # noqa: PLC0415
        _scene_to_task = {
            SCENE_CHAT: _TaskType.CHAT,
            SCENE_EXEC_INTENT: _TaskType.REASONING,
            SCENE_WORK_CODE: _TaskType.CODE,
            SCENE_WORK_TRANSLATE: _TaskType.TRANSLATE,
            SCENE_VISION_UI: _TaskType.CHAT,  # 视觉场景复用 CHAT 路由
        }
        _task_type = _scene_to_task.get(
            str(scene_type or SCENE_CHAT).strip().upper(),
            _TaskType.CHAT,
        )
        _latency = _LatencyBudget.FAST if sys_mode == "gaming" else _LatencyBudget.NORMAL
        _matrix = get_routing_matrix()
        _decision = _matrix.select_with_fallback(
            task_type=_task_type,
            latency_budget=_latency,
        )
        _selected_model = str(_decision.selected_model.value or "").strip().lower()
        _runtime_changed = False
        _not_applied_reason: str | None = None

        if _selected_model == HOST_CHAT_ROUTE_CLOUD:
            if route_target != HOST_CHAT_ROUTE_CLOUD:
                chat_provider_name = cloud_provider_name
                chat_model_id = cloud_model_id
                chat_timeout_ms = cloud_timeout_ms
                chat_llm_api_key = cloud_llm_api_key
                chat_available = cloud_available
                chat_provider_fn = cloud_provider_fn
                chat_base_url_override = cloud_base_url_override
                route_target = HOST_CHAT_ROUTE_CLOUD
                route_reason_code = "ROUTING_MATRIX_SELECTED_CLOUD"
                route_detail = (
                    f"routing_matrix selected cloud: task={_decision.task_type.value},"
                    f" latency={_decision.latency_budget.value}"
                )
                _runtime_changed = True
        elif _selected_model == HOST_CHAT_ROUTE_HOST_8B:
            if route_target != HOST_CHAT_ROUTE_HOST_8B and host_8b_ready and host_8b_candidate_runtime:
                chat_provider_name = str(host_8b_candidate_runtime.get("provider_name") or chat_provider_name)
                chat_model_id = str(host_8b_candidate_runtime.get("model_id") or chat_model_id)
                chat_timeout_ms = int(host_8b_candidate_runtime.get("timeout_ms") or chat_timeout_ms)
                chat_llm_api_key = host_8b_candidate_runtime.get("llm_api_key")
                chat_available = bool(host_8b_candidate_runtime.get("available", True))
                chat_provider_fn = (
                    host_8b_candidate_runtime.get("provider_fn")
                    or chat_provider_fn
                )
                chat_base_url_override = str(
                    host_8b_candidate_runtime.get("base_url_override")
                    or chat_base_url_override
                    or ""
                ).strip() or None
                route_target = HOST_CHAT_ROUTE_HOST_8B
                route_reason_code = "ROUTING_MATRIX_SELECTED_HOST_8B"
                route_detail = (
                    f"routing_matrix selected host_8b: task={_decision.task_type.value},"
                    f" latency={_decision.latency_budget.value}"
                )
                _runtime_changed = True
            elif route_target != HOST_CHAT_ROUTE_HOST_8B:
                _not_applied_reason = (
                    f"HOST_8B_UNAVAILABLE:{host_8b_unavailable_reason or 'UNKNOWN'}"
                )
        else:
            _not_applied_reason = f"UNSUPPORTED_MODEL_TARGET:{_selected_model or 'unknown'}"

        routing_matrix_decision = {
            "task_type": _decision.task_type.value,
            "selected_model": _decision.selected_model.value,
            "latency_budget": _decision.latency_budget.value,
            "is_fallback": _decision.is_fallback,
            "fallback_reason": _decision.fallback_reason,
            "runtime_applied": _runtime_changed,
            "runtime_selected_target": route_target,
            "not_applied_reason": _not_applied_reason,
        }
    except Exception as _rm_exc:
        routing_matrix_decision = {"status": "DEGRADED", "reason": str(_rm_exc)}

    return {
        "provider_name": chat_provider_name,
        "model_id": chat_model_id,
        "timeout_ms": chat_timeout_ms,
        "llm_api_key": chat_llm_api_key,
        "available": chat_available,
        "provider_fn": chat_provider_fn,
        "route_target": route_target,
        "route_reason_code": route_reason_code,
        "route_detail": route_detail,
        "base_url_override": chat_base_url_override,
        "sys_mode": sys_mode,
        "chat_provider_name": chat_provider_name,
        "chat_model_id": chat_model_id,
        "chat_timeout_ms": chat_timeout_ms,
        "chat_llm_api_key": chat_llm_api_key,
        "chat_available": chat_available,
        "chat_provider_fn": chat_provider_fn,
        "chat_base_url_override": chat_base_url_override,
        "cloud_provider_name": cloud_provider_name,
        "cloud_model_id": cloud_model_id,
        "cloud_timeout_ms": cloud_timeout_ms,
        "cloud_llm_api_key": cloud_llm_api_key,
        "cloud_available": cloud_available,
        "cloud_provider_fn": cloud_provider_fn,
        "cloud_base_url_override": cloud_base_url_override,
        "gray_rollout": dict(gray_rollout),
        "routing_matrix_decision": routing_matrix_decision,
    }


def build_chat_graph(
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
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_api_key: str | None = None,
    cloud_available: bool | None = None,
    chat_base_url_override: str | None = None,
    cloud_base_url_override: str | None = None,
    execution_profile: str = PROFILE_CHAT_FAST,
) -> dict[str, Any]:
    """Chat 图：START → chat_agent → renderer，无 Supervisor/agent_dispatch。"""
    return _build_graph_config_by_profile(
        execution_profile=execution_profile,
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=api_key,
        provider_fn=provider_fn,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
        cloud_provider_name=cloud_provider_name,
        cloud_model_id=cloud_model_id,
        cloud_timeout_ms=cloud_timeout_ms,
        cloud_api_key=cloud_api_key,
        cloud_available=cloud_available,
        chat_base_url_override=chat_base_url_override,
        cloud_base_url_override=cloud_base_url_override,
    )


def build_supervised_graph(
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
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_api_key: str | None = None,
    cloud_available: bool | None = None,
    chat_base_url_override: str | None = None,
    cloud_base_url_override: str | None = None,
    execution_profile: str = PROFILE_WORKFLOW_SUPERVISED,
) -> dict[str, Any]:
    """Supervised 图：START → supervisor_dispatch → agent_dispatch → judge_agent → renderer。"""
    return _build_graph_config_by_profile(
        execution_profile=execution_profile,
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=api_key,
        provider_fn=provider_fn,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
        cloud_provider_name=cloud_provider_name,
        cloud_model_id=cloud_model_id,
        cloud_timeout_ms=cloud_timeout_ms,
        cloud_api_key=cloud_api_key,
        cloud_available=cloud_available,
        chat_base_url_override=chat_base_url_override,
        cloud_base_url_override=cloud_base_url_override,
    )


def build_intent_graph(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    api_key: str | None,
    provider_fn: Callable[[dict[str, Any]], Any],
    scene_type: str = SCENE_EXEC_INTENT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_api_key: str | None = None,
    cloud_available: bool | None = None,
    chat_base_url_override: str | None = None,
    cloud_base_url_override: str | None = None,
    execution_profile: str = PROFILE_INTENT_EXEC,
) -> dict[str, Any]:
    """Intent 图：START → intent_agent_entry → safety_verifier → judge_agent → renderer（P1 占位，P4 可替换为 subgraph）。"""
    return _build_graph_config_by_profile(
        execution_profile=execution_profile,
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=api_key,
        provider_fn=provider_fn,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
        cloud_provider_name=cloud_provider_name,
        cloud_model_id=cloud_model_id,
        cloud_timeout_ms=cloud_timeout_ms,
        cloud_api_key=cloud_api_key,
        cloud_available=cloud_available,
        chat_base_url_override=chat_base_url_override,
        cloud_base_url_override=cloud_base_url_override,
    )


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
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_api_key: str | None = None,
    cloud_available: bool | None = None,
    chat_base_url_override: str | None = None,
    cloud_base_url_override: str | None = None,
    execution_profile: str | None = None,
) -> dict[str, Any]:
    """按 execution_profile 选图；未传时由 scene_type 解析（兼容旧调用）。"""
    profile = (
        str(execution_profile or "").strip()
        or resolve_execution_profile(scene_type)
    )
    return _build_graph_config_by_profile(
        execution_profile=profile,
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        api_key=api_key,
        provider_fn=provider_fn,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
        cloud_provider_name=cloud_provider_name,
        cloud_model_id=cloud_model_id,
        cloud_timeout_ms=cloud_timeout_ms,
        cloud_api_key=cloud_api_key,
        cloud_available=cloud_available,
        chat_base_url_override=chat_base_url_override,
        cloud_base_url_override=cloud_base_url_override,
    )


def _build_graph_config_by_profile(
    *,
    execution_profile: str,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    api_key: str | None,
    provider_fn: Callable[[dict[str, Any]], Any],
    scene_type: str = SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_api_key: str | None = None,
    cloud_available: bool | None = None,
    chat_base_url_override: str | None = None,
    cloud_base_url_override: str | None = None,
) -> dict[str, Any]:
    _ = provider_fn
    resolved_scene_type = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    is_chat_fast_path = execution_profile == PROFILE_CHAT_FAST
    use_supervised = execution_profile == PROFILE_WORKFLOW_SUPERVISED
    is_intent = execution_profile == PROFILE_INTENT_EXEC
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = "ja"

    resolved_chat_provider = str(provider_name or _HOST_CHAT_8B_DEFAULT_PROVIDER).strip().lower() or _HOST_CHAT_8B_DEFAULT_PROVIDER
    resolved_chat_model_id = str(model_id or "").strip() or _HOST_CHAT_8B_DEFAULT_MODEL_ID
    resolved_chat_timeout_ms = max(800, int(timeout_ms))
    resolved_chat_base_url_override = (
        _normalize_openai_compat_base_url(chat_base_url_override)
        if resolved_chat_provider == PROVIDER_OPENAI
        else None
    )
    if resolved_chat_provider == PROVIDER_OPENAI and not resolved_chat_base_url_override:
        resolved_chat_base_url_override = _resolve_host_8b_base_url(provider_name=resolved_chat_provider)
    resolved_chat_api_key = _resolve_provider_api_key(
        provider_name=resolved_chat_provider,
        preferred_api_key=str(api_key or "").strip() or None,
    )
    if _provider_requires_api_key(resolved_chat_provider) and not resolved_chat_api_key:
        if _parse_bool(os.environ.get("HOST_CHAT_HOST_8B_ALLOW_EMPTY_KEY"), default=True):
            resolved_chat_api_key = _HOST_CHAT_8B_FALLBACK_API_KEY
    resolved_chat_available = _is_provider_available(
        provider_name=resolved_chat_provider,
        api_key=resolved_chat_api_key,
        configured_available=bool(available),
    )

    resolved_cloud_provider = (
        str(cloud_provider_name or os.environ.get("HOST_CHAT_CLOUD_PROVIDER") or _HOST_CHAT_CLOUD_DEFAULT_PROVIDER)
        .strip()
        .lower()
        or _HOST_CHAT_CLOUD_DEFAULT_PROVIDER
    )
    if resolved_cloud_provider not in {
        PROVIDER_OPENAI,
        PROVIDER_GEMINI,
        PROVIDER_DEEPSEEK,
        PROVIDER_MOCK,
        PROVIDER_DISABLED,
    }:
        resolved_cloud_provider = _HOST_CHAT_CLOUD_DEFAULT_PROVIDER
    resolved_cloud_timeout_ms = max(800, int(cloud_timeout_ms or resolved_chat_timeout_ms))
    resolved_cloud_model_id = (
        str(cloud_model_id or "").strip() or resolve_host_llm_model_id(resolved_cloud_provider)
    )
    resolved_cloud_base_url_override = (
        _normalize_openai_compat_base_url(cloud_base_url_override)
        if resolved_cloud_provider == PROVIDER_OPENAI
        else None
    )
    resolved_cloud_api_key = _resolve_provider_api_key(
        provider_name=resolved_cloud_provider,
        preferred_api_key=str(cloud_api_key or "").strip() or None,
    )
    resolved_cloud_available_flag = (
        bool(cloud_available)
        if cloud_available is not None
        else bool(resolved_cloud_provider != PROVIDER_DISABLED)
    )
    resolved_cloud_available = _is_provider_available(
        provider_name=resolved_cloud_provider,
        api_key=resolved_cloud_api_key,
        configured_available=resolved_cloud_available_flag,
    )

    base_timeout_ms = max(800, int(resolved_cloud_timeout_ms))
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
    if resolved_cloud_provider != PROVIDER_GEMINI:
        for node_id in (
            "core_reasoner_primary",
            "core_reasoner_secondary",
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "consistency_verifier",
            "renderer_ja_c_style",
            "workbench_translate",
            "workbench_code_assist",
        ):
            node_model_ids[node_id] = resolved_cloud_model_id
    if is_chat_fast_path:
        node_model_ids[CHAT_DIRECT_NODE_ID] = resolved_chat_model_id
        # 修复：使用 max 而非 min，确保超时不低于 10,000ms（需求 1.1、1.2）
        node_timeouts[CHAT_DIRECT_NODE_ID] = max(resolved_chat_timeout_ms, 10_000)
        if resolved_cloud_provider == PROVIDER_GEMINI:
            node_model_ids["core_reasoner_primary"] = "gemini-2.5-flash"
        node_timeouts["core_reasoner_primary"] = min(base_timeout_ms, 5000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 3000)
        node_timeouts["renderer_ja_c_style"] = min(base_timeout_ms, 4000)
    elif use_supervised:
        # AUTO 路由下保留 chat_direct 快速回复模型，复杂任务仍走 primary 链路。
        node_model_ids[CHAT_DIRECT_NODE_ID] = resolved_chat_model_id
        node_timeouts[CHAT_DIRECT_NODE_ID] = max(resolved_chat_timeout_ms, 10_000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 9000)

    model_node_overrides: dict[str, dict[str, Any]] = {}
    for node_id in CHAT_GRAPH_NODE_ORDER:
        is_chat_node = node_id == CHAT_DIRECT_NODE_ID
        node_provider = resolved_chat_provider if is_chat_node else resolved_cloud_provider
        fallback_model_id = resolved_chat_model_id if is_chat_node else resolved_cloud_model_id
        node_model_id = str(node_model_ids.get(node_id) or fallback_model_id).strip() or fallback_model_id
        node_timeout = int(
            node_timeouts.get(node_id, resolved_chat_timeout_ms if is_chat_node else resolved_cloud_timeout_ms)
        )
        node_api_key = resolved_chat_api_key if is_chat_node else resolved_cloud_api_key
        node_available = resolved_chat_available if is_chat_node else resolved_cloud_available
        node_base_url_override = resolved_chat_base_url_override if is_chat_node else resolved_cloud_base_url_override
        node_override: dict[str, Any] = {
            "provider": node_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "api_key": str(node_api_key or "").strip() or None,
            "available": bool(node_available),
            "provider_fn": build_host_provider_fn_for_node(
                provider_name=node_provider,
                model_id=node_model_id,
                api_key=node_api_key,
                timeout_ms=node_timeout,
                node_id=node_id,
                base_url_override=node_base_url_override,
            ),
        }
        # 需求 2.1：CHAT 快速路径下，chat_direct_ja 节点限制 max_output_tokens=200
        if (is_chat_fast_path or use_supervised) and node_id == CHAT_DIRECT_NODE_ID:
            node_override["max_output_tokens"] = 200
        model_node_overrides[node_id] = node_override

    if is_intent:
        # P4：Intent 图主图仅 4 节点；子图节点仅入 node_bindings 供 entry handler 调用，不入 scene_node_sequence/edges
        scene_node_sequence = [
            INTENT_AGENT_ENTRY_NODE_ID,
            "safety_verifier",
            JUDGE_AGENT_NODE_ID,
            CHAT_RENDER_NODE_ID,
        ]
        node_bindings = [
            {
                "node_id": INTENT_AGENT_ENTRY_NODE_ID,
                "handler_id": INTENT_AGENT_ENTRY_HANDLER_ID,
                "task": "intent_agent_entry",
                "require_approval_token": False,
                # P6-2：局部循环边界配置（P6-1 建议默认值）
                "max_local_steps": 10,
                "max_retry_count": 2,
                "local_loop_timeout_ms": 60_000,
            },
            {"node_id": "safety_verifier", "model_node_id": "safety_verifier", "task": "safety_verifier", "require_approval_token": False},
            {
                "node_id": JUDGE_AGENT_NODE_ID,
                "handler_id": JUDGE_AGENT_HANDLER_ID,
                "task": "judge_agent",
                "require_approval_token": False,
            },
            {"node_id": CHAT_RENDER_NODE_ID, "model_node_id": CHAT_RENDER_NODE_ID, "task": CHAT_RENDER_NODE_ID, "require_approval_token": False},
            {"node_id": "core_reasoner_primary", "model_node_id": "core_reasoner_primary", "task": "core_reasoner_primary", "require_approval_token": False},
            {"node_id": "planner_constraints", "model_node_id": "planner_constraints", "task": "planner_constraints", "require_approval_token": False},
            {"node_id": "tool_planner", "model_node_id": "tool_planner", "task": "tool_planner", "require_approval_token": False},
        ]
        entry_node_id = INTENT_AGENT_ENTRY_NODE_ID
        edges = [
            {"from": INTENT_AGENT_ENTRY_NODE_ID, "to": "safety_verifier"},
            {"from": "safety_verifier", "to": JUDGE_AGENT_NODE_ID},
            {"from": JUDGE_AGENT_NODE_ID, "to": CHAT_RENDER_NODE_ID},
        ]
        fallback_edges = []
        chain_budget_ms = sum(
            int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence
        )
        primary_budget_node_id = INTENT_AGENT_ENTRY_NODE_ID
    elif use_supervised:
        # Supervised 图：supervisor_dispatch → agent_dispatch → judge_agent → renderer（无 scene_router_entry）
        scene_node_sequence = [
            SUPERVISOR_DISPATCH_NODE_ID,
            AGENT_DISPATCH_NODE_ID,
            JUDGE_AGENT_NODE_ID,
            CHAT_RENDER_NODE_ID,
        ]
        # P3.5：allowlist 由图配置提供；workbench_* 为 handler 可调用的 model 节点，不入边序列
        # P16：allowlist 对齐 _AGENT_HANDLER_MAP（graph_executor 内的全量 agent）
        _dispatch_allowlist = [
            CHAT_AGENT_HANDLER_ID,
            CODE_AGENT_HANDLER_ID,
            TRANSLATE_AGENT_HANDLER_ID,
            INTENT_AGENT_HANDLER_ID,
            JUDGE_AGENT_HANDLER_ID,
            RETRIEVAL_AGENT_HANDLER_ID,
            EMOTION_AGENT_HANDLER_ID,  # 方向 G Phase 2 新增
        ]
        node_bindings = [
            {
                "node_id": SUPERVISOR_DISPATCH_NODE_ID,
                "handler_id": SUPERVISOR_DISPATCH_HANDLER_ID,
                "task": "supervisor_dispatch",
                "require_approval_token": False,
                # P5-2：与 agent_dispatch 使用同一 allowlist，供 Supervisor 校验 proposal
                "agent_dispatch_allowlist": _dispatch_allowlist,
            },
            {
                "node_id": AGENT_DISPATCH_NODE_ID,
                "handler_id": "",  # P16：不再绑定 handler，由 graph_executor 直接分派
                "task": "agent_dispatch",
                "require_approval_token": False,
                "agent_dispatch_allowlist": _dispatch_allowlist,
            },
            {
                "node_id": JUDGE_AGENT_NODE_ID,
                "handler_id": JUDGE_AGENT_HANDLER_ID,
                "task": "judge_agent",
                "require_approval_token": False,
            },
            {"node_id": CHAT_RENDER_NODE_ID, "model_node_id": CHAT_RENDER_NODE_ID, "task": CHAT_RENDER_NODE_ID, "require_approval_token": False},
            {"node_id": "workbench_code_assist", "model_node_id": "workbench_code_assist", "task": "workbench_code_assist", "require_approval_token": False},
            {"node_id": "workbench_translate", "model_node_id": "workbench_translate", "task": "workbench_translate", "require_approval_token": False},
        ]
        entry_node_id = SUPERVISOR_DISPATCH_NODE_ID
        edges = [
            {"from": SUPERVISOR_DISPATCH_NODE_ID, "to": AGENT_DISPATCH_NODE_ID},
            {"from": AGENT_DISPATCH_NODE_ID, "to": JUDGE_AGENT_NODE_ID},
            {"from": JUDGE_AGENT_NODE_ID, "to": CHAT_RENDER_NODE_ID},
        ]
        fallback_edges = []
        chain_budget_ms = sum(
            int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence
        )
        primary_budget_node_id = SUPERVISOR_DISPATCH_NODE_ID
    else:
        # Chat 图：chat_direct → renderer（仅快路径两节点）
        # P3：code/translate 已迁入 Supervised 图，此处不再用旧长链做主路由；误入时强制快路径
        use_chat_sequence = is_chat_fast_path or (
            str(scene_type or "").strip().upper() in (SCENE_WORK_CODE, SCENE_WORK_TRANSLATE)
        )
        if use_chat_sequence:
            scene_node_sequence = [CHAT_DIRECT_NODE_ID, CHAT_RENDER_NODE_ID]
        else:
            scene_node_sequence = resolve_scene_node_sequence(scene_type)
        fallback_only_nodes = [] if use_chat_sequence else ["core_reasoner_secondary"]
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
        fallback_target_node = CHAT_RENDER_NODE_ID if use_chat_sequence else (
            "consistency_verifier"
            if "consistency_verifier" in scene_node_sequence
            else CHAT_RENDER_NODE_ID
        )
        fallback_edges = (
            []
            if use_chat_sequence
            else [
                {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
                {"from": "core_reasoner_secondary", "to": fallback_target_node},
            ]
        )
        chain_budget_ms = (
            sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence)
            + (
                int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
                if not use_chat_sequence
                else 0
            )
        )
        primary_budget_node_id = entry_node_id

    primary_timeout_ms = int(node_timeouts.get(primary_budget_node_id, base_timeout_ms))
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
    if primary_budget_node_id == _GEMINI_PRIMARY_RETRY_NODE_ID:
        retry_budget_ms = max(0, retry_attempts - 1) * (primary_timeout_ms + retry_delay_ms)
        fallback_budget_ms = (
            primary_timeout_ms
            if resolve_gemini_primary_fallback_model_id(str(node_model_ids.get(primary_budget_node_id) or ""))
            else 0
        )
    policy_timeout_ms = chain_budget_ms + retry_budget_ms + fallback_budget_ms + 5_000
    if is_chat_fast_path or (not use_supervised and not is_intent and use_chat_sequence):
        policy_timeout_ms = max(8_000, min(int(policy_timeout_ms), 12_000))
    elif use_supervised:
        policy_timeout_ms = max(30_000, min(int(policy_timeout_ms), 240_000))
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
        "fallback_edges": fallback_edges,
        "model_node_overrides": model_node_overrides,
        "graph_meta": {
            "execution_profile": execution_profile,
            "online_chain": (
                "intent_agent + safety_verifier + judge + renderer"
                if is_intent
                else (
                    "supervisor_dispatch + agent_dispatch + judge + renderer"
                    if use_supervised
                    else (
                        "chat_direct_single_node + scene_routed_langgraph"
                        if is_chat_fast_path
                        else "six_recall + scene_routed_langgraph + targeted_renderer"
                    )
                )
            ),
            "provider": resolved_chat_provider,
            "chat_provider": resolved_chat_provider,
            "chat_model_id": resolved_chat_model_id,
            "cloud_provider": resolved_cloud_provider,
            "cloud_model_id": resolved_cloud_model_id,
            "scene_type": resolved_scene_type,
            "scene_type_source": scene_type_source,
            "render_target_lang": resolved_render_lang,
            "active_node_count": len(scene_node_sequence),
            "active_nodes": list(scene_node_sequence),
        },
    }
