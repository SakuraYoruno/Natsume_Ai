"""Composition root for the unique model runtime service."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Iterable, Mapping

from host_brain.core.trace_redaction import sanitize_public_payload

from host_brain.model_runtime.contracts import (
    AttemptAuditLookup,
    AttemptAuditRepository,
    AttemptStatus,
    ModelAttemptRecord,
    ProviderAdapter,
    RoutePolicy,
)
from host_brain.model_runtime.model_attempt_service import ModelAttemptService
from host_brain.model_runtime.prompt_registry import PromptRegistry
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry
from host_brain.model_runtime.route_catalog import ServerRouteCatalog
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

    def records_for_request(self, request_id: str) -> tuple[ModelAttemptRecord, ...]:
        """Return the test/development audit trail for one request identity."""

        normalized = str(request_id or "").strip()
        if not normalized:
            return ()
        with self._lock:
            return tuple(record for record in self._records if record.request_id == normalized)


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


class LedgerAttemptAuditLookup:
    """Reopen immutable model-attempt rows from the canonical audit ledger."""

    def __init__(self, ledger: Any) -> None:
        reader = getattr(ledger, "fetch_by_request_id", None)
        if not callable(reader):
            raise TypeError("ledger must expose fetch_by_request_id(table, request_id)")
        self._reader = reader

    def records_for_request(self, request_id: str) -> tuple[ModelAttemptRecord, ...]:
        normalized = str(request_id or "").strip()
        if not normalized:
            raise ValueError("request_id must be non-empty")
        rows = self._reader("model_attempt_log", normalized)
        if not isinstance(rows, Iterable):
            raise TypeError("model attempt audit reader returned a non-iterable result")
        records: list[ModelAttemptRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("model attempt audit row must be a mapping")
            if str(row.get("request_id") or "").strip() != normalized:
                raise ValueError("model attempt audit lookup returned a request identity drift")
            try:
                records.append(
                    ModelAttemptRecord(
                        attempt_id=str(row.get("attempt_id") or ""),
                        request_id=normalized,
                        route_decision_id=str(row.get("route_decision_id") or ""),
                        attempt_index=int(row.get("attempt_index") or 0),
                        status=AttemptStatus(str(row.get("status") or "")),
                        retry_reason=str(row.get("retry_reason") or ""),
                        adapter_id=str(row.get("adapter_id") or ""),
                        provider=str(row.get("provider") or ""),
                        model_id=str(row.get("model_id") or ""),
                        prompt_id=str(row.get("prompt_id") or ""),
                        prompt_version=str(row.get("prompt_version") or ""),
                        prompt_digest=str(row.get("prompt_digest") or ""),
                        started_at_ms=int(row.get("started_at_ms") or 0),
                        ended_at_ms=(
                            None
                            if row.get("ended_at_ms") is None
                            else int(row.get("ended_at_ms"))
                        ),
                        latency_ms=(
                            None if row.get("latency_ms") is None else int(row.get("latency_ms"))
                        ),
                        output_digest=(
                            None
                            if row.get("output_digest") is None
                            else str(row.get("output_digest"))
                        ),
                        error_code=(
                            None if row.get("error_code") is None else str(row.get("error_code"))
                        ),
                        error_message=(
                            None
                            if row.get("error_message") is None
                            else str(row.get("error_message"))
                        ),
                        metadata=dict(row.get("metadata") or {}),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("model attempt audit row is invalid") from exc
        return tuple(records)


@dataclass(frozen=True, slots=True)
class ModelRuntimeComposition:
    prompt_registry: PromptRegistry
    route_policy: RoutePolicy
    route_catalog: ServerRouteCatalog | None
    provider_adapters: ProviderAdapterRegistry
    audit_repository: AttemptAuditRepository
    attempt_service: ModelAttemptService


def build_model_runtime_composition(
    *,
    prompt_registry: PromptRegistry | None = None,
    route_policy: RoutePolicy | None = None,
    route_catalog: ServerRouteCatalog | None = None,
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
        route_catalog=route_catalog,
        provider_adapters=adapters,
        audit_repository=audit,
        attempt_service=service,
    )


__all__ = [
    "InMemoryAttemptAuditRepository",
    "LedgerAttemptAuditRepository",
    "LedgerAttemptAuditLookup",
    "ModelRuntimeComposition",
    "build_model_runtime_composition",
]
