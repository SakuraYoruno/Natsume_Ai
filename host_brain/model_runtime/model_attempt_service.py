"""Single authority for routed model completion and streaming attempts."""

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
    ModelAttemptStreamEventKind,
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
    pass


class AttemptAuditError(ModelAttemptServiceError):
    pass


class OutputContractViolation(ModelAttemptServiceError):
    def __init__(self, message: str, *, code: str = "OUTPUT_SCHEMA_INVALID") -> None:
        super().__init__(message)
        self.code = code


class ModelBudgetGuardViolation(ModelAttemptServiceError):
    """A guarded model call cannot prove or honor its immutable limits."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ModelStreamInterruptedError(ModelAttemptServiceError):
    pass


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
    """Validate provider output and return parsed JSON when applicable."""

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
    return result


def _assert_stream_transport_attestation(response: ProviderResponse) -> None:
    """Require the terminal transport facts that a live guarded stream needs.

    Token/cost numbers alone are not enough for a user-visible provisional
    sentence: after a restart the audit trail must also identify which
    transport returned those numbers and how long it took.  This is scoped to
    the new streaming protocol; existing complete-call compatibility remains
    unchanged.
    """

    measurements = _provider_transport_measurements(response.metadata)
    required = {
        "provider_latency_ms",
        "transport_identity_digest",
        "transport_external",
    }
    missing = sorted(required - set(measurements))
    if missing:
        raise ModelBudgetGuardViolation(
            "provider stream omitted required transport attestation: "
            + ", ".join(missing),
            code="MODEL_PROVIDER_TRANSPORT_UNPROVEN",
        )


class ModelAttemptService:
    """Resolve once, then own every provider and semantic retry attempt."""

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
        prepared = await self._prepare(request)
        cached = self._get_cached(prepared.cache_key)
        if cached is not None:
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
        # An ordinary request cannot alter the route-owned provider budget.
        # A typed ModelBudgetGuard is different: it comes from a durable Task
        # reservation and can only reduce that server-selected ceiling.
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

        while len(attempts) < budget and target_index < len(prepared.route.targets):
            remaining_ms = self._remaining_ms(prepared.deadline_at)
            if remaining_ms <= 0:
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
                validate_output_contract(response.content, prompt.output_contract)
                directive = await self._evaluate_retry(request, response, attempt_request)
                if directive is not None and directive.retry:
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
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=(
                        AttemptStatus.REJECTED
                        if guard is not None
                        else AttemptStatus.TIMED_OUT
                    ),
                    error_code=(
                        "MODEL_PROVIDER_USAGE_UNPROVEN"
                        if guard is not None
                        else "PROVIDER_TIMEOUT"
                    ),
                    error_message=_safe_error_message(exc),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                if guard is not None:
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
            except Exception as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=(
                        AttemptStatus.REJECTED
                        if guard is not None
                        else AttemptStatus.FAILED
                    ),
                    error_code=(
                        "MODEL_PROVIDER_USAGE_UNPROVEN"
                        if guard is not None
                        else type(exc).__name__.upper()
                    ),
                    error_message=(
                        "guarded provider call failed without canonical usage"
                        if guard is not None
                        else _safe_error_message(exc)
                    ),
                )
                await self._audit(terminal)
                attempts.append(terminal)
                if guard is not None:
                    return ModelAttemptResult(
                        request_id=request.request_id,
                        success=False,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        error_code="MODEL_PROVIDER_USAGE_UNPROVEN",
                        error_message="guarded provider call failed without canonical usage",
                    )
                last_error_code = terminal.error_code or "PROVIDER_ERROR"
                last_error_message = terminal.error_message or "provider attempt failed"

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

        if request.model_budget_guard is not None:
            terminal: ModelAttemptResult | None = None
            async for event in self.stream_events(request):
                if event.kind is ModelAttemptStreamEventKind.DELTA:
                    if event.text:
                        yield event.text
                    continue
                terminal = event.result
            if terminal is None:
                raise ModelBudgetGuardViolation(
                    "guarded model stream ended without a terminal usage receipt",
                    code="MODEL_PROVIDER_USAGE_UNPROVEN",
                )
            if not terminal.success:
                raise ModelBudgetGuardViolation(
                    terminal.error_message or "guarded model stream cannot prove usage",
                    code=terminal.error_code or "MODEL_PROVIDER_USAGE_UNPROVEN",
                )
            return
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
            raise ModelAttemptServiceError("model stream exhausted without a successful attempt")
        raise ModelAttemptServiceError(_safe_error_message(last_error)) from last_error

    async def stream_events(
        self,
        request: ModelAttemptRequest,
    ) -> AsyncIterator[ModelAttemptStreamEvent]:
        """Stream one governed provider attempt with a final usage attestation.

        This narrow protocol is intentionally available only to a request that
        carries a node-bound ``ModelBudgetGuard``.  Normal Surface streaming
        keeps its backwards-compatible text-only path.  A governed stream can
        make text visible before final Task Acceptance, but every provider
        delta is accompanied by one audited terminal result; missing usage,
        an interrupted stream, or an output-cap breach all end in a failed
        terminal result rather than an estimated charge or a hidden fallback.
        """

        guard = request.model_budget_guard
        if guard is None:
            raise ModelAttemptServiceError(
                "stream_events requires a node-bound ModelBudgetGuard"
            )
        prepared = await self._prepare(request)
        # _prepare() disables cache for a guarded request.  Streaming remains
        # one route attempt: after any visible delta, a fallback would combine
        # two providers into one user-visible answer and break budget lineage.
        budget = min(prepared.route.max_attempts, guard.max_provider_attempts)
        attempts: list[ModelAttemptRecord] = []
        target_index = 0
        retry_reason = "initial"
        prompt = prepared.prompt
        buffered = (
            prompt.output_contract.kind is not OutputKind.TEXT
            or request.retry_evaluator is not None
        )

        while len(attempts) < budget and target_index < len(prepared.route.targets):
            remaining_ms = self._remaining_ms(prepared.deadline_at)
            if remaining_ms <= 0:
                yield ModelAttemptStreamEvent(
                    kind=ModelAttemptStreamEventKind.TERMINAL,
                    result=ModelAttemptResult(
                        request_id=request.request_id,
                        success=False,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        error_code="MODEL_DEADLINE_EXCEEDED",
                        error_message="model request deadline exhausted",
                    ),
                )
                return

            target = prepared.route.targets[target_index]
            attempt_request, started, started_monotonic = self._begin_attempt(
                request=request,
                route=prepared.route,
                prompt=prompt,
                target=target,
                attempt_index=len(attempts) + 1,
                retry_reason=retry_reason,
                remaining_ms=remaining_ms,
            )
            await self._audit(started)
            response: ProviderResponse | None = None
            chunks: list[str] = []
            emitted = False
            final_seen = False
            try:
                adapter = self.provider_adapters.get(target.adapter_id)
                stream_events = getattr(adapter, "stream_events", None)
                if not callable(stream_events):
                    raise ModelBudgetGuardViolation(
                        "provider adapter cannot expose a final stream usage receipt",
                        code="MODEL_BUDGET_STREAM_USAGE_UNSUPPORTED",
                    )
                if getattr(adapter, "supports_final_stream_usage", True) is not True:
                    raise ModelBudgetGuardViolation(
                        "provider adapter has no attested final stream usage protocol",
                        code="MODEL_BUDGET_STREAM_USAGE_UNSUPPORTED",
                    )
                async with asyncio.timeout(
                    max(0.001, attempt_request.timeout_ms / 1000.0)
                ):
                    async for raw_event in stream_events(attempt_request):
                        if not isinstance(raw_event, ProviderStreamEvent):
                            raise ModelBudgetGuardViolation(
                                "provider adapter emitted an untyped stream event",
                                code="MODEL_PROVIDER_USAGE_UNPROVEN",
                            )
                        if raw_event.kind is ProviderStreamEventKind.DELTA:
                            if final_seen:
                                raise ModelBudgetGuardViolation(
                                    "provider emitted a delta after its final usage receipt",
                                    code="MODEL_PROVIDER_USAGE_UNPROVEN",
                                )
                            text = str(raw_event.text or "")
                            if not text:
                                continue
                            chunks.append(text)
                            if not buffered:
                                emitted = True
                                yield ModelAttemptStreamEvent(
                                    kind=ModelAttemptStreamEventKind.DELTA,
                                    text=text,
                                )
                            continue
                        if final_seen:
                            raise ModelBudgetGuardViolation(
                                "provider emitted more than one final stream usage receipt",
                                code="MODEL_PROVIDER_USAGE_UNPROVEN",
                            )
                        final_seen = True
                        response = raw_event.response

                if not final_seen or response is None:
                    raise ModelBudgetGuardViolation(
                        "provider stream ended without a final canonical usage receipt",
                        code="MODEL_PROVIDER_USAGE_UNPROVEN",
                    )
                # A final SSE frame may only contain usage.  The transcript is
                # the accumulated delta text in that case; if the provider
                # also supplies a complete final message, prefer it as the
                # provider-owned canonical representation.
                response = ProviderResponse(
                    content=response.content or "".join(chunks),
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    metadata={**dict(response.metadata), "streamed": True},
                )
                self._assert_model_budget_guard_response(
                    response,
                    guard,
                    prior_attempts=attempts,
                )
                _assert_stream_transport_attestation(response)
                validate_output_contract(response.content, prompt.output_contract)
                directive = await self._evaluate_retry(request, response, attempt_request)
                if directive is not None and directive.retry:
                    if emitted:
                        raise ModelBudgetGuardViolation(
                            "semantic retry requested after a governed stream became visible",
                            code="MODEL_STREAM_RETRY_AFTER_VISIBILITY",
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
                    # A strict no-visible-output retry is still safe.  It is
                    # bounded by the frozen guard and retains every previous
                    # provider measurement for cap enforcement.
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
                attempts.append(terminal)
                if buffered:
                    for text in chunks:
                        if text:
                            yield ModelAttemptStreamEvent(
                                kind=ModelAttemptStreamEventKind.DELTA,
                                text=text,
                            )
                yield ModelAttemptStreamEvent(
                    kind=ModelAttemptStreamEventKind.TERMINAL,
                    result=ModelAttemptResult(
                        request_id=request.request_id,
                        success=True,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        response=response,
                        final_target=target,
                    ),
                )
                return
            except asyncio.CancelledError as exc:
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.CANCELLED,
                    response=response,
                    error_code="MODEL_ATTEMPT_CANCELLED",
                    error_message=_safe_error_message(exc),
                )
                await asyncio.shield(self._audit(terminal))
                raise
            except AttemptAuditError:
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    terminal = self._terminal_record(
                        started,
                        started_monotonic=started_monotonic,
                        status=AttemptStatus.CANCELLED,
                        response=response,
                        error_code="MODEL_STREAM_CANCELLED",
                        error_message=_safe_error_message(exc),
                    )
                    await asyncio.shield(self._audit(terminal))
                    raise
                if isinstance(exc, ModelBudgetGuardViolation):
                    code = exc.code
                    message = str(exc)
                elif isinstance(exc, TimeoutError):
                    code = "MODEL_PROVIDER_USAGE_UNPROVEN"
                    message = "guarded provider stream timed out without canonical usage"
                else:
                    code = "MODEL_PROVIDER_USAGE_UNPROVEN"
                    message = "guarded provider stream failed without canonical usage"
                terminal = self._terminal_record(
                    started,
                    started_monotonic=started_monotonic,
                    status=AttemptStatus.REJECTED,
                    response=response,
                    error_code=code,
                    error_message=message,
                )
                await self._audit(terminal)
                attempts.append(terminal)
                # Never fall back after a possibly visible sentence.  Before
                # visibility a retry could be safe, but a missing final usage
                # means the first transport charge cannot be settled, so the
                # durable task must reject rather than hide it behind another
                # provider response.
                yield ModelAttemptStreamEvent(
                    kind=ModelAttemptStreamEventKind.TERMINAL,
                    result=ModelAttemptResult(
                        request_id=request.request_id,
                        success=False,
                        route_decision=prepared.route,
                        attempts=tuple(attempts),
                        error_code=code,
                        error_message=message,
                    ),
                )
                return

        yield ModelAttemptStreamEvent(
            kind=ModelAttemptStreamEventKind.TERMINAL,
            result=ModelAttemptResult(
                request_id=request.request_id,
                success=False,
                route_decision=prepared.route,
                attempts=tuple(attempts),
                error_code="MODEL_ROUTE_EXHAUSTED",
                error_message="guarded model stream exhausted without a successful attempt",
            ),
        )

    async def _prepare(self, request: ModelAttemptRequest) -> _PreparedCall:
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
        # A cache hit has no provider usage for this execution. A guarded call
        # must produce a fresh, node-bound measurement before Task acceptance.
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
        # Model-node identity is a governed public dimension.  Preserve only
        # this explicit key from caller metadata; arbitrary request metadata
        # must never enter the durable audit journal.
        model_node_id = str(request.metadata.get("model_node_id") or "").strip()
        if model_node_id:
            audit_metadata["model_node_id"] = model_node_id
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
        model_cost_micros = sum(
            int(item.get("model_cost_micros", 0)) for item in prior_measurements
        )
        model_cost_micros += int(measured["model_cost_micros"])
        if model_cost_micros > guard.max_model_cost_micros:
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
        elapsed_ms = max(0, int((self._monotonic() - started_monotonic) * 1000))
        merged_metadata = dict(started.metadata)
        merged_metadata.update(dict(metadata or {}))
        # This is the only point that still owns the typed ProviderResponse.
        # Preserve provider-reported measurements for the outer Task budget;
        # never estimate a missing token or cost dimension.
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
    "OutputContractViolation",
    "validate_output_contract",
]
