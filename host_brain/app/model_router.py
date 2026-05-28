"""Model routing helpers for provider/model/single-key selection."""

from __future__ import annotations

import os
from typing import Any

from host_brain.app import config as host_config
from host_brain.app.model_capability_registry import resolve_model_capability_profile
from host_brain.core.key_profiles import resolve_secret_from_profiles
from host_brain.integrations.llm_gateway import PROVIDER_GEMINI

_GEMINI_CLOUD_DEFAULT_MODEL_ID = "gemini-2.5-flash-lite"
_GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID = "gemini-2.5-pro"
_GEMINI_DISPATCH_AGENT_DEFAULT_MODEL_ID = "gemini-2.5-flash-lite"
_GEMINI_EXECUTIVE_ROUTER_DEFAULT_MODEL_ID = _GEMINI_DISPATCH_AGENT_DEFAULT_MODEL_ID
_GEMINI_CORTEX_ORCHESTRATOR_DEFAULT_MODEL_ID = _GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID
_GEMINI_SUBAGENT_DEFAULT_MODEL_ID = "gemini-2.5-pro"


def _resolve_gemini_key_scope() -> str:
    return str(os.environ.get("HOST_LLM_KEY_PROFILE_SCOPE") or "host.llm").strip().lower() or "host.llm"


def _resolve_runtime_gemini_api_key() -> tuple[str | None, str | None, str | None]:
    try:
        profiles, secret_store, current_epoch, node_id = host_config._resolve_runtime_key_materials(  # noqa: SLF001
            env=os.environ,
            key_profiles=None,
            secret_store=None,
            node_id=None,
            current_epoch=None,
        )
        resolved = resolve_secret_from_profiles(
            provider=PROVIDER_GEMINI,
            node_id=node_id,
            scope=_resolve_gemini_key_scope(),
            key_profiles=profiles,
            secret_store=secret_store,
            current_epoch=current_epoch,
        )
    except Exception as exc:
        return None, None, f"runtime_profile_error:{exc}"
    if not resolved.success:
        return None, resolved.secret_ref, str(resolved.reason_code or resolved.detail or "runtime_profile_unavailable")
    return str(resolved.secret_value or "").strip() or None, resolved.secret_ref, None


def resolve_gemini_api_key(*, preferred_api_key: str | None = None) -> str | None:
    preferred = str(preferred_api_key or "").strip()
    if preferred:
        return preferred

    runtime_key, _, _ = _resolve_runtime_gemini_api_key()
    if runtime_key:
        return runtime_key

    value = str(os.environ.get("GEMINI_API_KEY") or "").strip()
    return value or None


def inspect_gemini_key_status(*, preferred_api_key: str | None = None) -> dict[str, Any]:
    preferred = str(preferred_api_key or "").strip()
    runtime_key, runtime_secret_ref, runtime_error = _resolve_runtime_gemini_api_key()
    env_key = str(os.environ.get("GEMINI_API_KEY") or "").strip()

    if preferred:
        source = "preferred"
        configured = True
    elif runtime_key:
        source = "runtime_profile"
        configured = True
    elif env_key:
        source = "env"
        configured = True
    else:
        source = "empty"
        configured = False

    return {
        "provider_name": PROVIDER_GEMINI,
        "scope": _resolve_gemini_key_scope(),
        "key_configured": configured,
        "source": source,
        "runtime_profile_available": bool(runtime_key),
        "runtime_secret_ref": runtime_secret_ref,
        "runtime_error": runtime_error,
        "env_key_configured": bool(env_key),
    }


def _resolve_gemini_model_id_for_role(role: str) -> str:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"chat_agent", "cortex_orchestrator"}:
        env_keys = (
            ("HOST_CORTEX_ORCHESTRATOR_MODEL_ID", "HOST_TOP_AGENT_MODEL_ID", "HOST_CHAT_AGENT_MODEL_ID")
            if normalized_role == "cortex_orchestrator"
            else ("HOST_CHAT_AGENT_MODEL_ID",)
        )
        for env_key in env_keys:
            value = str(os.environ.get(env_key) or "").strip()
            if value:
                return value
        return (
            _GEMINI_CORTEX_ORCHESTRATOR_DEFAULT_MODEL_ID
            if normalized_role == "cortex_orchestrator"
            else _GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID
        )
    if normalized_role == "executive_router":
        env_keys = (
            "HOST_EXECUTIVE_ROUTER_MODEL_ID",
            "HOST_DISPATCH_AGENT_MODEL_ID",
        )
        for env_key in env_keys:
            value = str(os.environ.get(env_key) or "").strip()
            if value:
                return value
        return (
            _GEMINI_EXECUTIVE_ROUTER_DEFAULT_MODEL_ID
            if normalized_role == "executive_router"
            else _GEMINI_DISPATCH_AGENT_DEFAULT_MODEL_ID
        )
    if normalized_role == "subagent":
        return (
            str(os.environ.get("HOST_SUBAGENT_MODEL_ID") or _GEMINI_SUBAGENT_DEFAULT_MODEL_ID).strip()
            or _GEMINI_SUBAGENT_DEFAULT_MODEL_ID
        )
    return (
        str(os.environ.get("HOST_GEMINI_MODEL_ID") or _GEMINI_CLOUD_DEFAULT_MODEL_ID).strip()
        or _GEMINI_CLOUD_DEFAULT_MODEL_ID
    )


def resolve_gemini_model_route(
    *,
    role: str,
    preferred_api_key: str | None = None,
    route_key: str | None = None,
) -> dict[str, Any]:
    _ = route_key
    normalized_role = str(role or "").strip().lower() or "cloud_primary"
    model_id = _resolve_gemini_model_id_for_role(normalized_role)
    selected_api_key = resolve_gemini_api_key(preferred_api_key=preferred_api_key)
    capability_profile = resolve_model_capability_profile(
        provider_name=PROVIDER_GEMINI,
        model_id=model_id,
        role=normalized_role,
    )

    return {
        "provider_name": PROVIDER_GEMINI,
        "role": normalized_role,
        "model_id": model_id,
        "api_key": selected_api_key,
        "key_configured": bool(selected_api_key),
        "key_source": inspect_gemini_key_status(preferred_api_key=preferred_api_key).get("source"),
        "selection_reason": (
            "GEMINI_SINGLE_KEY_SELECTED"
            if selected_api_key
            else "GEMINI_SINGLE_KEY_EMPTY"
        ),
        "capability_profile": capability_profile,
    }
