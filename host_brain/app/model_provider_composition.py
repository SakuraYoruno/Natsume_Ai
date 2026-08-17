"""应用拥有的模型提供方凭据与调用组合适配器。"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from host_brain.app import config as host_config
from host_brain.core.key_profiles import resolve_secret_from_profiles
from host_brain.integrations.llm_helpers import (
    _pop_last_provider_usage,
    _resolve_provider_base_url,
    _set_last_provider_usage,
    invoke_llm_text,
)


def _missing_model_attempt_service() -> Any:
    raise RuntimeError("HOST_APP_LOCAL_MODEL_ATTEMPT_PROVIDER_NOT_INJECTED")


@dataclass(frozen=True, slots=True)
class ApplicationModelAttemptServicePort:
    """指向进程模型组合的非拥有端口。

    Domain Pack 构建保持无副作用；账本和提供方图只在任务通过显式注入端口实际调用时
    解析，端口本身不缓存服务、更不创建另一套模型权威。
    """

    service_provider: Callable[[], Any] = _missing_model_attempt_service

    async def complete(self, request: object):  # noqa: ANN201
        return await self.service_provider().complete(request)

    def complete_sync(self, request: object):  # noqa: ANN201
        return self.service_provider().complete_sync(request)

    @property
    def prompt_registry(self):  # noqa: ANN201
        return self.service_provider().prompt_registry

def resolve_model_provider_credential(provider: str) -> str | None:
    """解析单个提供方凭据，不缓存也不修改环境。

    先尝试受 scope/epoch 约束的密钥档案，再回退到部署环境；本地 OpenAI 兼容端点的
    占位键仅满足本地协议形状，绝不能被视作远程提供方认证材料。
    """

    normalized = (
        "openai"
        if str(provider).strip().lower() == "openai_compatible"
        else str(provider or "").strip().lower()
    )
    try:
        profiles, secret_store, current_epoch, node_id = host_config._resolve_runtime_key_materials(  # noqa: SLF001
            env=os.environ,
            key_profiles=None,
            secret_store=None,
            node_id=None,
            current_epoch=None,
        )
        resolved = resolve_secret_from_profiles(
            provider=normalized,
            node_id=node_id,
            scope=str(os.environ.get("HOST_LLM_KEY_PROFILE_SCOPE") or "host.llm").strip().lower()
            or "host.llm",
            key_profiles=profiles,
            secret_store=secret_store,
            current_epoch=current_epoch,
        )
        if resolved.success:
            return str(resolved.secret_value or "").strip() or None
    except Exception:
        pass

    env_name = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }.get(normalized)
    value = str(os.environ.get(env_name or "") or "").strip() if env_name else ""
    if value:
        return value
    if normalized == "openai":
        parsed = urlparse(str(_resolve_provider_base_url(normalized) or ""))
        if (parsed.hostname or "").strip().lower() in {"localhost", "127.0.0.1", "::1"}:
            return "host-local-placeholder-key"
    return None


def resolve_model_provider_base_url(provider: str) -> str | None:
    normalized = "openai" if str(provider).strip().lower() == "openai_compatible" else provider
    return _resolve_provider_base_url(normalized)


def invoke_model_provider_transport(**kwargs: Any) -> dict[str, Any]:
    """执行一次提供方传输，并返回与该次调用绑定的用量快照。"""

    _set_last_provider_usage(None)
    content = invoke_llm_text(**kwargs)
    usage = _pop_last_provider_usage()
    return {
        "content": content,
        "usage": dict(usage) if isinstance(usage, dict) else {},
    }


__all__ = [
    "ApplicationModelAttemptServicePort",
    "invoke_model_provider_transport",
    "resolve_model_provider_base_url",
    "resolve_model_provider_credential",
]
