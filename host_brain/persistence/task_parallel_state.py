"""Pure shared normalization for parallel TaskGraph node state snapshots."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.persistence.task_record_codec import _node_record
from host_brain.persistence.task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TaskStoreError,
)


def _normalize_parallel_node_states(
    expected_node_states: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, int]]:
    if not isinstance(expected_node_states, Mapping) or not expected_node_states:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "parallel settlement requires a non-empty complete node snapshot",
        )
    normalized: dict[str, tuple[str, int]] = {}
    for raw_node_id, raw_state in expected_node_states.items():
        node_id = str(raw_node_id or "").strip()
        if not node_id or not isinstance(raw_state, Mapping):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement node snapshot is invalid",
            )
        state = dict(raw_state)
        if set(state) != {"status", "status_version"}:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement node state must contain status and status_version",
            )
        try:
            status = TaskNodeStatus(str(state["status"]))
        except ValueError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"parallel settlement node status is invalid: {node_id}",
            ) from exc
        version = state["status_version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"parallel settlement node version is invalid: {node_id}",
            )
        normalized[node_id] = (status.value, version)
    return dict(sorted(normalized.items()))


def _assert_parallel_node_states(
    node_rows: Sequence[sqlite3.Row],
    expected: Mapping[str, tuple[str, int]],
    *,
    graph_id: str,
    revision: int,
) -> None:
    actual_ids = {str(row["node_id"]) for row in node_rows}
    if actual_ids != set(expected):
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            f"parallel settlement node set drift for {graph_id}@{revision}",
        )
    for row in node_rows:
        node_id = str(row["node_id"])
        status, version = expected[node_id]
        durable = _node_record(row)
        if (
            str(row["status"]) != status
            or int(row["status_version"]) != version
            or str(durable["node"]["status"]) != status
            or int(durable["node"]["status_version"]) != version
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"parallel settlement node state drift: {node_id}",
            )
