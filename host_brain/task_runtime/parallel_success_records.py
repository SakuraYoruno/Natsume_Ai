"""Pure frozen route authority for 0091 parallel success."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedTaskNode
from .coordinator_records import _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .worker_registry import GovernedWorkerRoute


def _projection_route_authority(
    *,
    binding: AdmittedTaskNode,
    work_order: Any,
    command: HostCommand,
) -> tuple[str, str]:
    """Resolve a worker projection route from frozen graph/work-order facts."""

    try:
        frozen = GovernedWorkerRoute.parse(binding.worker_route)
    except Exception as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode route is invalid: {binding.node_id}",
        ) from exc
    expected_target = _text(frozen.target_id or command.target_id)
    if not expected_target:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode target is missing: {binding.node_id}",
        )

    metadata = getattr(work_order, "metadata", {})
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    planning_lineage = metadata.get("planning_lineage")
    planning_lineage = (
        dict(planning_lineage) if isinstance(planning_lineage, Mapping) else {}
    )
    raw_bindings = planning_lineage.get("node_bindings")
    if isinstance(raw_bindings, list) and raw_bindings:
        matches = [
            dict(candidate)
            for candidate in raw_bindings
            if isinstance(candidate, Mapping)
            and _text(candidate.get("node_id")) == binding.node_id
        ]
        if len(matches) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder node binding is ambiguous: {binding.node_id}",
            )
        work_order_route = _text(matches[0].get("worker_route")).lower()
        work_order_target = _text(matches[0].get("target_id"))
        if (
            work_order_route != frozen.kind
            or work_order_target != expected_target
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder route binding drifted: {binding.node_id}",
            )
    return frozen.kind, expected_target


__all__ = ["_projection_route_authority"]
# End of module.
