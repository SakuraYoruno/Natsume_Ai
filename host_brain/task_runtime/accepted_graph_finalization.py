"""One durable Graph completion path, including Acceptance Effects."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .acceptance_effects import compile_graph_acceptance_effects
from .admission import AdmittedExecution
from .coordinator_receipts import _digest_from_receipt_ref
from .coordinator_records import _status, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .operation_checkpoint_validation import (
    assert_canonical_projection_identity,
    assert_exact_committed_checkpoint,
    expected_route_authority,
)
from .ports import ExecutionProjectionReaderPort, TaskStorePort


class AcceptedGraphFinalizationRuntime:
    """Complete an all-ACCEPTED Graph through the same effect UoW."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        node_lifecycle: DurableNodeLifecycleRuntime,
        projection_reader: ExecutionProjectionReaderPort | None,
        acceptance_effects_enabled: bool,
    ) -> None:
        self._store = task_store
        self._node_lifecycle = node_lifecycle
        self._projection_reader = projection_reader
        self._effects_enabled = bool(acceptance_effects_enabled)

    async def complete(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        graph: Mapping[str, Any],
        node_grant_ref: str | None,
        worker_commit: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        graph_status = _status(graph)
        if graph_status == "COMPLETED":
            return dict(graph)
        if graph_status != "ACTIVE":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"accepted Graph cannot complete from {graph_status}",
            )

        commit = dict(worker_commit) if isinstance(worker_commit, Mapping) else None
        attempt_ref = _text(attempt_id)
        if self._effects_enabled and commit is None:
            commit, attempt_ref = await self._load_accepted_checkpoint(
                admitted=admitted,
                command=command,
                node_grant_ref=node_grant_ref,
            )
        if self._effects_enabled:
            if commit is None or not attempt_ref:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted Graph lacks its durable effect checkpoint",
                )
            effects = compile_graph_acceptance_effects(
                command=command,
                work_order=work_order,
                node_id=admitted.node_id,
                attempt_id=attempt_ref,
                worker_commit=commit,
            )
            return await self._node_lifecycle.transition_graph_with_acceptance_effects(
                admitted,
                graph,
                effect_intents=effects,
            )
        return await self._node_lifecycle.transition_graph(
            admitted,
            graph,
            "COMPLETED",
        )

    async def _load_accepted_checkpoint(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        node_grant_ref: str | None,
    ) -> tuple[dict[str, Any], str]:
        if self._projection_reader is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph has no durable projection reader",
            )
        raw_checkpoint = await _maybe_await(
            self._store.get_committed_operation_checkpoint(
                admitted.reservation_id
            )
        )
        if not isinstance(raw_checkpoint, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph has no exact Operation checkpoint",
            )
        raw_reservation = raw_checkpoint.get("reservation")
        raw_attempt = raw_checkpoint.get("attempt")
        raw_lease = raw_checkpoint.get("lease")
        if not all(
            isinstance(item, Mapping)
            for item in (raw_reservation, raw_attempt, raw_lease)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph Operation checkpoint is incomplete",
            )
        reservation = dict(raw_reservation)
        receipt_ref = _text((reservation or {}).get("receipt_ref"))
        if _status(reservation or {}) != "COMMITTED" or not receipt_ref:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph lacks its committed Operation receipt",
            )
        receipt_digest = _digest_from_receipt_ref(receipt_ref)
        if receipt_digest is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph receipt has no canonical digest identity",
            )
        checkpoint = assert_exact_committed_checkpoint(
            raw_checkpoint,
            reservation_id=admitted.reservation_id,
            business_scope=admitted.business_scope,
            operation_fingerprint=admitted.operation_fingerprint,
            attempt_id=_text(raw_attempt.get("attempt_id")),
            lease_id=_text(raw_lease.get("lease_id")),
            owner_id=_text(raw_lease.get("owner_id")),
            fencing_token=_strict_positive_int(
                raw_lease.get("fencing_token"),
                "historical Lease fence",
            ),
            expected_version=_strict_non_negative_int(
                reservation.get("status_version"),
                "reservation status version",
            )
            - 1,
            receipt_ref=receipt_ref,
            checkpoint_digest_ref=(
                f"sha256://execution/{receipt_digest.split(':', 1)[1]}"
            ),
        )
        latest_attempt = checkpoint["attempt"]
        attempt_id = _text(latest_attempt.get("attempt_id"))
        if (
            _status(latest_attempt) != "SUCCEEDED"
            or _text(latest_attempt.get("graph_id")) != admitted.graph_id
            or _strict_positive_int(
                latest_attempt.get("graph_revision"),
                "Attempt graph revision",
            )
            != admitted.graph_revision
            or _text(latest_attempt.get("node_id")) != admitted.node_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph checkpoint Attempt is not the exact SUCCEEDED node Attempt",
            )
        loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
        if not isinstance(loaded, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph receipt has no durable projection",
            )
        try:
            canonical = validate_worker_projection_payload(
                loaded,
                expected_projection_id=receipt_ref,
            )
        except WorkerProjectionStoreError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted Graph projection is not canonical",
            ) from exc
        expected_route, expected_target = expected_route_authority(
            admitted=admitted,
            command=command,
            node_grant_ref=node_grant_ref,
        )
        assert_canonical_projection_identity(
            canonical,
            admitted=admitted,
            command=command,
            latest_attempt=latest_attempt,
            expected_route=expected_route,
            expected_target=expected_target,
            node_grant_ref=node_grant_ref,
        )
        return {**canonical, "receipt_ref": receipt_ref}, attempt_id


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _strict_non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"accepted Graph checkpoint has invalid {field_name}",
        )
    return value


def _strict_positive_int(value: Any, field_name: str) -> int:
    normalized = _strict_non_negative_int(value, field_name)
    if normalized < 1:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"accepted Graph checkpoint has invalid {field_name}",
        )
    return normalized


__all__ = ["AcceptedGraphFinalizationRuntime"]
