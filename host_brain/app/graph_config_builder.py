"""Graph 配置构建模块。

负责 Chat 图的配置构建。意图识别与场景判定统一由 Thalamus 承接。
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
    PROVIDER_CODEX,
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
from host_brain.app.model_router import resolve_gemini_model_route
from host_brain.integrations.codex_cli_provider import codex_cli_lm_available, resolve_codex_cli_model

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
SCENE_TYPE_AUTO = "AUTO"
_GRAPH_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES = frozenset({SCENE_CHAT, SCENE_VISION_UI})

# M-MA1：Supervisor 分派节点常量
SUPERVISOR_DISPATCH_NODE_ID = "supervisor_dispatch"
SUPERVISOR_DISPATCH_HANDLER_ID = "supervisor_dispatch"

# M-MA5：各 Agent Handler 节点常量
SURFACE_REPLY_NODE_ID = "surface_reply_node"
SURFACE_REPLY_HANDLER_ID = "surface_reply"
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
PENTEST_AGENT_NODE_ID = "pentest_agent_node"
PENTEST_AGENT_HANDLER_ID = "pentest_agent"
# P16：M-MA6 动态路由虚拟节点（graph_executor 直接分派，不再经过 agent_dispatch_handler）
AGENT_DISPATCH_NODE_ID = "agent_dispatch_node"
AGENT_DISPATCH_HANDLER_ID = "agent_dispatch"  # 保留常量供其他模块引用，不再用于 binding

# 客户端允许显式指定的场景类型白名单
# 供 chat_handler.py 校验客户端传入的 scene_type，统一维护避免两处同步
# [选项一] SCENE_VISION_UI 已有完整实现（chat_handler_vision.py），加入白名单
# 注意：VISION_UI 场景需要客户端传入 image_upload_consent=true 才允许上传图片
# AUTO：显式走多 agent 编排（supervisor_dispatch）；默认已统一图+动态路由，可不传
ALLOWED_EXPLICIT_SCENE_TYPES: frozenset[str] = frozenset({
    SCENE_CHAT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_TYPE_AUTO,
})

HOST_CHAT_ROUTE_HOST_14B = "host_14b"
HOST_CHAT_ROUTE_CLOUD = "cloud"
HOST_CHAT_ROUTE_SKILLS = "skills"

ROUTE_DECISION_VERSION = "route.v1"
ROUTE_TARGET_HOST = "host"
ROUTE_TARGET_EDGE = "edge"
ROUTE_TARGET_CLOUD = "cloud"
ROUTE_TARGET_SKILLS = "skills"

_HOST_CHAT_14B_DEFAULT_PROVIDER = PROVIDER_OPENAI
_HOST_CHAT_14B_DEFAULT_MODEL_ID = "qwen3:14b"
_HOST_CHAT_14B_FALLBACK_API_KEY = "host-local-placeholder-key"
_HOST_CHAT_14B_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_HOST_CHAT_CLOUD_DEFAULT_PROVIDER = PROVIDER_GEMINI
_PROVIDERS_REQUIRING_API_KEY = frozenset({PROVIDER_OPENAI, PROVIDER_CODEX, PROVIDER_GEMINI, PROVIDER_DEEPSEEK})


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def resolve_scene_node_sequence(scene_type: str) -> list[str]:
    """Return the model-node sequence for explicit legacy graph profiles."""
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


def resolve_chat_retrieval_gate(*, scene_type: str) -> tuple[bool, str | None]:
    if not _parse_bool(os.environ.get("HOST_ENABLE_RETRIEVAL"), default=True):
        return False, "HOST_RETRIEVAL_DISABLED"
    if (
        str(scene_type or "").strip().upper() == SCENE_CHAT
        and _parse_bool(os.environ.get("HOST_CHAT_DISABLE_RETRIEVAL"), default=False)
    ):
        return False, "HOST_CHAT_RETRIEVAL_DISABLED"
    return True, None


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


def _graph_codex_migration_enabled(*, scene_type: str, execution_profile: str) -> bool:
    if not _parse_bool(os.environ.get("HOST_GRAPH_CODEX_MIGRATION_ENABLED"), default=False):
        return False
    if str(execution_profile or "").strip() == PROFILE_CHAT_FAST:
        return False
    normalized_scene = str(scene_type or "").strip().upper()
    excluded = {
        str(item or "").strip().upper()
        for item in _parse_csv_tokens(
            os.environ.get("HOST_GRAPH_CODEX_MIGRATION_EXCLUDED_SCENES")
            or ",".join(sorted(_GRAPH_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES))
        )
        if str(item or "").strip()
    }
    if normalized_scene in excluded:
        return False
    included = {
        str(item or "").strip().upper()
        for item in _parse_csv_tokens(os.environ.get("HOST_GRAPH_CODEX_MIGRATION_INCLUDED_SCENES"))
        if str(item or "").strip()
    }
    if included and normalized_scene not in included:
        return False
    return codex_cli_lm_available()


def _surface_reply_codex_experiment_enabled(*, scene_type: str, execution_profile: str) -> bool:
    if str(scene_type or "").strip().upper() != SCENE_CHAT:
        return False
    if str(execution_profile or "").strip() != PROFILE_CHAT_FAST:
        return False
    if not _parse_bool(os.environ.get("HOST_SURFACE_REPLY_CODEX_EXPERIMENT_ENABLED") or os.environ.get("HOST_CHAT_AGENT_CODEX_EXPERIMENT_ENABLED"), default=False):
        return False
    return codex_cli_lm_available()


def _resolve_surface_reply_codex_model_id() -> str:
    return resolve_codex_cli_model(
        os.environ.get("HOST_SURFACE_REPLY_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_SPARK_MODEL_ID")
        or os.environ.get("HOST_CODEX_CHAT_MODEL_ID")
        or os.environ.get("HOST_CODEX_MODEL_ID")
    )


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
        PROVIDER_CODEX: "CODEX_API_KEY",
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
    if provider == PROVIDER_CODEX and not str(api_key or "").strip() and codex_cli_lm_available():
        return True
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


def _resolve_cloud_provider_model_id(*, chain_name: str) -> str:
    normalized_chain = str(chain_name or "").strip().lower()
    if normalized_chain == "gemini":
        return str(os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-flash-lite").strip() or "gemini-2.5-flash-lite"
    if normalized_chain == "codex":
        return resolve_codex_cli_model(os.environ.get("HOST_CODEX_MODEL_ID"))
    if normalized_chain == "deepseek":
        return str(os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    return "mock-default"


def _resolve_codex_base_url() -> str | None:
    raw = str(
        os.environ.get("HOST_CODEX_BASE_URL")
        or os.environ.get("HOST_OPENAI_BASE_URL")
        or ""
    ).strip()
    if not raw:
        return None
    return _normalize_openai_compat_base_url(raw)


def _split_api_and_root_base(base_url: str | None) -> tuple[str | None, str | None]:
    normalized = _normalize_openai_compat_base_url(base_url)
    if not normalized:
        return None, None
    root = normalized[:-3].rstrip("/") if normalized.lower().endswith("/v1") else normalized.rstrip("/")
    return normalized, (root or normalized)


def _resolve_host_14b_base_url(*, provider_name: str) -> str | None:
    provider = str(provider_name or "").strip().lower()
    if provider != PROVIDER_OPENAI:
        return None
    raw = str(
        os.environ.get("HOST_CHAT_HOST_14B_BASE_URL")
        or os.environ.get("HOST_OPENAI_BASE_URL")
        or _HOST_CHAT_14B_DEFAULT_BASE_URL
    ).strip()
    return _normalize_openai_compat_base_url(raw)


def _default_host_14b_timeout_ms(*, provider_name: str, cloud_timeout_ms: int) -> int:
    """Give local Ollama enough time on CPU-class hosts before timing out."""
    provider = str(provider_name or "").strip().lower()
    default_timeout_ms = int(cloud_timeout_ms)
    if provider != PROVIDER_OPENAI:
        return default_timeout_ms
    base_url = str(_resolve_host_14b_base_url(provider_name=provider_name) or "").strip().lower()
    if "127.0.0.1:11434" in base_url or "localhost:11434" in base_url:
        return max(default_timeout_ms, 90_000)
    return default_timeout_ms


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
            "User-Agent": "NatsumeHost14BHealthProbe/1.0",
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


def _resolve_host_14b_health_status(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    if not _parse_bool(os.environ.get("HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED"), default=False):
        return True, "HOST_14B_HEALTHCHECK_DISABLED"

    ready_value = os.environ.get("HOST_CHAT_HOST_14B_READY")
    if ready_value is not None and not _parse_bool(ready_value, default=True):
        return False, "HOST_14B_READY_FLAG_DOWN"

    health_url = str(os.environ.get("HOST_CHAT_HOST_14B_HEALTHCHECK_URL") or "").strip()
    probe_urls: list[str] = []
    if health_url:
        probe_urls.append(health_url)
    else:
        api_base, root_base = _split_api_and_root_base(_resolve_host_14b_base_url(provider_name=provider_name))
        if api_base:
            probe_urls.append(f"{api_base}/models")
        if root_base:
            probe_urls.append(f"{root_base}/health")
            probe_urls.append(f"{root_base}/api/tags")

    if not probe_urls:
        if ready_value is not None:
            return _parse_bool(ready_value, default=True), "HOST_14B_READY_FLAG_ONLY"
        return True, "HOST_14B_NO_PROBE_URL"

    first_failure = ""
    for url in probe_urls:
        ok, detail = _probe_http_endpoint(url=url, timeout_ms=timeout_ms)
        if ok:
            return True, f"HOST_14B_PROBE_OK:{detail}"
        if not first_failure:
            first_failure = detail
    return False, f"HOST_14B_PROBE_FAILED:{first_failure or 'unknown'}"


async def _resolve_host_14b_health_status_async(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    """异步版本的 14B 健康探测——在事件循环中调用此函数，不阻塞事件循环。

    修复审计问题 #1：原 _resolve_host_14b_health_status 使用同步 urllib 在事件循环中
    直接调用，会阻塞事件循环最多 3s。此函数通过 asyncio.to_thread 包装解决该问题。
    """
    return await asyncio.to_thread(
        _resolve_host_14b_health_status,
        provider_name=provider_name,
        timeout_ms=timeout_ms,
    )


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
    """Chat 图：START → chat_direct_ja → renderer，无 Supervisor/agent_dispatch。"""
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

    resolved_chat_provider = str(provider_name or _HOST_CHAT_14B_DEFAULT_PROVIDER).strip().lower() or _HOST_CHAT_14B_DEFAULT_PROVIDER
    resolved_chat_model_id = str(model_id or "").strip() or _HOST_CHAT_14B_DEFAULT_MODEL_ID
    resolved_chat_timeout_ms = max(800, int(timeout_ms))
    chat_codex_experiment = _surface_reply_codex_experiment_enabled(
        scene_type=resolved_scene_type,
        execution_profile=execution_profile,
    )
    if chat_codex_experiment:
        resolved_chat_provider = PROVIDER_CODEX
        resolved_chat_model_id = _resolve_surface_reply_codex_model_id()
        resolved_chat_timeout_ms = max(
            resolved_chat_timeout_ms,
            _parse_int(
                os.environ.get("HOST_SURFACE_REPLY_CODEX_MIN_TIMEOUT_MS") or os.environ.get("HOST_CHAT_AGENT_CODEX_MIN_TIMEOUT_MS"),
                default=45_000,
                min_value=800,
                max_value=10 * 60 * 1000,
            ),
        )
    resolved_chat_base_url_override = (
        _normalize_openai_compat_base_url(chat_base_url_override)
        if resolved_chat_provider == PROVIDER_OPENAI
        else None
    )
    if resolved_chat_provider == PROVIDER_OPENAI and not resolved_chat_base_url_override:
        resolved_chat_base_url_override = _resolve_host_14b_base_url(provider_name=resolved_chat_provider)
    resolved_chat_api_key = _resolve_provider_api_key(
        provider_name=resolved_chat_provider,
        preferred_api_key=str(api_key or "").strip() or None,
    )
    if chat_codex_experiment:
        resolved_chat_api_key = None
    if _provider_requires_api_key(resolved_chat_provider) and not resolved_chat_api_key:
        if resolved_chat_provider == PROVIDER_OPENAI and _parse_bool(os.environ.get("HOST_CHAT_HOST_14B_ALLOW_EMPTY_KEY"), default=True):
            resolved_chat_api_key = _HOST_CHAT_14B_FALLBACK_API_KEY
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
        PROVIDER_CODEX,
        PROVIDER_GEMINI,
        PROVIDER_DEEPSEEK,
        PROVIDER_MOCK,
        PROVIDER_DISABLED,
    }:
        resolved_cloud_provider = _HOST_CHAT_CLOUD_DEFAULT_PROVIDER
    graph_codex_migrated = _graph_codex_migration_enabled(
        scene_type=resolved_scene_type,
        execution_profile=execution_profile,
    )
    if graph_codex_migrated:
        resolved_cloud_provider = PROVIDER_CODEX
    resolved_cloud_timeout_ms = max(800, int(cloud_timeout_ms or resolved_chat_timeout_ms))
    if graph_codex_migrated:
        resolved_cloud_timeout_ms = max(
            resolved_cloud_timeout_ms,
            _parse_int(os.environ.get("HOST_GRAPH_CODEX_MIN_TIMEOUT_MS"), default=30_000, min_value=800, max_value=10 * 60 * 1000),
        )
    resolved_cloud_model_id = (
        resolve_codex_cli_model(os.environ.get("HOST_GRAPH_CODEX_MODEL_ID") or os.environ.get("HOST_CODEX_MODEL_ID"))
        if graph_codex_migrated
        else (str(cloud_model_id or "").strip() or resolve_host_llm_model_id(resolved_cloud_provider))
    )
    resolved_cloud_base_url_override = (
        _normalize_openai_compat_base_url(cloud_base_url_override)
        if (not graph_codex_migrated and resolved_cloud_provider in {PROVIDER_OPENAI, PROVIDER_CODEX})
        else None
    )
    resolved_cloud_api_key = None if graph_codex_migrated else _resolve_provider_api_key(
        provider_name=resolved_cloud_provider,
        preferred_api_key=str(cloud_api_key or "").strip() or None,
    )
    resolved_cloud_available_flag = (
        True
        if graph_codex_migrated
        else (
        bool(cloud_available)
        if cloud_available is not None
        else bool(resolved_cloud_provider != PROVIDER_DISABLED)
        )
    )
    resolved_cloud_available = _is_provider_available(
        provider_name=resolved_cloud_provider,
        api_key=resolved_cloud_api_key,
        configured_available=resolved_cloud_available_flag,
    )

    base_timeout_ms = max(800, int(resolved_cloud_timeout_ms))
    node_model_ids: dict[str, str] = {
        "core_reasoner_primary": "gemini-2.5-pro",
        "core_reasoner_secondary": "gemini-2.5-flash-lite",
        "planner_constraints": "gemini-2.5-pro",
        "tool_planner": "gemini-2.5-flash-lite",
        "safety_verifier": "gemini-2.5-flash-lite",
        "consistency_verifier": "gemini-2.5-flash-lite",
        "renderer_ja_c_style": "gemini-2.5-flash-lite",
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
            node_model_ids["core_reasoner_primary"] = "gemini-2.5-flash-lite"
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
        # Supervised 图：supervisor_dispatch → agent_dispatch → judge_agent → renderer
        scene_node_sequence = [
            SUPERVISOR_DISPATCH_NODE_ID,
            AGENT_DISPATCH_NODE_ID,
            JUDGE_AGENT_NODE_ID,
            CHAT_RENDER_NODE_ID,
        ]
        # P3.5：allowlist 由图配置提供；workbench_* 为 handler 可调用的 model 节点，不入边序列
        # P16：allowlist 对齐 _AGENT_HANDLER_MAP（graph_executor 内的全量 agent）
        _dispatch_allowlist = [
            SURFACE_REPLY_HANDLER_ID,
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


# ---------------------------------------------------------------------------
# Phase XV: 三套 Agent 专用 LLM 运行时解析
# ---------------------------------------------------------------------------

def _legacy_resolve_surface_reply_llm_runtime(*, base_runtime: dict) -> dict:
    """SurfaceReply（旧角色本人）的 LLM 运行时配置。

    追求「好」：高质量模型 + 高温度 + 流式输出 + 双语生成能力。
    默认使用 gemini-2.5-pro，可通过 HOST_SURFACE_REPLY_MODEL_ID 覆盖；旧 HOST_CHAT_AGENT_MODEL_ID 仅兼容读取。

    Args:
        base_runtime: 来自 resolve_host_llm_runtime_async() 的基础运行时

    Returns:
        SurfaceReply 专用 LLM 运行时 dict，可直接传给 astream_llm_chunks()
    """
    model_id = str(
        os.environ.get("HOST_SURFACE_REPLY_MODEL_ID") or os.environ.get("HOST_CHAT_AGENT_MODEL_ID") or "gemini-2.5-pro"
    ).strip() or "gemini-2.5-pro"
    timeout_ms = max(800, _parse_int(
        os.environ.get("HOST_SURFACE_REPLY_TIMEOUT_MS") or os.environ.get("HOST_CHAT_AGENT_TIMEOUT_MS"),
        default=30_000,
        min_value=800,
        max_value=120_000,
    ))
    return {
        **base_runtime,
        "model_id": model_id,
        "chat_model_id": model_id,
        "timeout_ms": timeout_ms,
        "chat_timeout_ms": timeout_ms,
        # SurfaceReply 专属 gen_params：高温度让回复更自然有变化
        "agent_gen_params": {
            "temperature": 0.8,
            "top_p": 0.92,
        },
        "agent_role": "surface_reply",
    }


def _legacy_resolve_executive_router_llm_runtime(*, base_runtime: dict) -> dict:
    """ExecutiveRouter 的旧版 LLM 运行时配置。

    追求「快」：轻量模型 + 低温度 + 非流式 + 严格 JSON 输出。
    默认使用 gemini-2.5-flash-lite，可通过 HOST_DISPATCH_AGENT_MODEL_ID 覆盖。

    Args:
        base_runtime: 来自 resolve_host_llm_runtime_async() 的基础运行时

    Returns:
        ExecutiveRouter 专用 LLM 运行时 dict
    """
    model_id = str(
        os.environ.get("HOST_DISPATCH_AGENT_MODEL_ID") or "gemini-2.5-flash-lite"
    ).strip() or "gemini-2.5-flash-lite"
    timeout_ms = max(800, _parse_int(
        os.environ.get("HOST_DISPATCH_AGENT_TIMEOUT_MS"),
        default=5_000,
        min_value=800,
        max_value=30_000,
    ))
    return {
        **base_runtime,
        "model_id": model_id,
        "chat_model_id": model_id,
        "timeout_ms": timeout_ms,
        "chat_timeout_ms": timeout_ms,
        # Executive Router 专属 gen_params：低温度确保路由判断稳定可预测
        "agent_gen_params": {
            "temperature": 0.1,
            "top_p": 0.80,
        },
        "agent_role": "executive_router",
    }


def _legacy_resolve_subagent_llm_runtime(*, base_runtime: dict) -> dict:
    """SubAgent（code/translate/intent/vision）的 LLM 运行时配置。

    追求「准」：中等质量模型 + 低温度 + 非流式 + 多轮工具调用。
    默认使用 gemini-2.5-pro，可通过 HOST_SUBAGENT_MODEL_ID 覆盖。

    Args:
        base_runtime: 来自 resolve_host_llm_runtime_async() 的基础运行时

    Returns:
        SubAgent 专用 LLM 运行时 dict，可直接传给 ReAct 循环的 gateway 构建
    """
    model_id = str(
        os.environ.get("HOST_SUBAGENT_MODEL_ID") or "gemini-2.5-pro"
    ).strip() or "gemini-2.5-pro"
    timeout_ms = max(800, _parse_int(
        os.environ.get("HOST_SUBAGENT_TIMEOUT_MS"),
        default=30_000,
        min_value=800,
        max_value=120_000,
    ))
    return {
        **base_runtime,
        "model_id": model_id,
        "chat_model_id": model_id,
        "timeout_ms": timeout_ms,
        "chat_timeout_ms": timeout_ms,
        # SubAgent 专属 gen_params：低温度保证代码/翻译准确性
        "agent_gen_params": {
            "temperature": 0.3,
            "top_p": 0.85,
        },
        "agent_role": "sub_agent",
    }


def _resolve_non_gemini_agent_model_id_v2(
    *,
    provider_name: str,
    base_runtime: dict[str, Any],
    model_env_names: tuple[str, ...],
) -> str:
    for model_env_name in model_env_names:
        explicit_model = str(os.environ.get(model_env_name) or "").strip()
        if explicit_model and not explicit_model.lower().startswith("gemini-"):
            return explicit_model
    base_model_id = str(
        base_runtime.get("model_id")
        or base_runtime.get("chat_model_id")
        or ""
    ).strip()
    if base_model_id:
        return base_model_id
    return resolve_host_llm_model_id(provider_name)


def _first_non_empty_env(*env_names: str) -> str | None:
    for env_name in env_names:
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    return None


def _resolve_agent_llm_runtime_v2(
    *,
    base_runtime: dict[str, Any],
    role: str,
    model_env_name: str,
    timeout_env_name: str,
    timeout_default_ms: int,
    timeout_max_ms: int,
    gen_params: dict[str, float],
    fallback_model_env_names: tuple[str, ...] = (),
    fallback_timeout_env_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    provider_name = str(base_runtime.get("provider_name") or PROVIDER_GEMINI).strip().lower() or PROVIDER_GEMINI
    timeout_value = _first_non_empty_env(timeout_env_name, *fallback_timeout_env_names)
    timeout_ms = max(
        800,
        _parse_int(
            timeout_value,
            default=timeout_default_ms,
            min_value=800,
            max_value=timeout_max_ms,
        ),
    )
    selected_api_key = str(
        base_runtime.get("llm_api_key")
        or base_runtime.get("chat_llm_api_key")
        or ""
    ).strip() or None
    model_route: dict[str, Any] = {}
    if provider_name == PROVIDER_GEMINI:
        model_route = resolve_gemini_model_route(
            role=role,
            preferred_api_key=selected_api_key,
            route_key=(
                str(base_runtime.get("route_reason_code") or "").strip()
                or str(base_runtime.get("route_target") or "").strip()
                or None
            ),
        )
        model_id = str(
            model_route.get("model_id")
            or base_runtime.get("model_id")
            or base_runtime.get("chat_model_id")
            or "gemini-2.5-flash-lite"
        ).strip() or "gemini-2.5-flash-lite"
        selected_api_key = str(model_route.get("api_key") or selected_api_key or "").strip() or None
    else:
        model_id = _resolve_non_gemini_agent_model_id_v2(
            provider_name=provider_name,
            base_runtime=base_runtime,
            model_env_names=(model_env_name, *fallback_model_env_names),
        )

    return {
        **base_runtime,
        "model_id": model_id,
        "chat_model_id": model_id,
        "timeout_ms": timeout_ms,
        "chat_timeout_ms": timeout_ms,
        "llm_api_key": selected_api_key,
        "chat_llm_api_key": selected_api_key,
        "agent_gen_params": dict(gen_params),
        "agent_role": role,
        "agent_model_route": (model_route or None),
    }


def resolve_surface_reply_llm_runtime(*, base_runtime: dict) -> dict:
    return _resolve_agent_llm_runtime_v2(
        base_runtime=base_runtime,
        role="surface_reply",
        model_env_name="HOST_SURFACE_REPLY_MODEL_ID",
        timeout_env_name="HOST_SURFACE_REPLY_TIMEOUT_MS",
        timeout_default_ms=30_000,
        timeout_max_ms=120_000,
        gen_params={"temperature": 0.8, "top_p": 0.92},
        fallback_model_env_names=("HOST_CHAT_AGENT_MODEL_ID",),
        fallback_timeout_env_names=("HOST_CHAT_AGENT_TIMEOUT_MS",),
    )


def resolve_executive_router_llm_runtime(*, base_runtime: dict) -> dict:
    return _resolve_agent_llm_runtime_v2(
        base_runtime=base_runtime,
        role="executive_router",
        model_env_name="HOST_EXECUTIVE_ROUTER_MODEL_ID",
        timeout_env_name="HOST_EXECUTIVE_ROUTER_TIMEOUT_MS",
        timeout_default_ms=5_000,
        timeout_max_ms=30_000,
        gen_params={"temperature": 0.1, "top_p": 0.80},
        fallback_model_env_names=("HOST_DISPATCH_AGENT_MODEL_ID",),
        fallback_timeout_env_names=("HOST_DISPATCH_AGENT_TIMEOUT_MS",),
    )


def resolve_subagent_llm_runtime(*, base_runtime: dict) -> dict:
    runtime = _resolve_agent_llm_runtime_v2(
        base_runtime=base_runtime,
        role="subagent",
        model_env_name="HOST_SUBAGENT_MODEL_ID",
        timeout_env_name="HOST_SUBAGENT_TIMEOUT_MS",
        timeout_default_ms=30_000,
        timeout_max_ms=120_000,
        gen_params={"temperature": 0.3, "top_p": 0.85},
    )
    runtime["agent_role"] = "sub_agent"
    return runtime


# LLM 运行时解析函数已移至 graph_config_llm_runtime.py
from host_brain.app.graph_config_llm_runtime import (  # noqa: F401, E402
    resolve_host_llm_runtime,
    resolve_host_llm_runtime_async,
    resolve_surface_reply_llm_runtime,
    resolve_executive_router_llm_runtime,
    resolve_subagent_llm_runtime,
    _HOST_CHAT_14B_DEFAULT_PROVIDER,
    _HOST_CHAT_14B_DEFAULT_MODEL_ID,
)
