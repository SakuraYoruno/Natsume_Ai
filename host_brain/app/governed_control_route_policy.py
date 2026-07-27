"""App-layer construction and error translation for control route policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.governed_task_authority import (
    GovernedHostAdapterError,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    ControlRouteExecutionPolicyError,
    freeze_control_route_execution_policy,
    require_control_route_execution_policy,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import HostCommand, HostCommandSurface


def freeze_direct_control_route_policy(
    *,
    surface: HostCommandSurface,
    authority: Mapping[str, Any],
    intent_draft: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Freeze an enabled Host control route or return None for data routes."""

    if surface not in {
        HostCommandSurface.OPERATOR,
        HostCommandSurface.SCHEDULER,
    }:
        return None
    command_id = str(authority.get("operator_command_id") or "").strip()
    target_id = str(
        intent_draft.get("target_id")
        or intent_draft.get("agent_id")
        or ""
    ).strip()
    if not command_id or not target_id:
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_AUTHORITY_INVALID",
            "governed control route lacks an exact command or target",
        )
    try:
        return freeze_control_route_execution_policy(
            surface=surface,
            control_command_id=command_id,
            target_id=target_id,
            review_required=False,
            intent_review_required=surface is HostCommandSurface.OPERATOR,
        )
    except ControlRouteExecutionPolicyError as exc:
        raise GovernedHostAdapterError(exc.reason_code, exc.detail) from exc


def require_governed_control_route_policy(
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    error_factory: Any = GovernedHostAdapterError,
    reason_code: str | None = None,
) -> None:
    """Translate a frozen control policy violation into Host authority error."""

    try:
        require_control_route_execution_policy(
            command=command,
            work_order=work_order,
            graph=graph,
        )
    except ControlRouteExecutionPolicyError as exc:
        raise error_factory(reason_code or exc.reason_code, exc.detail) from exc


__all__ = [
    "freeze_direct_control_route_policy",
    "require_governed_control_route_policy",
]
