"""Graph executor layer: node execution, dependency checks, edge routing, model invocation.

Provides the step-by-step node execution engine backed by LangGraph,
with handler and model node invocation, orchestration status propagation,
and trace construction.
"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_NODE_MISSING,
    ORCH_PARAM_DRIFT,
    SKILL_TOKEN_MISSING,
)
from host_brain.integrations.graph_contract import (
    GraphState, NodeHandler, GatewayFactory,
    STATUS_AIRLOCK_PENDING, STATUS_DEGRADED, STATUS_FAILED, STATUS_OK,
    build_model_param_hash, extract_governance_refs, normalize_dependency_status, pick_api_key,
)
from host_brain.integrations.graph_fallback import (
    fail_hard, handle_airlock_pending, handle_dependency_unavailable, handle_execution_error,
    handle_timeout, pick_fallback_node, pick_next_node, set_finish,
)
from host_brain.integrations.llm_gateway import LlmGatewayContract


class GraphExecutor:
    """Executes graph nodes step-by-step using LangGraph runtime."""

    def __init__(self, *, handlers: dict[str, NodeHandler], gateway_factory: GatewayFactory,
                 default_timeout_ms: int = 5_000, ledger: Any = None) -> None:
        self._handlers = handlers
        self._gateway_factory = gateway_factory
        self._default_timeout_ms = default_timeout_ms
        self._ledger = ledger

    def compile_runtime_graph(self) -> Any:
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
            return set_finish(state)

        # M35-D：Route Commitment Rule — 在节点执行前检查 committed_route_token
        # AIRLOCK 令牌：安全终止，不执行任何节点
        token = str(state.get("committed_route_token") or "").strip()
        if token == "GOV_AIRLOCK_REVIEW":
            return handle_airlock_pending(state=state, node_id=node_id)
        # GOV_FAIL_FALLBACK：跳转 fallback 链路（不执行当前节点）
        if token == "GOV_FAIL_FALLBACK":
            return {
                **state,
                "status": STATUS_DEGRADED,
                "reason_code": "GOV_FAIL_FALLBACK",
                "detail": f"Governor 裁决失败，跳转 fallback，节点={node_id}",
                "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
                # 令牌消费后清空，避免 fallback 节点再次被拦截
                "committed_route_token": None,
            }

        binding = (state.get("node_bindings") or {}).get(node_id)
        if binding is None:
            return fail_hard(state, reason_code=ORCH_NODE_MISSING, detail=f"node binding missing: {node_id}")
        dep_status = normalize_dependency_status((state.get("dependency_statuses") or {}).get(node_id))
        handler_id = str(binding.get("handler_id") or "")
        if not dep_status["available"]:
            return handle_dependency_unavailable(state=state, node_id=node_id, handler_id=handler_id,
                                                  detail=dep_status["detail"] or f"dependency unavailable for node={node_id}")
        try:
            output = (self._invoke_handler(handler_id=handler_id, node_id=node_id, state=state)
                      if handler_id else self._invoke_model_node(node_id=node_id, binding=binding, state=state))
        except TimeoutError:
            return handle_timeout(state=state, node_id=node_id, handler_id=handler_id)
        except KeyError as exc:
            return fail_hard(state, reason_code=ORCH_NODE_MISSING, detail=f"{exc}")
        except Exception as exc:
            return handle_execution_error(state=state, node_id=node_id, handler_id=handler_id, error=exc)

        payload = dict(output or state.get("payload") or {})
        model_calls = list(state.get("model_calls") or [])
        if isinstance(payload.get("__model_calls__"), list):
            model_calls = list(payload.pop("__model_calls__") or model_calls)
        orch_status = str(payload.pop("__orchestration_status__", "") or "").strip().upper() or STATUS_OK
        prev_status = str(state.get("status") or STATUS_OK).upper()
        if prev_status == STATUS_FAILED:
            orch_status = STATUS_FAILED
        elif prev_status == STATUS_DEGRADED and orch_status == STATUS_OK:
            orch_status = STATUS_DEGRADED
        orch_reason = str(payload.pop("__orchestration_reason_code__", "") or "").strip() or None
        if orch_reason is None and prev_status == STATUS_DEGRADED:
            orch_reason = str(state.get("reason_code") or "").strip() or None
        orch_detail = str(payload.pop("__orchestration_detail__", "") or "").strip() or "node executed"

        trace = list(state.get("trace") or [])
        trace.append({"node_id": node_id, "handler_id": handler_id, "status": "OK", "reason_code": orch_reason, "detail": orch_detail})
        next_node = pick_next_node(output=payload, default_next=(state.get("edge_map") or {}).get(node_id))
        payload.pop("__next_node__", None)
        return {**state, "payload": payload, "trace": trace, "pending_node_id": next_node, "model_calls": model_calls,
                "status": (orch_status if orch_status in {STATUS_OK, STATUS_DEGRADED, STATUS_FAILED} else STATUS_OK),
                "reason_code": orch_reason, "detail": orch_detail}

    def _invoke_handler(self, *, handler_id: str, node_id: str, state: GraphState) -> dict[str, Any]:
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"handler missing for node={node_id}, handler_id={handler_id}")
        # M28-R（OPT-45）：context 携带情感状态，使图节点执行时能感知当前情感上下文
        emotion_ctx = state.get("emotion_context") or {}
        return handler(dict(state.get("payload") or {}), {
            "request_id": str(state.get("request_id") or ""), "node_id": node_id,
            "graph_id": str(state.get("graph_id") or ""), "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms),
            # M28-R：情感状态字段（默认值保证向后兼容）
            "emotion_tag": str(emotion_ctx.get("emotion_tag") or state.get("emotion_tag") or "NEUTRAL"),
            "mood_state": str(emotion_ctx.get("mood_state") or state.get("mood_state") or "steady"),
        })

    def _invoke_model_node(self, *, node_id: str, binding: Mapping[str, Any], state: GraphState) -> dict[str, Any]:
        model_node_id = str(binding.get("model_node_id") or node_id).strip()
        model_node = dict((state.get("model_nodes") or {}).get(model_node_id) or {})
        if not model_node:
            raise KeyError(f"model node missing for node={node_id}, model_node_id={model_node_id}")
        provider_name = str(model_node.get("provider") or "").strip().lower()
        if not provider_name:
            raise KeyError(f"provider missing for model_node_id={model_node_id}")

        gov_refs = extract_governance_refs(state=state)
        if bool(binding.get("require_approval_token", False)) and not gov_refs.get("approval_token_ref"):
            return self._build_token_missing_result(node_id, model_node_id, provider_name, model_node, gov_refs, state)

        timeout_value = int(model_node.get("timeout_ms") or state.get("timeout_ms") or self._default_timeout_ms)
        provider_fn = model_node.get("provider_fn")
        gateway = self._gateway_factory(
            ledger=self._ledger, provider=provider_fn if callable(provider_fn) else None,
            provider_name=provider_name,
            api_key=pick_api_key(state=state, provider_name=provider_name, model_node_id=model_node_id),
            available=bool(model_node.get("available", True)), timeout_ms=timeout_value)

        result = gateway.generate(
            request_id=str(state.get("request_id") or ""),
            model_profile_id=str(state.get("model_profile_id") or model_node.get("model_profile_id") or "default"),
            prompt_payload={"node_id": node_id, "model_node_id": model_node_id, "profile_id": state.get("model_profile_id"),
                            "task": str(binding.get("task") or node_id), "input": dict(state.get("payload") or {}),
                            "model": {"provider": provider_name, "model_id": str(model_node.get("model_id") or ""),
                                      "temperature": model_node.get("temperature"), "max_output_tokens": model_node.get("max_output_tokens"),
                                      "timeout_ms": timeout_value}})

        actual_hash = build_model_param_hash(provider=provider_name, model_id=str(model_node.get("model_id") or ""),
                                             temperature=model_node.get("temperature"), max_output_tokens=model_node.get("max_output_tokens"), timeout_ms=timeout_value)
        expected_hash = str(model_node.get("expected_param_hash") or "").strip() or None
        drift = bool(expected_hash and expected_hash != actual_hash)

        base = _model_call_base(node_id, model_node_id, provider_name, model_node, timeout_value, expected_hash, actual_hash, drift, gov_refs)
        payload = dict(state.get("payload") or {})
        payload[node_id] = {**base, "gateway_status": result.status, "reason_code": result.reason_code, "payload": result.output.get("payload")}
        mc = list(state.get("model_calls") or [])
        mc.append({**base, "gateway_status": result.status, "reason_code": (ORCH_PARAM_DRIFT if drift else result.reason_code)})

        if drift:
            return {**payload, "__next_node__": pick_fallback_node(state=state, node_id=node_id), "__model_calls__": mc,
                    "__orchestration_status__": STATUS_DEGRADED, "__orchestration_reason_code__": ORCH_PARAM_DRIFT,
                    "__orchestration_detail__": f"model param drift detected for node={node_id}"}
        if not result.success:
            return {**payload, "__next_node__": pick_fallback_node(state=state, node_id=node_id), "__model_calls__": mc,
                    "__orchestration_status__": STATUS_DEGRADED, "__orchestration_reason_code__": str(result.reason_code or ORCH_DEPENDENCY_UNAVAILABLE),
                    "__orchestration_detail__": f"gateway degraded for node={node_id}"}
        return {**payload, "__next_node__": (state.get("edge_map") or {}).get(node_id), "__model_calls__": mc, "__orchestration_status__": STATUS_OK}

    def _build_token_missing_result(self, node_id: str, model_node_id: str, provider_name: str,
                                     model_node: dict, gov_refs: dict, state: GraphState) -> dict[str, Any]:
        payload = dict(state.get("payload") or {})
        payload[node_id] = {"gateway_status": STATUS_DEGRADED, "reason_code": SKILL_TOKEN_MISSING,
                            "provider": provider_name, "model_id": str(model_node.get("model_id") or ""), "governance_refs": gov_refs}
        mc = list(state.get("model_calls") or [])
        mc.append({"node_id": node_id, "model_node_id": model_node_id, "provider": provider_name,
                    "model_id": str(model_node.get("model_id") or ""), "gateway_status": STATUS_DEGRADED,
                    "reason_code": SKILL_TOKEN_MISSING, "governance_refs": gov_refs})
        return {**payload, "__next_node__": pick_fallback_node(state=state, node_id=node_id), "__model_calls__": mc,
                "__orchestration_status__": STATUS_DEGRADED, "__orchestration_reason_code__": SKILL_TOKEN_MISSING,
                "__orchestration_detail__": f"approval token missing for node={node_id}"}

    def _route_after_step(self, state: GraphState) -> str:
        # M35-D：Route Commitment Rule 完整落地（ModelPlane.md §10 Rule 1）
        # 条件边优先读取 committed_route_token，不直接消费 LLM 原始输出
        token = str(state.get("committed_route_token") or "").strip()
        if token == "GOV_AIRLOCK_REVIEW":
            # AIRLOCK 暂停：安全终止，不进入 fallback
            return "finish"
        if token == "GOV_FAIL_FALLBACK":
            # 裁决失败：跳转 fallback 链路
            return "finish"
        if token == "GOV_SECONDARY_REASONER":
            # 降级到 secondary reasoner（若存在）
            return "finish"
        # GOV_OK_RENDER 或 token=None（向后兼容）：保持原有路由逻辑
        if str(state.get("status") or "") == STATUS_FAILED:
            return "finish"
        return "step" if str(state.get("pending_node_id") or "").strip() else "finish"


def _model_call_base(node_id: str, model_node_id: str, provider: str, mn: dict, timeout_ms: int,
                     expected_hash: str | None, actual_hash: str, drift: bool, gov_refs: dict) -> dict[str, Any]:
    """Build common model call/payload fields."""
    return {
        "node_id": node_id, "model_node_id": model_node_id, "provider": provider,
        "model_id": str(mn.get("model_id") or ""), "temperature": mn.get("temperature"),
        "max_output_tokens": mn.get("max_output_tokens"), "timeout_ms": timeout_ms,
        "expected_param_hash": expected_hash, "actual_param_hash": actual_hash,
        "drift_detected": drift, "param_hash": actual_hash,
        "selected_node": str(mn.get("selected_node") or model_node_id),
        "candidate_nodes": list(mn.get("candidate_nodes") or []),
        "selector_env": mn.get("selector_env"), "governance_refs": gov_refs,
    }
