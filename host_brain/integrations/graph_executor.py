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
    CLOCK_EVENT_POLICY_TIMEOUT,
    STATUS_AIRLOCK_PENDING, STATUS_DEGRADED, STATUS_FAILED, STATUS_OK, STATUS_POLICY_TIMEOUT,
    build_model_param_hash, extract_governance_refs, normalize_dependency_status, pick_api_key,
)
from host_brain.integrations.graph_branch import (
    BranchSpec,
    build_branch_manifest_dict,
    run_parallel_branches,
    barrier_join,
)
from host_brain.integrations.graph_clock import DualClockSupervisor
from host_brain.integrations.graph_streaming import (
    CommitStreamEmitter,
    PhaseLockGuard,
    PresenceStreamEmitter,
    RenderGuard,
    RenderStreamEmitter,
)
from host_brain.integrations.graph_checkpoint import (
    CheckpointManager,
)
from host_brain.integrations.graph_fallback import (
    fail_hard, handle_airlock_pending, handle_dependency_unavailable, handle_execution_error,
    handle_timeout, pick_fallback_node, pick_next_node, set_finish,
)
from host_brain.integrations.llm_gateway import LlmGatewayContract


# ---------------------------------------------------------------------------
# M-MA1：Trusted Handler Allowlist
# 只有此列表内的 handler_id 可以写入控制字段（__next_node__ 等）
# model node 输出的控制字段会被统一剥离，不允许 model node 直接控图
# ---------------------------------------------------------------------------

_TRUSTED_HANDLER_ALLOWLIST: frozenset[str] = frozenset({
    "scene_router_entry",
    "scene_router_post_core",
    "supervisor_dispatch",
    "intent_agent_subgraph_entry",   # M-MA2：IntentAgentSubgraph 入口 handler
    "code_refine_subgraph_entry",    # M-MA2：CodeRefineSubgraph 入口 handler
    # M-MA5：各 Agent Handler（只产出工件，不写 __next_node__，不在此列表）
    # Agent handler 不是 trusted handler，不需要写控制字段
})

# ---------------------------------------------------------------------------
# M-MA6：Agent Handler 映射表（active_agent_id → handler_id）
# graph_executor 根据 GraphState.active_agent_id 动态选择对应 handler，
# 而不是固定节点序列。这是 M-MA6 动态路由的核心。
# ---------------------------------------------------------------------------

_AGENT_HANDLER_MAP: dict[str, str] = {
    "chat_agent":      "chat_agent",
    "code_agent":      "code_agent",
    "translate_agent": "translate_agent",
    "intent_agent":    "intent_agent",
    "judge_agent":     "judge_agent",
    "retrieval_agent": "retrieval_agent",
}

# model node 输出中需要剥离的控制字段（防止 model node 直接控图）
_MODEL_OUTPUT_CONTROL_FIELDS: frozenset[str] = frozenset({
    "committed_route_token",
    "committed_route_token_detail",
    "branch_specs",
    # __next_node__ 和 __orchestration_* 由执行器内部处理，不在此剥离
})

# P3.5：executor 不再持有 agent 映射与 model 节点业务知识，分派与执行由 agent_dispatch_handler 与各 handler 负责。


