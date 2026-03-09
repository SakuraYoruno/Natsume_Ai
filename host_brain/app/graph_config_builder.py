"""Graph 配置构建模块。

从 fastapi_app.py 拆分，负责 Chat 图的配置构建和场景路由。
"""

from __future__ import annotations

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


def _normalize_scene_type(scene_type: str | None) -> str:
    scene = str(scene_type or "").strip().upper()
    if scene in {SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE}:
        return scene
    return SCENE_CHAT


def _extract_scene_router_message(payload: dict[str, Any]) -> str:
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


def resolve_host_llm_runtime() -> dict[str, Any]:
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

    host_8b_enabled = _parse_bool(os.environ.get("HOST_CHAT_HOST_8B_ENABLED"), default=True)
    force_cloud = _parse_bool(os.environ.get("HOST_CHAT_FORCE_CLOUD"), default=False)

    if force_cloud:
        route_reason_code = "HOST_CHAT_FORCE_CLOUD"
        route_detail = "HOST_CHAT_FORCE_CLOUD=true"
    elif not host_8b_enabled:
        route_reason_code = "HOST_CHAT_HOST_8B_DISABLED"
        route_detail = "HOST_CHAT_HOST_8B_ENABLED=false"
    elif sys_mode == "gaming":
        route_reason_code = "HOST_CHAT_HOST_8B_DISABLED_GAMING_MODE"
        route_detail = "sys_mode=gaming"
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

        host_8b_available = _is_provider_available(
            provider_name=host_8b_provider,
            api_key=host_8b_api_key,
            configured_available=True,
        )
        if host_8b_available:
            healthy, health_detail = _resolve_host_8b_health_status(
                provider_name=host_8b_provider,
                timeout_ms=min(host_8b_timeout_ms, 3_000),
            )
            if healthy:
                chat_provider_name = host_8b_provider
                chat_model_id = host_8b_model_id
                chat_timeout_ms = host_8b_timeout_ms
                chat_llm_api_key = host_8b_api_key
                chat_available = True
                chat_base_url_override = _resolve_host_8b_base_url(provider_name=chat_provider_name)
                chat_provider_fn = build_host_provider_fn(
                    provider_name=chat_provider_name,
                    model_id=chat_model_id,
                    api_key=chat_llm_api_key,
                    timeout_ms=chat_timeout_ms,
                    base_url_override=chat_base_url_override,
                )
                route_target = HOST_CHAT_ROUTE_HOST_8B
                route_reason_code = "HOST_CHAT_HOST_8B_ACTIVE"
                route_detail = "non-gaming mode defaults to host 8b runtime"
            else:
                route_reason_code = "HOST_CHAT_HOST_8B_UNHEALTHY"
                route_detail = health_detail
        elif route_reason_code == "HOST_CHAT_ROUTE_CLOUD_DEFAULT":
            route_reason_code = "HOST_CHAT_HOST_8B_UNAVAILABLE"
            route_detail = "host 8b provider unavailable"

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
    scene_router_enabled = _parse_bool(os.environ.get("HOST_GRAPH_SCENE_ROUTER_ENABLED"), default=True)
    use_scene_router = bool(scene_router_enabled and resolved_scene_type == SCENE_TYPE_AUTO)
    is_chat_fast_path = resolved_scene_type == SCENE_CHAT
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
    elif use_scene_router:
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
        # 日常对话 2-3 句即可，减少生成时间约 30%
        if (is_chat_fast_path or use_scene_router) and node_id == CHAT_DIRECT_NODE_ID:
            node_override["max_output_tokens"] = 200
        model_node_overrides[node_id] = node_override

    if use_scene_router:
        scene_node_sequence = [
            SCENE_ROUTER_ENTRY_NODE_ID,
            CHAT_DIRECT_NODE_ID,
            CHAT_INTENT_SOURCE_NODE_ID,
            SCENE_ROUTER_POST_CORE_NODE_ID,
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "workbench_translate",
            "workbench_code_assist",
            "consistency_verifier",
            CHAT_RENDER_NODE_ID,
        ]
        node_bindings = [
            {
                "node_id": SCENE_ROUTER_ENTRY_NODE_ID,
                "handler_id": SCENE_ROUTER_ENTRY_HANDLER_ID,
                "task": "scene_router_entry",
                "require_approval_token": False,
            },
            {
                "node_id": SCENE_ROUTER_POST_CORE_NODE_ID,
                "handler_id": SCENE_ROUTER_POST_CORE_HANDLER_ID,
                "task": "scene_router_post_core",
                "require_approval_token": False,
            },
        ] + [
            {
                "node_id": node_id,
                "model_node_id": node_id,
                "task": node_id,
                "require_approval_token": False,
            }
            for node_id in CHAT_GRAPH_NODE_ORDER
        ]
        entry_node_id = SCENE_ROUTER_ENTRY_NODE_ID
        edges = [
            {"from": SCENE_ROUTER_ENTRY_NODE_ID, "to": CHAT_DIRECT_NODE_ID},
            {"from": CHAT_INTENT_SOURCE_NODE_ID, "to": SCENE_ROUTER_POST_CORE_NODE_ID},
            {"from": SCENE_ROUTER_POST_CORE_NODE_ID, "to": "planner_constraints"},
            {"from": "planner_constraints", "to": "tool_planner"},
            {"from": "tool_planner", "to": "safety_verifier"},
            {"from": "safety_verifier", "to": "consistency_verifier"},
            {"from": "workbench_translate", "to": "consistency_verifier"},
            {"from": "workbench_code_assist", "to": "consistency_verifier"},
            {"from": "consistency_verifier", "to": CHAT_RENDER_NODE_ID},
        ]
        fallback_edges = [
            {"from": CHAT_INTENT_SOURCE_NODE_ID, "to": "core_reasoner_secondary"},
            {"from": "core_reasoner_secondary", "to": SCENE_ROUTER_POST_CORE_NODE_ID},
        ]
        budget_routes = [
            [CHAT_DIRECT_NODE_ID],
            [CHAT_INTENT_SOURCE_NODE_ID, "planner_constraints", "tool_planner", "safety_verifier", "consistency_verifier", CHAT_RENDER_NODE_ID],
            [CHAT_INTENT_SOURCE_NODE_ID, "workbench_translate", "consistency_verifier", CHAT_RENDER_NODE_ID],
            [CHAT_INTENT_SOURCE_NODE_ID, "workbench_code_assist", "consistency_verifier", CHAT_RENDER_NODE_ID],
        ]
        chain_budget_ms = max(
            sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in route)
            for route in budget_routes
        ) + int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
        primary_budget_node_id = CHAT_INTENT_SOURCE_NODE_ID
    else:
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
        fallback_edges = (
            []
            if is_chat_fast_path
            else [
                {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
                {"from": "core_reasoner_secondary", "to": fallback_target_node},
            ]
        )
        chain_budget_ms = (
            sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence)
            + (
                int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
                if not is_chat_fast_path
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
    if is_chat_fast_path:
        policy_timeout_ms = max(8_000, min(int(policy_timeout_ms), 12_000))
    elif use_scene_router:
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
            "online_chain": (
                "graph_entry_scene_router + scene_routed_langgraph"
                if use_scene_router
                else (
                    "chat_direct_single_node + scene_routed_langgraph"
                    if is_chat_fast_path
                    else "six_recall + scene_routed_langgraph + targeted_renderer"
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
