"""路由模型完成与流式尝试的唯一权威。

服务集中处理提示词渲染、路由、一次性 adapter 调用、语义重试、预算计量、审计与缓存；
任一调用方都不能自行重试、伪造用量或在没有审计收据的情况下把输出交给受治理任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import inspect
import json
import re
import threading
import time
import uuid
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from host_brain.model_runtime.contracts import (
    AttemptAuditRepository,
    AttemptStatus,
    CacheScope,
    ModelBudgetGuard,
    ModelAttemptRecord,
    ModelAttemptRequest,
    ModelAttemptResult,
    ModelAttemptStreamEvent,
    OutputContract,
    OutputKind,
    ProviderAttemptRequest,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamEventKind,
    ProviderTarget,
    RenderedPrompt,
    RetryDirective,
    RouteDecision,
    RoutePolicy,
    sha256_digest,
)
from host_brain.model_runtime.prompt_registry import PromptRegistry
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry


class ModelAttemptServiceError(RuntimeError):
    """带稳定、非秘密原因码的路由模型调用失败。"""

    def __init__(self, message: str, *, code: str = "MODEL_ATTEMPT_FAILED") -> None:
        super().__init__(message)
        self.code = str(code or "MODEL_ATTEMPT_FAILED").strip() or "MODEL_ATTEMPT_FAILED"


class AttemptAuditError(ModelAttemptServiceError):
    pass


class OutputContractViolation(ModelAttemptServiceError):
    def __init__(self, message: str, *, code: str = "OUTPUT_SCHEMA_INVALID") -> None:
        super().__init__(message)
        self.code = code


class ModelBudgetGuardViolation(ModelAttemptServiceError):
    """受 guard 的调用无法证明或遵守不可变限制，必须在接受前失败。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ModelStreamInterruptedError(ModelAttemptServiceError):
    pass


class ModelStreamProtocolError(ModelAttemptServiceError):
    """携带用量证明的 provider 流违反终态契约；增量不能替代最终收据。"""

    def __init__(self, message: str, *, code: str = "MODEL_STREAM_RESULT_INVALID") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    response: ProviderResponse
    target: ProviderTarget


@dataclass(frozen=True, slots=True)
class _PreparedCall:
    prompt: RenderedPrompt
    route: RouteDecision
    deadline_at: float
    cache_key: str | None


