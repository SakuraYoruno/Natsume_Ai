"""Single-call production provider transport adapters.

Credentials and HTTP clients are owned by the injected callables.  This
module only validates that an authenticated callable was deliberately bound
and converts the canonical attempt into a provider-neutral chat payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from host_brain.model_runtime.contracts import (
    ProviderAttemptRequest,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamEventKind,
)
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
SingleAttemptTransport = Callable[..., str | Mapping[str, Any]]

_DEFAULT_PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "deepseek": "https://api.deepseek.com/v1",
    "minimax": "https://api.minimaxi.com/v1",
}


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
    if raw is not None and not isinstance(raw, Mapping):
        raise ProductionProviderConfigurationError("generation_parameters must be a mapping")
    source = dict(raw or {})
    unknown = set(source) - _GENERATION_KEYS
    if unknown:
        raise ProductionProviderConfigurationError(
            "unsupported generation parameters: " + ", ".join(sorted(str(item) for item in unknown))
        )
    params = {str(key): value for key, value in source.items()}
    guard = request.model_budget_guard
    if guard is None:
        return params
    configured = _positive_int(params.get("max_tokens"))
    params["max_tokens"] = (
        min(configured, guard.max_output_tokens)
        if configured is not None
        else guard.max_output_tokens
    )
    return params


def _transport_generation_parameters(request: ProviderAttemptRequest) -> dict[str, Any]:
    """Keep the hard cap internal to the first-party transport adapter."""

    params = _generation_parameters(request)
    guard = request.model_budget_guard
    if guard is not None:
        params["_natsume_hard_max_output_tokens"] = guard.max_output_tokens
    return params


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


class VaultBackedSingleAttemptAdapter:
    """Resolve a credential at call time and perform exactly one transport call.

    No credential is stored in ``ModelAttemptRequest``, ``ProviderTarget`` or
    audit metadata.  The adapter intentionally has no retry or fallback loop.
    """

    def __init__(
        self,
        provider: str,
        *,
        credential_resolver: CredentialResolver,
        base_url_resolver: BaseUrlResolver,
        transport: SingleAttemptTransport,
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

    @property
    def supports_final_stream_usage(self) -> bool:
        """Only transports with a documented final usage SSE frame qualify."""

        return self._provider in {"openai", "openai_compatible", "deepseek"}

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse:
        credential = str(self._credential_resolver(self._provider) or "").strip()
        if not credential and self._provider != "codex":
            raise ProductionProviderConfigurationError(
                f"provider credential unavailable: {self._provider}"
            )
        if request.model_budget_guard is not None and self._provider == "codex" and not credential:
            raise ProductionProviderConfigurationError(
                "guarded Codex CLI calls do not expose a hard output cap"
            )
        provider_name = "openai" if self._provider == "openai_compatible" else self._provider
        base_url_override = self._base_url_resolver(self._provider)
        transport_identity_digest, transport_external = _transport_identity(
            provider=provider_name,
            base_url=base_url_override,
        )
        started_ns = time.monotonic_ns()
        raw_response = await asyncio.to_thread(
            self._transport,
            provider_name=provider_name,
            model_id=request.target.model_id,
            api_key=credential,
            timeout_ms=request.timeout_ms,
            messages=request.prompt.as_message_dicts(),
            gen_params=_transport_generation_parameters(request),
            response_format=request.prompt.output_contract.response_format(),
            base_url_override=base_url_override,
            feature_key=f"model_attempt:{request.scene_type.lower()}",
        )
        elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
        if isinstance(raw_response, Mapping):
            usage = raw_response.get("usage")
            metadata = raw_response.get("metadata")
            transport_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            transport_metadata.update(
                {
                    "provider_latency_ms": int(elapsed_ms),
                    "transport_identity_digest": transport_identity_digest,
                    "transport_external": transport_external,
                }
            )
            return ProviderResponse(
                content=str(
                    raw_response.get("content")
                    or raw_response.get("text")
                    or raw_response.get("output")
                    or ""
                ),
                finish_reason=(
                    str(raw_response.get("finish_reason"))
                    if raw_response.get("finish_reason") is not None
                    else None
                ),
                usage=dict(usage) if isinstance(usage, Mapping) else {},
                metadata=transport_metadata,
            )
        return ProviderResponse(
            content=str(raw_response or ""),
            metadata={
                "provider_latency_ms": int(elapsed_ms),
                "transport_identity_digest": transport_identity_digest,
                "transport_external": transport_external,
            },
        )

    def stream(self, request: ProviderAttemptRequest):
        return self._stream(request)

    def stream_events(self, request: ProviderAttemptRequest):
        return self._stream_events(request)

    async def _stream(self, request: ProviderAttemptRequest):
        """Use the real provider stream for unguarded live Surface calls.

        ``ModelAttemptService`` keeps guarded calls on ``complete`` until the
        provider stream protocol carries a final canonical usage record.  This
        adapter therefore never weakens a budget guard; it only removes the
        old accidental buffering for ordinary production text streams.
        """

        credential = str(self._credential_resolver(self._provider) or "").strip()
        if not credential and self._provider != "codex":
            raise ProductionProviderConfigurationError(
                f"provider credential unavailable: {self._provider}"
            )
        if request.model_budget_guard is not None and self._provider == "codex" and not credential:
            raise ProductionProviderConfigurationError(
                "guarded Codex CLI calls do not expose a hard output cap"
            )
        from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415

        provider_name = "openai" if self._provider == "openai_compatible" else self._provider
        base_url_override = self._base_url_resolver(self._provider)
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=request.target.model_id,
            api_key=credential,
            timeout_ms=request.timeout_ms,
            messages=request.prompt.as_message_dicts(),
            gen_params=_transport_generation_parameters(request),
            base_url_override=base_url_override,
        ):
            text = str(chunk or "")
            if text:
                yield text

    async def _stream_events(self, request: ProviderAttemptRequest):
        """Expose provider-final stream usage for guarded model attempts.

        Only the OpenAI-compatible transport helper currently has a protocol
        for a final usage frame.  Unsupported providers deliberately raise;
        the model authority converts that to a rejected, unmetered attempt
        instead of estimating tokens or cost from text.
        """

        credential = str(self._credential_resolver(self._provider) or "").strip()
        if not credential and self._provider != "codex":
            raise ProductionProviderConfigurationError(
                f"provider credential unavailable: {self._provider}"
            )
        if request.model_budget_guard is not None and self._provider == "codex" and not credential:
            raise ProductionProviderConfigurationError(
                "guarded Codex CLI calls do not expose a hard output cap"
            )
        from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
            astream_llm_stream_events,
        )

        provider_name = "openai" if self._provider == "openai_compatible" else self._provider
        base_url_override = self._base_url_resolver(self._provider)
        transport_identity_digest, transport_external = _transport_identity(
            provider=provider_name,
            base_url=base_url_override,
        )
        started_ns = time.monotonic_ns()
        async for raw_event in astream_llm_stream_events(
            provider_name=provider_name,
            model_id=request.target.model_id,
            api_key=credential,
            timeout_ms=request.timeout_ms,
            messages=request.prompt.as_message_dicts(),
            gen_params=_transport_generation_parameters(request),
            base_url_override=base_url_override,
        ):
            event = dict(raw_event) if isinstance(raw_event, Mapping) else {}
            kind = str(event.get("kind") or "").strip().lower()
            if kind == "delta":
                text = str(event.get("text") or "")
                if text:
                    yield ProviderStreamEvent(
                        kind=ProviderStreamEventKind.DELTA,
                        text=text,
                    )
                continue
            if kind != "final":
                raise ProductionProviderConfigurationError(
                    "provider stream emitted an unsupported final-usage frame"
                )
            elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
            usage = event.get("usage")
            metadata = event.get("metadata")
            transport_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            transport_metadata.update(
                {
                    "provider_latency_ms": int(elapsed_ms),
                    "transport_identity_digest": transport_identity_digest,
                    "transport_external": transport_external,
                }
            )
            yield ProviderStreamEvent(
                kind=ProviderStreamEventKind.FINAL,
                response=ProviderResponse(
                    content=str(event.get("content") or ""),
                    finish_reason=(
                        str(event.get("finish_reason"))
                        if event.get("finish_reason") is not None
                        else None
                    ),
                    usage=dict(usage) if isinstance(usage, Mapping) else {},
                    metadata=transport_metadata,
                ),
            )


def _transport_identity(*, provider: str, base_url: str | None) -> tuple[str, bool]:
    """Return a secret-free digest and conservative external-network decision.

    The raw endpoint never enters ModelAttempt metadata.  Missing well-known
    provider URLs are resolved to their public defaults; Codex CLI and unknown
    endpoints remain non-external.  A hostname is not DNS-resolved here, so
    runtime network policy remains a separate defence.
    """

    normalized_provider = str(provider or "").strip().lower()
    raw_url = str(base_url or "").strip()
    if not raw_url:
        raw_url = _DEFAULT_PROVIDER_BASE_URLS.get(normalized_provider, "")
    scheme = ""
    hostname = ""
    port: int | None = None
    path = ""
    try:
        parsed = urlsplit(raw_url)
        scheme = str(parsed.scheme or "").strip().lower()
        hostname = str(parsed.hostname or "").strip().rstrip(".").lower()
        port = parsed.port
        path = "/" + "/".join(
            segment for segment in str(parsed.path or "").split("/") if segment
        )
    except (TypeError, ValueError):
        scheme = ""
        hostname = ""
        port = None
        path = ""
    canonical = {
        "schema": "natsume.model-provider-transport-identity.v1",
        "provider": normalized_provider,
        "scheme": scheme,
        "hostname": hostname,
        "port": port,
        "path": path,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return digest, _is_external_https_host(scheme=scheme, hostname=hostname)


def _is_external_https_host(*, scheme: str, hostname: str) -> bool:
    if scheme != "https" or not hostname:
        return False
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".lan")
        or hostname.endswith(".home.arpa")
        or "." not in hostname
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return bool(address.is_global)


def build_vault_backed_provider_registry(
    providers: Iterable[str] = tuple(sorted(SUPPORTED_PRODUCTION_PROVIDERS)),
    *,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
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
