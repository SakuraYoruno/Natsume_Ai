"""Graph orchestration contract for deterministic runtime assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_EDGE_INVALID,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
)
from host_brain.core.ids import now_ms


S_ORCH_VALIDATE = "S_ORCH_VALIDATE"
S_ORCH_RUN = "S_ORCH_RUN"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

NodeHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class GraphRunResult:
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class GraphRuntimeContract:
    """Deterministic graph orchestration runtime with fallback edges."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        default_timeout_ms: int = 5_000,
    ) -> None:
        self.ledger = ledger
        self.default_timeout_ms = int(default_timeout_ms)
        self._handlers: dict[str, NodeHandler] = {}

    def register_node_handler(self, handler_id: str, handler: NodeHandler) -> None:
        key = str(handler_id or "").strip()
        if not key:
            raise ValueError("handler_id must be non-empty")
        self._handlers[key] = handler

    def run_graph(
        self,
        *,
        request_id: str,
        graph_config: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
        dependency_statuses: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> GraphRunResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        validation = validate_graph_config(graph_config)
        if validation["status"] == STATUS_FAILED:
            return self._finalize(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code=str(validation["reason_code"]),
                stage=S_ORCH_VALIDATE,
                detail=str(validation["detail"]),
                graph_config=graph_config,
                graph_trace=[],
                payload=dict(payload or {}),
                ts_ms=now_value,
            )

        timeout_limit = int(timeout_ms if timeout_ms is not None else self.default_timeout_ms)
        dependencies = dict(dependency_statuses or {})
        node_bindings = {str(node["node_id"]): dict(node) for node in graph_config.get("node_bindings", [])}
        edge_map = _build_edge_map(graph_config.get("edges", []))
        fallback_map = _build_edge_map(graph_config.get("fallback_edges", []))

        current_node = str(graph_config.get("entry_node") or "")
        runtime_payload = dict(payload or {})
        trace: list[dict[str, Any]] = []
        reason_code: str | None = None
        status = STATUS_OK
        detail = "graph execution complete"

        while current_node:
            binding = node_bindings.get(current_node)
            if binding is None:
                return self._finalize(
                    request_id=request_id,
                    status=STATUS_FAILED,
                    reason_code=ORCH_NODE_MISSING,
                    stage=S_ORCH_RUN,
                    detail=f"node binding missing: {current_node}",
                    graph_config=graph_config,
                    graph_trace=trace,
                    payload=runtime_payload,
                    ts_ms=now_value,
                )

            dep_status = _normalize_dependency_status(dependencies.get(current_node))
            if not dep_status["available"]:
                reason_code = ORCH_DEPENDENCY_UNAVAILABLE
                status = STATUS_DEGRADED
                detail = dep_status["detail"] or f"dependency unavailable for node={current_node}"
                trace.append(
                    {
                        "node_id": current_node,
                        "handler_id": str(binding.get("handler_id") or ""),
                        "status": "SKIPPED",
                        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
                        "detail": detail,
                    }
                )
                next_node = fallback_map.get(current_node)
                if not next_node:
                    break
                current_node = next_node
                continue

            handler_id = str(binding.get("handler_id") or "")
            handler = self._handlers.get(handler_id)
            if handler is None:
                return self._finalize(
                    request_id=request_id,
                    status=STATUS_FAILED,
                    reason_code=ORCH_NODE_MISSING,
                    stage=S_ORCH_RUN,
                    detail=f"handler missing for node={current_node}, handler_id={handler_id}",
                    graph_config=graph_config,
                    graph_trace=trace,
                    payload=runtime_payload,
                    ts_ms=now_value,
                )

            try:
                output = handler(
                    runtime_payload,
                    {
                        "request_id": request_id,
                        "node_id": current_node,
                        "graph_id": str(graph_config.get("graph_id") or ""),
                        "timeout_ms": timeout_limit,
                    },
                )
            except TimeoutError:
                reason_code = ORCH_TIMEOUT
                status = STATUS_DEGRADED
                detail = f"node timeout: {current_node}"
                trace.append(
                    {
                        "node_id": current_node,
                        "handler_id": handler_id,
                        "status": "TIMEOUT",
                        "reason_code": ORCH_TIMEOUT,
                        "detail": detail,
                    }
                )
                next_node = fallback_map.get(current_node)
                if not next_node:
                    break
                current_node = next_node
                continue
            except Exception as exc:
                reason_code = ORCH_DEPENDENCY_UNAVAILABLE
                status = STATUS_DEGRADED
                detail = f"node execution failed: {current_node}: {exc}"
                trace.append(
                    {
                        "node_id": current_node,
                        "handler_id": handler_id,
                        "status": "FAILED",
                        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
                        "detail": detail,
                    }
                )
                next_node = fallback_map.get(current_node)
                if not next_node:
                    break
                current_node = next_node
                continue

            runtime_payload = dict(output or runtime_payload)
            trace.append(
                {
                    "node_id": current_node,
                    "handler_id": handler_id,
                    "status": "OK",
                    "reason_code": None,
                    "detail": "node executed",
                }
            )

            current_node = _pick_next_node(
                output=runtime_payload,
                default_next=edge_map.get(current_node),
            )

        if status == STATUS_OK:
            reason_code = None
        return self._finalize(
            request_id=request_id,
            status=status,
            reason_code=reason_code,
            stage=S_ORCH_RUN,
            detail=detail,
            graph_config=graph_config,
            graph_trace=trace,
            payload=runtime_payload,
            ts_ms=now_value,
        )

    def _finalize(
        self,
        *,
        request_id: str,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        graph_config: Mapping[str, Any],
        graph_trace: list[dict[str, Any]],
        payload: dict[str, Any],
        ts_ms: int,
    ) -> GraphRunResult:
        if reason_code is not None:
            self._log_error(
                request_id=request_id,
                stage=stage,
                reason_code=reason_code,
                detail=detail,
            )
        output = {
            "request_id": request_id,
            "graph_id": str(graph_config.get("graph_id") or ""),
            "graph_version": str(graph_config.get("version") or ""),
            "graph_status": status,
            "reason_code": reason_code,
            "stage": stage,
            "replay_ref": f"request::{request_id}",
            "graph_trace": graph_trace,
            "payload": payload,
            "ts_ms": int(ts_ms),
        }
        return GraphRunResult(
            status=status,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            output=output,
        )

    def _log_error(self, *, request_id: str, stage: str, reason_code: str, detail: str) -> None:
        if self.ledger is None:
            return
        self.ledger.log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )


