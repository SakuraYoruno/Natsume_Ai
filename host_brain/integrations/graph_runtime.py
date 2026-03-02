"""Graph orchestration runtime: thin entry point assembling contract/executor/fallback layers.

This module is the public API for graph orchestration. It delegates to:
- graph_contract: config validation, model node resolution, shared types
- graph_executor: step-by-step node execution via LangGraph
- graph_fallback: degradation / timeout / dependency-down handling

All public symbols are re-exported for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.integrations.graph_contract import (
    DEFAULT_MODEL_PLANE_DIR,
    GraphRunResult,
    GraphState,
    GatewayFactory,
    NodeHandler,
    S_ORCH_RUN,
    S_ORCH_VALIDATE,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    build_edge_map,
    build_model_param_hash,
    pick_model_profile_id,
    validate_graph_config,
)
from host_brain.integrations.graph_model_resolve import resolve_model_nodes
from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_fallback import (
    fail_hard,
    handle_dependency_unavailable,
    handle_execution_error,
    handle_timeout,
    pick_fallback_node,
    pick_next_node,
    set_finish,
)
from host_brain.integrations.llm_gateway import LlmGatewayContract

# 向后兼容：保留旧的私有函数名作为模块级别别名
_build_edge_map = build_edge_map
_pick_model_profile_id = pick_model_profile_id
_build_model_param_hash = build_model_param_hash


class GraphRuntimeContract:
    """Deterministic graph orchestration runtime backed by LangGraph execution graph.

    Public API is identical to the pre-refactor monolithic version.
    Internally delegates to GraphExecutor (execution) and graph_contract
    (validation / model resolution).
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        default_timeout_ms: int = 5_000,
        gateway_factory: GatewayFactory | None = None,
        model_plane_dir: str | Path | None = None,
    ) -> None:
        self.ledger = ledger
        self.default_timeout_ms = int(default_timeout_ms)
        self._handlers: dict[str, NodeHandler] = {}
        self._gateway_factory = gateway_factory or self._default_gateway_factory
        self._model_plane_dir = Path(model_plane_dir) if model_plane_dir is not None else DEFAULT_MODEL_PLANE_DIR

    def register_node_handler(self, handler_id: str, handler: NodeHandler) -> None:
        """Register a handler function for a handler_id."""
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
        """Execute a graph and return a stable GraphRunResult."""
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # 1. 契约层：校验配置
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
                model_calls=[],
                model_profile_id=None,
                ts_ms=now_value,
            )

        # 2. 契约层：解析模型节点
        timeout_limit = int(timeout_ms if timeout_ms is not None else self.default_timeout_ms)
        node_bindings = {str(node["node_id"]): dict(node) for node in graph_config.get("node_bindings", [])}
        edge_map = build_edge_map(graph_config.get("edges", []))
        fallback_map = build_edge_map(graph_config.get("fallback_edges", []))

        model_profile_id = pick_model_profile_id(graph_config)
        try:
            model_nodes = resolve_model_nodes(
                graph_config=graph_config,
                node_bindings=node_bindings,
                model_profile_id=model_profile_id,
                model_plane_dir=self._model_plane_dir,
            )
        except KeyError as exc:
            return self._finalize(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code="ORCH_NODE_MISSING",
                stage=S_ORCH_VALIDATE,
                detail=str(exc),
                graph_config=graph_config,
                graph_trace=[],
                payload=dict(payload or {}),
                model_calls=[],
                model_profile_id=model_profile_id,
                ts_ms=now_value,
            )

        # 3. 执行层：编译并运行图
        executor = GraphExecutor(
            handlers=self._handlers,
            gateway_factory=self._gateway_factory,
            default_timeout_ms=self.default_timeout_ms,
            ledger=self.ledger,
        )
        compiled = executor.compile_runtime_graph()
        initial_state: GraphState = {
            "request_id": request_id,
            "graph_id": str(graph_config.get("graph_id") or ""),
            "timeout_ms": timeout_limit,
            "payload": dict(payload or {}),
            "trace": [],
            "status": STATUS_OK,
            "reason_code": None,
            "detail": "graph execution complete",
            "pending_node_id": str(graph_config.get("entry_node") or "") or None,
            "edge_map": edge_map,
            "fallback_map": fallback_map,
            "node_bindings": node_bindings,
            "dependency_statuses": dict(dependency_statuses or {}),
            "model_nodes": model_nodes,
            "model_profile_id": model_profile_id,
            "model_calls": [],
        }
        state = compiled.invoke(initial_state)

        # 4. 最终化输出
        return self._finalize(
            request_id=request_id,
            status=str(state["status"]),
            reason_code=(str(state["reason_code"]).strip() if state.get("reason_code") else None),
            stage=S_ORCH_RUN,
            detail=str(state["detail"]),
            graph_config=graph_config,
            graph_trace=list(state.get("trace") or []),
            payload=dict(state.get("payload") or {}),
            model_calls=list(state.get("model_calls") or []),
            model_profile_id=state.get("model_profile_id"),
            ts_ms=now_value,
        )

    def _default_gateway_factory(self, **kwargs: Any) -> LlmGatewayContract:
        return LlmGatewayContract(**kwargs)

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
        model_calls: list[dict[str, Any]],
        model_profile_id: str | None,
        ts_ms: int,
    ) -> GraphRunResult:
        """Finalize result: log errors and model control, build output dict."""
        if reason_code is not None:
            self._log_error(
                request_id=request_id,
                stage=stage,
                reason_code=reason_code,
                detail=detail,
            )
        self._log_model_control_rows(
            request_id=request_id,
            model_profile_id=model_profile_id,
            model_calls=model_calls,
            stage=stage,
            ts_ms=ts_ms,
        )
        output = {
            "request_id": request_id,
            "graph_id": str(graph_config.get("graph_id") or ""),
            "graph_version": str(graph_config.get("version") or ""),
            "graph_status": status,
            "reason_code": reason_code,
            "stage": stage,
            "replay_ref": f"request::{request_id}",
            "runtime_backend": "langgraph+langchain",
            "model_profile_id": model_profile_id,
            "model_control": model_calls,
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

    def _log_model_control_rows(
        self,
        *,
        request_id: str,
        model_profile_id: str | None,
        model_calls: list[dict[str, Any]],
        stage: str,
        ts_ms: int,
    ) -> None:
        if self.ledger is None or not hasattr(self.ledger, "log_model_control"):
            return
        for call in model_calls:
            refs = dict(call.get("governance_refs") or {})
            self.ledger.log_model_control(
                {
                    "request_id": request_id,
                    "node_id": call.get("node_id"),
                    "model_node_id": call.get("model_node_id"),
                    "model_profile_id": (call.get("model_profile_id") or model_profile_id),
                    "provider": call.get("provider"),
                    "model_id": call.get("model_id"),
                    "expected_param_hash": call.get("expected_param_hash"),
                    "actual_param_hash": call.get("actual_param_hash"),
                    "drift_detected": bool(call.get("drift_detected", False)),
                    "approval_token_ref": refs.get("approval_token_ref"),
                    "evidence_ref": refs.get("evidence_ref"),
                    "reason_code": call.get("reason_code"),
                    "status": call.get("gateway_status"),
                    "stage": stage,
                    "ts_ms": int(ts_ms),
                }
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
