"""Graph 配置构建模块。

负责 Chat 图的配置构建。意图识别与场景判定统一由 Thalamus 承接。
"""

from __future__ import annotations

import os
from typing import Any, Callable

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
    resolve_gemini_primary_fallback_model_id,
    resolve_host_llm_model_id,
)
from host_brain.integrations.codex_cli_provider import codex_cli_lm_allowed_for_background, codex_cli_lm_available, resolve_codex_cli_model

# ---------------------------------------------------------------------------
# Constants re-exported from graph_config_constants.py (backward compat)
# ---------------------------------------------------------------------------
from host_brain.app.graph_config_constants import (  # noqa: E402
    CHAT_GRAPH_NODE_ORDER,
    CHAT_INTENT_SOURCE_NODE_ID,
    SCENE_TYPE_AUTO,
    _GRAPH_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES,
    SUPERVISOR_DISPATCH_NODE_ID,
    SUPERVISOR_DISPATCH_HANDLER_ID,
    SURFACE_REPLY_NODE_ID,
    SURFACE_REPLY_HANDLER_ID,
    TRANSLATE_AGENT_NODE_ID,
    TRANSLATE_AGENT_HANDLER_ID,
    INTENT_AGENT_NODE_ID,
    INTENT_AGENT_HANDLER_ID,
    INTENT_AGENT_ENTRY_NODE_ID,
    INTENT_AGENT_ENTRY_HANDLER_ID,
    JUDGE_AGENT_NODE_ID,
    JUDGE_AGENT_HANDLER_ID,
    RETRIEVAL_AGENT_NODE_ID,
    RETRIEVAL_AGENT_HANDLER_ID,
    EMOTION_AGENT_NODE_ID,
    EMOTION_AGENT_HANDLER_ID,
    REPAIR_AGENT_NODE_ID,
    REPAIR_AGENT_HANDLER_ID,
    PENTEST_AGENT_NODE_ID,
    PENTEST_AGENT_HANDLER_ID,
    PENTEST_ATTACK_AGENT_HANDLER_ID,
    AGENT_DISPATCH_NODE_ID,
    AGENT_DISPATCH_HANDLER_ID,
    ALLOWED_EXPLICIT_SCENE_TYPES,
    HOST_CHAT_ROUTE_HOST_14B,
    HOST_CHAT_ROUTE_CLOUD,
    HOST_CHAT_ROUTE_SKILLS,
    ROUTE_DECISION_VERSION,
    ROUTE_TARGET_HOST,
    ROUTE_TARGET_EDGE,
    ROUTE_TARGET_CLOUD,
    ROUTE_TARGET_SKILLS,
    _HOST_CHAT_14B_DEFAULT_PROVIDER,
    _HOST_CHAT_14B_DEFAULT_MODEL_ID,
    _HOST_CHAT_14B_FALLBACK_API_KEY,
    _HOST_CHAT_14B_DEFAULT_BASE_URL,
    _HOST_CHAT_CLOUD_DEFAULT_PROVIDER,
    _PROVIDERS_REQUIRING_API_KEY,
)

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


def _env_value(name: str, legacy_name: str | None = None, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    if legacy_name:
        legacy_value = str(os.environ.get(legacy_name) or "").strip()
        if legacy_value:
            return legacy_value
    return default


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
    return codex_cli_lm_allowed_for_background()


def _surface_reply_codex_experiment_enabled(*, scene_type: str, execution_profile: str) -> bool:
    if str(scene_type or "").strip().upper() != SCENE_CHAT:
        return False
    if str(execution_profile or "").strip() != PROFILE_CHAT_FAST:
        return False
    if not _parse_bool(os.environ.get("HOST_SURFACE_REPLY_CODEX_EXPERIMENT_ENABLED") or os.environ.get("HOST_CHAT_AGENT_CODEX_EXPERIMENT_ENABLED"), default=False):
        return False
    return codex_cli_lm_allowed_for_background()


def _resolve_surface_reply_codex_model_id() -> str:
    return resolve_codex_cli_model(
        os.environ.get("HOST_SURFACE_REPLY_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_SPARK_MODEL_ID")
        or os.environ.get("HOST_CODEX_CHAT_MODEL_ID")
        or os.environ.get("HOST_CODEX_MODEL_ID")
    )




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
    # Legacy transport inputs are accepted only for call-site compatibility;
    # graph configuration is a secret-free projection of the RouteCatalog.
    del provider_fn, api_key, cloud_api_key, chat_base_url_override, cloud_base_url_override
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
    resolved_chat_available = bool(available and resolved_chat_provider != PROVIDER_DISABLED)

    resolved_cloud_provider = (
        str(cloud_provider_name or resolved_chat_provider or _HOST_CHAT_CLOUD_DEFAULT_PROVIDER)
        .strip()
        .lower()
        or _HOST_CHAT_CLOUD_DEFAULT_PROVIDER
    )
    if resolved_cloud_provider not in {
        PROVIDER_OPENAI,
            PROVIDER_GEMINI,
        PROVIDER_DEEPSEEK,
        PROVIDER_CODEX,
        PROVIDER_MOCK,
        PROVIDER_DISABLED,
    }:
        resolved_cloud_provider = _HOST_CHAT_CLOUD_DEFAULT_PROVIDER
    resolved_cloud_timeout_ms = max(800, int(cloud_timeout_ms or resolved_chat_timeout_ms))
    resolved_cloud_model_id = str(cloud_model_id or "").strip() or resolved_chat_model_id
    resolved_cloud_available_flag = (
        bool(cloud_available)
        if cloud_available is not None
        else bool(resolved_cloud_provider != PROVIDER_DISABLED)
    )
    resolved_cloud_available = bool(resolved_cloud_available_flag)

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
        node_available = resolved_chat_available if is_chat_node else resolved_cloud_available
        node_override: dict[str, Any] = {
            "provider": node_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "available": bool(node_available),
            # Transport callables and credentials are intentionally absent.
            # GraphExecutor crosses the canonical ModelAttemptService port.
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
            REPAIR_AGENT_HANDLER_ID,
            PENTEST_AGENT_HANDLER_ID,
            PENTEST_ATTACK_AGENT_HANDLER_ID,
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


# Production callers consume the canonical secret-free RouteCatalog projection.
from host_brain.app.route_catalog_runtime import (  # noqa: F401, E402
    resolve_host_llm_runtime,
    resolve_host_llm_runtime_async,
    resolve_surface_reply_llm_runtime,
    resolve_executive_router_llm_runtime,
    resolve_subagent_llm_runtime,
)
