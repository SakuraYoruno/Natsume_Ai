"""Read-only reopen of a committed TaskNode verification decision."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import _mapping, _version
from .ports import TaskStorePort


async def reopen_durable_verification(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
    current_node: Mapping[str, Any],
) -> dict[str, Any]:
    raw = task_store.get_node_verification_decision(
        admitted.graph_id,
        admitted.graph_revision,
        admitted.node_id,
        node_version_after=_version(current_node),
    )
    if inspect.isawaitable(raw):
        raw = await raw
    decision = _mapping(raw, field_name="durable verification decision")
    return _mapping(
        decision.get("verification_result"),
        field_name="durable verification result",
    )


__all__ = ["reopen_durable_verification"]
