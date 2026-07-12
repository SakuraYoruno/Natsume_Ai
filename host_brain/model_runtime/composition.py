"""Composition root for the unique model runtime service."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Iterable

from host_brain.core.trace_redaction import sanitize_public_payload

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
    """Durable adapter to the canonical model-attempt audit journal."""

    def __init__(self, ledger: Any) -> None:
        writer = getattr(ledger, "log_model_attempt", None)
        if not callable(writer):
            raise TypeError("ledger must expose log_model_attempt(row)")
        self._writer = writer

    async def record_attempt(self, record: ModelAttemptRecord) -> None:
        payload = record.as_dict()
        payload["metadata"] = sanitize_public_payload(payload.get("metadata") or {})
        self._writer(payload)


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
