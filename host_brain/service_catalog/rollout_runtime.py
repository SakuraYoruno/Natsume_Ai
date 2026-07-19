"""Typed, durable Service Catalog candidate activation runner."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from host_brain.service_catalog.startup_coordinator import (
    MODE_AUDIT,
    STATUS_STARTED,
    ServiceCatalogStartupCoordinator,
)
from host_brain.contracts.service_catalog import (
    CatalogCoordinatorAuthorityV1,
    CatalogMode,
    CatalogRolloutOperationV1,
    ServiceCatalogRolloutError,
    ServiceCatalogRolloutRepository,
    catalog_operation_identity_digest,
)
from host_brain.service_catalog.models import CatalogSnapshot, EndpointSpec
from host_brain.service_catalog.probes import HealthProbe, HealthProbeResult


@dataclass(frozen=True, slots=True)
class CatalogActivationOutcomeV1:
    operation_id: str
    stage: CatalogMode
    status: str
    reason_code: str
    started: bool
    exact_replay: bool
    recovered: bool
    candidate_handle: Any = None


class IdentityCapturingHealthProbe:
    """Health probe wrapper retaining only the last verified evidence payload."""

    def __init__(self, delegate: HealthProbe) -> None:
        self._delegate = delegate
        self._payloads: dict[str, dict[str, Any]] = {}

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        result = self._delegate.inspect(endpoint)
        payload_reader = getattr(self._delegate, "identity_payload", None)
        if callable(payload_reader):
            payload = payload_reader(endpoint.endpoint_id)
            if isinstance(payload, Mapping):
                self._payloads[endpoint.endpoint_id] = dict(payload)
        return result

    def identity_payload(self, endpoint_id: str) -> dict[str, Any] | None:
        payload = self._payloads.get(str(endpoint_id))
        return dict(payload) if payload is not None else None


class DurableServiceCatalogActivationRunner:
    """Operation-first runner; a pending effect is reconciled before start."""

    def __init__(
        self,
        *,
        snapshot: CatalogSnapshot,
        coordinator: ServiceCatalogStartupCoordinator,
        store: ServiceCatalogRolloutRepository,
        authority: CatalogCoordinatorAuthorityV1,
        health_probe: IdentityCapturingHealthProbe,
        candidate_owner_id: str,
        start_candidate: Callable[[], Any],
        stop_candidate: Callable[[Any], None],
        bind_probe: Any,
        rollback_ref: str,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._coordinator = coordinator
        self._store = store
        self._authority = authority
        self._health_probe = health_probe
        self._candidate_owner_id = str(candidate_owner_id)
        self._start_candidate = start_candidate
        self._stop_candidate = stop_candidate
        self._bind_probe = bind_probe
        self._rollback_ref = str(rollback_ref)
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._endpoints = tuple(
            item
            for item in snapshot.endpoints
            if item.enabled and item.owner_id == self._candidate_owner_id
        )
        if not self._endpoints:
            raise ServiceCatalogRolloutError("catalog candidate has no enabled endpoint")

    def activate(self, *, operation_id: str | None = None) -> CatalogActivationOutcomeV1:
        stage = CatalogMode(self._coordinator.mode)
        exact_id = str(operation_id or "catalog-rollout-" + uuid4().hex)
        existing = self._store.get_operation(exact_id)
        if existing is not None:
            self._validate_exact_operation(existing, stage)
            state = self._store.operation_state(exact_id)
            if state in {"STARTED", "FAILED", "ROLLED_BACK"}:
                if state == "STARTED" and not self._verify_health(
                    operation_id=exact_id,
                    stage=stage,
                ):
                    raise ServiceCatalogRolloutError(
                        "exact STARTED replay has no live matching candidate; "
                        "a new operation identity is required"
                    )
                return CatalogActivationOutcomeV1(
                    operation_id=exact_id,
                    stage=stage,
                    status=state,
                    reason_code="SERVICE_CATALOG_EXACT_REPLAY",
                    started=state == "STARTED",
                    exact_replay=True,
                    recovered=state == "STARTED",
                )
            if state == "REGISTERED":
                return self._activate_registered_operation(
                    existing,
                    stage,
                    exact_replay=True,
                    recovered=True,
                )
            return self._reconcile_pending(existing, stage, exact_replay=True)

        pending = self._store.find_pending(
            runtime_scope_id=self._authority.runtime_scope_id,
            candidate_owner_id=self._candidate_owner_id,
            catalog_snapshot_digest=self._snapshot.digest,
            stage=stage,
        )
        if pending is not None:
            self._validate_exact_operation(pending, stage)
            recovered = self._reconcile_pending(pending, stage, exact_replay=False)
            if recovered.status != "ROLLED_BACK":
                return recovered

        operation = CatalogRolloutOperationV1(
            operation_id=exact_id,
            catalog_snapshot_digest=self._snapshot.digest,
            candidate_owner_id=self._candidate_owner_id,
            stage=stage,
            endpoint_ids=tuple(item.endpoint_id for item in self._endpoints),
            rollback_ref=self._rollback_ref,
        )
        registered = self._store.register_operation(
            operation,
            self._authority,
            now_ms=self._clock(),
        )
        return self._activate_registered_operation(
            registered,
            stage,
            exact_replay=False,
            recovered=False,
        )

    def _activate_registered_operation(
        self,
        operation: Mapping[str, Any],
        stage: CatalogMode,
        *,
        exact_replay: bool,
        recovered: bool,
    ) -> CatalogActivationOutcomeV1:
        operation_id = str(operation["operation_id"])
        prepared = self._coordinator.prepare_service_start(
            candidate_owner_id=self._candidate_owner_id
        )
        prepared_payload = {
            "catalog_snapshot_digest": self._snapshot.digest,
            "candidate_owner_id": self._candidate_owner_id,
            "stage": stage.value,
            "endpoint_ids": list(operation["endpoint_ids"]),
            "prepared_status": prepared.status,
            "prepared_reason_code": prepared.reason_code,
            "issue_codes": list(prepared.issue_codes),
            "rollback_ref": self._rollback_ref,
        }
        self._store.append_receipt(
            operation_id,
            event_type="ROLLOUT_PREPARED",
            status="PREPARED" if (prepared.start_allowed or stage is CatalogMode.AUDIT) else "BLOCKED",
            payload=prepared_payload,
            authority=self._authority,
            now_ms=self._clock(),
        )
        if not prepared.start_allowed and stage is not CatalogMode.AUDIT:
            return self._fail(
                operation_id,
                stage,
                reason_code=prepared.reason_code,
                detail="catalog preparation blocked",
                exact_replay=exact_replay,
                recovered=recovered,
            )

        self._store.append_receipt(
            operation_id,
            event_type="ACTIVATION_STARTING",
            status="STARTING",
            payload={
                **prepared_payload,
                "preparation_identity": prepared.as_dict(),
            },
            authority=self._authority,
            now_ms=self._clock(),
        )
        candidate_handle: Any = None
        activation: Any = None
        if stage.value == MODE_AUDIT:
            try:
                candidate_handle = self._start_candidate()
            except Exception as exc:
                if not self._bind_is_free():
                    raise ServiceCatalogRolloutError(
                        "candidate start failed with an unknown listener effect; "
                        "reconciliation is required"
                    ) from exc
                return self._fail(
                    operation_id,
                    stage,
                    reason_code="SERVICE_CATALOG_START_CALLBACK_FAILED",
                    detail=type(exc).__name__,
                    exact_replay=exact_replay,
                    recovered=recovered,
                )
            health_ok = self._verify_health(
                operation_id=operation_id,
                stage=stage,
            )
            activation_status = STATUS_STARTED if health_ok else "HEALTH_FAILED"
        else:
            activation = self._coordinator.activate_candidate(
                prepared,
                start_candidate=self._start_candidate,
                stop_candidate=self._stop_candidate,
            )
            activation_status = activation.status
            candidate_handle = getattr(self._start_candidate, "candidate_handle", None)

        if activation_status != STATUS_STARTED:
            if stage.value == MODE_AUDIT and candidate_handle is not None:
                try:
                    self._stop_candidate(candidate_handle)
                except Exception as exc:
                    raise ServiceCatalogRolloutError(
                        "candidate stop failed; activation effect requires reconciliation"
                    ) from exc
                if not self._bind_is_free():
                    raise ServiceCatalogRolloutError(
                        "candidate stop did not release the listener; "
                        "activation effect requires reconciliation"
                    )
                return self._fail(
                    operation_id,
                    stage,
                    reason_code="SERVICE_CATALOG_POST_HEALTH_FAILED",
                    detail=activation_status,
                    rolled_back=True,
                    exact_replay=exact_replay,
                    recovered=recovered,
                )
            if activation is not None and activation.start_called is True:
                if (
                    activation.stop_called is True
                    and activation.candidate_stopped is True
                    and self._bind_is_free()
                ):
                    return self._fail(
                        operation_id,
                        stage,
                        reason_code="SERVICE_CATALOG_POST_HEALTH_FAILED",
                        detail=activation_status,
                        rolled_back=True,
                        exact_replay=exact_replay,
                        recovered=recovered,
                    )
                if self._bind_is_free():
                    return self._fail(
                        operation_id,
                        stage,
                        reason_code="SERVICE_CATALOG_START_CALLBACK_FAILED",
                        detail=activation_status,
                        exact_replay=exact_replay,
                        recovered=recovered,
                    )
                raise ServiceCatalogRolloutError(
                    "candidate stop is unverified and the listener remains occupied; "
                    "activation effect requires reconciliation"
                )
            return self._fail(
                operation_id,
                stage,
                reason_code=str(getattr(activation, "reason_code", activation_status)),
                detail=activation_status,
                exact_replay=exact_replay,
                recovered=recovered,
            )

        try:
            terminal_payload = self._terminal_payload(
                stage,
                operation_id=operation_id,
            )
            self._store.append_receipt(
                operation_id,
                event_type="ACTIVATION_STARTED",
                status="STARTED",
                payload=terminal_payload,
                authority=self._authority,
                now_ms=self._clock(),
            )
        except BaseException:
            # The process cannot know whether a failed durable append happened
            # before or after SQLite committed.  Stop the candidate and leave
            # the operation for restart reconciliation.  A later coordinator
            # will reopen the receipt/head; it must never keep an unrecorded
            # listener alive or blindly execute the start callback again.
            if candidate_handle is not None:
                try:
                    self._stop_candidate(candidate_handle)
                except Exception:
                    pass
            raise
        return CatalogActivationOutcomeV1(
            operation_id=operation_id,
            stage=stage,
            status="STARTED",
            reason_code="SERVICE_CATALOG_CANDIDATE_STARTED",
            started=True,
            exact_replay=exact_replay,
            recovered=recovered,
            candidate_handle=candidate_handle,
        )

    def _reconcile_pending(
        self,
        operation: Mapping[str, Any],
        stage: CatalogMode,
        *,
        exact_replay: bool,
    ) -> CatalogActivationOutcomeV1:
        operation_id = str(operation["operation_id"])
        state = self._store.operation_state(operation_id)
        if state == "BLOCKED":
            return self._fail(
                operation_id,
                stage,
                reason_code="SERVICE_CATALOG_BLOCKED_PREPARATION_RECOVERED",
                detail="blocked preparation had no candidate effect",
            )
        if state == "PREPARED":
            # A crash after durable preparation but before STARTING proves no
            # candidate effect was authorized.  Close the legal state-machine
            # edge first, then reconcile the absent effect as ROLLED_BACK.
            self._store.append_receipt(
                operation_id,
                event_type="ACTIVATION_STARTING",
                status="STARTING",
                payload={
                    "catalog_snapshot_digest": self._snapshot.digest,
                    "candidate_owner_id": self._candidate_owner_id,
                    "stage": stage.value,
                    "endpoint_ids": list(operation["endpoint_ids"]),
                    "rollback_ref": self._rollback_ref,
                    "recovery_boundary": "PREPARED_BEFORE_EFFECT",
                },
                authority=self._authority,
                now_ms=self._clock(),
            )
        if self._verify_health(operation_id=operation_id, stage=stage):
            self._store.append_receipt(
                operation_id,
                event_type="ACTIVATION_STARTED",
                status="STARTED",
                payload=self._terminal_payload(stage, operation_id=operation_id),
                authority=self._authority,
                now_ms=self._clock(),
            )
            return CatalogActivationOutcomeV1(
                operation_id=operation_id,
                stage=stage,
                status="STARTED",
                reason_code="SERVICE_CATALOG_PENDING_EFFECT_RECONCILED",
                started=True,
                exact_replay=exact_replay,
                recovered=True,
            )
        bind_observations = tuple(self._bind_probe.inspect(item) for item in self._endpoints)
        if all(item.available is True for item in bind_observations):
            self._store.append_receipt(
                operation_id,
                event_type="ACTIVATION_ROLLED_BACK",
                status="ROLLED_BACK",
                payload={
                    "catalog_snapshot_digest": self._snapshot.digest,
                    "candidate_owner_id": self._candidate_owner_id,
                    "stage": stage.value,
                    "rollback_ref": self._rollback_ref,
                    "reason_code": "SERVICE_CATALOG_PENDING_EFFECT_NOT_PRESENT",
                },
                authority=self._authority,
                now_ms=self._clock(),
            )
            return CatalogActivationOutcomeV1(
                operation_id=operation_id,
                stage=stage,
                status="ROLLED_BACK",
                reason_code="SERVICE_CATALOG_PENDING_EFFECT_NOT_PRESENT",
                started=False,
                exact_replay=exact_replay,
                recovered=True,
            )
        raise ServiceCatalogRolloutError(
            "pending activation requires reconciliation; listener identity is unknown"
        )

    def _verify_health(self, *, operation_id: str, stage: CatalogMode) -> bool:
        for endpoint in self._endpoints:
            result = self._health_probe.inspect(endpoint)
            expected = endpoint.health
            if (
                result.healthy is not True
                or result.endpoint_id != endpoint.endpoint_id
                or result.owner_id != expected.expected_owner_id
                or result.protocol_id != expected.expected_protocol
                or result.protocol_version != expected.expected_protocol_version
            ):
                return False
            payload = self._health_probe.identity_payload(endpoint.endpoint_id)
            activation_identity = (
                payload.get("activation_identity")
                if isinstance(payload, Mapping)
                else None
            )
            if not isinstance(activation_identity, Mapping) or (
                str(activation_identity.get("operation_id_digest") or "")
                != catalog_operation_identity_digest(operation_id)
                or str(activation_identity.get("catalog_snapshot_digest") or "")
                != self._snapshot.digest
                or str(activation_identity.get("candidate_owner_id") or "")
                != self._candidate_owner_id
                or str(activation_identity.get("stage") or "") != stage.value
            ):
                return False
        return True

    def _terminal_payload(
        self,
        stage: CatalogMode,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        endpoint = self._endpoints[0]
        payload = self._health_probe.identity_payload(endpoint.endpoint_id)
        if not isinstance(payload, Mapping):
            raise ServiceCatalogRolloutError("exact Host health identity is unavailable")
        health_identity = payload.get("health_identity")
        runtime_authority = payload.get("runtime_authority")
        activation_identity = payload.get("activation_identity")
        if (
            not isinstance(health_identity, Mapping)
            or not isinstance(runtime_authority, Mapping)
            or not isinstance(activation_identity, Mapping)
        ):
            raise ServiceCatalogRolloutError("Host health authority identity is incomplete")
        expected_operation_digest = catalog_operation_identity_digest(operation_id)
        if (
            str(activation_identity.get("operation_id_digest") or "")
            != expected_operation_digest
            or str(activation_identity.get("catalog_snapshot_digest") or "")
            != self._snapshot.digest
            or str(activation_identity.get("candidate_owner_id") or "")
            != self._candidate_owner_id
            or str(activation_identity.get("stage") or "") != stage.value
        ):
            raise ServiceCatalogRolloutError("Host activation identity drift")
        return {
            "catalog_snapshot_digest": self._snapshot.digest,
            "candidate_owner_id": self._candidate_owner_id,
            "stage": stage.value,
            "health_identity": dict(health_identity),
            "runtime_authority": dict(runtime_authority),
            "activation_identity": {
                "operation_id": operation_id,
                "operation_id_digest": expected_operation_digest,
                "catalog_snapshot_digest": self._snapshot.digest,
                "candidate_owner_id": self._candidate_owner_id,
                "stage": stage.value,
            },
            "rollback_ref": self._rollback_ref,
        }

    def _bind_is_free(self) -> bool:
        try:
            observations = tuple(self._bind_probe.inspect(item) for item in self._endpoints)
        except Exception:
            return False
        return bool(observations) and all(item.available is True for item in observations)

    def _fail(
        self,
        operation_id: str,
        stage: CatalogMode,
        *,
        reason_code: str,
        detail: str,
        rolled_back: bool = False,
        exact_replay: bool = False,
        recovered: bool = False,
    ) -> CatalogActivationOutcomeV1:
        event_type = "ACTIVATION_ROLLED_BACK" if rolled_back else "ACTIVATION_FAILED"
        status = "ROLLED_BACK" if rolled_back else "FAILED"
        self._store.append_receipt(
            operation_id,
            event_type=event_type,
            status=status,
            payload={
                "catalog_snapshot_digest": self._snapshot.digest,
                "candidate_owner_id": self._candidate_owner_id,
                "stage": stage.value,
                "reason_code": str(reason_code),
                "detail": str(detail),
                "rollback_ref": self._rollback_ref,
            },
            authority=self._authority,
            now_ms=self._clock(),
        )
        return CatalogActivationOutcomeV1(
            operation_id=operation_id,
            stage=stage,
            status=status,
            reason_code=str(reason_code),
            started=False,
            exact_replay=exact_replay,
            recovered=recovered,
        )

    def _validate_exact_operation(self, operation: Mapping[str, Any], stage: CatalogMode) -> None:
        if (
            str(operation["runtime_scope_id"]) != self._authority.runtime_scope_id
            or str(operation["catalog_snapshot_digest"]) != self._snapshot.digest
            or str(operation["candidate_owner_id"]) != self._candidate_owner_id
            or str(operation["stage"]) != stage.value
            or str(operation["rollback_ref"]) != self._rollback_ref
            or tuple(operation["endpoint_ids"])
            != tuple(sorted(item.endpoint_id for item in self._endpoints))
        ):
            raise ServiceCatalogRolloutError("operation replay identity drift")


__all__ = [
    "CatalogActivationOutcomeV1",
    "DurableServiceCatalogActivationRunner",
    "IdentityCapturingHealthProbe",
]
