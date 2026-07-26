"""Immutable node-grant authority recovery for atomic graph re-entry."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .node_execution_projection import NodeExecutionProjection
from .ports import ExecutionProjectionReaderPort


async def restore_prior_parallel_success_grants(
    *,
    admitted: AdmittedExecution,
    statuses: Mapping[str, str],
    grant_refs: dict[str, str],
    node_execution_projection: NodeExecutionProjection,
    projection_reader: ExecutionProjectionReaderPort | None,
) -> None:
    """Restore grant refs for nodes accepted before the current invocation.

    Review continuation renews every still-runnable node grant after a
    potentially long human pause. Nodes accepted before that pause keep the
    grant frozen in their committed worker projection. Only those historical
    nodes are restored; workers executed now must still prove the live grant
    supplied to this invocation.
    """

    if projection_reader is None:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "parallel success re-entry requires a projection reader",
        )
    for binding in admitted.nodes:
        if statuses.get(binding.node_id) != "ACCEPTED":
            continue
        reservation = await node_execution_projection.try_operation(
            admitted.for_node(binding.node_id).reservation_id
        )
        receipt_ref = _text((reservation or {}).get("receipt_ref"))
        if _status(reservation or {}) != "COMMITTED" or not receipt_ref:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode lacks a committed operation projection: "
                f"{binding.node_id}",
            )
        loaded = await _maybe_await(projection_reader.load(receipt_ref))
        if not isinstance(loaded, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode lacks its immutable worker projection: "
                f"{binding.node_id}",
            )
        try:
            projection = validate_worker_projection_payload(
                loaded,
                expected_projection_id=receipt_ref,
            )
        except WorkerProjectionStoreError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted TaskNode worker projection is not canonical: "
                f"{binding.node_id}",
            ) from exc
        historical_ref = _text(projection.get("node_grant_ref"))
        if (
            _text(projection.get("graph_id")) != admitted.graph_id
            or int(projection.get("graph_revision") or 0) != admitted.graph_revision
            or _text(projection.get("node_id")) != binding.node_id
            or not historical_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"accepted TaskNode projection authority drifted: {binding.node_id}",
            )
        grant_refs[binding.node_id] = historical_ref


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["restore_prior_parallel_success_grants"]
