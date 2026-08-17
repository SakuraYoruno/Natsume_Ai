"""只执行一次调用的生产 provider 传输适配器。

凭据和 HTTP 客户端由注入 callable 所有。本模块只验证经过认证的 callable 被有意绑定，
并将规范 attempt 转换为 provider 中立的聊天负载。
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
    StreamResultCallable,
)
from host_brain.model_runtime.provider_cost_policy import enrich_provider_usage


SUPPORTED_PRODUCTION_PROVIDERS = frozenset(
    {"openai", "openai_compatible", "gemini", "deepseek", "codex", "minimax"}
)
_GENERATION_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "stop",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "thinking_mode",
    }
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
    """预认证传输 callable 的无秘密声明。"""

    provider: str
    complete: ProviderPayloadCallable
    stream: ProviderPayloadCallable | None = None
    stream_with_result: ProviderPayloadCallable | None = None
    credential_available: bool = False
    adapter_id: str | None = None
    run_sync_in_thread: bool = False


def production_adapter_id(provider: str) -> str:
    # 适配器 ID 只能由受支持 provider 集派生，禁止将任意名称注册进生产 registry，
    # 以免未审查的传输实现被路由策略选择。
    normalized = str(provider or "").strip().lower()
    if normalized not in SUPPORTED_PRODUCTION_PROVIDERS:
        raise ProductionProviderConfigurationError(f"unsupported production provider: {normalized or '<empty>'}")
    return f"production.{normalized}"


def _generation_parameters(request: ProviderAttemptRequest) -> dict[str, Any]:
    # 路由层传来的参数仍需在传输前复核白名单；adapter 不信任可变 metadata，也不会
    # 让 provider 私有字段绕过模型预算的输出上限。
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
    if "thinking_mode" in params:
        thinking_mode = str(params["thinking_mode"] or "").strip().lower()
        if thinking_mode not in {"enabled", "disabled"}:
            raise ProductionProviderConfigurationError(
                "thinking_mode must be enabled or disabled"
            )
        params["thinking_mode"] = thinking_mode
    if (
        str(request.target.provider or "").strip().lower() == "deepseek"
        and str(request.target.model_id or "").strip().lower().startswith("deepseek-v4-flash")
    ):
        # 对不支持该能力组合的快速模型强制关闭 thinking，部署配置不能借此突破实时
        # 路线的延迟/费用约束。
        params["thinking_mode"] = "disabled"
    guard = request.model_budget_guard
    if guard is None:
        return params
    configured = _positive_int(params.get("max_tokens"))
    # 预算 guard 是硬上限：配置可以更小，绝不能把 max_tokens 提到 guard 之上。
    params["max_tokens"] = (
        min(configured, guard.max_output_tokens)
        if configured is not None
        else guard.max_output_tokens
    )
    return params


def _transport_generation_parameters(request: ProviderAttemptRequest) -> dict[str, Any]:
    """将硬上限保留在第一方传输 adapter 内部。"""

    # 这是第一方 transport 的内部参数形状；provider-neutral 提示必须先映射/移除，
    # 不能作为未知字段泄漏给第三方 SDK。
    params = _generation_parameters(request)
    provider = str(request.target.provider or "").strip().lower()
    model_id = str(request.target.model_id or "").strip().lower()
    thinking_mode = params.get("thinking_mode")
    if provider != "deepseek":
        # ``thinking_mode`` 是 provider 中立的路由元数据，绝不能作为不受支持的请求字段
        # 泄露给其他 provider。
        params.pop("thinking_mode", None)
    if provider == "gemini" and model_id.startswith("gemini-3.6-flash"):
        # Gemini 3.6 用有界 thinking level 替换了二元提示；``minimal`` 是其支持的
        # 等价设置，可满足实时路由的禁用推理延迟契约。
        params.pop("temperature", None)
        params.pop("top_p", None)
        params.pop("top_k", None)
        if thinking_mode == "disabled":
            params["thinking_level"] = "minimal"
        elif thinking_mode == "enabled":
            params["thinking_level"] = "medium"
    guard = request.model_budget_guard
    if guard is not None:
        # 内部硬帽与公开 payload 分离，方便 transport 在流式/SDK 适配中再次执行预算
        # 约束，而不要求外部 provider 理解 Natsume 私有字段。
        params["_natsume_hard_max_output_tokens"] = guard.max_output_tokens
    return params


def _provider_payload_generation_parameters(
    request: ProviderAttemptRequest,
) -> dict[str, Any]:
    """将类型化路由提示映射为注入 provider 的公开负载。"""

    params = _generation_parameters(request)
    thinking_mode = params.pop("thinking_mode", None)
    provider = str(request.target.provider or "").strip().lower()
    model_id = str(request.target.model_id or "").strip().lower()
    if thinking_mode is not None and provider == "deepseek":
        params["thinking"] = {"type": thinking_mode}
    elif provider == "gemini" and model_id.startswith("gemini-3.6-flash"):
        params.pop("temperature", None)
        params.pop("top_p", None)
        params.pop("top_k", None)
        if thinking_mode == "disabled":
            params["thinking_level"] = "minimal"
        elif thinking_mode == "enabled":
            params["thinking_level"] = "medium"
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
    require_final_usage: bool = False,
) -> dict[str, Any]:
    """为一次注入传输调用创建不含凭据的负载。"""

    # payload 有意不包含 api_key、base URL、adapter ID 或路由内部元数据；敏感信息仅在
    # vault-backed adapter 调用被注入的 transport 时短暂取得。
    payload: dict[str, Any] = {
        "model": request.target.model_id,
        "messages": request.prompt.as_message_dicts(),
        "stream": bool(stream),
        "request_id": request.request_id,
    }
    response_format = request.prompt.output_contract.response_format()
    if response_format is not None:
        payload["response_format"] = response_format
    payload.update(_provider_payload_generation_parameters(request))
    if require_final_usage:
        # 受治理流式调用必须要求 provider 给出终态用量帧；这是能力请求，不是本地
        # 猜测 token/费用，缺少该帧的 provider 不可进入此路径。
        # 这是显式传输能力请求而非 token/成本估算；binding 仍必须返回含 provider 所属
        # 用量维度的终态响应，才能满足 Task 预算。
        payload["stream_options"] = {"include_usage": True}
        payload["require_final_usage"] = True
    return payload


def _build_adapter(binding: ProductionProviderBinding) -> CallableProviderAdapter:
    provider = str(binding.provider or "").strip().lower()
    default_adapter_id = production_adapter_id(provider)
    if not callable(binding.complete):
        raise ProductionProviderConfigurationError(f"provider {provider} has no complete callable")
    if binding.stream is not None and not callable(binding.stream):
        raise ProductionProviderConfigurationError(f"provider {provider} has invalid stream callable")
    if binding.stream_with_result is not None and not callable(binding.stream_with_result):
        raise ProductionProviderConfigurationError(
            f"provider {provider} has invalid stream_with_result callable"
        )
    if not binding.credential_available:
        # 构造阶段 fail-closed；错误信息刻意不包含 callable 表示、绑定状态或凭据值，
        # 防止配置异常经日志泄露秘密。
        # 错误中不得包含 callable repr、凭据值或 binding 状态。
        raise ProductionProviderConfigurationError(f"provider credential unavailable: {provider}")

    def _complete(request: ProviderAttemptRequest) -> Any:
        return binding.complete(production_provider_payload(request, stream=False))

    stream_fn: StreamCallable | None = None
    if binding.stream is not None:
        def _stream(request: ProviderAttemptRequest) -> Any:
            assert binding.stream is not None
            return binding.stream(production_provider_payload(request, stream=True))

        stream_fn = _stream

    stream_result_fn: StreamResultCallable | None = None
    if binding.stream_with_result is not None:
        def _stream_with_result(request: ProviderAttemptRequest) -> Any:
            assert binding.stream_with_result is not None
            return binding.stream_with_result(
                production_provider_payload(
                    request,
                    stream=True,
                    require_final_usage=True,
                )
            )

        stream_result_fn = _stream_with_result

    adapter_id = str(binding.adapter_id or "").strip() or default_adapter_id
    return CallableProviderAdapter(
        adapter_id,
        complete_fn=_complete,
        stream_fn=stream_fn,
        stream_result_fn=stream_result_fn,
        run_sync_in_thread=binding.run_sync_in_thread,
    )


def build_production_provider_registry(
    bindings: Iterable[ProductionProviderBinding],
) -> ProviderAdapterRegistry:
    """构建显式 registry；缺失或未知 provider 时失败关闭。"""

    # registry 仅接受显式绑定；空集不提供隐式网络默认值，使部署遗漏在组合期暴露。
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
    def supports_canonical_stream_result(self) -> bool:
        """Expose only transports with a real final provider usage frame."""

        return self._provider in {"openai", "openai_compatible", "deepseek"}

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse:
        # 凭据按单次调用解析，不进入 request、target 或审计元数据；Codex CLI 在受治理
        # 情况下也被拒绝，因为其路径无法证明硬输出上限。
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
        # 仅记录端点规范化摘要和保守外网标记，保留网络审计关联而不写入原始 URL。
        transport_identity_digest, transport_external = _transport_identity(
            provider=provider_name,
            base_url=base_url_override,
        )
        started_ns = time.monotonic_ns()
        # 一个 adapter 调用只进行一次传输；重试/回退属于上层 ModelAttemptService，
        # 避免 provider 适配器暗中扩大预算或产生不可见尝试。
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
            # provider 使用量经统一估价策略补充，原始 metadata 先复制再叠加脱敏传输
            # 证据，避免 transport 返回对象被原地修改。
            usage = raw_response.get("usage")
            measured_usage, cost_policy_digest = enrich_provider_usage(
                provider=provider_name,
                model_id=request.target.model_id,
                base_url=base_url_override,
                usage=usage if isinstance(usage, Mapping) else None,
                tool_calls_enabled=False,
            )
            metadata = raw_response.get("metadata")
            transport_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            transport_metadata.update(
                {
                    "provider_latency_ms": int(elapsed_ms),
                    "transport_identity_digest": transport_identity_digest,
                    "transport_external": transport_external,
                }
            )
            if cost_policy_digest is not None:
                transport_metadata["model_cost_policy_digest"] = cost_policy_digest
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
                usage=measured_usage,
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

    def stream_with_result(self, request: ProviderAttemptRequest):
        return self._stream_with_result(request)

    async def _stream(self, request: ProviderAttemptRequest):
        """Use the real provider stream for unguarded live Surface calls.

        ``ModelAttemptService`` keeps guarded calls on ``complete`` until the
        provider stream protocol carries a final canonical usage record.  This
        adapter therefore never weakens a budget guard; it only removes the
        old accidental buffering for ordinary production text streams.
        """

        # 普通实时流仅用于未受 guard 约束的 Surface；受治理调用由服务层保留在 complete
        # 或有终态 usage 的 stream_with_result 路径，不能以低延迟牺牲结算证据。
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

    async def _stream_with_result(self, request: ProviderAttemptRequest):
        """Forward a provider-final usage record without deriving a cost.

        This is the production counterpart to the injected binding protocol.
        It is deliberately unavailable for providers whose native stream has
        no canonical terminal usage frame, so governed Chat keeps its safe
        buffered behaviour instead of accepting an inferred budget charge.
        """

        # 只有明确声明支持规范终态用量帧的 provider 才可进入；否则宁可拒绝并走安全
        # 缓冲路径，也不根据增量文本估算成本。
        if not self.supports_canonical_stream_result:
            raise ProductionProviderConfigurationError(
                "provider does not support canonical final usage for governed streaming"
            )
        credential = str(self._credential_resolver(self._provider) or "").strip()
        if not credential:
            raise ProductionProviderConfigurationError(
                f"provider credential unavailable: {self._provider}"
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
            kind = str(event.get("kind") or "").strip().upper()
            if kind == ProviderStreamEventKind.DELTA.value:
                text = str(event.get("text") or "")
                if text:
                    yield ProviderStreamEvent.delta(text)
                continue
            if kind != ProviderStreamEventKind.FINAL.value:
                # 流协议除了 delta/final 不接受其他帧，避免 provider 私有事件被误解释为
                # 完成或在没有用量收据时提前释放预算。
                raise ProductionProviderConfigurationError(
                    "provider stream emitted an unsupported final-usage frame"
                )
            elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
            usage = event.get("usage")
            measured_usage, cost_policy_digest = enrich_provider_usage(
                provider=provider_name,
                model_id=request.target.model_id,
                base_url=base_url_override,
                usage=usage if isinstance(usage, Mapping) else None,
                tool_calls_enabled=False,
            )
            metadata = event.get("metadata")
            transport_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            transport_metadata.update(
                {
                    "provider_latency_ms": int(elapsed_ms),
                    "transport_identity_digest": transport_identity_digest,
                    "transport_external": transport_external,
                }
            )
            if cost_policy_digest is not None:
                transport_metadata["model_cost_policy_digest"] = cost_policy_digest
            yield ProviderStreamEvent.final(
                ProviderResponse(
                    content=str(event.get("content") or ""),
                    finish_reason=(
                        str(event.get("finish_reason"))
                        if event.get("finish_reason") is not None
                        else None
                    ),
                    usage=measured_usage,
                    metadata=transport_metadata,
                )
            )


def _transport_identity(*, provider: str, base_url: str | None) -> tuple[str, bool]:
    """Return a secret-free digest and conservative external-network decision.

    The raw endpoint never enters ModelAttempt metadata.  Missing well-known
    provider URLs are resolved to their public defaults; Codex CLI and unknown
    endpoints remain non-external.  A hostname is not DNS-resolved here, so
    runtime network policy remains a separate defence.
    """

    # URL 被拆成规范字段后哈希；端口/路径变化会反映为新身份，原文永远不写入模型
    # 尝试记录，避免 endpoint 中潜在的敏感查询/用户名泄露。
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
    # 外网判断保守且不做 DNS：本地后缀、私网 IP 和非 HTTPS 都视为非外网；DNS 解析
    # 与实际 egress 拦截仍由运行时网络策略负责。
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
    # 去重后为每个部署允许的 provider 创建一个延迟取凭据的 adapter；不在 registry
    # 构造时读取 secret，避免启动日志或长生命周期对象保留凭据。
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
