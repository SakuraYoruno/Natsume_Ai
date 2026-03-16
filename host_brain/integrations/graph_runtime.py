"""Graph orchestration runtime facade.
Delegates to graph_contract, graph_executor, and graph_fallback.
Public symbols remain re-exported for backward compatibility.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_emotion_state,
    get_chat_runtime_graph,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.integrations.graph_contract import (
    DEFAULT_MODEL_PLANE_DIR,
    DEFAULT_POLICY_TIMEOUT_MS,
    GraphRunResult,
    GraphState,
    GatewayFactory,
    NodeHandler,
    S_ORCH_RUN,
    S_ORCH_VALIDATE,
    STATUS_AIRLOCK_PENDING,
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

# Evidence Plane 专用线程池：最多 2 线程，不阻塞主图执行路径
_evidence_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence")

# 注册 atexit 钩子，确保进程退出时线程池优雅关闭，防止线程泄漏
import atexit as _atexit
_atexit.register(_evidence_executor.shutdown, wait=False, cancel_futures=True)

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
        # 修复：CheckpointManager 必须在实例级别共享，而非在 resume_graph 中每次 new。
        # 原逻辑在 resume_graph 中 `manager = CheckpointManager()` 创建全新实例，
        # 其内部 CheckpointStore 是纯内存存储，与 interrupt() 时保存 checkpoint 的实例不同，
        # 导致 load_state() 永远返回 None，resume_graph 始终返回 CHECKPOINT_NOT_FOUND。
        from host_brain.integrations.graph_checkpoint import CheckpointManager as _CM
        self._checkpoint_manager = _CM()

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
        edges = [dict(row) for row in graph_config.get("edges", []) if isinstance(row, Mapping)]
        fallback_edges = [dict(row) for row in graph_config.get("fallback_edges", []) if isinstance(row, Mapping)]
        edge_map, fallback_map = build_edge_map(edges + fallback_edges), build_edge_map(fallback_edges)

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
        # M36-C：初始化双时钟字段
        policy_timeout_ms = int(
            graph_config.get("policy_timeout_ms") or timeout_limit or DEFAULT_POLICY_TIMEOUT_MS
        )
        payload_dict = dict(payload or {})
        graph_section = get_chat_runtime_graph(payload_dict)
        emotion_context = get_chat_runtime_emotion_state(payload_dict)
        if emotion_context:
            emotion_context = {
                "mood_state": str(emotion_context.get("mood_state") or "steady").strip().lower() or "steady",
                "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL",
                "emotion_range_hint": str(emotion_context.get("emotion_range_hint") or "").strip(),
            }
        mood_state = (
            str(emotion_context.get("mood_state") or "").strip().lower()
            if emotion_context
            else None
        )
        emotion_tag = (
            str(emotion_context.get("emotion_tag") or "").strip().upper()
            if emotion_context
            else None
        )
        emotion_range_hint = (
            str(emotion_context.get("emotion_range_hint") or "").strip()
            if emotion_context
            else None
        )
        initial_state: GraphState = {
            "request_id": request_id,
            "graph_id": str(graph_config.get("graph_id") or ""),
            "timeout_ms": timeout_limit,
            "payload": payload_dict,
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
            "emotion_context": (emotion_context or None),
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "emotion_range_hint": emotion_range_hint,
            # M35-B：从 payload 提取 Governor 已编译的 committed_route_token
            # 由 fastapi_app.py 在 Governor 裁决后、调用 run_graph 前注入
            "committed_route_token": str(
                graph_section.get("committed_route_token") or payload_dict.get("committed_route_token") or ""
            ).strip() or None,
            "committed_route_token_detail": (
                graph_section.get("committed_route_token_detail")
                or payload_dict.get("committed_route_token_detail")
                or None
            ),
            # M35-A：透明子图节点执行追踪（初始为空列表）
            "subgraph_trace": [],
            # M36-C：双时钟字段初始化
            "policy_start_ms": now_value,
            "policy_timeout_ms": policy_timeout_ms,
            "clock_events": [],
            "node_exec_stats": {},
            # M37-C：只读并行分支字段初始化
            "branch_specs": [],
            "branch_manifest": None,
            # M38-D：Phase-Locked Streaming 字段初始化
            "presence_events": [],
            "commit_snapshot": None,
            "render_snapshot_ref": None,
            "streaming_phase": None,
            # M39-D：Checkpoint 字段初始化
            "gov_checkpoint_id": None,
            "interrupt_reason": None,
            "resume_allowed": None,
            "resume_deadline_at": None,
            # M40-E：barrier_join / RenderGuard 字段初始化
            "barrier_join_result": None,
            "render_guard_result": None,
        }

        # M-MA1：注入 agent_runtime 默认结构（多 agent 第一阶段放入 payload）
        import os as _os
        _multi_agent_enabled = str(_os.environ.get("MULTI_AGENT_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"}
        if "agent_runtime" not in payload_dict:
            payload_dict["agent_runtime"] = {
                "plan_id": None,
                "active_agent_id": None,
                "agent_hops": 0,
                "max_hops": 2,
                "risk_mode": "safe",
                "need_judge": False,
                "budget": {
                    "max_hops": 2,
                    "per_agent_timeout_ms": 12000,
                    "total_budget_ms": 30000,
                },
                "artifacts": {},
                "handoff_history": [],
                "multi_agent_enabled": _multi_agent_enabled,
            }
        else:
            # 确保 multi_agent_enabled 标记存在
            payload_dict["agent_runtime"].setdefault("multi_agent_enabled", _multi_agent_enabled)
        initial_state["payload"] = payload_dict
        state = compiled.invoke(initial_state)

        # M36-F：图执行完成后异步写入 Evidence Plane（不阻塞主流程返回）
        _evidence_executor.submit(self._write_evidence, request_id=request_id, state=state)

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

    def resume_graph(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        now_ms: int | None = None,
    ) -> GraphRunResult:
        """从 checkpoint 恢复图执行（M39-F）。

        恢复前调用 HostPolicyGuard 验证：
        - 验证失败时写入 SyntheticTimeoutEvent 并返回 STATUS_FAILED
        - 验证通过时从 checkpoint 加载 state 并继续执行

        Args:
            checkpoint_id: checkpoint ID
            request_id: 请求 ID
            now_ms: 当前时间戳（毫秒），默认为 now_ms()

        Returns:
            GraphRunResult（恢复执行结果或 synthetic timeout 结果）
        """
        from host_brain.integrations.graph_checkpoint import (
            CheckpointManager,
            InterruptSignal,
            SyntheticTimeoutEvent,
        )
        from host_brain.integrations.graph_contract import (
            INTERRUPT_REASON_HUMAN_REVIEW,
            RESUME_DENIED_DEADLINE_EXCEEDED,
        )

        from host_brain.core.ids import now_ms as _now_ms
        current_ms = int(now_ms if now_ms is not None else _now_ms())
        # 修复：使用实例级别共享的 _checkpoint_manager，而非每次 new CheckpointManager()
        manager = self._checkpoint_manager

        # 1. 加载 checkpoint
        state = manager.load_state(checkpoint_id)
        if state is None:
            return GraphRunResult(
                status=STATUS_FAILED,
                reason_code="CHECKPOINT_NOT_FOUND",
                stage=S_ORCH_RUN,
                detail=f"checkpoint 不存在: {checkpoint_id}",
                output={
                    "request_id": request_id,
                    "checkpoint_id": checkpoint_id,
                    "status": STATUS_FAILED,
                    "reason_code": "CHECKPOINT_NOT_FOUND",
                },
            )

        # 2. 构建 InterruptSignal（从 state 中恢复）
        resume_deadline_at = int(state.get("resume_deadline_at") or (current_ms + 300_000))
        interrupt_signal = InterruptSignal(
            checkpoint_id=checkpoint_id,
            interrupt_reason=str(state.get("interrupt_reason") or INTERRUPT_REASON_HUMAN_REVIEW),
            interrupted_at_ms=int(state.get("policy_start_ms") or current_ms),
            resume_deadline_at=resume_deadline_at,
        )
        decision = manager.resume(
            interrupt_signal,
            now_ms=current_ms,
            policy_timeout_ms=int(state.get("policy_timeout_ms") or DEFAULT_POLICY_TIMEOUT_MS),
            policy_start_ms=int(state.get("policy_start_ms") or current_ms),
        )

        if not decision.allowed:
            # 验证失败：写入 SyntheticTimeoutEvent
            synthetic = decision.synthetic_event or SyntheticTimeoutEvent(
                checkpoint_id=checkpoint_id,
                error_code=RESUME_DENIED_DEADLINE_EXCEEDED,
                ts_ms=current_ms,
            ).to_dict()
            _evidence_executor.submit(self._write_evidence, request_id=request_id, state=state)
            return GraphRunResult(
                status=STATUS_FAILED,
                reason_code=str(synthetic.get("error_code") or "RESUME_DENIED"),
                stage=S_ORCH_RUN,
                detail=decision.reason,
                output={
                    "request_id": request_id,
                    "checkpoint_id": checkpoint_id,
                    "status": STATUS_FAILED,
                    "reason_code": str(synthetic.get("error_code") or "RESUME_DENIED"),
                    "synthetic_event": synthetic,
                    "resume_decision": decision.to_dict(),
                },
            )

        # 4. 验证通过：从 checkpoint 恢复并继续执行
        executor = GraphExecutor(
            handlers=self._handlers,
            gateway_factory=self._gateway_factory,
            default_timeout_ms=self.default_timeout_ms,
            ledger=self.ledger,
        )
        compiled = executor.compile_runtime_graph()
        # 更新 state：清除 checkpoint 中断标记，重置 resume_allowed
        resumed_state = {
            **state,
            "gov_checkpoint_id": checkpoint_id,
            "resume_allowed": True,
            "interrupt_reason": None,
        }
        final_state = compiled.invoke(resumed_state)
        _evidence_executor.submit(self._write_evidence, request_id=request_id, state=final_state)

        return GraphRunResult(
            status=str(final_state.get("status") or STATUS_OK),
            reason_code=(str(final_state["reason_code"]).strip() if final_state.get("reason_code") else None),
            stage=S_ORCH_RUN,
            detail=str(final_state.get("detail") or "graph resumed"),
            output={
                "request_id": request_id,
                "checkpoint_id": checkpoint_id,
                "graph_id": str(final_state.get("graph_id") or ""),
                "graph_status": str(final_state.get("status") or STATUS_OK),
                "reason_code": final_state.get("reason_code"),
                "stage": S_ORCH_RUN,
                "payload": dict(final_state.get("payload") or {}),
                "graph_trace": list(final_state.get("trace") or []),
                "resume_decision": decision.to_dict(),
            },
        )

    def _default_gateway_factory(self, **kwargs: Any) -> LlmGatewayContract:
        return LlmGatewayContract(**kwargs)

    def _write_evidence(self, *, request_id: str, state: Any) -> None:
        """M36-F：图执行完成后写入 Evidence Plane（静默降级）。"""
        try:
            from host_brain.integrations.graph_evidence import get_evidence_plane
            ep = get_evidence_plane()
            graph_id = str((state or {}).get("graph_id") or "")
            # 写入 trace
            trace = list((state or {}).get("trace") or [])
            if trace:
                ep.write_trace(request_id=request_id, graph_id=graph_id, trace=trace)
            # 写入 subgraph_commit_summary
            summary = ((state or {}).get("payload") or {}).get("subgraph_commit_summary")
            if summary:
                ep.write_subgraph_summary(request_id=request_id, summary=summary)
            # 写入 clock_events
            clock_events = list((state or {}).get("clock_events") or [])
            if clock_events:
                ep.write_clock_events(request_id=request_id, graph_id=graph_id, clock_events=clock_events)
            # M37-L：写入 branch_manifest
            branch_manifest = (state or {}).get("branch_manifest")
            if branch_manifest:
                ep.write_branch_manifest(request_id=request_id, graph_id=graph_id, manifest=branch_manifest)
            # M38-I：写入 streaming 事件
            presence_events = list((state or {}).get("presence_events") or [])
            if presence_events:
                ep.write_stream_events(request_id=request_id, graph_id=graph_id, events=presence_events)
            # M39-H：写入 checkpoint 信息
            gov_checkpoint_id = str((state or {}).get("gov_checkpoint_id") or "").strip()
            if gov_checkpoint_id:
                ep.write_checkpoint(
                    checkpoint_id=gov_checkpoint_id,
                    request_id=request_id,
                    graph_id=graph_id,
                    interrupt_reason=str((state or {}).get("interrupt_reason") or ""),
                    resume_deadline_at=int((state or {}).get("resume_deadline_at") or 0),
                )
            # M40-I：写入 barrier_join 结果
            barrier_join_result = (state or {}).get("barrier_join_result")
            if barrier_join_result and isinstance(barrier_join_result, dict):
                ep.write_barrier_join(
                    request_id=request_id,
                    graph_id=graph_id,
                    barrier_join_result=barrier_join_result,
                )
        except Exception:
            pass  # 静默降级，不影响主流程

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
