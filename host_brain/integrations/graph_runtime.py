"""图编排运行时的宿主入口。

该门面按固定顺序完成配置契约校验、模型节点解析、LangGraph 执行、恢复裁决和
证据归档。它不实现节点业务逻辑，而是把各专门模块连接成一条可审计的调用链，
使任何未通过校验的图都不会进入副作用阶段。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from host_brain.integrations.chat_runtime_contract import (
    get_chat_runtime_emotion_state,
    get_chat_runtime_graph,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.core.errors import ORCH_PARAM_DRIFT
from host_brain.integrations.graph_contract import (
    DEFAULT_MODEL_PLANE_DIR,
    DEFAULT_POLICY_TIMEOUT_MS,
    GraphRunResult,
    GraphState,
    GatewayFactory,
    NodeHandler,
    S_ORCH_RUN,
    S_ORCH_VALIDATE,
    STATUS_FAILED,
    STATUS_OK,
    build_edge_map,
    pick_model_profile_id,
    validate_graph_config,
)
from host_brain.integrations.graph_model_resolve import (
    resolve_model_nodes,
    sanitize_model_node_spec,
)
from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_checkpoint import HostPolicyGuard
from host_brain.integrations.llm_gateway import LlmGatewayContract
from host_brain.integrations.governor_rules_cache import GovernorRulesCache, get_governor_rules_cache

# Evidence Plane 专用线程池：最多 2 线程，不阻塞主图执行路径
_evidence_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence")

# 注册 atexit 钩子，确保进程退出时线程池优雅关闭，防止线程泄漏
import atexit as _atexit
_atexit.register(_evidence_executor.shutdown, wait=False, cancel_futures=True)

_logger = logging.getLogger(__name__)


def _inject_execution_profile_from_payload_only(
    payload_dict: Mapping[str, Any],
    graph_config: Mapping[str, Any],
    request_id: str,
) -> str | None:
    # 执行档案由请求输入单向决定；配置元数据只能用于发现不一致，不能覆盖入口选择。
    """§14.3：execution_profile 唯一真相源为 payload；graph_meta 仅用于一致性校验与日志。"""
    from_payload = str((payload_dict.get("execution_profile") or "").strip()) or None
    meta = graph_config.get("graph_meta") or {}
    from_meta = str((meta.get("execution_profile") or "").strip()) or None
    if from_meta and from_payload and from_meta != from_payload:
        _logger.warning(
            "execution_profile mismatch: payload=%r vs graph_meta=%r; using payload (request_id=%s)",
            from_payload,
            from_meta,
            request_id,
        )
    return from_payload


def _sanitize_model_nodes_for_state(model_nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """向图状态写入规范且无秘密的模型节点副本，避免恢复快照保存运行时能力。"""

    return {
        str(model_node_id): sanitize_model_node_spec(str(model_node_id), spec)
        for model_node_id, spec in model_nodes.items()
    }


class GraphRuntimeContract:
    """以 LangGraph 为执行后端的确定性图运行时门面。

    公共入口负责装配受信能力，执行细节委托给 ``GraphExecutor``，配置合法性和
    模型选择委托给契约层。分层让调用方无法跳过校验直接触发模型、路由或恢复。
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        default_timeout_ms: int = 5_000,
        gateway_factory: GatewayFactory | None = None,
        model_plane_dir: str | Path | None = None,
        model_attempt_service: Any = None,
    ) -> None:
        """注入持久化、模型调用和网关能力；节点处理器仍需显式注册。"""
        self.ledger = ledger
        self.default_timeout_ms = int(default_timeout_ms)
        self._handlers: dict[str, NodeHandler] = {}
        self._gateway_factory = gateway_factory or LlmGatewayContract
        self._model_plane_dir = Path(model_plane_dir) if model_plane_dir is not None else DEFAULT_MODEL_PLANE_DIR
        self._model_attempt_service = model_attempt_service
        self._policy_guard = HostPolicyGuard()
        # FIX-4.1：获取 GovernorRulesCache 单例（TRIBE 族 → Governor 参数通道）
        self._governor_rules_cache: GovernorRulesCache = get_governor_rules_cache()

    def register_node_handler(self, handler_id: str, handler: NodeHandler) -> None:
        """注册具名节点处理器；空身份被拒绝，防止图绑定退化为不可追溯的调用。"""
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
        """运行一张已声明图，并始终返回包含阶段、原因码与重放关联的稳定结果。"""
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # 1. 契约层：校验配置
        validation = validate_graph_config(graph_config)
        if validation["status"] == STATUS_FAILED:
            # 构建期问题在这里终结：绝不让缺节点或悬空边进入执行器后再猜测处理。
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
        except (KeyError, ValueError) as exc:
            # 模型解析错误属于运行前契约失败，尚未发出任何实际模型调用。
            reason_code = ORCH_PARAM_DRIFT if isinstance(exc, ValueError) else "ORCH_NODE_MISSING"
            return self._finalize(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code=reason_code,
                stage=S_ORCH_VALIDATE,
                detail=str(exc),
                graph_config=graph_config,
                graph_trace=[],
                payload=dict(payload or {}),
                model_calls=[],
                model_profile_id=model_profile_id,
                ts_ms=now_value,
            )

        # 3. 执行层：编译并运行图（FIX-5.1：compile_runtime_graph 内部直接注入 LangGraph SqliteSaver）
        executor = GraphExecutor(
            handlers=self._handlers,
            gateway_factory=self._gateway_factory,
            default_timeout_ms=self.default_timeout_ms,
            ledger=self.ledger,
            model_nodes=model_nodes,
            model_attempt_service=self._model_attempt_service,
        )
        compiled = executor.compile_runtime_graph()
        # 状态只保存净化后的模型规格，检查点恢复也不会重新暴露密钥或可调用对象。
        serialized_model_nodes = _sanitize_model_nodes_for_state(model_nodes)
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
            "model_nodes": serialized_model_nodes,
            "model_profile_id": model_profile_id,
            "model_calls": [],
            "emotion_context": (emotion_context or None),
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "emotion_range_hint": emotion_range_hint,
            # M35-B：从 payload 提取 Governor 已编译的 committed_route_token
            # 由 chat runtime 在 Governor 裁决后、调用 run_graph 前注入
            "committed_route_token": str(
                graph_section.get("committed_route_token") or payload_dict.get("committed_route_token") or ""
            ).strip() or None,
            "committed_route_token_detail": (
                graph_section.get("committed_route_token_detail")
                or payload_dict.get("committed_route_token_detail")
                or None
            ),
            # FIX-4.1：TRIBE 族写入的 Governor 规则参数（L3 → L2 单向通道）
            # 在图执行前快照当前 TRIBE 族参数，供 Governor 裁决和 Evidence Plane 审计使用
            "tribe_governor_params": self._governor_rules_cache.get_effective_rules(),
            "immune_threat_level": None,
            "endocrine_state": None,
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
            # M-MA3：Multi-Agent GraphState 顶层字段（统一入口，不再写 payload["agent_runtime"]）
            "active_agent_id": None,
            "handoff_history": [],
            "agent_budget": {
                "max_hops": 2,
                "per_agent_timeout_ms": 12000,
                "total_budget_ms": 30000,
            },
            "agent_artifacts": {},
            "judge_result": None,
            # §14.3 真相源单源：仅从 payload 注入；graph_meta 仅做一致性校验与日志
            "execution_profile": _inject_execution_profile_from_payload_only(
                payload_dict=payload_dict,
                graph_config=graph_config,
                request_id=request_id,
            ),
        }
        initial_state["payload"] = payload_dict
        # request_id 同时是 LangGraph thread_id，令检查点、审计和请求谱系拥有同一稳定身份。
        # FIX-5.1：使用 LangGraph thread_id（request_id）执行图，SqliteSaver 自动保存 checkpoint
        _graph_run_start_ms = now_ms()
        state = compiled.invoke(
            initial_state,
            config={"configurable": {"thread_id": request_id}},
        )
        _graph_run_end_ms = now_ms()

        # P5-4：Supervisor 决策写入 ledger，供 metrics 聚合
        _sd = (state.get("payload") or {}).get("supervisor_dispatch")
        if isinstance(_sd, dict) and self.ledger is not None:
            try:
                self.ledger.log_supervisor_decision({
                    "request_id": request_id,
                    "ts_ms": now_value,
                    "supervisor_mode": str(_sd.get("supervisor_mode") or "rule").strip(),
                    "supervisor_decision_source": str(_sd.get("supervisor_decision_source") or "rule").strip(),
                    "supervisor_failure_code": str(_sd.get("supervisor_failure_code") or "").strip() or None,
                    "supervisor_selected_agent": str(_sd.get("supervisor_selected_agent") or _sd.get("selected_agent") or "").strip(),
                })
            except Exception:
                pass  # 静默降级，不影响主流程

        # P8-1：run 完成后写入 P8 ledger 专用表（静默降级，不影响主流程）
        if self.ledger is not None:
            self._write_p8_ledger(
                request_id=request_id,
                state=state,
                ts_ms=_graph_run_end_ms,
                run_start_ms=_graph_run_start_ms,
            )

        # M36-F：图执行完成后异步写入 Evidence Plane（不阻塞主流程返回）
        _evidence_executor.submit(self._write_evidence, request_id=request_id, state=state)
        # 归档故障不能改变已完成请求的结果；证据写入在线程池中尽力执行。

        # 4. 最终化输出（P4/P6：subgraph_trace 由 handler 写入 state，一并返回供审计/验收）
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
            subgraph_trace=state.get("subgraph_trace"),
        )

    def resume_graph(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        now_ms: int | None = None,
    ) -> GraphRunResult:
        # 检查点证明状态曾被保存，不证明其仍可执行；恢复窗口和全局策略预算都必须
        # 在当前时刻重新校验，拒绝时返回合成超时结果而不重新进入图。
        """从 checkpoint 恢复图执行（FIX-5.1：LangGraph 原生 thread_id 恢复）。

        恢复前调用 HostPolicyGuard 验证 Policy Clock（不变）：
        - 验证失败时写入 SyntheticTimeoutEvent 并返回 STATUS_FAILED
        - 验证通过时使用 LangGraph 原生 thread_id 恢复（真正的断点恢复，不重新执行整张图）

        FIX-5.1 变更：
        - 不再依赖旧的手工 load_state 恢复路径
        - 使用 compiled.invoke(None, config={"configurable": {"thread_id": checkpoint_id}}) 恢复
        - checkpoint_id 即 LangGraph thread_id（run_graph 时使用 request_id 作为 thread_id）

        Args:
            checkpoint_id: checkpoint ID（即 LangGraph thread_id，通常等于 request_id）
            request_id: 请求 ID
            now_ms: 当前时间戳（毫秒），默认为 now_ms()

        Returns:
            GraphRunResult（恢复执行结果或 synthetic timeout 结果）
        """
        from host_brain.integrations.graph_checkpoint import (
            InterruptSignal,
            SyntheticTimeoutEvent,
            check_resume_allowed,
        )
        from host_brain.integrations.graph_contract import (
            INTERRUPT_REASON_HUMAN_REVIEW,
            RESUME_DENIED_DEADLINE_EXCEEDED,
        )
        from host_brain.core.ids import now_ms as _now_ms

        current_ms = int(now_ms if now_ms is not None else _now_ms())

        # 1. Policy Clock 检查（保留不变）
        # 从 LangGraph SqliteSaver 中读取最新 checkpoint 的 state 元数据
        # 用于构建 InterruptSignal（resume_deadline_at / policy_timeout_ms）
        try:
            # 尝试从 LangGraph SqliteSaver 读取 checkpoint 元数据
            from host_brain.integrations.graph_checkpoint import get_langgraph_checkpointer  # noqa: PLC0415
            lg_checkpointer = get_langgraph_checkpointer()
            config = {"configurable": {"thread_id": checkpoint_id}}
            checkpoint_tuple = lg_checkpointer.get_tuple(config)
            if checkpoint_tuple is None:
                # 不把未知检查点当作新运行：那会混淆恢复语义并绕开原始输入校验。
                return GraphRunResult(
                    status=STATUS_FAILED,
                    reason_code="CHECKPOINT_NOT_FOUND",
                    stage=S_ORCH_RUN,
                    detail=f"LangGraph checkpoint 不存在: thread_id={checkpoint_id}",
                    output={
                        "request_id": request_id,
                        "checkpoint_id": checkpoint_id,
                        "status": STATUS_FAILED,
                        "reason_code": "CHECKPOINT_NOT_FOUND",
                    },
                )
            # FIX-P2：多路径读取 Policy Clock 字段，避免依赖 LangGraph 内部格式
            # LangGraph 不同版本的 checkpoint 格式可能不同，使用多路径兜底策略：
            # 路径1：channel_values（LangGraph 0.x 标准格式）
            # 路径2：pending_writes（部分版本的中间状态）
            # 路径3：metadata（自定义元数据字段）
            # 路径4：直接访问 checkpoint 顶层字段（最宽松的兜底）
            ckpt = checkpoint_tuple.checkpoint or {}
            channel_values = ckpt.get("channel_values") or {}

            def _read_policy_field(field_name: str, default: Any) -> Any:
                # 兼容不同 LangGraph 存储布局只为读取既有事实，不为缺失字段伪造授权。
                """多路径读取 Policy Clock 字段，任一路径命中即返回。"""
                # 路径1：channel_values（标准格式）
                v = channel_values.get(field_name)
                if v is not None:
                    return v
                # 路径2：pending_writes（中间状态格式）
                for write_entry in (ckpt.get("pending_writes") or []):
                    if isinstance(write_entry, (list, tuple)) and len(write_entry) >= 2:
                        if write_entry[0] == field_name:
                            return write_entry[1]
                    elif isinstance(write_entry, dict) and write_entry.get("key") == field_name:
                        return write_entry.get("value")
                # 路径3：checkpoint_tuple.metadata（自定义元数据）
                meta = getattr(checkpoint_tuple, "metadata", None) or {}
                if isinstance(meta, dict):
                    v = meta.get(field_name)
                    if v is not None:
                        return v
                # 路径4：checkpoint 顶层字段（最宽松兜底）
                v = ckpt.get(field_name)
                if v is not None:
                    return v
                return default

            resume_deadline_at = int(_read_policy_field("resume_deadline_at", current_ms + 300_000))
            policy_timeout_ms = int(_read_policy_field("policy_timeout_ms", DEFAULT_POLICY_TIMEOUT_MS))
            policy_start_ms = int(_read_policy_field("policy_start_ms", current_ms))
            interrupt_reason = str(_read_policy_field("interrupt_reason", INTERRUPT_REASON_HUMAN_REVIEW))
        except Exception as exc:
            # 元数据无法读取时采用保守的新窗口；后续策略守卫仍可能拒绝恢复。
            _logger.warning("resume_graph: 读取 LangGraph checkpoint 元数据失败（%s），使用默认值", exc)
            resume_deadline_at = current_ms + 300_000
            policy_timeout_ms = DEFAULT_POLICY_TIMEOUT_MS
            policy_start_ms = current_ms
            interrupt_reason = INTERRUPT_REASON_HUMAN_REVIEW

        interrupt_signal = InterruptSignal(
            checkpoint_id=checkpoint_id,
            interrupt_reason=interrupt_reason,
            interrupted_at_ms=policy_start_ms,
            resume_deadline_at=resume_deadline_at,
        )
        decision = check_resume_allowed(
            interrupt_signal=interrupt_signal,
            now_ms=current_ms,
            policy_timeout_ms=policy_timeout_ms,
            policy_start_ms=policy_start_ms,
            policy_guard=self._policy_guard,
        )

        if not decision.allowed:
            # 策略拒绝即终止恢复，不调用 compiled.invoke，确保暂停不能变成绕过时钟的入口。
            synthetic = decision.synthetic_event or SyntheticTimeoutEvent(
                checkpoint_id=checkpoint_id,
                error_code=RESUME_DENIED_DEADLINE_EXCEEDED,
                ts_ms=current_ms,
            ).to_dict()
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

        # 2. Policy Clock 通过：使用 LangGraph 原生 thread_id 恢复（真正的断点恢复）
        executor = GraphExecutor(
            handlers=self._handlers,
            gateway_factory=self._gateway_factory,
            default_timeout_ms=self.default_timeout_ms,
            ledger=self.ledger,
            model_attempt_service=self._model_attempt_service,
        )
        compiled = executor.compile_runtime_graph()
        # FIX-5.1：传 None 作为 input，LangGraph 从 thread_id 对应的 checkpoint 恢复
        # 不重新执行整张图，从上次中断的节点继续执行
        final_state = compiled.invoke(
            None,
            config={"configurable": {"thread_id": checkpoint_id}},
        )
        final_state = dict(final_state) if final_state else {}
        _evidence_executor.submit(self._write_evidence, request_id=request_id, state=final_state)
        # 恢复轨迹同样异步归档，不能因观测后端暂不可用而重放节点。

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

    def _write_p8_ledger(
        self,
        *,
        request_id: str,
        state: Any,
        ts_ms: int,
        run_start_ms: int,
    ) -> None:
        # 尽力写入子图/预算运行记录；分类或存储异常不得干扰主图响应。
        """P8-1：run 完成后将 subgraph_failure / local_loop failure / 延迟 / budget breach 写入 ledger 专用表。

        归类优先级（两者互斥，不重复计数）：
          error_code 属于 LOCAL_LOOP_* → log_category='local_loop'
          其余 → log_category='subgraph'
        """
        from host_brain.audit.ledger_constants import P8_LOCAL_LOOP_FAILURE_CODES
        from host_brain.integrations.graph_contract import is_budget_breach_failure_code

        try:
            execution_profile = str((state or {}).get("execution_profile") or "").strip() or None
            status = str((state or {}).get("status") or "").strip() or None
            payload = dict((state or {}).get("payload") or {})

            # 1. 写入 subgraph_failure / local_loop failure 记录
            subgraph_failure = payload.get("subgraph_failure")
            if isinstance(subgraph_failure, dict):
                error_code = str(subgraph_failure.get("error_code") or "").strip()
                if error_code:
                    # 归类优先级：LOCAL_LOOP_* 优先归入 local_loop
                    if error_code in P8_LOCAL_LOOP_FAILURE_CODES:
                        log_category = "local_loop"
                    else:
                        log_category = "subgraph"
                    self.ledger.log_p8_subgraph_failure({
                        "request_id": request_id,
                        "ts_ms": ts_ms,
                        "execution_profile": execution_profile,
                        "log_category": log_category,
                        "error_code": error_code,
                        "failed_node": str(subgraph_failure.get("failed_node") or "").strip() or None,
                        "detail": str(subgraph_failure.get("safe_fallback_reason") or "").strip() or None,
                    })

            # 2. 写入 graph_run_log（延迟 + budget breach）
            # latency_ms = ts_ms（图执行结束时间）- run_start_ms（图执行开始时间）
            actual_latency_ms = max(0, int(ts_ms) - int(run_start_ms)) if run_start_ms else None
            budget_breach_code: str | None = None
            reason_code = str((state or {}).get("reason_code") or "").strip() or None
            if reason_code and is_budget_breach_failure_code(reason_code):
                budget_breach_code = reason_code
            # 也检查 payload 中的 subgraph_failure（Intent 图 budget breach 可能在此上报）
            if not budget_breach_code and isinstance(subgraph_failure, dict):
                sf_code = str(subgraph_failure.get("error_code") or "").strip()
                if sf_code and is_budget_breach_failure_code(sf_code):
                    budget_breach_code = sf_code

            self.ledger.log_p8_graph_run({
                "request_id": request_id,
                "ts_ms": ts_ms,
                "execution_profile": execution_profile,
                "latency_ms": actual_latency_ms,
                "status": status,
                "budget_breach_code": budget_breach_code,
            })
        except Exception:
            pass  # 静默降级，不影响主流程

    def _write_evidence(self, *, request_id: str, state: Any) -> None:
        """经组合层拥有的类型化仓储追加证据，避免运行时直接拼接持久化细节。"""
        try:
            from host_brain.persistence.graph_evidence_repository import (  # noqa: PLC0415
                GraphEvidenceRepository,
            )

            repository = GraphEvidenceRepository.from_ledger(self.ledger)
            graph_id = str((state or {}).get("graph_id") or "")
            # 写入 trace
            trace = list((state or {}).get("trace") or [])
            if trace:
                repository.append_traces(request_id=request_id, graph_id=graph_id, trace=trace)
            # 写入 subgraph_commit_summary
            summary = ((state or {}).get("payload") or {}).get("subgraph_commit_summary")
            if summary:
                repository.append_subgraph_summary(request_id=request_id, summary=summary)
            # 写入 clock_events
            clock_events = list((state or {}).get("clock_events") or [])
            if clock_events:
                repository.append_clock_events(request_id=request_id, graph_id=graph_id, events=clock_events)
            # M37-L：写入 branch_manifest
            branch_manifest = (state or {}).get("branch_manifest")
            if branch_manifest:
                repository.append_branch_manifest(request_id=request_id, graph_id=graph_id, manifest=branch_manifest)
            # M38-I：写入 streaming 事件
            presence_events = list((state or {}).get("presence_events") or [])
            if presence_events:
                repository.append_stream_events(request_id=request_id, graph_id=graph_id, events=presence_events)
            # M39-H：写入 checkpoint 信息
            gov_checkpoint_id = str((state or {}).get("gov_checkpoint_id") or "").strip()
            if gov_checkpoint_id:
                repository.save_checkpoint(
                    checkpoint_id=gov_checkpoint_id,
                    request_id=request_id,
                    graph_id=graph_id,
                    interrupt_reason=str((state or {}).get("interrupt_reason") or ""),
                    resume_deadline_at=int((state or {}).get("resume_deadline_at") or 0),
                )
            # M40-I：写入 barrier_join 结果
            barrier_join_result = (state or {}).get("barrier_join_result")
            if barrier_join_result and isinstance(barrier_join_result, dict):
                repository.append_barrier_join(
                    request_id=request_id,
                    graph_id=graph_id,
                    result=barrier_join_result,
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
        subgraph_trace: list[dict[str, Any]] | None = None,
    ) -> GraphRunResult:
        """归一化最终结果并记录错误、模型控制行；输出只含可重放的引用与诊断。"""
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
        if subgraph_trace is not None:
            output["subgraph_trace"] = list(subgraph_trace)
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
        """把每次模型调用的参数漂移和治理引用写入审计账本，不记录令牌值。"""
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
        """在账本可用时记录结构化错误；缺少账本不阻塞对调用方的结果返回。"""
        if self.ledger is None:
            return
        self.ledger.log_error(
            request_id=request_id,
            stage=stage,
            reason_code=reason_code,
            detail=detail,
        )