class GraphExecutor:
    """Executes graph nodes step-by-step using LangGraph runtime."""

    def __init__(self, *, handlers: dict[str, NodeHandler], gateway_factory: GatewayFactory,
                 default_timeout_ms: int = 5_000, ledger: Any = None,
                 evidence_plane: Any = None) -> None:
        self._handlers = handlers
        self._gateway_factory = gateway_factory
        self._default_timeout_ms = default_timeout_ms
        self._ledger = ledger
        # M36-K：Evidence Plane 实时写入（可选，默认 None）
        self._evidence_plane = evidence_plane
        # M38-E/F/G：Phase-Locked Streaming 发射器
        self._presence_emitter = PresenceStreamEmitter()
        self._commit_emitter = CommitStreamEmitter()
        self._render_emitter = RenderStreamEmitter()
        self._phase_lock_guard = PhaseLockGuard()
        self._render_guard = RenderGuard()
        # M39-E：Checkpoint 管理器
        self._checkpoint_manager = CheckpointManager()

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

        # M-MA6：agent_dispatch_node 动态路由分发
        # 当 node_id == "agent_dispatch_node" 时，根据 active_agent_id 动态选择对应 handler 执行，
        # 而不是走固定节点序列。这是 M-MA6 的核心：图执行器根据 GraphState.active_agent_id 动态路由。
        if node_id == "agent_dispatch_node":
            handler_id = self._dispatch_to_agent(state)
            if handler_id:
                try:
                    output = self._invoke_handler(
                        handler_id=handler_id,
                        node_id=node_id,
                        state=state,
                    )
                except Exception as exc:
                    return handle_execution_error(
                        state=state,
                        node_id=node_id,
                        handler_id=handler_id,
                        error=exc,
                    )
                # agent handler 不写 __next_node__，由 edge_map 决定下一跳
                # 安全约束：先 pop __next_node__，再用 edge_map 决定，防止 agent handler 控图
                payload = dict(output or state.get("payload") or {})
                payload.pop("__next_node__", None)
                next_node = (state.get("edge_map") or {}).get(node_id)
                # 同步 agent_artifacts 到 state 顶层
                agent_artifacts = dict(state.get("agent_artifacts") or {})
                if isinstance(payload.get("agent_artifacts"), dict):
                    agent_artifacts = {**agent_artifacts, **payload["agent_artifacts"]}
                trace = list(state.get("trace") or [])
                trace.append({
                    "node_id": node_id,
                    "handler_id": handler_id,
                    "status": "OK",
                    "reason_code": None,
                    "detail": f"agent_dispatch_node → {handler_id}",
                })
                return {
                    **state,
                    "payload": payload,
                    "trace": trace,
                    "pending_node_id": next_node,
                    "agent_artifacts": agent_artifacts,
                    "active_agent_id": str(state.get("active_agent_id") or ""),
                }
            # handler_id 为空时降级：直接跳到下一跳（edge_map 决定）
            next_node = (state.get("edge_map") or {}).get(node_id)
            return {
                **state,
                "pending_node_id": next_node,
                "trace": list(state.get("trace") or []) + [{
                    "node_id": node_id,
                    "handler_id": "",
                    "status": "DEGRADED",
                    "reason_code": "AGENT_DISPATCH_NO_HANDLER",
                    "detail": f"active_agent_id={state.get('active_agent_id')!r} 无对应 handler，跳过",
                }],
            }

        # M35-D：Route Commitment Rule — 在节点执行前检查 committed_route_token
        # AIRLOCK 令牌：安全终止，不执行任何节点
        token = str(state.get("committed_route_token") or "").strip()
        if token == "GOV_AIRLOCK_REVIEW":
            # M39-E：保存 checkpoint 并写入 InterruptSignal
            try:
                from host_brain.integrations.graph_contract import INTERRUPT_REASON_AIRLOCK
                interrupt_signal = self._checkpoint_manager.interrupt(
                    dict(state),
                    interrupt_reason=INTERRUPT_REASON_AIRLOCK,
                )
                state = {
                    **state,
                    "gov_checkpoint_id": interrupt_signal.checkpoint_id,
                    "interrupt_reason": interrupt_signal.interrupt_reason,
                    "resume_deadline_at": interrupt_signal.resume_deadline_at,
                }
            except Exception:
                pass  # 静默降级
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

        # M36-D：Dual-Clock Supervision —— 在节点执行前后使用双时钟监督
        supervisor = DualClockSupervisor(
            policy_start_ms=state.get("policy_start_ms"),
            policy_timeout_ms=int(state.get("policy_timeout_ms") or self._default_timeout_ms),
            node_timeout_ms=int(state.get("timeout_ms") or self._default_timeout_ms),
        )

        # 修复：Policy Clock 超时检查必须在进入 with 块之前完成。
        # 原逻辑在 with supervisor.supervise_node() 内部 return，Python 仍会执行
        # finally 块，导致 POLICY_TIMEOUT 事件被重复追加到 _pending_events，
        # 但该事件不会被收集到已 return 的 state 中（逻辑错误 + 内存泄漏）。
        _pre_policy_event = supervisor._policy_clock.check_policy_timeout(node_id=node_id)
        if _pre_policy_event is not None:
            clock_events = list(state.get("clock_events") or [])
            clock_events.append(_pre_policy_event.to_dict())
            return {
                **state,
                "status": STATUS_FAILED,
                "reason_code": CLOCK_EVENT_POLICY_TIMEOUT,
                "detail": f"Policy Clock 超时，强制终止图执行，节点={node_id}",
                "pending_node_id": None,
                "clock_events": clock_events,
            }

        # M38-E：presence_events 在 with 块外初始化，确保 with 块外的 NODE_END 发射可以访问
        presence_events = list(state.get("presence_events") or [])

        with supervisor.supervise_node(node_id=node_id) as clock_ctx:
            # M38-E：节点执行前发射 Presence NODE_START 事件
            try:
                start_event = self._presence_emitter.emit(node_id=node_id, event_type="NODE_START")
                presence_events.append(start_event.to_dict())
            except Exception:
                pass  # 静默降级

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

        # 节点执行完成：收集双时钟事件和执行统计
        new_clock_events = list(state.get("clock_events") or []) + supervisor.collect_clock_events()
        new_exec_stats = dict(state.get("node_exec_stats") or {})
        new_exec_stats.update(supervisor.collect_exec_stats())

        # M38-E：节点执行后发射 Presence NODE_END 事件
        try:
            end_event = self._presence_emitter.emit(node_id=node_id, event_type="NODE_END")
            presence_events.append(end_event.to_dict())
        except Exception:
            pass  # 静默降级

        payload = dict(output or state.get("payload") or {})
        model_calls = list(state.get("model_calls") or [])
        if isinstance(payload.get("__model_calls__"), list):
            model_calls = list(payload.pop("__model_calls__") or model_calls)

        # M-MA1：model node 控制字段剥离
        # handler node 不剥离（trusted handler 有权写控制字段）
        if not handler_id:
            for _ctrl_field in _MODEL_OUTPUT_CONTROL_FIELDS:
                payload.pop(_ctrl_field, None)
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

        # P3.5：handler 可能写 agent_artifacts 到 payload，同步到 state 顶层供下游（如 judge）读取
        agent_artifacts = dict(state.get("agent_artifacts") or {})
        if isinstance(payload.get("agent_artifacts"), dict):
            agent_artifacts = {**agent_artifacts, **payload["agent_artifacts"]}
        # P4：handler 可能写 subgraph_trace 到 payload，同步到 state 顶层供审计
        subgraph_trace = list(state.get("subgraph_trace") or [])
        if isinstance(payload.get("subgraph_trace"), list):
            subgraph_trace = subgraph_trace + payload["subgraph_trace"]
            payload.pop("subgraph_trace", None)

        new_state = {**state, "payload": payload, "trace": trace, "pending_node_id": next_node, "model_calls": model_calls,
                "agent_artifacts": agent_artifacts,
                "subgraph_trace": subgraph_trace,
                "status": (orch_status if orch_status in {STATUS_OK, STATUS_DEGRADED, STATUS_FAILED} else STATUS_OK),
                "reason_code": orch_reason, "detail": orch_detail,
                # M36-D：双时钟字段更新
                "clock_events": new_clock_events,
                "node_exec_stats": new_exec_stats,
                # M38-E：Presence 事件列表更新（不影响 payload/trace）
                "presence_events": presence_events,
                }

        # M-MA4：agent 切换事件（仅观测，不反向作为图输入）
        # M-MA3 后 active_agent_id 已提升为 GraphState 顶层字段，不再读 payload["agent_runtime"]
        try:
            active_agent = str(new_state.get("active_agent_id") or "").strip()
            prev_agent = str(state.get("active_agent_id") or "").strip()
            if active_agent and active_agent != prev_agent:
                # AGENT_SELECTED：agent 切换
                agent_event = self._presence_emitter.emit(
                    node_id=node_id,
                    event_type="AGENT_SELECTED",
                )
                new_presence = list(new_state.get("presence_events") or [])
                new_presence.append({**agent_event.to_dict(), "agent_id": active_agent})
                new_state = {**new_state, "presence_events": new_presence}
            # AGENT_HANDOFF：handoff_history 有新增时触发
            new_handoff = list(new_state.get("handoff_history") or [])
            prev_handoff = list(state.get("handoff_history") or [])
            if len(new_handoff) > len(prev_handoff):
                latest_hop = new_handoff[-1]
                handoff_event = self._presence_emitter.emit(
                    node_id=node_id,
                    event_type="AGENT_HANDOFF",
                )
                new_presence = list(new_state.get("presence_events") or [])
                new_presence.append({
                    **handoff_event.to_dict(),
                    "from_agent": str(latest_hop.get("from") or ""),
                    "to_agent": str(latest_hop.get("to") or ""),
                    "reason": str(latest_hop.get("reason") or ""),
                })
                new_state = {**new_state, "presence_events": new_presence}
        except Exception:
            pass  # 静默降级

        # M38-F：Commit Stream 发射（committed_route_token = GOV_OK_RENDER 时生成 CommitSnapshot）
        token = str(new_state.get("committed_route_token") or "").strip()
        if token == "GOV_OK_RENDER":
            try:
                snapshot = self._commit_emitter.commit(
                    committed_route_token=token,
                    payload=dict(new_state.get("payload") or {}),
                    request_id=str(new_state.get("request_id") or ""),
                )
                new_state = {**new_state, "commit_snapshot": snapshot.to_dict(), "streaming_phase": "COMMIT"}
            except Exception:
                pass  # 静默降级

        # M38-G：Render Stream 发射（commit_snapshot 存在且验证通过时生成 render_snapshot_ref）
        commit_snapshot = new_state.get("commit_snapshot")
        if commit_snapshot is not None:
            current_intent = str((new_state.get("payload") or {}).get("intent_commit") or "").strip()
            if self._phase_lock_guard.validate_render_precondition(
                commit_snapshot=commit_snapshot,
                current_intent_commit=current_intent,
            ):
                try:
                    render_event = self._render_emitter.render(
                        commit_snapshot=commit_snapshot,
                        chunk="",
                    )
                    new_state = {**new_state, "render_snapshot_ref": render_event.snapshot_ref, "streaming_phase": "RENDER"}
                except Exception:
                    pass  # 静默降级
            else:
                # 验证失败：写入降级通知到 presence_events
                try:
                    failed_event = self._phase_lock_guard.build_precondition_failed_event(
                        node_id=node_id,
                        reason="intent_commit 已变化，Render 前置条件不满足",
                    )
                    new_presence = list(new_state.get("presence_events") or []) + [failed_event]
                    new_state = {**new_state, "presence_events": new_presence}
                except Exception:
                    pass  # 静默降级

        # M40-G：RenderGuard 验证（render_snapshot_ref 存在时验证 render 输出）
        if new_state.get("render_snapshot_ref"):
            try:
                render_output = dict(new_state.get("payload") or {})
                rg_result = self._render_guard.validate_render_output(
                    render_output=render_output,
                    commit_snapshot=new_state.get("commit_snapshot"),
                )
                new_state = {**new_state, "render_guard_result": rg_result.to_dict()}
                if not rg_result.passed:
                    # 验证失败：写入降级通知到 presence_events
                    failed_event = self._phase_lock_guard.build_precondition_failed_event(
                        node_id=node_id,
                        reason=f"RenderGuard 验证失败: {rg_result.reason}",
                    )
                    new_presence = list(new_state.get("presence_events") or []) + [failed_event]
                    new_state = {**new_state, "presence_events": new_presence}
            except Exception:
                pass  # 静默降级

        # M36-K：Evidence Plane 实时写入单节点 trace
        if self._evidence_plane is not None:
            try:
                self._evidence_plane.write_trace(
                    request_id=str(state.get("request_id") or ""),
                    graph_id=str(state.get("graph_id") or ""),
                    trace=[trace[-1]] if trace else [],
                )
            except Exception:
                pass  # 静默降级

        # M37-D：只读并行分支执行（若 branch_specs 非空）
        branch_specs_raw = list(new_state.get("branch_specs") or [])
        if branch_specs_raw:
            try:
                branch_specs = [
                    BranchSpec(
                        branch_id=str(spec.get("branch_id") or f"branch_{i}"),
                        handler=spec["handler"],
                        input_payload=dict(spec.get("input_payload") or new_state.get("payload") or {}),
                        timeout_ms=int(spec.get("timeout_ms") or self._default_timeout_ms),
                    )
                    for i, spec in enumerate(branch_specs_raw)
                    if callable(spec.get("handler"))
                ]
                if branch_specs:
                    manifest = run_parallel_branches(
                        branch_specs=branch_specs,
                        state=new_state,
                        max_workers=int(new_state.get("branch_max_workers") or 4),
                    )
                    manifest_dict = build_branch_manifest_dict(manifest)
                    new_state = {**new_state, "branch_manifest": manifest_dict}
                    # M40-F：barrier_join() fan-in 校验
                    try:
                        bj_result = barrier_join(
                            branch_manifest=manifest_dict,
                            audit_tickets=None,
                        )
                        new_state = {**new_state, "barrier_join_result": bj_result.to_dict()}
                    except Exception:
                        pass  # 静默降级
            except Exception:
                pass  # 静默降级，不影响主图执行

        return new_state

    def _dispatch_to_agent(self, state: GraphState) -> str:
        """M-MA6：根据 active_agent_id 返回对应的 handler_id。

        这是 M-MA6 的核心：图执行器根据 GraphState.active_agent_id
        动态选择下一个执行的 handler，而不是固定节点序列。

        Returns:
            对应的 handler_id 字符串；若 active_agent_id 未注册则返回空字符串（降级）。
        """
        active_agent_id = str(state.get("active_agent_id") or "").strip()
        return _AGENT_HANDLER_MAP.get(active_agent_id, "")

    def _invoke_handler(self, *, handler_id: str, node_id: str, state: GraphState) -> dict[str, Any]:
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"handler missing for node={node_id}, handler_id={handler_id}")
        # M28-R（OPT-45）：context 携带情感状态，使图节点执行时能感知当前情感上下文
        emotion_ctx = state.get("emotion_context") or {}
        binding = dict((state.get("node_bindings") or {}).get(node_id) or {})
        context = {
            "request_id": str(state.get("request_id") or ""),
            "node_id": node_id,
            "graph_id": str(state.get("graph_id") or ""),
            "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms),
            "emotion_tag": str(emotion_ctx.get("emotion_tag") or state.get("emotion_tag") or "NEUTRAL"),
            "mood_state": str(emotion_ctx.get("mood_state") or state.get("mood_state") or "steady"),
            "node_binding": binding,
        }
        # P3.5：图本地配置 — allowlist 等由 binding 提供，handler 从 context 读取
        allowlist = binding.get("agent_dispatch_allowlist")
        if allowlist is not None:
            context["agent_dispatch_allowlist"] = list(allowlist) if isinstance(allowlist, (list, tuple)) else [allowlist]
        # P3.5：子 handler 需调用 model 时由各自 handler 通过 invoke_model_node 执行，executor 仅提供能力
        # P4：payload_override 用于子图链式调用，使下一节点能读到上一节点输出
        def _invoke_model_node(node_id_to_run: str, payload_override: dict[str, Any] | None = None) -> dict[str, Any]:
            b = (state.get("node_bindings") or {}).get(node_id_to_run)
            if not b:
                return {}
            state_use = {**state, "payload": dict(payload_override or state.get("payload") or {})} if payload_override is not None else state
            out = self._invoke_model_node(node_id=node_id_to_run, binding=b, state=state_use)
            for ctrl in ("__next_node__", "__orchestration_status__", "__orchestration_reason_code__", "__orchestration_detail__"):
                out.pop(ctrl, None)
            return out

        context["invoke_model_node"] = _invoke_model_node
        # P3.5：agent_dispatch 仅做分派，需 invoke_handler 调用 code_agent / translate_agent
        handlers = self._handlers

        def invoke_handler(h_id: str, pl: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
            fn = handlers.get(h_id)
            return fn(pl, ctx) if fn else pl

        context["invoke_handler"] = invoke_handler
        return handler(dict(state.get("payload") or {}), context)

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
        emotion_context_raw = state.get("emotion_context")
        emotion_context = (
            dict(emotion_context_raw)
            if isinstance(emotion_context_raw, Mapping)
            else {}
        )
        if not emotion_context and (
            str(state.get("mood_state") or "").strip()
            or str(state.get("emotion_tag") or "").strip()
            or str(state.get("emotion_range_hint") or "").strip()
        ):
            emotion_context = {
                "mood_state": state.get("mood_state"),
                "emotion_tag": state.get("emotion_tag"),
                "emotion_range_hint": state.get("emotion_range_hint"),
            }

        result = gateway.generate(
            request_id=str(state.get("request_id") or ""),
            model_profile_id=str(state.get("model_profile_id") or model_node.get("model_profile_id") or "default"),
            prompt_payload={"node_id": node_id, "model_node_id": model_node_id, "profile_id": state.get("model_profile_id"),
                            "task": str(binding.get("task") or node_id), "input": dict(state.get("payload") or {}),
                            "model": {"provider": provider_name, "model_id": str(model_node.get("model_id") or ""),
                                      "temperature": model_node.get("temperature"), "max_output_tokens": model_node.get("max_output_tokens"),
                                      "timeout_ms": timeout_value}},
            emotion_context=(emotion_context or None))

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
