"""Graph orchestration contract for deterministic runtime assembly."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, TypedDict

import yaml
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_EDGE_INVALID,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
    ORCH_PARAM_DRIFT,
)
from host_brain.core.ids import now_ms
from host_brain.integrations.llm_gateway import LlmGatewayContract


S_ORCH_VALIDATE = "S_ORCH_VALIDATE"
S_ORCH_RUN = "S_ORCH_RUN"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

NodeHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
GatewayFactory = Callable[..., LlmGatewayContract]

DEFAULT_MODEL_PLANE_DIR = Path("configs/model_plane")


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


@dataclass(frozen=True)
class GraphRunResult:
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class GraphRuntimeContract:
    """Deterministic graph orchestration runtime backed by LangGraph execution graph."""

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
                model_calls=[],
                model_profile_id=None,
                ts_ms=now_value,
            )

        timeout_limit = int(timeout_ms if timeout_ms is not None else self.default_timeout_ms)
        node_bindings = {str(node["node_id"]): dict(node) for node in graph_config.get("node_bindings", [])}
        edge_map = _build_edge_map(graph_config.get("edges", []))
        fallback_map = _build_edge_map(graph_config.get("fallback_edges", []))

        model_profile_id = _pick_model_profile_id(graph_config)
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
                reason_code=ORCH_NODE_MISSING,
                stage=S_ORCH_VALIDATE,
                detail=str(exc),
                graph_config=graph_config,
                graph_trace=[],
                payload=dict(payload or {}),
                model_calls=[],
                model_profile_id=model_profile_id,
                ts_ms=now_value,
            )

        compiled = self._compile_runtime_graph()
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

    def _compile_runtime_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("step", RunnableLambda(self._run_single_step))
        graph.add_node("finish", RunnableLambda(lambda state: state))
        graph.add_edge(START, "step")
        graph.add_conditional_edges("step", self._route_after_step, {"step": "step", "finish": "finish"})
        graph.add_edge("finish", END)
        return graph.compile()

    def _run_single_step(self, state: GraphState) -> GraphState:
        node_id = str(state.get("pending_node_id") or "").strip()
        if not node_id:
            return self._set_finish(state)

        node_bindings = dict(state.get("node_bindings") or {})
        binding = node_bindings.get(node_id)
        if binding is None:
            return self._fail_hard(
                state,
                reason_code=ORCH_NODE_MISSING,
                detail=f"node binding missing: {node_id}",
            )

        dep_status = _normalize_dependency_status((state.get("dependency_statuses") or {}).get(node_id))
        handler_id = str(binding.get("handler_id") or "")
        trace = list(state.get("trace") or [])

        if not dep_status["available"]:
            detail = dep_status["detail"] or f"dependency unavailable for node={node_id}"
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
                "pending_node_id": _pick_fallback_node(state=state, node_id=node_id),
            }

        try:
            if handler_id:
                output = self._invoke_handler(handler_id=handler_id, node_id=node_id, state=state)
            else:
                output = self._invoke_model_node(node_id=node_id, binding=binding, state=state)
        except TimeoutError:
            detail = f"node timeout: {node_id}"
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
                "pending_node_id": _pick_fallback_node(state=state, node_id=node_id),
            }
        except KeyError as exc:
            return self._fail_hard(
                state,
                reason_code=ORCH_NODE_MISSING,
                detail=f"{exc}",
            )
        except Exception as exc:
            detail = f"node execution failed: {node_id}: {exc}"
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
                "pending_node_id": _pick_fallback_node(state=state, node_id=node_id),
            }

        payload = dict(output or state.get("payload") or {})
        model_calls = list(state.get("model_calls") or [])
        if isinstance(payload.get("__model_calls__"), list):
            model_calls = list(payload.get("__model_calls__") or model_calls)
            payload.pop("__model_calls__", None)
        orchestration_status = str(payload.get("__orchestration_status__") or "").strip().upper() or STATUS_OK
        previous_status = str(state.get("status") or STATUS_OK).upper()
        if previous_status == STATUS_FAILED:
            orchestration_status = STATUS_FAILED
        elif previous_status == STATUS_DEGRADED and orchestration_status == STATUS_OK:
            orchestration_status = STATUS_DEGRADED

        orchestration_reason = str(payload.get("__orchestration_reason_code__") or "").strip() or None
        if orchestration_reason is None and previous_status == STATUS_DEGRADED:
            orchestration_reason = str(state.get("reason_code") or "").strip() or None
        orchestration_detail = str(payload.get("__orchestration_detail__") or "").strip() or "node executed"
        payload.pop("__orchestration_status__", None)
        payload.pop("__orchestration_reason_code__", None)
        payload.pop("__orchestration_detail__", None)

        trace.append(
            {
                "node_id": node_id,
                "handler_id": handler_id,
                "status": "OK",
                "reason_code": orchestration_reason,
                "detail": orchestration_detail,
            }
        )
        next_node = _pick_next_node(
            output=payload,
            default_next=(state.get("edge_map") or {}).get(node_id),
        )
        payload.pop("__next_node__", None)
        return {
            **state,
            "payload": payload,
            "trace": trace,
            "pending_node_id": next_node,
            "model_calls": model_calls,
            "status": (orchestration_status if orchestration_status in {STATUS_OK, STATUS_DEGRADED, STATUS_FAILED} else STATUS_OK),
            "reason_code": orchestration_reason,
            "detail": orchestration_detail,
        }

    def _invoke_handler(self, *, handler_id: str, node_id: str, state: GraphState) -> dict[str, Any]:
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"handler missing for node={node_id}, handler_id={handler_id}")
        return handler(
            dict(state.get("payload") or {}),
            {
                "request_id": str(state.get("request_id") or ""),
                "node_id": node_id,
                "graph_id": str(state.get("graph_id") or ""),
                "timeout_ms": int(state.get("timeout_ms") or self.default_timeout_ms),
            },
        )

    def _invoke_model_node(self, *, node_id: str, binding: Mapping[str, Any], state: GraphState) -> dict[str, Any]:
        model_node_id = str(binding.get("model_node_id") or node_id).strip()
        model_node = dict((state.get("model_nodes") or {}).get(model_node_id) or {})
        if not model_node:
            raise KeyError(f"model node missing for node={node_id}, model_node_id={model_node_id}")

        provider_name = str(model_node.get("provider") or "").strip().lower()
        if not provider_name:
            raise KeyError(f"provider missing for model_node_id={model_node_id}")
        provider_fn = model_node.get("provider_fn")
        provider_callable = provider_fn if callable(provider_fn) else None
        timeout_value = int(model_node.get("timeout_ms") or state.get("timeout_ms") or self.default_timeout_ms)
        api_key = _pick_api_key(state=state, provider_name=provider_name)
        gateway = self._gateway_factory(
            ledger=self.ledger,
            provider=provider_callable,
            provider_name=provider_name,
            api_key=api_key,
            available=bool(model_node.get("available", True)),
            timeout_ms=timeout_value,
        )

        prompt_payload = {
            "node_id": node_id,
            "model_node_id": model_node_id,
            "profile_id": state.get("model_profile_id"),
            "task": str(binding.get("task") or node_id),
            "input": dict(state.get("payload") or {}),
            "model": {
                "provider": provider_name,
                "model_id": str(model_node.get("model_id") or ""),
                "temperature": model_node.get("temperature"),
                "max_output_tokens": model_node.get("max_output_tokens"),
                "timeout_ms": timeout_value,
            },
        }
        result = gateway.generate(
            request_id=str(state.get("request_id") or ""),
            model_profile_id=str(state.get("model_profile_id") or model_node.get("model_profile_id") or "default"),
            prompt_payload=prompt_payload,
        )

        actual_param_hash = _build_model_param_hash(
            provider=provider_name,
            model_id=str(model_node.get("model_id") or ""),
            temperature=model_node.get("temperature"),
            max_output_tokens=model_node.get("max_output_tokens"),
            timeout_ms=timeout_value,
        )
        expected_param_hash = str(model_node.get("expected_param_hash") or "").strip() or None
        drift_detected = bool(expected_param_hash and expected_param_hash != actual_param_hash)

        payload = dict(state.get("payload") or {})
        payload[node_id] = {
            "gateway_status": result.status,
            "reason_code": result.reason_code,
            "provider": provider_name,
            "model_id": str(model_node.get("model_id") or ""),
            "temperature": model_node.get("temperature"),
            "max_output_tokens": model_node.get("max_output_tokens"),
            "timeout_ms": timeout_value,
            "expected_param_hash": expected_param_hash,
            "actual_param_hash": actual_param_hash,
            "drift_detected": drift_detected,
            "param_hash": actual_param_hash,
            "selected_node": str(model_node.get("selected_node") or model_node_id),
            "candidate_nodes": list(model_node.get("candidate_nodes") or []),
            "selector_env": model_node.get("selector_env"),
            "payload": result.output.get("payload"),
        }

        model_calls = list(state.get("model_calls") or [])
        model_calls.append(
            {
                "node_id": node_id,
                "model_node_id": model_node_id,
                "provider": provider_name,
                "model_id": str(model_node.get("model_id") or ""),
                "temperature": model_node.get("temperature"),
                "max_output_tokens": model_node.get("max_output_tokens"),
                "timeout_ms": timeout_value,
                "expected_param_hash": expected_param_hash,
                "actual_param_hash": actual_param_hash,
                "drift_detected": drift_detected,
                "param_hash": actual_param_hash,
                "selected_node": str(model_node.get("selected_node") or model_node_id),
                "candidate_nodes": list(model_node.get("candidate_nodes") or []),
                "selector_env": model_node.get("selector_env"),
                "gateway_status": result.status,
                "reason_code": (ORCH_PARAM_DRIFT if drift_detected else result.reason_code),
            }
        )

        if drift_detected:
            return {
                **payload,
                "__next_node__": _pick_fallback_node(state=state, node_id=node_id),
                "__model_calls__": model_calls,
                "__orchestration_status__": STATUS_DEGRADED,
                "__orchestration_reason_code__": ORCH_PARAM_DRIFT,
                "__orchestration_detail__": f"model param drift detected for node={node_id}",
            }

        if not result.success:
            return {
                **payload,
                "__next_node__": _pick_fallback_node(state=state, node_id=node_id),
                "__model_calls__": model_calls,
                "__orchestration_status__": STATUS_DEGRADED,
                "__orchestration_reason_code__": str(result.reason_code or ORCH_DEPENDENCY_UNAVAILABLE),
                "__orchestration_detail__": f"gateway degraded for node={node_id}",
            }

        return {
            **payload,
            "__next_node__": (state.get("edge_map") or {}).get(node_id),
            "__model_calls__": model_calls,
            "__orchestration_status__": STATUS_OK,
        }

    def _default_gateway_factory(self, **kwargs: Any) -> LlmGatewayContract:
        return LlmGatewayContract(**kwargs)

    def _route_after_step(self, state: GraphState) -> str:
        if str(state.get("status") or "") == STATUS_FAILED:
            return "finish"
        node_id = str(state.get("pending_node_id") or "").strip()
        return "step" if node_id else "finish"

    def _set_finish(self, state: GraphState) -> GraphState:
        return {
            **state,
            "pending_node_id": None,
        }

    def _fail_hard(self, state: GraphState, *, reason_code: str, detail: str) -> GraphState:
        return {
            **state,
            "status": STATUS_FAILED,
            "reason_code": reason_code,
            "detail": detail,
            "pending_node_id": None,
        }

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
                _issue(
                    reason_code=ORCH_NODE_MISSING,
                    detail=f"handler_id or model_node_id required for node={node_id}",
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


def resolve_model_nodes(
    *,
    graph_config: Mapping[str, Any],
    node_bindings: Mapping[str, dict[str, Any]],
    model_profile_id: str | None,
    model_plane_dir: Path,
) -> dict[str, dict[str, Any]]:
    explicit = _normalize_model_nodes(graph_config.get("model_nodes"))
    if explicit:
        return explicit
    if not model_profile_id:
        return {}

    env_name = str(graph_config.get("env") or "").strip().lower() or None
    allowlist = {
        str(item).strip()
        for item in (graph_config.get("node_allowlist") or [])
        if str(item).strip()
    }

    profiles_payload = _read_yaml(model_plane_dir / "model_profiles.yaml")
    registry_payload = _read_yaml(model_plane_dir / "node_registry.yaml")
    profiles = dict(profiles_payload.get("profiles") or {}) if isinstance(profiles_payload, Mapping) else {}
    registry_nodes = dict(registry_payload.get("nodes") or {}) if isinstance(registry_payload, Mapping) else {}

    profile = dict(profiles.get(model_profile_id) or {})
    if not profile:
        raise KeyError(f"model profile missing: {model_profile_id}")

    resolved: dict[str, dict[str, Any]] = {}
    for node_id, binding in node_bindings.items():
        handler_id = str(binding.get("handler_id") or "").strip()
        if handler_id:
            continue

        route = _pick_profile_route(profile=profile, binding=binding)
        candidates = [
            str(item).strip()
            for item in (route.get("chain") or [])
            if str(item).strip() and str(item).strip() in registry_nodes
        ]
        explicit_model_node_id = str(binding.get("model_node_id") or "").strip()
        if explicit_model_node_id:
            selected_model_node_id = explicit_model_node_id
            if explicit_model_node_id not in candidates:
                candidates = [explicit_model_node_id, *candidates]
        else:
            selected_model_node_id = _pick_candidate_node(candidates=candidates, allowlist=allowlist)

        if not selected_model_node_id:
            raise KeyError(f"model node candidate missing for node={node_id}")

        row = dict(registry_nodes.get(selected_model_node_id) or {})
        if not row:
            raise KeyError(f"model node missing in registry: {selected_model_node_id}")
        provider = str(row.get("provider") or "").strip().lower()
        model_id = str(row.get("model_id") or "").strip()
        if not provider or not model_id:
            raise KeyError(f"model node missing provider/model_id: {selected_model_node_id}")

        timeout_ms = _resolve_timeout_ms(
            profile=profile,
            binding=binding,
            model_node_id=selected_model_node_id,
            route=route,
        )
        expected_param_hash = _build_model_param_hash(
            provider=provider,
            model_id=model_id,
            temperature=row.get("temperature"),
            max_output_tokens=row.get("max_output_tokens"),
            timeout_ms=timeout_ms,
        )
        node_config = {
            "provider": provider,
            "model_id": model_id,
            "temperature": row.get("temperature"),
            "max_output_tokens": row.get("max_output_tokens"),
            "timeout_ms": timeout_ms,
            "expected_param_hash": expected_param_hash,
            "model_profile_id": model_profile_id,
            "source": "model_plane",
            "selected_node": selected_model_node_id,
            "candidate_nodes": candidates,
            "selector_env": env_name,
        }
        resolved[selected_model_node_id] = dict(node_config)
        if node_id != selected_model_node_id:
            resolved[node_id] = dict(node_config)
    return resolved


def _pick_profile_route(*, profile: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    routing = dict(profile.get("routing") or {})
    task = str(binding.get("task") or binding.get("node_id") or "").strip()
    route = routing.get(task)
    if route is None:
        route = routing.get(task.upper())
    if isinstance(route, Mapping):
        return dict(route)
    return {}


def _pick_candidate_node(*, candidates: list[str], allowlist: set[str]) -> str | None:
    if allowlist:
        for item in candidates:
            if item in allowlist:
                return item
        return None
    return candidates[0] if candidates else None

@lru_cache(maxsize=8)
def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    return dict(payload or {}) if isinstance(payload, Mapping) else {}


def _resolve_timeout_ms(
    *,
    profile: Mapping[str, Any],
    binding: Mapping[str, Any],
    model_node_id: str,
    route: Mapping[str, Any] | None = None,
) -> int | None:
    resolved_route = dict(route or _pick_profile_route(profile=profile, binding=binding) or {})
    timeout_map = dict(resolved_route.get("timeouts_ms") or {})
    raw = timeout_map.get(model_node_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _build_model_param_hash(
    *,
    provider: str,
    model_id: str,
    temperature: Any,
    max_output_tokens: Any,
    timeout_ms: Any,
) -> str:
    canonical = {
        "provider": str(provider),
        "model_id": str(model_id),
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "timeout_ms": timeout_ms,
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pick_model_profile_id(graph_config: Mapping[str, Any]) -> str | None:
    value = str(graph_config.get("model_profile_id") or "").strip()
    return value or None


def _normalize_model_nodes(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, Mapping):
        out: dict[str, dict[str, Any]] = {}
        for key, row in raw.items():
            if not isinstance(row, Mapping):
                continue
            out[str(key)] = dict(row)
        return out
    return {}


def _pick_api_key(*, state: GraphState, provider_name: str) -> str | None:
    dependency = (state.get("dependency_statuses") or {}).get(provider_name)
    if isinstance(dependency, Mapping):
        raw_key = str(dependency.get("api_key") or "").strip()
        if raw_key:
            return raw_key
    return None


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


def _pick_fallback_node(*, state: GraphState, node_id: str) -> str | None:
    fallback_map = dict(state.get("fallback_map") or {})
    next_node = str(fallback_map.get(node_id) or "").strip()
    return next_node or None


def _issue(*, reason_code: str, detail: str, failed_artifact: str) -> dict[str, Any]:
    return {
        "check_id": S_ORCH_VALIDATE,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "failed_artifact": str(failed_artifact),
    }
