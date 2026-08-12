"""图编排的共享契约、配置校验与确定性辅助函数。

这里定义的是执行器可依赖的最小事实：图配置先被确定性地验证，节点和边
才能进入运行期；模型节点的具体解析则刻意放在 ``graph_model_resolve``，
避免配置契约持有提供方或凭据等运行时权力。
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
    ORCH_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Constants / Stages / Statuses
# ---------------------------------------------------------------------------

S_ORCH_VALIDATE = "S_ORCH_VALIDATE"
S_ORCH_RUN = "S_ORCH_RUN"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"
# M35-K：AIRLOCK 暂停状态（GOV_AIRLOCK_REVIEW 令牌触发，等待人工审核）
STATUS_AIRLOCK_PENDING = "AIRLOCK_PENDING"
# M36-L：Policy Clock 超时状态
STATUS_POLICY_TIMEOUT = "POLICY_TIMEOUT"

# M36-G：Dual-Clock 事件类型常量
CLOCK_EVENT_EXEC_TIMEOUT = "EXEC_TIMEOUT"
CLOCK_EVENT_POLICY_TIMEOUT = "POLICY_TIMEOUT"
# M36-G：默认全局策略超时（30 秒）
DEFAULT_POLICY_TIMEOUT_MS = 30_000

# M37-F：只读并行分支常量
BRANCH_STATUS_OK = "OK"
BRANCH_STATUS_FAILED = "FAILED"
# M37-F：默认并行分支最大线程数
DEFAULT_BRANCH_MAX_WORKERS = 4

# M38-C：Phase-Locked Streaming 流阶段常量
STREAM_PHASE_PRESENCE = "PRESENCE"
STREAM_PHASE_COMMIT = "COMMIT"
STREAM_PHASE_RENDER = "RENDER"
# M38-C：Presence Stream 事件类型常量
STREAM_EVENT_NODE_START = "NODE_START"
STREAM_EVENT_NODE_END = "NODE_END"
STREAM_EVENT_CHUNK = "CHUNK"
STREAM_EVENT_PROGRESS = "PROGRESS"
# M38-C：Render 前置条件失败事件
STREAM_EVENT_RENDER_PRECONDITION_FAILED = "RENDER_PRECONDITION_FAILED"

# M39-C：Checkpoint / Interrupt / Resume 常量
CHECKPOINT_STATUS_SAVED = "SAVED"
CHECKPOINT_STATUS_LOADED = "LOADED"
INTERRUPT_REASON_HUMAN_REVIEW = "HUMAN_REVIEW"
INTERRUPT_REASON_POLICY_TIMEOUT = "POLICY_TIMEOUT"
INTERRUPT_REASON_AIRLOCK = "AIRLOCK"
RESUME_DENIED_DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
RESUME_DENIED_POLICY_EXPIRED = "POLICY_EXPIRED"
SYNTHETIC_EVENT_TIMEOUT = "TIMEOUT_SYNTHETIC"
# M39-C：默认恢复截止时间（5 分钟）
DEFAULT_RESUME_DEADLINE_MS = 300_000

# M40-D：barrier_join 相关常量
BARRIER_JOIN_POLICY_STRICT = "strict"   # 严格模式：任一分支缺证据则 fail closed
BARRIER_JOIN_POLICY_PARTIAL = "partial"  # 部分成功模式：允许部分分支失败，但必须记录 join_policy_ref
BARRIER_JOIN_STATUS_PASSED = "PASSED"
BARRIER_JOIN_STATUS_FAILED = "FAILED"
# M40-D：RenderGuard 相关常量
RENDER_GUARD_STATUS_PASSED = "PASSED"
RENDER_GUARD_STATUS_FAILED = "FAILED"
# M40-D：允许修改的派生字段集合（render 输出只能修改这些字段）
RENDER_ALLOWED_FIELDS: frozenset[str] = frozenset({"render_text", "render_html", "render_markdown"})
# M40-D：允许修改的派生字段前缀（display_ 前缀字段允许修改）
RENDER_ALLOWED_FIELD_PREFIX = "display_"

DEFAULT_MODEL_PLANE_DIR = Path("configs/model_plane")

NodeHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
GatewayFactory = Callable[..., Any]


# ---------------------------------------------------------------------------
# Shared State TypedDict
# ---------------------------------------------------------------------------

class GraphState(TypedDict):
    """贯穿图执行的受约束状态载体。

    顶层字段区分输入载荷、执行轨迹、治理令牌和恢复元数据。节点应通过各自
    的受权写入路径更新它，而不能把任意控制字段藏进 ``payload`` 来绕过
    编排器的边界。
    """
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
    # M45：情感上下文透传字段（chat -> runtime -> executor -> gateway）
    emotion_context: dict[str, Any] | None
    emotion_tag: str | None
    mood_state: str | None
    emotion_range_hint: str | None
    # M35-A：Route Commitment Rule 字段（ModelPlane.md §10 Rule 1）
    # committed_route_token：Governor 已提交的路由令牌（只能由 Host Governor 写入）
    committed_route_token: str | None
    # committed_route_token_detail：完整 CommittedRouteDecision 字典（审计用）
    committed_route_token_detail: dict[str, Any] | None
    # M35-A：Transparent Subgraph 字段（ModelPlane.md §10 Rule 3）
    # subgraph_trace：透明子图节点执行追踪（只能由 compile_governed_subgraph 写入）
    subgraph_trace: list[dict[str, Any]]
    # M36-B：Dual-Clock Supervision 字段（ModelPlane.md §10 Rule 2）
    # policy_start_ms：图级策略时钟启动时间戳（毫秒）
    policy_start_ms: int | None
    # policy_timeout_ms：全局策略超时阈值（毫秒），超时时强制终止图执行
    policy_timeout_ms: int | None
    # clock_events：时钟事件列表（EXEC_TIMEOUT / POLICY_TIMEOUT）
    clock_events: list[dict[str, Any]]
    # node_exec_stats：节点执行统计（exec_start_ms / exec_end_ms / exec_duration_ms）
    node_exec_stats: dict[str, dict[str, Any]]
    # M37-B：只读并行分支字段（ModelPlane.md §10 Rule 4）
    # branch_specs：分支规格列表（由主图配置注入，只读）
    branch_specs: list[dict[str, Any]]
    # branch_manifest：分支执行结果清单（由 run_parallel_branches 写入）
    branch_manifest: dict[str, Any] | None
    # M38-B：Phase-Locked Streaming 字段（ModelPlane.md §10 Rule 5）
    # presence_events：在场流事件列表（只向 UI 输出，不作为下游节点输入）
    presence_events: list[dict[str, Any]]
    # commit_snapshot：提交快照（由 CommitStreamEmitter 写入，GOV_OK_RENDER 时产生）
    commit_snapshot: dict[str, Any] | None
    # render_snapshot_ref：渲染快照引用（由 RenderStreamEmitter 写入）
    render_snapshot_ref: str | None
    # streaming_phase：当前流阶段（PRESENCE / COMMIT / RENDER / None）
    streaming_phase: str | None
    # M39-B：Checkpoint / Interrupt / Resume 字段（ModelPlane.md §10 Rule 6）
    # gov_checkpoint_id：当前 checkpoint ID（由 graph_checkpoint helper 写入，避免与 LangGraph 保留字段冲突）
    gov_checkpoint_id: str | None
    # interrupt_reason：中断原因（HUMAN_REVIEW / POLICY_TIMEOUT / AIRLOCK）
    interrupt_reason: str | None
    # resume_allowed：是否允许恢复（由 HostPolicyGuard 写入）
    resume_allowed: bool | None
    # resume_deadline_at：恢复截止时间戳（毫秒），超过则写入 SyntheticTimeoutEvent
    resume_deadline_at: int | None
    # M40-C：barrier_join / RenderGuard 字段（ModelPlane.md §10 Rule 4、Rule 5）
    # barrier_join_result：barrier_join 结果（由 barrier_join() 写入）
    barrier_join_result: dict[str, Any] | None
    # render_guard_result：RenderGuard 结果（由 RenderGuard.validate_render_output() 写入）
    render_guard_result: dict[str, Any] | None
    # M-MA3：Multi-Agent 字段（第三阶段统一到 GraphState 顶层，不再写 payload["agent_runtime"]）
    # active_agent_id：当前活跃的子 agent ID
    active_agent_id: str | None
    # handoff_history：agent handoff 历史记录列表
    handoff_history: list[dict[str, Any]]
    # agent_budget：agent 预算（max_hops / per_agent_timeout_ms / total_budget_ms）
    agent_budget: dict[str, Any] | None
    # agent_artifacts：子 agent 输出工件集合 {agent_id: artifact_dict}
    agent_artifacts: dict[str, Any] | None
    # judge_result：JudgeAgent 裁判结果
    judge_result: dict[str, Any] | None
    # execution_profile：入口层写入，决定跑哪张图；仅入口可写，下游只读
    execution_profile: str | None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphRunResult:
    """图边界对外返回的不可变结果，保留阶段与失败码以便上层安全降级。"""
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


# ---------------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------------

def validate_graph_config(graph_config: Mapping[str, Any]) -> dict[str, Any]:
    """以稳定排序验证图配置，并在进入执行器前汇总所有结构性问题。

    校验只接纳映射形态的节点和边，入口必须存在，所有边必须指向已声明节点；
    无法由处理器、模型节点或图级模型配置解释的节点也会被拒绝。排序保证相同
    配置始终给出相同的首个失败原因，便于审计、重放和调用方的确定性处理。
    """
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
    # 主执行链只允许每个起点确定一个后继；保留配置中的首条边防止静默改写路由。
    """Build from→to edge map from edge list."""
    edge_map: dict[str, str] = {}
    for edge in edges:
        edge_from = str(edge.get("from") or "").strip()
        edge_to = str(edge.get("to") or "").strip()
        if edge_from and edge_to and edge_from not in edge_map:
            edge_map[edge_from] = edge_to
    return edge_map


def pick_model_profile_id(graph_config: Mapping[str, Any]) -> str | None:
    """提取可选模型档案身份；空值保持 ``None``，不在契约层隐式挑选默认模型。"""
    value = str(graph_config.get("model_profile_id") or "").strip()
    return value or None


def build_model_param_hash(*, provider: str, model_id: str, temperature: Any, max_output_tokens: Any, timeout_ms: Any) -> str:
    """对实际模型参数生成规范化哈希，用于发现解析、覆写或恢复时的路由漂移。"""
    canonical = {"provider": str(provider), "model_id": str(model_id), "temperature": temperature, "max_output_tokens": max_output_tokens, "timeout_ms": timeout_ms}
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_governance_refs(*, state: GraphState) -> dict[str, str | None]:
    """从请求与意图中提取治理关联，且不生成缺失引用。

    请求载荷优先于嵌套意图，最后才回退到状态请求 ID；该顺序让入口显式传入
    的授权、证据与意图身份保留其语义，而非由节点自行猜测。
    """
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
    """归一化依赖可用性，兼容旧布尔输入而为执行器提供固定输出形状。"""
    if isinstance(raw, Mapping):
        return {"available": bool(raw.get("available", True)), "detail": str(raw.get("detail") or "")}
    if isinstance(raw, bool):
        return {"available": bool(raw), "detail": ""}
    return {"available": True, "detail": ""}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _issue(*, reason_code: str, detail: str, failed_artifact: str) -> dict[str, Any]:
    """构造统一校验问题记录，使失败可按阶段、工件和原因码关联。"""
    return {"check_id": S_ORCH_VALIDATE, "reason_code": str(reason_code), "detail": str(detail), "failed_artifact": str(failed_artifact)}


# ---------------------------------------------------------------------------
# M37-F：只读并行分支辅助函数
# ---------------------------------------------------------------------------

def build_branch_manifest_summary(manifest_dict: dict[str, Any]) -> dict[str, Any]:
    """从 branch_manifest 字典中提取摘要信息。

    Args:
        manifest_dict: build_branch_manifest_dict() 返回的字典

    Returns:
        包含 total_count、success_count、fail_count 的摘要字典
    """
    return {
        "total_count": int(manifest_dict.get("total_count") or 0),
        "success_count": int(manifest_dict.get("success_count") or 0),
        "fail_count": int(manifest_dict.get("fail_count") or 0),
        "committed_at_ms": int(manifest_dict.get("committed_at_ms") or 0),
    }


# ---------------------------------------------------------------------------
# M36-G：Dual-Clock 辅助函数
# ---------------------------------------------------------------------------

def build_clock_event(
    *,
    event_type: str,
    node_id: str,
    elapsed_ms: int,
    threshold_ms: int,
) -> dict[str, Any]:
    """构建时钟事件字典，供 Evidence Plane 写入和 clock_events 追加。

    Args:
        event_type: 事件类型（EXEC_TIMEOUT / POLICY_TIMEOUT）
        node_id: 触发事件的节点 ID
        elapsed_ms: 已耗时（毫秒）
        threshold_ms: 超时阈值（毫秒）

    Returns:
        时钟事件字典（含 event_type、node_id、elapsed_ms、threshold_ms、ts_ms）
    """
    import time as _time
    return {
        "event_type": str(event_type),
        "node_id": str(node_id),
        "elapsed_ms": int(elapsed_ms),
        "threshold_ms": int(threshold_ms),
        "ts_ms": int(_time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# M35-F：Transparent Subgraph 辅助函数
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# M-MA3：Multi-Agent 校验辅助函数
# ---------------------------------------------------------------------------

def validate_agent_handoff_target(
    target_agent_id: str,
    allowed_agents: frozenset[str] | None = None,
) -> tuple[bool, str]:
    """校验 handoff 目标 agent_id 是否合法。

    Args:
        target_agent_id: 目标 agent ID
        allowed_agents: 允许的 agent ID 集合（None 时使用默认 allowlist）

    Returns:
        (is_valid, reason)：is_valid=True 表示目标合法
    """
    if allowed_agents is None:
        # 延迟导入，避免循环依赖
        try:
            from host_brain.agent_runtime.agent_registry import AGENT_ALLOWLIST  # noqa: PLC0415
            allowed_agents = AGENT_ALLOWLIST
        except ImportError:
            return False, "AGENT_REGISTRY_UNAVAILABLE"

    target = str(target_agent_id or "").strip()
    if not target:
        return False, "HANDOFF_TARGET_EMPTY"
    if target not in allowed_agents:
        return False, f"HANDOFF_TARGET_NOT_ALLOWED:{target}"
    return True, "HANDOFF_TARGET_VALID"


def validate_risk_mode(risk_mode: str) -> tuple[bool, str]:
    """校验 risk_mode 是否合法。

    Args:
        risk_mode: 风险模式字符串

    Returns:
        (is_valid, reason)
    """
    try:
        from host_brain.agent_runtime.agent_contract import VALID_RISK_MODES  # noqa: PLC0415
        valid_modes = VALID_RISK_MODES
    except ImportError:
        valid_modes = frozenset({"safe", "guarded", "strict"})

    mode = str(risk_mode or "").strip()
    if not mode:
        return False, "RISK_MODE_EMPTY"
    if mode not in valid_modes:
        return False, f"RISK_MODE_INVALID:{mode}"
    return True, "RISK_MODE_VALID"


def extract_agent_runtime(state: "GraphState") -> dict[str, Any]:
    """从 GraphState 顶层字段提取 agent_runtime 结构。

    第三阶段已统一到 GraphState 顶层，直接读取顶层字段。

    Args:
        state: 当前图状态

    Returns:
        agent_runtime 字典（可能为空字典）
    """
    return {
        "active_agent_id": state.get("active_agent_id"),
        "handoff_history": list(state.get("handoff_history") or []),
        "agent_budget": dict(state.get("agent_budget") or {}),
        "agent_artifacts": dict(state.get("agent_artifacts") or {}),
        "judge_result": state.get("judge_result"),
    }


def build_subgraph_commit_summary(
    *,
    subgraph_id: str,
    subgraph_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """将 subgraph_trace 展平为 subgraph_commit_summary，供 Evidence Plane 写入。

    Args:
        subgraph_id: 子图 ID
        subgraph_trace: 子图节点执行追踪列表

    Returns:
        subgraph_commit_summary: 包含 subgraph_id、node_count、success_count、
            fail_count、graph_trace_flat、committed_at_ms
    """
    import time as _time

    node_count = len(subgraph_trace)
    success_count = sum(
        1 for entry in subgraph_trace
        if str(entry.get("status") or "").upper() in {"OK", "SUCCESS", STATUS_OK}
    )
    fail_count = node_count - success_count

    # 展平：每个节点记录保留 node_id、status、ts_ms、reason_code
    graph_trace_flat: list[dict[str, Any]] = [
        {
            "node_id": str(entry.get("node_id") or ""),
            "status": str(entry.get("status") or STATUS_OK),
            "ts_ms": int(entry.get("ts_ms") or 0),
            "reason_code": entry.get("reason_code"),
        }
        for entry in subgraph_trace
    ]

    return {
        "subgraph_id": str(subgraph_id),
        "node_count": node_count,
        "success_count": success_count,
        "fail_count": fail_count,
        "graph_trace_flat": graph_trace_flat,
        "committed_at_ms": int(_time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# P4 子图 payload 契约（MULTI_AGENT_P4_SCOPE §4 冻结）
# 下游（safety_verifier / judge_agent）仅允许从以下固定键读取，禁止各自发明字段。
# ---------------------------------------------------------------------------

# 子图成功摘要：由 intent_agent_entry（或子图出口）写入 payload，形态由 build_subgraph_commit_summary 产出
PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY = "subgraph_commit_summary"
# 子图失败归一化输出：由 intent_agent_entry 写入 payload，最少包含以下键
PAYLOAD_KEY_SUBGRAPH_FAILURE = "subgraph_failure"
# subgraph_failure 必含字段（P4 §4.2）
SUBGRAPH_FAILURE_KEYS = ("failed_node", "error_code", "safe_fallback_reason", "trace_ref")
# subgraph_commit_summary 必含字段（与 build_subgraph_commit_summary 返回一致）
SUBGRAPH_COMMIT_SUMMARY_KEYS = (
    "subgraph_id", "node_count", "success_count", "fail_count",
    "graph_trace_flat", "committed_at_ms",
)


# ---------------------------------------------------------------------------
# P6 局部循环契约（MULTI_AGENT_P6_SCOPE §7、P6-1）
# 边界控制字段名、失败码、虚拟节点名（用于 failed_node）。
# ---------------------------------------------------------------------------

# 循环边界配置键（由 context / node_binding 注入）
LOCAL_LOOP_CONFIG_MAX_STEPS = "max_local_steps"
LOCAL_LOOP_CONFIG_MAX_RETRY = "max_retry_count"
LOCAL_LOOP_CONFIG_TIMEOUT_MS = "local_loop_timeout_ms"

# 局部循环失败码（与 P4 subgraph_failure 共用外壳，error_code 用下列值）
LOCAL_LOOP_FAILURE_MAX_STEPS = "LOCAL_LOOP_MAX_STEPS"
LOCAL_LOOP_FAILURE_TIMEOUT = "LOCAL_LOOP_TIMEOUT"
LOCAL_LOOP_FAILURE_INTERNAL_ERROR = "LOCAL_LOOP_INTERNAL_ERROR"
LOCAL_LOOP_FAILURE_CODES = (
    LOCAL_LOOP_FAILURE_MAX_STEPS,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    LOCAL_LOOP_FAILURE_INTERNAL_ERROR,
)

# 局部循环虚拟节点名（subgraph_failure.failed_node 在循环边界/超时/内部错误时可用）
LOCAL_LOOP_VIRTUAL_NODE_ID = "local_loop"


# ---------------------------------------------------------------------------
# P5 Supervisor proposal 契约（MULTI_AGENT_P5_SCOPE §5 §6）
# LLM 只产出提案，handler 校验后编译；禁止 proposal 内含控图字段。
# ---------------------------------------------------------------------------

# Proposal 必含字段（selected_agent 必填；need_judge / risk_mode 可有默认值）
SUPERVISOR_PROPOSAL_REQUIRED_KEYS = ("selected_agent",)
# Proposal 允许的键（除禁止键外，仅允许以下或由 handler 忽略未知键）
SUPERVISOR_PROPOSAL_ALLOWED_KEYS = frozenset({
    "selected_agent", "reason", "need_judge", "risk_mode", "budget",
})
# risk_mode 仅允许既定枚举（§5.1）
SUPERVISOR_PROPOSAL_VALID_RISK_MODES = frozenset({"safe", "guarded", "strict"})
# Proposal 中禁止出现的键（控图 / 路由字段不得由 LLM 输出）
SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS = frozenset({
    "__next_node__", "committed_route_token", "branch_specs",
    "execution_profile", "active_agent_id",
})
# 禁止键前缀（凡以此前缀开头的键均不允许）
SUPERVISOR_PROPOSAL_FORBIDDEN_PREFIX = "committed_"

# LLM proposal 失败码（§6.1 统一归类，回退到 rule 时写入；P5-2 口径）
# 分类：JSON 解析失败/非 JSON → BAD_JSON；可解析但缺 selected_agent/禁止键/非法 risk_mode → SCHEMA_INVALID；
# agent 不在 allowlist → AGENT_NOT_ALLOWED；timeout → TIMEOUT；无 gateway/其它 → INTERNAL_ERROR
SUPERVISOR_FAILURE_LLM_TIMEOUT = "LLM_TIMEOUT"
SUPERVISOR_FAILURE_LLM_BAD_JSON = "LLM_BAD_JSON"
SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID = "LLM_SCHEMA_INVALID"
SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED = "LLM_AGENT_NOT_ALLOWED"
SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR = "LLM_INTERNAL_ERROR"
SUPERVISOR_PROPOSAL_FAILURE_CODES = (
    SUPERVISOR_FAILURE_LLM_TIMEOUT,
    SUPERVISOR_FAILURE_LLM_BAD_JSON,
    SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID,
    SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED,
    SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR,
)

# 决策来源（§6.3 审计字段）
SUPERVISOR_DECISION_SOURCE_RULE = "rule"
SUPERVISOR_DECISION_SOURCE_LLM = "llm"
SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE = "llm_fallback_rule"


def validate_supervisor_proposal_for_graph(
    proposal: Mapping[str, Any],
    allowlist: list[str] | tuple[str, ...],
) -> tuple[bool, str | None]:
    """P5：按契约校验 Supervisor proposal，仅允许 allowlist 内 agent。

    Args:
        proposal: LLM 或规则产出的提案字典
        allowlist: 当前图允许的 agent_id 列表（来自 node_binding agent_dispatch_allowlist）

    Returns:
        (is_valid, failure_code): 合法时 failure_code 为 None；非法时返回对应失败码。
    """
    if not isinstance(proposal, Mapping):
        return False, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
    allow_set = frozenset(str(a).strip() for a in (allowlist or []) if str(a).strip())
    # 禁止键检查
    for key in (proposal or {}).keys():
        k = str(key).strip()
        if k in SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS:
            return False, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
        if k.startswith(SUPERVISOR_PROPOSAL_FORBIDDEN_PREFIX):
            return False, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
    # 必填字段
    selected = str((proposal or {}).get("selected_agent") or "").strip()
    if not selected:
        return False, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
    # allowlist 校验（P5 §3.4）
    if allow_set and selected not in allow_set:
        return False, SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED
    # risk_mode 若存在则必须在枚举内
    rm = str((proposal or {}).get("risk_mode") or "").strip().lower()
    if rm and rm not in SUPERVISOR_PROPOSAL_VALID_RISK_MODES:
        return False, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
    return True, None


# ---------------------------------------------------------------------------
# P7 预算治理契约（MULTI_AGENT_P7_SCOPE §3.2）
# 预算层级、配置键名、预算突破失败码集合；仅命名与观测，不改变既有执行逻辑。
# ---------------------------------------------------------------------------

# 预算层级（用于观测与失败矩阵分类）
BUDGET_LEVEL_GRAPH = "graph"
BUDGET_LEVEL_SUBGRAPH = "subgraph"
BUDGET_LEVEL_NODE = "node"
BUDGET_LEVELS = (BUDGET_LEVEL_GRAPH, BUDGET_LEVEL_SUBGRAPH, BUDGET_LEVEL_NODE)

# 图级/子图级/节点级预算配置键名（与现有 graph_config / node_binding 一致，供文档与观测引用）
BUDGET_CONFIG_GRAPH_TIMEOUT_MS = "policy_timeout_ms"
BUDGET_CONFIG_NODE_TIMEOUT_MS = "timeout_ms"
BUDGET_CONFIG_SUBGRAPH_TIMEOUT_MS = LOCAL_LOOP_CONFIG_TIMEOUT_MS
BUDGET_CONFIG_SUBGRAPH_MAX_STEPS = LOCAL_LOOP_CONFIG_MAX_STEPS
BUDGET_CONFIG_SUBGRAPH_MAX_RETRY = LOCAL_LOOP_CONFIG_MAX_RETRY

# 预算突破失败码：所有表示“超时/超步数/重试耗尽”的失败码，供观测聚合与失败矩阵使用
BUDGET_BREACH_FAILURE_CODES = frozenset({
    ORCH_TIMEOUT,
    CLOCK_EVENT_POLICY_TIMEOUT,
    CLOCK_EVENT_EXEC_TIMEOUT,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    LOCAL_LOOP_FAILURE_MAX_STEPS,
    SUPERVISOR_FAILURE_LLM_TIMEOUT,
})


def is_budget_breach_failure_code(code: str | None) -> bool:
    """P7：判断给定 failure/reason_code 是否属于预算突破类（用于观测与聚合）。"""
    if not code:
        return False
    return str(code).strip() in BUDGET_BREACH_FAILURE_CODES


# ---------------------------------------------------------------------------
# FIX-3.2：子图受保护字段显式黑名单（替代字符串前缀匹配）
# ---------------------------------------------------------------------------

SUBGRAPH_PROTECTED_FIELDS: frozenset[str] = frozenset({
    # committed_* 系列（路由主权，只有 Host Governor 可写入）
    "committed_route_token",
    "committed_route_token_detail",
    # Governor 控制字段
    "gov_checkpoint_id",
    "interrupt_reason",
    "resume_allowed",
    "resume_deadline_at",
    "execution_profile",
    # TRIBE 族写入的 Governor 参数（L3 → L2 单向通道，子图不可覆写）
    "tribe_governor_params",
    "immune_threat_level",
    "endocrine_state",
    # 未来新增受保护字段在此处统一维护（不依赖字段名前缀）
})