def validate_graph_config(graph_config: Mapping[str, Any]) -> dict[str, Any]:
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
            issues.append(
                _issue(
                    reason_code=ORCH_EDGE_INVALID,
                    detail="edge requires from/to",
                    failed_artifact="edge",
                )
            )
            continue
        if edge_from not in node_ids or edge_to not in node_ids:
            issues.append(
                _issue(
                    reason_code=ORCH_EDGE_INVALID,
                    detail=f"edge points to unknown node: {edge_from}->{edge_to}",
                    failed_artifact=f"{edge_from}->{edge_to}",
                )
            )

    for node in nodes:
        handler_id = str(node.get("handler_id") or "").strip()
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            issues.append(_issue(reason_code=ORCH_NODE_MISSING, detail="node_id missing", failed_artifact="node"))
            continue
        if not handler_id:
            issues.append(
                _issue(
                    reason_code=ORCH_NODE_MISSING,
                    detail=f"handler_id missing for node={node_id}",
                    failed_artifact=node_id,
                )
            )

    issues.sort(
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            str(row.get("reason_code") or ""),
            str(row.get("detail") or ""),
        )
    )
    return {
        "stage": S_ORCH_VALIDATE,
        "status": (STATUS_FAILED if issues else STATUS_OK),
        "reason_code": (issues[0]["reason_code"] if issues else None),
        "detail": (issues[0]["detail"] if issues else "graph config validated"),
        "summary": {
            "node_count": len(node_ids),
            "edge_count": len(edges),
            "fallback_edge_count": len(fallback_edges),
            "issue_count": len(issues),
        },
        "issues": issues,
    }


def _normalize_dependency_status(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return {
            "available": bool(raw.get("available", True)),
            "detail": str(raw.get("detail") or ""),
        }
    if isinstance(raw, bool):
        return {"available": bool(raw), "detail": ""}
    return {"available": True, "detail": ""}


def _build_edge_map(edges: list[dict[str, Any]]) -> dict[str, str]:
    edge_map: dict[str, str] = {}
    for edge in edges:
        edge_from = str(edge.get("from") or "").strip()
        edge_to = str(edge.get("to") or "").strip()
        if edge_from and edge_to and edge_from not in edge_map:
            edge_map[edge_from] = edge_to
    return edge_map


def _pick_next_node(*, output: Mapping[str, Any], default_next: str | None) -> str | None:
    explicit = str(output.get("__next_node__") or "").strip()
    if explicit:
        return explicit
    return str(default_next) if default_next else None


def _issue(*, reason_code: str, detail: str, failed_artifact: str) -> dict[str, Any]:
    return {
        "check_id": S_ORCH_VALIDATE,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "failed_artifact": str(failed_artifact),
    }