def _safe_error_message(exc: BaseException, *, limit: int = 1000) -> str:
    # 所有对外错误与审计记录经过同一脱敏截断；异常文本来自 provider/SDK，不能因
    # 调试信息携带 Bearer、密钥或连接参数而穿透持久日志。
    value = f"{type(exc).__name__}: {exc}".strip()
    for pattern in (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|authorization)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
    ):
        value = pattern.sub("[REDACTED]", value)
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _json_type_matches(value: Any, expected: str) -> bool:
    return {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(expected, True)


def _validate_json_schema_object(value: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    required = [str(item) for item in schema.get("required", [])]
    missing = [field for field in required if field not in value]
    if missing:
        raise OutputContractViolation("missing required output fields: " + ", ".join(sorted(missing)))
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise OutputContractViolation("output schema properties must be an object")
    for field, definition in properties.items():
        if field not in value or not isinstance(definition, Mapping):
            continue
        expected = str(definition.get("type") or "").strip()
        if expected and not _json_type_matches(value[field], expected):
            raise OutputContractViolation(f"output field {field} must be {expected}")
        allowed = definition.get("enum")
        if isinstance(allowed, Sequence) and not isinstance(allowed, (str, bytes)):
            if value[field] not in allowed:
                raise OutputContractViolation(f"output field {field} is not in its enum")
    if schema.get("additionalProperties") is False:
        extra = set(value) - {str(field) for field in properties}
        if extra:
            raise OutputContractViolation("unexpected output fields: " + ", ".join(sorted(extra)))


def validate_output_contract(content: str, contract: OutputContract) -> Any:
    """验证 provider 输出；结构化契约只接受可解析且符合 schema 的 JSON 对象。"""

    # 结构化输出在接受前必须完整解析并校验；服务不修补模型 JSON，避免“看似可用”
    # 的半结构化文本进入后续授权/执行链。
    text = str(content or "")
    if contract.kind is OutputKind.TEXT:
        if contract.strict and not text.strip():
            raise OutputContractViolation("provider returned empty text", code="EMPTY_OUTPUT")
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputContractViolation(f"provider returned invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise OutputContractViolation("provider JSON output must be an object")
    if contract.kind is OutputKind.JSON_SCHEMA:
        _validate_json_schema_object(parsed, dict(contract.schema or {}))
    return parsed


def _provider_budget_measurements(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """Normalize only provider-reported, unit-explicit budget dimensions."""

    # 只保留 provider 明确报告且带整数单位的维度，不从文本、延迟或价格表反推 token；
    # 缺失维度由受治理 guard 拒绝，而不是伪造零用量。
    if not isinstance(usage, Mapping) or not usage:
        return {}

    def _present_int(*keys: str) -> tuple[bool, int]:
        for key in keys:
            if key not in usage:
                continue
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return False, 0
            return True, int(value)
        return False, 0

    measured: dict[str, int] = {}
    has_total, total = _present_int("total_tokens", "totalTokenCount")
    has_prompt, prompt = _present_int(
        "prompt_tokens", "input_tokens", "promptTokenCount", "inputTokenCount"
    )
    has_completion, completion = _present_int(
        "completion_tokens",
        "output_tokens",
        "candidatesTokenCount",
        "outputTokenCount",
    )
    if has_prompt:
        measured["prompt_tokens"] = prompt
    if has_completion:
        measured["completion_tokens"] = completion
    if has_total:
        measured["token_count"] = total
    elif has_prompt and has_completion:
        measured["token_count"] = prompt + completion

    # Cost is accepted only in the canonical integer micro-unit.  Currency
    # floats and provider-specific price estimates are deliberately ignored.
    has_cost, cost = _present_int("model_cost_micros")
    if has_cost:
        measured["model_cost_micros"] = cost
    has_tool_calls, tool_calls = _present_int("tool_call_count")
    if has_tool_calls:
        measured["tool_call_count"] = tool_calls
    return measured


def _provider_transport_measurements(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded server-owned transport evidence in the audit trail."""

    # 审计仅接纳有限的服务端传输证据（延迟、摘要、外网标记）；原始 endpoint、headers
    # 及 provider 私有 metadata 均不进入长期尝试记录。
    if not isinstance(metadata, Mapping):
        return {}
    result: dict[str, Any] = {}
    value = metadata.get("provider_latency_ms")
    if not isinstance(value, bool) and isinstance(value, int) and value >= 0:
        result["provider_latency_ms"] = int(value)
    identity = str(metadata.get("transport_identity_digest") or "").strip().lower()
    if len(identity) == 64 and all(char in "0123456789abcdef" for char in identity):
        result["transport_identity_digest"] = identity
    external = metadata.get("transport_external")
    if isinstance(external, bool):
        result["transport_external"] = external
    cost_policy_digest = str(metadata.get("model_cost_policy_digest") or "").strip().lower()
    if len(cost_policy_digest) == 64 and all(
        char in "0123456789abcdef" for char in cost_policy_digest
    ):
        result["model_cost_policy_digest"] = cost_policy_digest
    return result


class ModelAttemptService:
    """一次解析路由后，独占全部 provider 尝试与语义重试。

    每次尝试先写 STARTED 审计记录，再由相同服务写终态记录；这样超时、取消、回退和
    schema 修复都可从持久化证据恢复，而不会由 adapter 内部隐藏。
    """

    def __init__(
        self,
        *,
        prompt_registry: PromptRegistry,
        route_policy: RoutePolicy,
        provider_adapters: ProviderAdapterRegistry,
        audit_repository: AttemptAuditRepository,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock_ms: Callable[[], int] | None = None,
        cache_max_entries: int = 512,
    ) -> None:
        # 服务依赖均从组合根注入：路由、适配器和审计缺一不可，调用方不能用局部函数
        # 替换某一环来旁路统一的尝试/收据语义。
        if not callable(getattr(route_policy, "resolve", None)):
            raise TypeError("route_policy must implement resolve()")
        if not callable(getattr(audit_repository, "record_attempt", None)):
            raise TypeError("audit_repository must implement record_attempt()")
        self.prompt_registry = prompt_registry
        self.route_policy = route_policy
        self.provider_adapters = provider_adapters
        self.audit_repository = audit_repository
        self._monotonic = monotonic
        self._wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
        self._cache_max_entries = max(1, int(cache_max_entries))
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = threading.RLock()

    async def complete(self, request: ModelAttemptRequest) -> ModelAttemptResult:
        """完成一个非流式模型请求，按冻结路由、deadline 和 guard 依次执行尝试。"""
        # 先在同一个 deadline 内固定 prompt、route 与缓存资格；后续所有 attempt 复用
        # 该决策，不能在失败后重新选模型或读取变化中的配置。
        prepared = await self._prepare(request)
        cached = self._get_cached(prepared.cache_key)
        if cached is not None:
            # 缓存响应没有本次 provider 测量；受 guard 调用在 _prepare 已禁用缓存，
            # 因此这里的命中不会被误用为 Task 可结算的新模型消耗。
            return ModelAttemptResult(
                request_id=request.request_id,
                success=True,
                route_decision=prepared.route,
                attempts=(),
                response=cached.response,
                final_target=cached.target,
                cached=True,
                metadata={"cache_scope": prepared.prompt.cache_policy.scope.value},
            )

        attempts: list[ModelAttemptRecord] = []
        # 普通请求不能改变路由拥有的 provider 预算；来自持久化 Task reservation 的 typed
        # ModelBudgetGuard 例外，但它只能收紧服务端选定的上限。
        # 路由尝试数和 node-bound guard 的尝试额度取更小值；预算只能收紧路线，
        # 绝不能因多个 fallback target 获得额外 provider 调用机会。
        guard = request.model_budget_guard
        budget = min(
            prepared.route.max_attempts,
            (
                guard.max_provider_attempts
                if guard is not None
                else prepared.route.max_attempts
            ),
        )
        target_index = 0
        retry_reason = "initial"
        prompt = prepared.prompt
        last_error_code = "MODEL_ROUTE_EXHAUSTED"
        last_error_message = "model route exhausted without a successful attempt"
        previsibility_transport_retries = int(
            request.previsibility_transport_retries
        )
        availability_first = previsibility_transport_retries > 0

        while len(attempts) < budget and target_index < len(prepared.route.targets):
            remaining_ms = self._remaining_ms(prepared.deadline_at)
            if remaining_ms <= 0:
                # deadline 在发起下一次传输前耗尽时直接结束，避免把负余量转换为一个
                # 新的 provider 超时尝试并污染审计链。
                last_error_code = "MODEL_DEADLINE_EXCEEDED"
                last_error_message = "model request deadline exhausted"
                break
            target = prepared.route.targets[target_index]
            attempt_index = len(attempts) + 1
            attempt_request, started, started_monotonic = self._begin_attempt(
                request=request,
                route=prepared.route,
                prompt=prompt,
                target=target,
                attempt_index=attempt_index,
                retry_reason=retry_reason,
                remaining_ms=remaining_ms,
            )
            await self._audit(started)

            response: ProviderResponse | None = None
            try:
                adapter = self.provider_adapters.get(target.adapter_id)
                response = await asyncio.wait_for(
                    adapter.complete(attempt_request),
                    timeout=max(0.001, attempt_request.timeout_ms / 1000.0),
                )
                self._assert_model_budget_guard_response(
                    response,
                    guard,
                    prior_attempts=attempts,
                )
                # 先验证 provider 计量与输出契约，再询问语义重试器；不合格响应不能
                # 作为重试评估输入之外的成功结果被缓存或交付。
                validate_output_contract(response.content, prompt.output_contract)
                directive = await self._evaluate_retry(request, response, attempt_request)
                if directive is not None and directive.retry:
                    # 重试决定本身也被终态审计。可选 repair prompt 需回到 registry 验证，
                    # 防止 evaluator 返回未登记/被篡改的提示词直接跨模型边界。
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.REJECTED,
                        response=response,
                        error_code="SEMANTIC_RETRY_REQUESTED",
                        error_message=directive.reason,
                        metadata={"retry_directive": dict(directive.metadata)},
                    )
                    await self._audit(terminal)
                    attempts.append(terminal)
                    prompt = (
                        self.prompt_registry.validate_rendered(directive.rendered_prompt)
                        if directive.rendered_prompt is not None
                        else prompt
                    )
                    retry_reason = directive.reason
                    if not directive.reuse_target:
                        target_index += 1
                    last_error_code = "ATTEMPT_BUDGET_EXHAUSTED"
                    last_error_message = f"retry requested: {directive.reason}"
                    continue

                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.SUCCEEDED,
                    response=response,
                )
                await self._audit(terminal)
                attempts.append(terminal)
                self._put_cached(prepared.cache_key, prepared.prompt, response, target)
                return ModelAttemptResult(
                    request_id=request.request_id,
                    success=True,
                    route_decision=prepared.route,
                    attempts=tuple(attempts),
                    response=response,
                    final_target=target,
                )
            except asyncio.CancelledError as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.CANCELLED,
                    error_code="MODEL_ATTEMPT_CANCELLED",
                    error_message=_safe_error_message(exc),
                )
                await asyncio.shield(self._audit(terminal))
                raise
            except ModelBudgetGuardViolation as exc:
                # 无法证明完整用量或已超 cap 时 fail-closed；不得 fallback，因为后续成功
                # 无法补回未知前次消耗，Task 账本会失真。
                # Do not retry or fall back after an unmetered/over-cap call.
                # Its true charge cannot be reconstructed safely, so accepting
                # any later provider response would leave the Task ledger false.
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.REJECTED,
                    response=response,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                return ModelAttemptResult(
                    request_id=request.request_id,
                    success=False,
                    route_decision=prepared.route,
                    attempts=tuple(attempts),
                    error_code=exc.code,
                    error_message=str(exc),
                )
            except OutputContractViolation as exc:
                # schema 修复只在尚有同一冻结路由预算时进行，原始失败响应被审计，
                # 以便恢复/诊断而不把两次输出拼接给调用方。
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.REJECTED,
                    response=response,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                last_error_code = exc.code
                last_error_message = str(exc)
                if len(attempts) < budget:
                    prompt = self._schema_repair_prompt(prompt, response, exc)
                    retry_reason = "output_schema_repair"
                    continue
                break
            except AttemptAuditError:
                raise
            except TimeoutError as exc:
                # 受 guard 调用若已经向 provider 发出请求却没有终态用量，就不能确认
                # 是否发生计费；除预可见性探测外必须停止，而非把它当作普通可回退超时。
                timeout_status = (
                    AttemptStatus.TIMED_OUT
                    if availability_first or guard is None
                    else AttemptStatus.REJECTED
                )
                timeout_error_code = (
                    "PROVIDER_TIMEOUT"
                    if availability_first or guard is None
                    else "MODEL_PROVIDER_USAGE_UNPROVEN"
                )
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=timeout_status,
                    error_code=timeout_error_code,
                    error_message=_safe_error_message(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                if guard is not None and not availability_first:
                    return ModelAttemptResult(
                        request_id=request.request_id,
                        success=False,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        error_code="MODEL_PROVIDER_USAGE_UNPROVEN",
                        error_message="guarded provider call timed out without canonical usage",
                    )
                last_error_code = "PROVIDER_TIMEOUT"
                last_error_message = "provider attempt timed out"
                if (
                    previsibility_transport_retries > 0
                    and len(attempts) < budget
                ):
                    previsibility_transport_retries -= 1
                    retry_reason = "previsibility_transport_retry:provider_timeout"
                    continue
            except Exception as exc:
                # 同样地，受治理的传输异常在未知用量下终止；未受 guard 或预可见性阶段
                # 才允许记录失败后继续按冻结 route 尝试下一目标。
                provider_error_code = str(
                    getattr(exc, "code", type(exc).__name__.upper())
                ).strip() or "PROVIDER_ERROR"
                provider_failure_status = (
                    AttemptStatus.FAILED
                    if availability_first or guard is None
                    else AttemptStatus.REJECTED
                )
                audited_error_code = (
                    provider_error_code
                    if availability_first or guard is None
                    else "MODEL_PROVIDER_USAGE_UNPROVEN"
                )
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=provider_failure_status,
                    error_code=audited_error_code,
                    error_message=(
                        _safe_error_message(exc)
                        if availability_first or guard is None
                        else "guarded provider call failed without canonical usage"
                    ),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                if guard is not None and not availability_first:
                    return ModelAttemptResult(
                        request_id=request.request_id,
                        success=False,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        error_code="MODEL_PROVIDER_USAGE_UNPROVEN",
                        error_message="guarded provider call failed without canonical usage",
                    )
                last_error_code = provider_error_code
                last_error_message = terminal.error_message or "provider attempt failed"
                if (
                    previsibility_transport_retries > 0
                    and len(attempts) < budget
                ):
                    previsibility_transport_retries -= 1
                    retry_reason = (
                        "previsibility_transport_retry:"
                        f"{last_error_code.lower()}"
                    )
                    continue

            target_index += 1
            retry_reason = f"provider_fallback:{last_error_code.lower()}"

        return ModelAttemptResult(
            request_id=request.request_id,
            success=False,
            route_decision=prepared.route,
            attempts=tuple(attempts),
            error_code=last_error_code,
            error_message=last_error_message,
        )

    def complete_sync(self, request: ModelAttemptRequest) -> ModelAttemptResult:
        """Synchronous compatibility port for worker-only call sites."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.complete(request))
        raise RuntimeError("complete_sync cannot run inside an active event loop; await complete()")

    async def stream(self, request: ModelAttemptRequest) -> AsyncIterator[str]:
        """Stream through the same route, budget and attempt audit authority.

        Structured output and requests with semantic evaluators are buffered
        until validation succeeds so a retry never concatenates two providers'
        output. Plain text is emitted immediately; once emitted, route
        commitment forbids fallback on interruption.
        """

        # 普通 stream 没有要求终态用量收据，故受治理调用必须改用 stream_with_result；
        # 不能把已发出的字符当成可结算模型结果。
        if request.model_budget_guard is not None:
            # Streaming adapters expose text chunks but not a final canonical
            # usage record, so they cannot satisfy a durable budget guard.
            raise ModelBudgetGuardViolation(
                "guarded model calls require complete usage accounting",
                code="MODEL_BUDGET_STREAM_UNSUPPORTED",
            )
        prepared = await self._prepare(request)
        cached = self._get_cached(prepared.cache_key)
        if cached is not None:
            if cached.response.content:
                yield cached.response.content
            return

        budget = prepared.route.max_attempts
        target_index = 0
        attempt_count = 0
        retry_reason = "initial"
        prompt = prepared.prompt
        # 结构化或可能语义重试的流先缓冲，确保修复不会让用户看到两个 provider 的
        # 拼接结果；纯文本一旦发出则提交该 route，后续失败禁止 fallback。
        buffered = prompt.output_contract.kind is not OutputKind.TEXT or request.retry_evaluator is not None
        last_error: BaseException | None = None

        while attempt_count < budget and target_index < len(prepared.route.targets):
            remaining_ms = self._remaining_ms(prepared.deadline_at)
            if remaining_ms <= 0:
                last_error = TimeoutError("model request deadline exhausted")
                break
            target = prepared.route.targets[target_index]
            attempt_count += 1
            attempt_request, started, started_monotonic = self._begin_attempt(
                request=request,
                route=prepared.route,
                prompt=prompt,
                target=target,
                attempt_index=attempt_count,
                retry_reason=retry_reason,
                remaining_ms=remaining_ms,
            )
            await self._audit(started)
            chunks: list[str] = []
            emitted = False
            try:
                adapter = self.provider_adapters.get(target.adapter_id)
                async with asyncio.timeout(max(0.001, attempt_request.timeout_ms / 1000.0)):
                    async for raw_chunk in adapter.stream(attempt_request):
                        chunk = str(raw_chunk or "")
                        if not chunk:
                            continue
                        chunks.append(chunk)
                        if not buffered:
                            emitted = True
                            yield chunk
                response = ProviderResponse(content="".join(chunks), metadata={"streamed": True})
                validate_output_contract(response.content, prompt.output_contract)
                directive = await self._evaluate_retry(request, response, attempt_request)
                if directive is not None and directive.retry:
                    if emitted:
                        raise ModelStreamInterruptedError(
                            "semantic retry requested after stream route commitment"
                        )
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.REJECTED,
                        response=response,
                        error_code="SEMANTIC_RETRY_REQUESTED",
                        error_message=directive.reason,
                        metadata={"retry_directive": dict(directive.metadata)},
                    )
                    await self._audit(terminal)
                    prompt = (
                        self.prompt_registry.validate_rendered(directive.rendered_prompt)
                        if directive.rendered_prompt is not None
                        else prompt
                    )
                    retry_reason = directive.reason
                    if not directive.reuse_target:
                        target_index += 1
                    continue

                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.SUCCEEDED,
                    response=response,
                )
                await self._audit(terminal)
                self._put_cached(prepared.cache_key, prepared.prompt, response, target)
                if buffered:
                    for chunk in chunks:
                        yield chunk
                return
            except AttemptAuditError:
                raise
            except asyncio.CancelledError as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.CANCELLED,
                    error_code="MODEL_ATTEMPT_CANCELLED",
                    error_message=_safe_error_message(exc),
                )
                await asyncio.shield(self._audit(terminal))
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.CANCELLED,
                        error_code="MODEL_STREAM_CANCELLED",
                        error_message=_safe_error_message(exc),
                    )
                    await asyncio.shield(self._audit(terminal))
                    raise
                status = (
                    AttemptStatus.TIMED_OUT
                    if isinstance(exc, TimeoutError)
                    else AttemptStatus.REJECTED
                    if isinstance(exc, OutputContractViolation)
                    else AttemptStatus.FAILED
                )
                code = (
                    "PROVIDER_TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else getattr(exc, "code", type(exc).__name__.upper())
                )
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=status,
                    error_code=str(code),
                    error_message=_safe_error_message(exc),
                )
                await self._audit(terminal)
                last_error = exc
                if emitted:
                    # 已可见 delta 不能撤回，任何 fallback 都会破坏单次回复的来源与
                    # 顺序语义，因此转为明确的 stream interrupted 错误。
                    raise ModelStreamInterruptedError(
                        "provider stream failed after output was emitted; fallback is forbidden"
                    ) from exc
                if isinstance(exc, OutputContractViolation) and attempt_count < budget:
                    response = ProviderResponse(content="".join(chunks), metadata={"streamed": True})
                    prompt = self._schema_repair_prompt(prompt, response, exc)
                    retry_reason = "output_schema_repair"
                    continue
                target_index += 1
                retry_reason = f"provider_fallback:{str(code).lower()}"

        if last_error is None:
            raise ModelAttemptServiceError(
                "model stream exhausted without a successful attempt",
                code="MODEL_STREAM_EXHAUSTED",
            )
        raise ModelAttemptServiceError(
            _safe_error_message(last_error),
            code=str(getattr(last_error, "code", "MODEL_STREAM_FAILED")),
        ) from last_error

    async def stream_with_result(
        self,
        request: ModelAttemptRequest,
    ) -> AsyncIterator[ModelAttemptStreamEvent]:
        """Stream deltas and end with an audited provider-owned result.

        Unlike the compatibility ``stream`` method, this protocol requires a
        provider terminal response.  Its explicit measurements carry the same
        canonical usage that ``complete`` uses for a node-bound budget
        settlement; an unguarded direct call may still be audited but
        unmeasured when the provider omits a required field.  It does not
        estimate tokens or costs from text.  A caller may expose a
        delta only when it has its own durable visibility authority; this
        method intentionally does not pretend a model stream is accepted
        merely because its provider has started sending characters.
        """

        # 此协议的 final 事件才代表可接受结果：delta 只是可见性信号，成功/预算结算
        # 仍以 provider 的规范终态 response 与审计记录为准。
        prepared = await self._prepare(request)
        cached = self._get_cached(prepared.cache_key)
        if cached is not None:
            if cached.response.content:
                yield ModelAttemptStreamEvent.delta(cached.response.content)
            yield ModelAttemptStreamEvent.final(
                ModelAttemptResult(
                    request_id=request.request_id,
                    success=True,
                    route_decision=prepared.route,
                    attempts=(),
                    response=cached.response,
                    final_target=cached.target,
                    cached=True,
                    metadata={"cache_scope": prepared.prompt.cache_policy.scope.value},
                )
            )
            return

        attempts: list[ModelAttemptRecord] = []
        guard = request.model_budget_guard
        budget = min(
            prepared.route.max_attempts,
            guard.max_provider_attempts if guard is not None else prepared.route.max_attempts,
        )
        target_index = 0
        retry_reason = "initial"
        prompt = prepared.prompt
        last_error: BaseException | None = None
        last_error_code = "MODEL_STREAM_RESULT_UNSUPPORTED"
        last_error_message = "route has no provider stream with a final canonical response"

        while len(attempts) < budget and target_index < len(prepared.route.targets):
            remaining_ms = self._remaining_ms(prepared.deadline_at)
            if remaining_ms <= 0:
                last_error_code = "MODEL_DEADLINE_EXCEEDED"
                last_error_message = "model request deadline exhausted"
                break
            target = prepared.route.targets[target_index]
            adapter = self.provider_adapters.get(target.adapter_id)
            stream_with_result = getattr(adapter, "stream_with_result", None)
            capability = getattr(adapter, "supports_canonical_stream_result", None)
            supported = (
                bool(capability() if callable(capability) else capability)
                if capability is not None
                else callable(stream_with_result)
            )
            if not callable(stream_with_result) or not supported:
                # 没有终态 usage 能力的 target 可在未受治理场景使用普通流，但不能进入
                # 此带结果协议；跳过后尝试已冻结路由中的下一显式目标。
                last_error_code = "MODEL_STREAM_RESULT_UNSUPPORTED"
                last_error_message = "provider stream has no final canonical usage response"
                target_index += 1
                continue

            attempt_index = len(attempts) + 1
            attempt_request, started, started_monotonic = self._begin_attempt(
                request=request,
                route=prepared.route,
                prompt=prompt,
                target=target,
                attempt_index=attempt_index,
                retry_reason=retry_reason,
                remaining_ms=remaining_ms,
            )
            await self._audit(started)
            chunks: list[str] = []
            emitted = False
            response: ProviderResponse | None = None
            try:
                stream = stream_with_result(attempt_request)
                if inspect.isawaitable(stream):
                    stream = await stream
                if not hasattr(stream, "__aiter__"):
                    raise ModelStreamProtocolError(
                        "provider stream_with_result must return an async iterator"
                    )
                saw_final = False
                async with asyncio.timeout(max(0.001, attempt_request.timeout_ms / 1000.0)):
                    async for raw_event in stream:
                        if not isinstance(raw_event, ProviderStreamEvent):
                            raise ModelStreamProtocolError(
                                "provider stream_with_result emitted an untyped event"
                            )
                        if raw_event.kind is ProviderStreamEventKind.DELTA:
                            if saw_final:
                                raise ModelStreamProtocolError(
                                    "provider stream emitted a delta after its final response"
                                )
                            chunks.append(raw_event.text)
                            emitted = True
                            yield ModelAttemptStreamEvent.delta(raw_event.text)
                            continue
                        if saw_final:
                            raise ModelStreamProtocolError(
                                "provider stream emitted more than one final response"
                            )
                        saw_final = True
                        response = raw_event.response
                if not saw_final or response is None:
                    # 连接正常结束但缺 final 仍是协议失败，不能把累积 delta 视为完成，
                    # 特别是受 guard 情况下它没有用量证明。
                    raise ModelStreamProtocolError(
                        "provider stream ended without a final canonical response"
                    )
                content = "".join(chunks)
                if response.content and response.content != content:
                    raise ModelStreamProtocolError(
                        "provider final response content differs from emitted stream text"
                    )
                response = replace(response, content=content)
                self._assert_model_budget_guard_response(
                    response,
                    guard,
                    prior_attempts=attempts,
                )
                validate_output_contract(response.content, prompt.output_contract)
                directive = await self._evaluate_retry(request, response, attempt_request)
                if directive is not None and directive.retry:
                    if emitted:
                        raise ModelStreamInterruptedError(
                            "semantic retry requested after a stream delta; retry is forbidden"
                        )
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.REJECTED,
                        response=response,
                        error_code="SEMANTIC_RETRY_REQUESTED",
                        error_message=directive.reason,
                        metadata={"retry_directive": dict(directive.metadata)},
                    )
                    await self._audit(terminal)
                    attempts.append(terminal)
                    prompt = (
                        self.prompt_registry.validate_rendered(directive.rendered_prompt)
                        if directive.rendered_prompt is not None
                        else prompt
                    )
                    retry_reason = directive.reason
                    if not directive.reuse_target:
                        target_index += 1
                    last_error_code = "ATTEMPT_BUDGET_EXHAUSTED"
                    last_error_message = f"retry requested: {directive.reason}"
                    continue

                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.SUCCEEDED,
                    response=response,
                )
                await self._audit(terminal)
                attempts.append(terminal)
                self._put_cached(prepared.cache_key, prepared.prompt, response, target)
                yield ModelAttemptStreamEvent.final(
                    ModelAttemptResult(
                        request_id=request.request_id,
                        success=True,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        response=response,
                        final_target=target,
                    )
                )
                return
            except asyncio.CancelledError as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.CANCELLED,
                    error_code="MODEL_ATTEMPT_CANCELLED",
                    error_message=_safe_error_message(exc),
                )
                await asyncio.shield(self._audit(terminal))
                raise
            except ModelBudgetGuardViolation as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.REJECTED,
                    response=response,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                if emitted:
                    raise ModelStreamInterruptedError(
                        "provider stream failed its guarded terminal measurement after a delta"
                    ) from exc
                raise
            except AttemptAuditError:
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.CANCELLED,
                        error_code="MODEL_STREAM_CANCELLED",
                        error_message=_safe_error_message(exc),
                    )
                    await asyncio.shield(self._audit(terminal))
                    raise
                status = (
                    AttemptStatus.TIMED_OUT
                    if isinstance(exc, TimeoutError)
                    else AttemptStatus.REJECTED
                    if isinstance(exc, (OutputContractViolation, ModelStreamProtocolError))
                    else AttemptStatus.FAILED
                )
                code = (
                    "PROVIDER_TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else getattr(exc, "code", type(exc).__name__.upper())
                )
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=status,
                    response=response,
                    error_code=str(code),
                    error_message=_safe_error_message(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                last_error = exc
                last_error_code = str(code)
                last_error_message = _safe_error_message(exc)
                if emitted:
                    raise ModelStreamInterruptedError(
                        "provider stream failed after a delta; fallback is forbidden"
                    ) from exc
                if isinstance(exc, OutputContractViolation) and len(attempts) < budget:
                    prompt = self._schema_repair_prompt(prompt, response, exc)
                    retry_reason = "output_schema_repair"
                    continue

            target_index += 1
            retry_reason = f"provider_fallback:{last_error_code.lower()}"

        if guard is not None and last_error_code == "MODEL_STREAM_RESULT_UNSUPPORTED":
            raise ModelBudgetGuardViolation(
                "guarded stream requires a provider final canonical usage response",
                code="MODEL_BUDGET_STREAM_UNSUPPORTED",
            )
        if last_error is not None:
            raise ModelAttemptServiceError(
                last_error_message,
                code=last_error_code,
            ) from last_error
        raise ModelStreamProtocolError(last_error_message, code=last_error_code)

    async def _prepare(self, request: ModelAttemptRequest) -> _PreparedCall:
        """在同一 deadline 内渲染/验证提示词并解析路由，再决定是否允许缓存。"""
        # 渲染和路由共享一个总 deadline，避免它们消耗大部分时间后仍给 provider 分配
        # 完整 timeout；guard 请求同时关闭缓存以保留本节点的新鲜、可计量结果。
        started_at = self._monotonic()
        deadline_at = started_at + request.deadline_ms / 1000.0
        if isinstance(request.prompt, RenderedPrompt):
            prompt = self.prompt_registry.validate_rendered(request.prompt)
        else:
            prompt = self.prompt_registry.render(request.prompt)
        remaining = deadline_at - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("model deadline exhausted before route resolution")
        route = await asyncio.wait_for(
            self.route_policy.resolve(request, prompt),
            timeout=max(0.001, remaining),
        )
        if not isinstance(route, RouteDecision):
            raise TypeError("route policy must return RouteDecision")
        # 缓存命中没有本次 provider 用量；受 guard 调用必须产生新的、绑定节点的计量后
        # 才能被 Task 接受。
        cache_key = (
            None
            if request.model_budget_guard is not None
            else self._cache_key(request, prompt, route)
        )
        return _PreparedCall(prompt=prompt, route=route, deadline_at=deadline_at, cache_key=cache_key)

    async def _evaluate_retry(
        self,
        request: ModelAttemptRequest,
        response: ProviderResponse,
        attempt_request: ProviderAttemptRequest,
    ) -> RetryDirective | None:
        # retry evaluator 仅产生类型化指令，不能直接调用 adapter 或修改 route；异步
        # evaluator 同样被包含在本服务的调用顺序中。
        evaluator = request.retry_evaluator
        if evaluator is None:
            return None
        directive: Any = evaluator(response, attempt_request)
        if inspect.isawaitable(directive):
            directive = await directive
        if directive is not None and not isinstance(directive, RetryDirective):
            raise TypeError("retry evaluator must return RetryDirective or None")
        return directive

    def _begin_attempt(
        self,
        *,
        request: ModelAttemptRequest,
        route: RouteDecision,
        prompt: RenderedPrompt,
        target: ProviderTarget,
        attempt_index: int,
        retry_reason: str,
        remaining_ms: int,
    ) -> tuple[ProviderAttemptRequest, ModelAttemptRecord, float]:
        """创建单次 provider 请求及其 STARTED 审计记录，只白名单安全元数据。"""
        # 每次 provider 调用在网络前取得独立 attempt_id 并先写 STARTED 收据；审计
        # 元数据采用显式白名单，不将任意 request.metadata 持久化。
        attempt_id = uuid.uuid4().hex
        timeout_ms = min(remaining_ms, target.timeout_ms or remaining_ms)
        attempt_request = ProviderAttemptRequest(
            request_id=request.request_id,
            attempt_id=attempt_id,
            attempt_index=attempt_index,
            retry_reason=retry_reason,
            scene_type=request.scene_type,
            cognitive_tier=request.cognitive_tier,
            prompt=prompt,
            target=target,
            timeout_ms=max(1, timeout_ms),
            metadata=dict(request.metadata),
            model_budget_guard=request.model_budget_guard,
        )
        audit_metadata: dict[str, Any] = {
            "scene_type": request.scene_type,
            "cognitive_tier": request.cognitive_tier,
        }
        # 模型节点身份及证明有界 Surface 指令的无秘密摘要属于受治理公开维度；只保留
        # 白名单键，任意请求元数据不得进入持久化审计日志。
        model_node_id = str(request.metadata.get("model_node_id") or "").strip()
        if model_node_id:
            audit_metadata["model_node_id"] = model_node_id
        expression_directive_digest = str(
            request.metadata.get("surface_expression_directive_digest") or ""
        ).strip().lower()
        if len(expression_directive_digest) == 64 and all(
            char in "0123456789abcdef"
            for char in expression_directive_digest
        ):
            audit_metadata[
                "surface_expression_directive_digest"
            ] = expression_directive_digest
        knowledge_set_digest = str(
            request.metadata.get("surface_active_knowledge_set_digest") or ""
        ).strip().lower()
        knowledge_count = request.metadata.get("surface_active_knowledge_count")
        if (
            len(knowledge_set_digest) == 64
            and all(
                char in "0123456789abcdef" for char in knowledge_set_digest
            )
            and isinstance(knowledge_count, int)
            and not isinstance(knowledge_count, bool)
            and 1 <= knowledge_count <= 4
        ):
            audit_metadata[
                "surface_active_knowledge_set_digest"
            ] = knowledge_set_digest
            audit_metadata["surface_active_knowledge_count"] = knowledge_count
        started = ModelAttemptRecord(
            attempt_id=attempt_id,
            request_id=request.request_id,
            route_decision_id=route.decision_id,
            attempt_index=attempt_index,
            status=AttemptStatus.STARTED,
            retry_reason=retry_reason,
            adapter_id=target.adapter_id,
            provider=target.provider,
            model_id=target.model_id,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            prompt_digest=prompt.digest,
            started_at_ms=self._wall_clock_ms(),
            metadata=audit_metadata,
        )
        return attempt_request, started, self._monotonic()

    @staticmethod
    def _assert_model_budget_guard_response(
        response: ProviderResponse,
        guard: ModelBudgetGuard | None,
        *,
        prior_attempts: Sequence[ModelAttemptRecord] = (),
    ) -> None:
        """只使用 provider 终态报告的明确单位计量，累加验证 guard 上限。"""
        # guard 只信任 provider 终态计量，并累计此前已审计 attempt；缺字段、超输出、
        # 超 token 或超费用均在交付响应前拒绝。
        if guard is None:
            return
        measured = _provider_budget_measurements(response.usage)
        missing = [
            dimension
            for dimension in guard.required_usage_dimensions
            if dimension not in measured
        ]
        if missing:
            raise ModelBudgetGuardViolation(
                "provider response omitted required budget measurements: "
                + ", ".join(missing),
                code="MODEL_PROVIDER_USAGE_REQUIRED",
            )
        completion_tokens = int(measured["completion_tokens"])
        if completion_tokens > guard.max_output_tokens:
            raise ModelBudgetGuardViolation(
                "provider response exceeded the governed output token cap",
                code="MODEL_OUTPUT_BUDGET_EXCEEDED",
            )
        prior_measurements = [
            _provider_budget_measurements(
                item.metadata.get("provider_budget_measurements")
            )
            for item in prior_attempts
        ]
        token_count = sum(
            int(item.get("token_count", 0)) for item in prior_measurements
        )
        token_count += int(measured["token_count"])
        if token_count > guard.max_token_count:
            raise ModelBudgetGuardViolation(
                "provider responses exceeded the governed total token budget",
                code="MODEL_TOKEN_BUDGET_EXCEEDED",
            )
        measured_costs = [
            int(item["model_cost_micros"])
            for item in prior_measurements
            if "model_cost_micros" in item
        ]
        if "model_cost_micros" in measured:
            measured_costs.append(int(measured["model_cost_micros"]))
        if (
            measured_costs
            and sum(measured_costs) > guard.max_model_cost_micros
        ):
            raise ModelBudgetGuardViolation(
                "provider responses exceeded the governed model cost budget",
                code="MODEL_COST_BUDGET_EXCEEDED",
            )

    def _terminal_record(
        self,
        started: ModelAttemptRecord,
        *,
        started_monotonic: float,
        status: AttemptStatus,
        response: ProviderResponse | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ModelAttemptRecord:
        """把 STARTED 记录封存为终态，并仅附加 provider 所有的用量/传输证据。"""
        # STARTED → terminal 形成不可变尝试对；只把 provider 规范用量与脱敏传输证据
        # 附加进去，既支持恢复结算，也避免终态记录携带原始模型内容。
        elapsed_ms = max(0, int((self._monotonic() - started_monotonic) * 1000))
        merged_metadata = dict(started.metadata)
        merged_metadata.update(dict(metadata or {}))
        # 此处是仍持有 typed ProviderResponse 的唯一位置：为外层 Task 预算保留 provider
        # 报告的计量，绝不估算缺失的 token 或成本维度。
        provider_budget = _provider_budget_measurements(
            response.usage if response is not None else None
        )
        if provider_budget:
            merged_metadata["provider_budget_measurements"] = provider_budget
        provider_transport = _provider_transport_measurements(
            response.metadata if response is not None else None
        )
        if provider_transport:
            merged_metadata["provider_transport_measurements"] = provider_transport
        return replace(
            started,
            status=status,
            ended_at_ms=self._wall_clock_ms(),
            latency_ms=elapsed_ms,
            output_digest=(sha256_digest(response.content) if response is not None else None),
            error_code=error_code,
            error_message=error_message,
            metadata=merged_metadata,
        )

    async def _audit(self, record: ModelAttemptRecord) -> None:
        """写入尝试审计；审计不可用即失败，避免产生无法恢复的模型副作用。"""
        # 审计写入失败即整体失败：执行过外部模型却没有 STARTED/终态收据无法安全恢复，
        # 因而不能降级为仅日志告警。
        try:
            await self.audit_repository.record_attempt(record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AttemptAuditError(
                f"attempt audit rejected {record.attempt_id}/{record.status.value}: {_safe_error_message(exc)}"
            ) from exc

    def _remaining_ms(self, deadline_at: float) -> int:
        return max(0, int((deadline_at - self._monotonic()) * 1000))

    def _schema_repair_prompt(
        self,
        prompt: RenderedPrompt,
        response: ProviderResponse | None,
        violation: OutputContractViolation,
    ) -> RenderedPrompt:
        # 修复提示词仅追加有限截断的失败输出与契约，不修改原 prompt 身份；生成后的
        # RenderedPrompt 仍会在调用点由 registry 验证。
        messages = list(prompt.messages)
        if response is not None and response.content:
            messages.append({"role": "assistant", "content": response.content[:4000]})
        contract = json.dumps(prompt.output_contract.as_dict(), ensure_ascii=False, sort_keys=True)
        messages.append(
            {
                "role": "user",
                "content": (
                    "The previous output violated the required output contract. "
                    f"Reason: {violation}. Return a corrected response only. Contract: {contract}"
                ),
            }
        )
        return prompt.with_messages(messages, metadata={"repair_reason": violation.code})

    def _cache_key(
        self,
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
        route: RouteDecision,
    ) -> str | None:
        """生成包含路由身份的缓存键；前台 GLOBAL 缓存和受 guard 调用均在更早处禁用。"""
        # 缓存键绑定 prompt 摘要、场景、层级和冻结 targets；guard 调用在更早处禁用，
        # 前台全局缓存亦禁止，防止跨用户/跨预算复用模型结果。
        policy = prompt.cache_policy
        if policy.scope is CacheScope.NONE:
            return None
        if policy.scope is CacheScope.GLOBAL and request.foreground:
            return None
        scope_key = request.request_id if policy.scope is CacheScope.REQUEST else "background-global"
        return sha256_digest(
            {
                "scope": policy.scope.value,
                "scope_key": scope_key,
                "scene_type": request.scene_type,
                "cognitive_tier": request.cognitive_tier,
                "prompt_digest": prompt.digest,
                "route_targets": [target.identity() for target in route.targets],
            }
        )

    def _get_cached(self, key: str | None) -> _CacheEntry | None:
        if key is None:
            return None
        now = self._monotonic()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return entry

    def _put_cached(
        self,
        key: str | None,
        prompt: RenderedPrompt,
        response: ProviderResponse,
        target: ProviderTarget,
    ) -> None:
        if key is None:
            return
        # 容量回收先删除过期项，再按最早过期时间淘汰；锁只保护本地缓存状态，不覆盖
        # 审计或 provider 的并发权威。
        expires_at = self._monotonic() + prompt.cache_policy.ttl_ms / 1000.0
        with self._cache_lock:
            if len(self._cache) >= self._cache_max_entries:
                now = self._monotonic()
                expired = [cache_key for cache_key, entry in self._cache.items() if entry.expires_at <= now]
                for cache_key in expired:
                    self._cache.pop(cache_key, None)
                if len(self._cache) >= self._cache_max_entries:
                    oldest_key = min(self._cache, key=lambda item: self._cache[item].expires_at)
                    self._cache.pop(oldest_key, None)
            self._cache[key] = _CacheEntry(expires_at=expires_at, response=response, target=target)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()


__all__ = [
    "AttemptAuditError",
    "ModelAttemptService",
    "ModelAttemptServiceError",
    "ModelStreamInterruptedError",
    "ModelStreamProtocolError",
    "OutputContractViolation",
    "validate_output_contract",
]
