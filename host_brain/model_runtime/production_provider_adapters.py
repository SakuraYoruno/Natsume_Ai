"""Single-call production provider transport adapters.

Credentials and HTTP clients are owned by the injected callables.  This
module only validates that an authenticated callable was deliberately bound
and converts the canonical attempt into a provider-neutral chat payload.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from host_brain.model_runtime.contracts import ProviderAttemptRequest, ProviderResponse
from host_brain.model_runtime.provider_adapters import (
    CallableProviderAdapter,
    ProviderAdapterError,
    ProviderAdapterRegistry,
    StreamCallable,
)


SUPPORTED_PRODUCTION_PROVIDERS = frozenset(
    {"openai", "openai_compatible", "gemini", "deepseek", "codex", "minimax"}
)
_GENERATION_KEYS = frozenset(
    {"temperature", "top_p", "top_k", "max_tokens", "stop", "seed", "presence_penalty", "frequency_penalty"}
)


class ProductionProviderConfigurationError(ProviderAdapterError):
    pass


ProviderPayloadCallable = Callable[[Mapping[str, Any]], Any]
CredentialResolver = Callable[[str], str | None]
BaseUrlResolver = Callable[[str], str | None]
SingleAttemptTransport = Callable[..., str]


@dataclass(frozen=True, slots=True)
class ProductionProviderBinding:
    """Secret-free declaration for a pre-authenticated transport callable."""

    provider: str
    complete: ProviderPayloadCallable
    stream: ProviderPayloadCallable | None = None
    credential_available: bool = False
    adapter_id: str | None = None
    run_sync_in_thread: bool = False


def production_adapter_id(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized not in SUPPORTED_PRODUCTION_PROVIDERS:
        raise ProductionProviderConfigurationError(f"unsupported production provider: {normalized or '<empty>'}")
    return f"production.{normalized}"


def _generation_parameters(request: ProviderAttemptRequest) -> dict[str, Any]:
    raw = request.target.metadata.get("generation_parameters")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ProductionProviderConfigurationError("generation_parameters must be a mapping")
    unknown = set(raw) - _GENERATION_KEYS
    if unknown:
        raise ProductionProviderConfigurationError(
            "unsupported generation parameters: " + ", ".join(sorted(str(item) for item in unknown))
        )
    return {str(key): value for key, value in raw.items()}


def production_provider_payload(
    request: ProviderAttemptRequest,
    *,
    stream: bool,
) -> dict[str, Any]:
    """Create a credential-free payload for one injected transport call."""

    payload: dict[str, Any] = {
        "model": request.target.model_id,
        "messages": request.prompt.as_message_dicts(),
        "stream": bool(stream),
        "request_id": request.request_id,
    }
    response_format = request.prompt.output_contract.response_format()
    if response_format is not None:
        payload["response_format"] = response_format
    payload.update(_generation_parameters(request))
    return payload


def _build_adapter(binding: ProductionProviderBinding) -> CallableProviderAdapter:
    provider = str(binding.provider or "").strip().lower()
    default_adapter_id = production_adapter_id(provider)
    if not callable(binding.complete):
        raise ProductionProviderConfigurationError(f"provider {provider} has no complete callable")
    if binding.stream is not None and not callable(binding.stream):
        raise ProductionProviderConfigurationError(f"provider {provider} has invalid stream callable")
    if not binding.credential_available:
        # Do not include callable repr, credential values, or binding state.
        raise ProductionProviderConfigurationError(f"provider credential unavailable: {provider}")

    def _complete(request: ProviderAttemptRequest) -> Any:
        return binding.complete(production_provider_payload(request, stream=False))

    stream_fn: StreamCallable | None = None
    if binding.stream is not None:
        def _stream(request: ProviderAttemptRequest) -> Any:
            assert binding.stream is not None
            return binding.stream(production_provider_payload(request, stream=True))

        stream_fn = _stream

    adapter_id = str(binding.adapter_id or "").strip() or default_adapter_id
    return CallableProviderAdapter(
        adapter_id,
        complete_fn=_complete,
        stream_fn=stream_fn,
        run_sync_in_thread=binding.run_sync_in_thread,
    )


def build_production_provider_registry(
    bindings: Iterable[ProductionProviderBinding],
) -> ProviderAdapterRegistry:
    """Build an explicit registry; missing/unknown providers fail closed."""

    resolved = tuple(bindings)
    if not resolved:
        raise ProductionProviderConfigurationError("no production provider bindings configured")
    return ProviderAdapterRegistry(_build_adapter(binding) for binding in resolved)


def _default_credential_resolver(provider: str) -> str | None:
    """Resolve a secret behind the adapter without mutating process env."""

    normalized = (
        "openai"
        if str(provider).strip().lower() == "openai_compatible"
        else str(provider or "").strip().lower()
    )
    try:
        from host_brain.app import config as host_config  # noqa: PLC0415
        from host_brain.core.key_profiles import resolve_secret_from_profiles  # noqa: PLC0415

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
        parsed = urlparse(str(_default_base_url_resolver(normalized) or ""))
        if (parsed.hostname or "").strip().lower() in {"localhost", "127.0.0.1", "::1"}:
            return "host-local-placeholder-key"
    return None


def _default_base_url_resolver(provider: str) -> str | None:
    from host_brain.integrations.llm_helpers import _resolve_provider_base_url  # noqa: PLC0415, SLF001

    normalized = "openai" if str(provider).strip().lower() == "openai_compatible" else provider
    return _resolve_provider_base_url(normalized)


def _default_single_attempt_transport(**kwargs: Any) -> str:
    from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: PLC0415

    return invoke_llm_text(**kwargs)


class VaultBackedSingleAttemptAdapter:
    """Resolve a credential at call time and perform exactly one transport call.

    No credential is stored in ``ModelAttemptRequest``, ``ProviderTarget`` or
    audit metadata.  The adapter intentionally has no retry or fallback loop.
    """

    def __init__(
        self,
        provider: str,
        *,
        credential_resolver: CredentialResolver = _default_credential_resolver,
        base_url_resolver: BaseUrlResolver = _default_base_url_resolver,
        transport: SingleAttemptTransport = _default_single_attempt_transport,
    ) -> None:
        self._provider = str(provider or "").strip().lower()
        self._adapter_id = production_adapter_id(self._provider)
        if not callable(credential_resolver) or not callable(base_url_resolver) or not callable(transport):
            raise TypeError("vault-backed provider dependencies must be callable")
        self._credential_resolver = credential_resolver
        self._base_url_resolver = base_url_resolver
        self._transport = transport

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse:
        credential = str(self._credential_resolver(self._provider) or "").strip()
        if not credential and self._provider != "codex":
            raise ProductionProviderConfigurationError(
                f"provider credential unavailable: {self._provider}"
            )
        provider_name = "openai" if self._provider == "openai_compatible" else self._provider
        content = await asyncio.to_thread(
            self._transport,
            provider_name=provider_name,
            model_id=request.target.model_id,
            api_key=credential,
            timeout_ms=request.timeout_ms,
            messages=request.prompt.as_message_dicts(),
            gen_params=_generation_parameters(request),
            response_format=request.prompt.output_contract.response_format(),
            base_url_override=self._base_url_resolver(self._provider),
            feature_key=f"model_attempt:{request.scene_type.lower()}",
        )
        return ProviderResponse(content=str(content or ""))

    def stream(self, request: ProviderAttemptRequest):
        return self._stream(request)

    async def _stream(self, request: ProviderAttemptRequest):
        response = await self.complete(request)
        if response.content:
            yield response.content


def build_vault_backed_provider_registry(
    providers: Iterable[str] = tuple(sorted(SUPPORTED_PRODUCTION_PROVIDERS)),
    *,
    credential_resolver: CredentialResolver = _default_credential_resolver,
    base_url_resolver: BaseUrlResolver = _default_base_url_resolver,
    transport: SingleAttemptTransport = _default_single_attempt_transport,
) -> ProviderAdapterRegistry:
    resolved = tuple(dict.fromkeys(str(item or "").strip().lower() for item in providers))
    if not resolved or any(not item for item in resolved):
        raise ProductionProviderConfigurationError("production provider set must not be empty")
    return ProviderAdapterRegistry(
        VaultBackedSingleAttemptAdapter(
            provider,
            credential_resolver=credential_resolver,
            base_url_resolver=base_url_resolver,
            transport=transport,
        )
        for provider in resolved
    )


__all__ = [
    "BaseUrlResolver",
    "CredentialResolver",
    "ProductionProviderBinding",
    "ProductionProviderConfigurationError",
    "SingleAttemptTransport",
    "SUPPORTED_PRODUCTION_PROVIDERS",
    "VaultBackedSingleAttemptAdapter",
    "build_production_provider_registry",
    "build_vault_backed_provider_registry",
    "production_adapter_id",
    "production_provider_payload",
]
