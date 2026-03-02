"""Graph fallback layer: unified degradation handling for timeout, dependency-down, and node-missing.

Provides deterministic fallback routing and degradation result construction
for the graph orchestration pipeline.
"""

from __future__ import annotations

from typing import Any

from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
)
from host_brain.integrations.graph_contract import (
    GraphState,
    STATUS_DEGRADED,
    STATUS_FAILED,
)


# ---------------------------------------------------------------------------
# Fallback routing
# ---------------------------------------------------------------------------

def pick_fallback_node(*, state: GraphState, node_id: str) -> str | None:
    """Pick fallback node from fallback_map for a given node_id."""
    fallback_map = dict(state.get("fallback_map") or {})
    next_node = str(fallback_map.get(node_id) or "").strip()
    return next_node or None


def pick_next_node(*, output: dict[str, Any], default_next: str | None) -> str | None:
    """Pick next node from output or default edge map."""
    explicit = str(output.get("__next_node__") or "").strip()
    if explicit:
        return explicit
    return str(default_next) if default_next else None


# ---------------------------------------------------------------------------
# Degradation handlers
# ---------------------------------------------------------------------------

def handle_dependency_unavailable(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
    detail: str,
) -> GraphState:
    """Handle dependency unavailable: skip node, route to fallback, set DEGRADED."""
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "SKIPPED",
            "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def handle_timeout(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
) -> GraphState:
    """Handle node timeout: mark TIMEOUT in trace, route to fallback, set DEGRADED."""
    detail = f"node timeout: {node_id}"
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "TIMEOUT",
            "reason_code": ORCH_TIMEOUT,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_TIMEOUT,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def handle_execution_error(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
    error: Exception,
) -> GraphState:
    """Handle generic execution failure: mark FAILED in trace, route to fallback."""
    detail = f"node execution failed: {node_id}: {error}"
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "FAILED",
            "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def fail_hard(
    state: GraphState,
    *,
    reason_code: str,
    detail: str,
) -> GraphState:
    """Hard failure: set FAILED status and stop graph execution."""
    return {
        **state,
        "status": STATUS_FAILED,
        "reason_code": reason_code,
        "detail": detail,
        "pending_node_id": None,
    }


def set_finish(state: GraphState) -> GraphState:
    """Mark graph execution as finished (no more pending nodes)."""
    return {
        **state,
        "pending_node_id": None,
    }
