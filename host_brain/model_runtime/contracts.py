"""Typed contracts for the single model-attempt authority.

The contracts in this module deliberately contain no provider SDK imports.
Routing selects an adapter-owned target, while credentials and transport
clients stay inside the adapter implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)


JsonMapping = Mapping[str, Any]
PromptMessage = Mapping[str, Any]


def _clean_required(value: Any, *, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_digest(value: Any) -> str:
    """Return a stable sha256 digest for JSON-compatible contract data."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class CacheScope(str, Enum):
    NONE = "none"
    REQUEST = "request"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Prompt-owned cache policy.

    ``GLOBAL`` is only honored by ``ModelAttemptService`` for background
    requests. Foreground requests can at most use request-scoped caching.
    """

    scope: CacheScope | str = CacheScope.NONE
    ttl_ms: int = 0

    def __post_init__(self) -> None:
        try:
            scope = self.scope if isinstance(self.scope, CacheScope) else CacheScope(str(self.scope))
        except ValueError as exc:
            raise ValueError(f"unsupported cache scope: {self.scope}") from exc
        ttl_ms = int(self.ttl_ms or 0)
        if scope is CacheScope.NONE:
            ttl_ms = 0
        elif ttl_ms <= 0:
            raise ValueError("cache ttl_ms must be positive when caching is enabled")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "ttl_ms", ttl_ms)

    def as_dict(self) -> dict[str, Any]:
        return {"scope": self.scope.value, "ttl_ms": self.ttl_ms}


class OutputKind(str, Enum):
    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True, slots=True)
class OutputContract:
    """Expected provider output and optional JSON schema."""

    kind: OutputKind | str = OutputKind.TEXT
    schema: JsonMapping | None = None
    strict: bool = True

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, OutputKind) else OutputKind(str(self.kind))
        except ValueError as exc:
            raise ValueError(f"unsupported output contract kind: {self.kind}") from exc
        schema = dict(self.schema or {})
        if kind is OutputKind.JSON_SCHEMA:
            if not schema:
                raise ValueError("json_schema output contract requires a schema")
            schema_type = str(schema.get("type") or "object").strip()
            if schema_type != "object":
                raise ValueError("top-level output schema type must be object")
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
                raise ValueError("output schema required must be a sequence")
            if not isinstance(properties, Mapping):
                raise ValueError("output schema properties must be an object")
            unknown_required = {str(item) for item in required} - {str(item) for item in properties}
            if unknown_required:
                raise ValueError(
                    "output schema required fields missing from properties: "
                    + ", ".join(sorted(unknown_required))
                )
        elif schema:
            raise ValueError(f"{kind.value} output contract must not define a JSON schema")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "schema", schema or None)
        object.__setattr__(self, "strict", bool(self.strict))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "schema": dict(self.schema or {}),
            "strict": self.strict,
        }

    def response_format(self) -> dict[str, Any] | None:
        if self.kind is OutputKind.TEXT:
            return None
        if self.kind is OutputKind.JSON_OBJECT:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": dict(self.schema or {}),
            "strict": self.strict,
        }


@dataclass(frozen=True, slots=True)
class PromptRef:
    """Reference to a registered prompt plus immutable expectations.

    Version, digest and output/cache fields are assertions or restrictions;
    they never broaden the registry-owned definition.
    """

    prompt_id: str
    variables: JsonMapping = field(default_factory=dict)
    version: str | None = None
    digest: str | None = None
    output_contract: OutputContract | None = None
    cache_policy: CachePolicy | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_id", _clean_required(self.prompt_id, field_name="prompt_id"))
        object.__setattr__(self, "variables", dict(self.variables or {}))
        if self.version is not None:
            object.__setattr__(self, "version", _clean_required(self.version, field_name="version"))
        if self.digest is not None:
            digest = str(self.digest or "").strip().lower()
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError("prompt digest must be a sha256 hex digest")
            object.__setattr__(self, "digest", digest)


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    prompt_id: str
    version: str
    digest: str
    messages: tuple[PromptMessage, ...]
    output_contract: OutputContract = field(default_factory=OutputContract)
    cache_policy: CachePolicy = field(default_factory=CachePolicy)
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_id", _clean_required(self.prompt_id, field_name="prompt_id"))
        object.__setattr__(self, "version", _clean_required(self.version, field_name="version"))
        digest = str(self.digest or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("rendered prompt digest must be a sha256 hex digest")
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "messages", tuple(dict(item) for item in self.messages))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def as_message_dicts(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]

    def with_messages(
        self,
        messages: Sequence[PromptMessage],
        *,
        metadata: JsonMapping | None = None,
    ) -> "RenderedPrompt":
        normalized = tuple(dict(message) for message in messages)
        digest = rendered_prompt_digest(
            prompt_id=self.prompt_id,
            version=self.version,
            messages=normalized,
            output_contract=self.output_contract,
            cache_policy=self.cache_policy,
        )
        merged_metadata = dict(self.metadata)
        merged_metadata.update(dict(metadata or {}))
        return replace(self, messages=normalized, digest=digest, metadata=merged_metadata)


def rendered_prompt_digest(
    *,
    prompt_id: str,
    version: str,
    messages: Sequence[PromptMessage],
    output_contract: OutputContract,
    cache_policy: CachePolicy,
) -> str:
    return sha256_digest(
        {
            "prompt_id": str(prompt_id),
            "version": str(version),
            "messages": [dict(item) for item in messages],
            "output_contract": output_contract.as_dict(),
            "cache_policy": cache_policy.as_dict(),
        }
    )


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    """Secret-free route target. Credentials live in its adapter."""

    adapter_id: str
    provider: str
    model_id: str
    timeout_ms: int | None = None
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _clean_required(self.adapter_id, field_name="adapter_id"))
        object.__setattr__(self, "provider", _clean_required(self.provider, field_name="provider"))
        object.__setattr__(self, "model_id", _clean_required(self.model_id, field_name="model_id"))
        if self.timeout_ms is not None and int(self.timeout_ms) <= 0:
            raise ValueError("provider target timeout_ms must be positive")
        if self.timeout_ms is not None:
            object.__setattr__(self, "timeout_ms", int(self.timeout_ms))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def identity(self) -> tuple[str, str, str]:
        return self.adapter_id, self.provider, self.model_id


@dataclass(frozen=True, slots=True)
class RouteDecision:
    decision_id: str
    targets: tuple[ProviderTarget, ...]
    reason: str
    max_attempts: int
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _clean_required(self.decision_id, field_name="decision_id"))
        object.__setattr__(self, "reason", _clean_required(self.reason, field_name="reason"))
        targets = tuple(self.targets)
        if not targets:
            raise ValueError("route decision must contain at least one target")
        if int(self.max_attempts) <= 0:
            raise ValueError("route decision max_attempts must be positive")
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "max_attempts", int(self.max_attempts))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True, slots=True)
class RetryDirective:
    """Explicit semantic retry decision returned by a request evaluator."""

    retry: bool
    reason: str
    rendered_prompt: RenderedPrompt | None = None
    reuse_target: bool = True
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        reason = str(self.reason or "").strip()
        if self.retry and not reason:
            raise ValueError("retry directive reason must not be empty")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def accept(cls) -> "RetryDirective":
        return cls(retry=False, reason="accepted")


MODEL_BUDGET_GUARD_SCHEMA_VERSION = "Natsume.model_runtime.budget_guard.v1"
_MODEL_BUDGET_USAGE_DIMENSIONS = frozenset(
    {"token_count", "completion_tokens", "model_cost_micros"}
)
_REQUIRED_MODEL_BUDGET_USAGE_DIMENSIONS = (
    "token_count",
    "completion_tokens",
    "model_cost_micros",
)
_MINIMUM_MODEL_BUDGET_USAGE_DIMENSIONS = (
    "token_count",
    "completion_tokens",
)


@dataclass(frozen=True, slots=True)
class ModelBudgetGuard:
    """Immutable, node-bound upper bounds for one governed model call.

    The guard travels from a durable Task budget through an already-frozen
    worker input. It can only reduce the server-selected route's attempt and
    output limits; it never selects a model, provider, or route.
    """

    budget_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    deadline_ms: int
    max_provider_attempts: int
    max_token_count: int
    max_output_tokens: int
    max_model_cost_micros: int
    required_usage_dimensions: tuple[str, ...] = _REQUIRED_MODEL_BUDGET_USAGE_DIMENSIONS
    schema_version: str = MODEL_BUDGET_GUARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_BUDGET_GUARD_SCHEMA_VERSION:
            raise ValueError("model budget guard schema version is invalid")
        for field_name in ("budget_id", "graph_id", "node_id", "attempt_id"):
            object.__setattr__(
                self,
                field_name,
                _clean_required(getattr(self, field_name), field_name=field_name),
            )
        for field_name in (
            "graph_revision",
            "deadline_ms",
            "max_provider_attempts",
            "max_token_count",
            "max_output_tokens",
            "max_model_cost_micros",
        ):
            try:
                value = int(getattr(self, field_name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} must be a positive integer") from exc
            if value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
            object.__setattr__(self, field_name, value)
        if self.max_output_tokens > self.max_token_count:
            raise ValueError("max_output_tokens must not exceed max_token_count")
        dimensions = tuple(
            dict.fromkeys(str(item or "").strip() for item in self.required_usage_dimensions)
        )
        canonical_dimensions = tuple(
            item
            for item in _REQUIRED_MODEL_BUDGET_USAGE_DIMENSIONS
            if item in dimensions
        )
        if (
            not dimensions
            or not set(dimensions).issubset(_MODEL_BUDGET_USAGE_DIMENSIONS)
            or not set(_MINIMUM_MODEL_BUDGET_USAGE_DIMENSIONS).issubset(dimensions)
            or dimensions != canonical_dimensions
        ):
            raise ValueError("model budget guard usage dimensions are invalid")
        object.__setattr__(self, "required_usage_dimensions", dimensions)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ModelBudgetGuard":
        data = dict(payload or {})
        expected = {
            "schema_version",
            "budget_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "deadline_ms",
            "max_provider_attempts",
            "max_token_count",
            "max_output_tokens",
            "max_model_cost_micros",
            "required_usage_dimensions",
        }
        if set(data) != expected:
            raise ValueError("model budget guard payload shape is invalid")
        raw_dimensions = data.get("required_usage_dimensions")
        if not isinstance(raw_dimensions, Sequence) or isinstance(
            raw_dimensions,
            (str, bytes, bytearray),
        ):
            raise ValueError("model budget guard usage dimensions must be a sequence")
        return cls(
            schema_version=str(data.get("schema_version") or ""),
            budget_id=str(data.get("budget_id") or ""),
            graph_id=str(data.get("graph_id") or ""),
            graph_revision=data.get("graph_revision"),
            node_id=str(data.get("node_id") or ""),
            attempt_id=str(data.get("attempt_id") or ""),
            deadline_ms=data.get("deadline_ms"),
            max_provider_attempts=data.get("max_provider_attempts"),
            max_token_count=data.get("max_token_count"),
            max_output_tokens=data.get("max_output_tokens"),
            max_model_cost_micros=data.get("max_model_cost_micros"),
            required_usage_dimensions=tuple(str(item or "") for item in raw_dimensions),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "budget_id": self.budget_id,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "deadline_ms": self.deadline_ms,
            "max_provider_attempts": self.max_provider_attempts,
            "max_token_count": self.max_token_count,
            "max_output_tokens": self.max_output_tokens,
            "max_model_cost_micros": self.max_model_cost_micros,
            "required_usage_dimensions": list(self.required_usage_dimensions),
        }


@dataclass(frozen=True, slots=True)
class ProviderAttemptRequest:
    request_id: str
    attempt_id: str
    attempt_index: int
    retry_reason: str
    scene_type: str
    cognitive_tier: str
    prompt: RenderedPrompt
    target: ProviderTarget
    timeout_ms: int
    metadata: JsonMapping = field(default_factory=dict)
    model_budget_guard: ModelBudgetGuard | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _clean_required(self.request_id, field_name="request_id"))
        object.__setattr__(self, "attempt_id", _clean_required(self.attempt_id, field_name="attempt_id"))
        if int(self.attempt_index) <= 0:
            raise ValueError("attempt_index must be positive")
        if int(self.timeout_ms) <= 0:
            raise ValueError("attempt timeout_ms must be positive")
        object.__setattr__(self, "attempt_index", int(self.attempt_index))
        object.__setattr__(self, "timeout_ms", int(self.timeout_ms))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if self.model_budget_guard is not None and not isinstance(
            self.model_budget_guard,
            ModelBudgetGuard,
        ):
            raise TypeError("model_budget_guard must be a ModelBudgetGuard")


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str
    finish_reason: str | None = None
    usage: JsonMapping = field(default_factory=dict)
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "usage", dict(self.usage or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


class ProviderStreamEventKind(str, Enum):
    """The only two records permitted by a usage-attested provider stream."""

    DELTA = "DELTA"
    FINAL = "FINAL"


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    """One provider stream delta or its terminal canonical response.

    A text delta intentionally carries no usage estimate.  The terminal event
    carries the provider-owned :class:`ProviderResponse`, including the exact
    usage/transport evidence that a governed Task budget needs.  This keeps a
    caller from converting token heuristics into a fake final measurement.
    """

    kind: ProviderStreamEventKind | str
    text: str = ""
    response: ProviderResponse | None = None

    def __post_init__(self) -> None:
        try:
            kind = (
                self.kind
                if isinstance(self.kind, ProviderStreamEventKind)
                else ProviderStreamEventKind(str(self.kind).upper())
            )
        except ValueError as exc:
            raise ValueError("provider stream event kind is invalid") from exc
        text = str(self.text or "")
        if kind is ProviderStreamEventKind.DELTA:
            if not text:
                raise ValueError("provider stream delta must contain text")
            if self.response is not None:
                raise ValueError("provider stream delta must not carry a response")
        else:
            if self.response is None:
                raise ValueError("provider stream final event requires a response")
            if not isinstance(self.response, ProviderResponse):
                raise TypeError("provider stream final event response must be a ProviderResponse")
            if text:
                raise ValueError("provider stream final event must not carry delta text")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", text)

    @classmethod
    def delta(cls, text: str) -> "ProviderStreamEvent":
        return cls(kind=ProviderStreamEventKind.DELTA, text=text)

    @classmethod
    def final(cls, response: ProviderResponse) -> "ProviderStreamEvent":
        return cls(kind=ProviderStreamEventKind.FINAL, response=response)


class AttemptStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ModelAttemptRecord:
    attempt_id: str
    request_id: str
    route_decision_id: str
    attempt_index: int
    status: AttemptStatus | str
    retry_reason: str
    adapter_id: str
    provider: str
    model_id: str
    prompt_id: str
    prompt_version: str
    prompt_digest: str
    started_at_ms: int
    ended_at_ms: int | None = None
    latency_ms: int | None = None
    output_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, AttemptStatus) else AttemptStatus(str(self.status))
        object.__setattr__(self, "status", status)
        if int(self.attempt_index) <= 0:
            raise ValueError("attempt_index must be positive")
        if status is AttemptStatus.STARTED and self.ended_at_ms is not None:
            raise ValueError("started attempt record must not have ended_at_ms")
        if status is not AttemptStatus.STARTED and self.ended_at_ms is None:
            raise ValueError("terminal attempt record requires ended_at_ms")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "request_id": self.request_id,
            "route_decision_id": self.route_decision_id,
            "attempt_index": self.attempt_index,
            "status": self.status.value,
            "retry_reason": self.retry_reason,
            "adapter_id": self.adapter_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_digest": self.prompt_digest,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "latency_ms": self.latency_ms,
            "output_digest": self.output_digest,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }


RetryEvaluator = Callable[
    [ProviderResponse, ProviderAttemptRequest],
    RetryDirective | None | Awaitable[RetryDirective | None],
]


@dataclass(frozen=True, slots=True)
class ModelAttemptRequest:
    request_id: str
    scene_type: str
    cognitive_tier: str
    prompt: PromptRef | RenderedPrompt
    deadline_ms: int
    max_attempts: int = 1
    foreground: bool = True
    retry_evaluator: RetryEvaluator | None = None
    metadata: JsonMapping = field(default_factory=dict)
    model_budget_guard: ModelBudgetGuard | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _clean_required(self.request_id, field_name="request_id"))
        object.__setattr__(self, "scene_type", _clean_required(self.scene_type, field_name="scene_type").upper())
        object.__setattr__(
            self,
            "cognitive_tier",
            _clean_required(self.cognitive_tier, field_name="cognitive_tier").upper(),
        )
        if int(self.deadline_ms) <= 0:
            raise ValueError("deadline_ms must be positive")
        if int(self.max_attempts) <= 0:
            raise ValueError("max_attempts must be positive")
        object.__setattr__(self, "deadline_ms", int(self.deadline_ms))
        object.__setattr__(self, "max_attempts", int(self.max_attempts))
        object.__setattr__(self, "foreground", bool(self.foreground))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if self.model_budget_guard is not None and not isinstance(
            self.model_budget_guard,
            ModelBudgetGuard,
        ):
            raise TypeError("model_budget_guard must be a ModelBudgetGuard")


@dataclass(frozen=True, slots=True)
class ModelAttemptResult:
    request_id: str
    success: bool
    route_decision: RouteDecision
    attempts: tuple[ModelAttemptRecord, ...]
    response: ProviderResponse | None = None
    final_target: ProviderTarget | None = None
    cached: bool = False
    error_code: str | None = None
    error_message: str | None = None
    metadata: JsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.success and self.response is None:
            raise ValueError("successful model attempt result requires a response")
        if not self.success and not self.error_code:
            raise ValueError("failed model attempt result requires error_code")
        object.__setattr__(self, "attempts", tuple(self.attempts))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def content(self) -> str:
        return self.response.content if self.response is not None else ""


class ModelAttemptStreamEventKind(str, Enum):
    """Public result emitted by ``ModelAttemptService.stream_with_result``."""

    DELTA = "DELTA"
    FINAL = "FINAL"


@dataclass(frozen=True, slots=True)
class ModelAttemptStreamEvent:
    """One text delta or the audited final result of a model stream."""

    kind: ModelAttemptStreamEventKind | str
    text: str = ""
    result: ModelAttemptResult | None = None

    def __post_init__(self) -> None:
        try:
            kind = (
                self.kind
                if isinstance(self.kind, ModelAttemptStreamEventKind)
                else ModelAttemptStreamEventKind(str(self.kind).upper())
            )
        except ValueError as exc:
            raise ValueError("model stream event kind is invalid") from exc
        text = str(self.text or "")
        if kind is ModelAttemptStreamEventKind.DELTA:
            if not text or self.result is not None:
                raise ValueError("model stream delta requires text and no result")
        else:
            if self.result is None:
                raise ValueError("model stream final event requires a result")
            if not isinstance(self.result, ModelAttemptResult):
                raise TypeError("model stream final event result must be a ModelAttemptResult")
            if not self.result.success:
                raise ValueError("model stream final event requires a successful result")
            if text:
                raise ValueError("model stream final event must not carry delta text")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", text)

    @classmethod
    def delta(cls, text: str) -> "ModelAttemptStreamEvent":
        return cls(kind=ModelAttemptStreamEventKind.DELTA, text=text)

    @classmethod
    def final(cls, result: ModelAttemptResult) -> "ModelAttemptStreamEvent":
        return cls(kind=ModelAttemptStreamEventKind.FINAL, result=result)


@runtime_checkable
class ProviderAdapter(Protocol):
    """One transport call per method; adapters must not retry internally."""

    @property
    def adapter_id(self) -> str: ...

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse: ...

    def stream(self, request: ProviderAttemptRequest) -> AsyncIterator[str]: ...


@runtime_checkable
class ProviderResultStreamingAdapter(Protocol):
    """Optional capability for streams that end in canonical usage evidence."""

    def stream_with_result(
        self,
        request: ProviderAttemptRequest,
    ) -> AsyncIterator[ProviderStreamEvent]: ...


@runtime_checkable
class AttemptAuditRepository(Protocol):
    async def record_attempt(self, record: ModelAttemptRecord) -> None: ...


@runtime_checkable
class AttemptAuditLookup(Protocol):
    """Read canonical attempt lifecycle records for one logical request.

    Evidence consumers use this narrow port to reopen the audit trail after a
    model call.  It intentionally exposes immutable records rather than raw
    ledger rows, prompts, responses or provider credentials.
    """

    def records_for_request(self, request_id: str) -> tuple[ModelAttemptRecord, ...]: ...


@runtime_checkable
class RoutePolicy(Protocol):
    async def resolve(
        self,
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> RouteDecision: ...


__all__ = [
    "AttemptAuditRepository",
    "AttemptAuditLookup",
    "AttemptStatus",
    "CachePolicy",
    "CacheScope",
    "JsonMapping",
    "MODEL_BUDGET_GUARD_SCHEMA_VERSION",
    "ModelAttemptRecord",
    "ModelAttemptRequest",
    "ModelAttemptResult",
    "ModelAttemptStreamEvent",
    "ModelAttemptStreamEventKind",
    "ModelBudgetGuard",
    "OutputContract",
    "OutputKind",
    "PromptMessage",
    "PromptRef",
    "ProviderAdapter",
    "ProviderAttemptRequest",
    "ProviderResponse",
    "ProviderResultStreamingAdapter",
    "ProviderStreamEvent",
    "ProviderStreamEventKind",
    "ProviderTarget",
    "RenderedPrompt",
    "RetryDirective",
    "RetryEvaluator",
    "RouteDecision",
    "RoutePolicy",
    "rendered_prompt_digest",
    "sha256_digest",
]
