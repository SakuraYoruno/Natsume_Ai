"""Graph contract layer: configuration validation, schema, and shared types.

Provides deterministic graph config validation, node/edge legality checks,
and all shared types/constants for the graph orchestration pipeline.
Model node resolution is in graph_model_resolve.py.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, TypedDict

from host_brain.core.errors import (
    ORCH_EDGE_INVALID,
    ORCH_NODE_MISSING,
)

# ---------------------------------------------------------------------------
# Constants / Stages / Statuses
# ---------------------------------------------------------------------------

S_ORCH_VALIDATE = "S_ORCH_VALIDATE"
S_ORCH_RUN = "S_ORCH_RUN"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

DEFAULT_MODEL_PLANE_DIR = Path("configs/model_plane")

NodeHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
GatewayFactory = Callable[..., Any]


# ---------------------------------------------------------------------------
# Shared State TypedDict
# ---------------------------------------------------------------------------

class GraphState(TypedDict):
    request_id: str
    graph_id: str
    timeout_ms: int
    payload: dict[str, Any]
    trace: list[dict[str, Any]]
    status: str
    reason_code: str | None
    detail: str
    pending_node_id: str | None
    edge_map: dict[str, str]
    fallback_map: dict[str, str]
    node_bindings: dict[str, dict[str, Any]]
    dependency_statuses: dict[str, Any]
    model_nodes: dict[str, dict[str, Any]]
    model_profile_id: str | None
    model_calls: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphRunResult:
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


# ---------------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------------

def validate_graph_config(graph_config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate graph configuration deterministically."""
    graph_id = str(graph_config.get("graph_id") or "").strip()
    version = str(graph_config.get("version") or "").strip()
    entry_node = str(graph_config.get("entry_node") or "").strip()
    nodes = [dict(row) for row in graph_config.get("node_bindings", []) if isinstance(row, Mapping)]
    edges = [dict(row) for row in graph_config.get("edges", []) if isinstance(row, Mapping)]
    fallback_edges = [dict(row) for row in graph_config.get("fallback_edges", []) if isinstance(row, Mapping)]

    node_ids = sorted({str(row.get("node_id") or "").strip() for row in nodes if str(row.get("node_id") or "").strip()})
    issues: list[dict[str, Any]] = []
    if not graph_id:
        issues.append(_issue(reason_code=ORCH_NODE_MISSING, detail="graph_id missing", failed_artifact="graph_id"))
    if not version:
        issues.append(_issue(reason_code=ORCH_NODE_MISSING, detail="version missing", failed_artifact="version"))
    if not entry_node:
        issues.append(_issue(reason_code=ORCH_NODE_MISSING, detail="entry_node missing", failed_artifact="entry_node"))
    if entry_node and entry_node not in node_ids:
        issues.append(
            _issue(
                reason_code=ORCH_NODE_MISSING,
                detail=f"entry_node not defined in node_bindings: {entry_node}",
                failed_artifact=entry_node,
            )
        )

    for edge in edges + fallback_edges:
        edge_from = str(edge.get("from") or "").strip()
        edge_to = str(edge.get("to") or "").strip()
        if not edge_from or not edge_to:
            issues.append(_issue(reason_code=ORCH_EDGE_INVALID, detail="edge requires from/to", failed_artifact="edge"))
            continue
        if edge_from not in node_ids or edge_to not in node_ids:
            issues.append(
                _issue(reason_code=ORCH_EDGE_INVALID, detail=f"edge points to unknown node: {edge_from}->{edge_to}", failed_artifact=f"{edge_from}->{edge_to}")
            )

    profile_id = str(graph_config.get("model_profile_id") or "").strip()
    for node in nodes:
        node_id = str(node.get("node_id") or "").strip()
        handler_id = str(node.get("handler_id") or "").strip()
        model_node_id = str(node.get("model_node_id") or "").strip()
        if not node_id:
            issues.append(_issue(reason_code=ORCH_NODE_MISSING, detail="node_id missing", failed_artifact="node"))
            continue
        if not handler_id and not model_node_id and not profile_id:
            issues.append(
                _issue(reason_code=ORCH_NODE_MISSING, detail=f"handler_id or model_node_id required for node={node_id}", failed_artifact=node_id)
            )

    issues.sort(key=lambda row: (str(row.get("failed_artifact") or ""), str(row.get("reason_code") or ""), str(row.get("detail") or "")))
    return {
        "stage": S_ORCH_VALIDATE,
        "status": (STATUS_FAILED if issues else STATUS_OK),
        "reason_code": (issues[0]["reason_code"] if issues else None),
        "detail": (issues[0]["detail"] if issues else "graph config validated"),
        "summary": {"node_count": len(node_ids), "edge_count": len(edges), "fallback_edge_count": len(fallback_edges), "issue_count": len(issues)},
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Edge / Graph Helpers
# ---------------------------------------------------------------------------

def build_edge_map(edges: list[dict[str, Any]]) -> dict[str, str]:
    """Build from→to edge map from edge list."""
    edge_map: dict[str, str] = {}
    for edge in edges:
        edge_from = str(edge.get("from") or "").strip()
        edge_to = str(edge.get("to") or "").strip()
        if edge_from and edge_to and edge_from not in edge_map:
            edge_map[edge_from] = edge_to
    return edge_map


def pick_model_profile_id(graph_config: Mapping[str, Any]) -> str | None:
    """Extract model_profile_id from graph config."""
    value = str(graph_config.get("model_profile_id") or "").strip()
    return value or None


def build_model_param_hash(*, provider: str, model_id: str, temperature: Any, max_output_tokens: Any, timeout_ms: Any) -> str:
    """Build deterministic SHA-256 hash of model parameters."""
    canonical = {"provider": str(provider), "model_id": str(model_id), "temperature": temperature, "max_output_tokens": max_output_tokens, "timeout_ms": timeout_ms}
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_governance_refs(*, state: GraphState) -> dict[str, str | None]:
    """Extract governance references from state payload."""
    payload = dict(state.get("payload") or {})
    intent = dict(payload.get("intent") or {}) if isinstance(payload.get("intent"), Mapping) else {}

    def pick(*keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                value = intent.get(key)
            text = str(value or "").strip()
            if text:
                return text
        return None

    return {
        "request_id": pick("request_id") or str(state.get("request_id") or "").strip() or None,
        "intent_id": pick("intent_id", "id"),
        "approval_token_ref": pick("approval_token_ref"),
        "evidence_ref": pick("evidence_ref"),
    }


def normalize_dependency_status(raw: Any) -> dict[str, Any]:
    """Normalize a dependency status value to {available, detail}."""
    if isinstance(raw, Mapping):
        return {"available": bool(raw.get("available", True)), "detail": str(raw.get("detail") or "")}
    if isinstance(raw, bool):
        return {"available": bool(raw), "detail": ""}
    return {"available": True, "detail": ""}


def pick_api_key(*, state: GraphState, provider_name: str) -> str | None:
    """Pick API key from dependency_statuses for a given provider."""
    dependency = (state.get("dependency_statuses") or {}).get(provider_name)
    if isinstance(dependency, Mapping):
        raw_key = str(dependency.get("api_key") or "").strip()
        if raw_key:
            return raw_key
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _issue(*, reason_code: str, detail: str, failed_artifact: str) -> dict[str, Any]:
    return {"check_id": S_ORCH_VALIDATE, "reason_code": str(reason_code), "detail": str(detail), "failed_artifact": str(failed_artifact)}
