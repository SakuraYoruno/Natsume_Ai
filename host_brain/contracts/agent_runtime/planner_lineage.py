"""Canonical lineage binding for planner-authorized child work orders."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    SUB_AGENT_LINEAGE_INVALID,
    ContractViolation,
)


PLANNER_ROUTE_TYPE_METADATA_KEY = "planner_route_type"
PLANNER_TARGET_ID_METADATA_KEY = "planner_target_id"
PLANNER_TARGET_BINDING_METADATA_KEY = "planner_target_binding"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any, *, field_name: str, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be an integer",
        ) from exc
    if parsed < minimum:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be at least {minimum}",
        )
    return parsed


def _semantic_digest(value: Any) -> str:
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "planner lineage binding must be finite JSON data",
        ) from exc
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def build_planner_target_binding(
    *,
    child_work_order_id: str,
    root_work_order_id: str,
    parent_work_order_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    route_type: str,
    target_id: str,
) -> str:
    """Bind a child authorization to one route target on one graph revision."""

    payload = {
        "child_work_order_id": _text(child_work_order_id),
        "root_work_order_id": _text(root_work_order_id),
        "parent_work_order_id": _text(parent_work_order_id),
        "graph_id": _text(graph_id),
        "graph_revision": _positive_int(graph_revision, field_name="graph_revision"),
        "node_id": _text(node_id),
        "route_type": _text(route_type).lower(),
        "target_id": _text(target_id),
    }
    if not all(payload.values()):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "planner target binding requires complete child lineage, route, and target",
        )
    return _semantic_digest(payload)


__all__ = [
    "PLANNER_ROUTE_TYPE_METADATA_KEY",
    "PLANNER_TARGET_BINDING_METADATA_KEY",
    "PLANNER_TARGET_ID_METADATA_KEY",
    "build_planner_target_binding",
]
