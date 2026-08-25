"""Gemini 密钥状态读取；模型路由由服务端 RouteCatalog 统一负责。"""

from __future__ import annotations

import os
from typing import Any

import host_brain.app.config as host_config
from host_brain.core.key_profiles import resolve_secret_from_profiles
from host_brain.integrations.llm_gateway import PROVIDER_GEMINI


def _resolve_gemini_key_scope() -> str:
    return str(os.environ.get("HOST_LLM_KEY_PROFILE_SCOPE") or "host.llm").strip().lower() or "host.llm"


def _resolve_runtime_gemini_api_key() -> tuple[str | None, str | None, str | None]:
    """按运行时密钥档案解析 Gemini 密钥；失败仅返回可诊断原因和引用，不泄露值。"""
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


def inspect_gemini_key_status(*, preferred_api_key: str | None = None) -> dict[str, Any]:
    """导出无密钥的配置状态，供健康报告判断来源而不能恢复认证材料。"""
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
