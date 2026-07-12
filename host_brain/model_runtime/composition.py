"""Composition root for the unique model runtime service."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Iterable

from host_brain.model_runtime.contracts import (
    AttemptAuditRepository,
    ModelAttemptRecord,
    ProviderAdapter,
    RoutePolicy,
)
from host_brain.model_runtime.model_attempt_service import ModelAttemptService
from host_brain.model_runtime.prompt_registry import PromptRegistry
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry
from host_brain.model_runtime.route_policy import RejectingRoutePolicy


class InMemoryAttemptAuditRepository:
    """Typed test/development repository; production should inject a ledger."""

    def __init__(self) -> None:
        self._records: list[ModelAttemptRecord] = []
        self._lock = threading.RLock()

    async def record_attempt(self, record: ModelAttemptRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[ModelAttemptRecord, ...]:
        with self._lock:
            return tuple(self._records)


class LedgerAttemptAuditRepository:
    """Adapter to the existing typed ``llm_node_call_log`` ledger API.

    Migration 0080 can later replace this projection with a dedicated attempt
    table without changing ``ModelAttemptService``.
    """

    def __init__(self, ledger: Any) -> None:
        writer = getattr(ledger, "log_llm_node_call", None)
        if not callable(writer):
            raise TypeError("ledger must expose log_llm_node_call(row)")
        self._ledger = ledger

    async def record_attempt(self, record: ModelAttemptRecord) -> None:
        reason = record.error_code or record.retry_reason
        self._ledger.log_llm_node_call(
            {
                "request_id": record.request_id,
                "node_id": f"model_attempt:{record.attempt_id}",
                "provider": record.provider,
                "model_id": record.model_id,
                "status": record.status.value.upper(),
                "reason_code": reason,
                "latency_ms": record.latency_ms,
                "input_digest": record.prompt_digest,
                "output_digest": record.output_digest,
                "ts_ms": record.ended_at_ms or record.started_at_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class ModelRuntimeComposition:
    prompt_registry: PromptRegistry
    route_policy: RoutePolicy
    provider_adapters: ProviderAdapterRegistry
    audit_repository: AttemptAuditRepository
    attempt_service: ModelAttemptService


def build_model_runtime_composition(
    *,
    prompt_registry: PromptRegistry | None = None,
    route_policy: RoutePolicy | None = None,
    provider_adapters: ProviderAdapterRegistry | Iterable[ProviderAdapter] | None = None,
    audit_repository: AttemptAuditRepository | None = None,
    ledger: Any | None = None,
) -> ModelRuntimeComposition:
    """Build one internally consistent model runtime object graph.

    A rejecting route is the safe default. Concrete provider transports and
    the route policy are installed by the application composition layer.
    """

    if audit_repository is not None and ledger is not None:
        raise ValueError("provide audit_repository or ledger, not both")
    registry = prompt_registry or PromptRegistry()
    resolved_route_policy = route_policy or RejectingRoutePolicy()
    if provider_adapters is None:
        adapters = ProviderAdapterRegistry()
    elif isinstance(provider_adapters, ProviderAdapterRegistry):
        adapters = provider_adapters
    else:
        adapters = ProviderAdapterRegistry(provider_adapters)
    audit = audit_repository
    if audit is None:
        audit = LedgerAttemptAuditRepository(ledger) if ledger is not None else InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=registry,
        route_policy=resolved_route_policy,
        provider_adapters=adapters,
        audit_repository=audit,
    )
    return ModelRuntimeComposition(
        prompt_registry=registry,
        route_policy=resolved_route_policy,
        provider_adapters=adapters,
        audit_repository=audit,
        attempt_service=service,
    )


__all__ = [
    "InMemoryAttemptAuditRepository",
    "LedgerAttemptAuditRepository",
    "ModelRuntimeComposition",
    "build_model_runtime_composition",
]
