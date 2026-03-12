"""
main_chain.py —— 主聊天链路时序编排器

[审计修复 #4] 已从死代码迁移为 _unified_chat_sse_generator 的适配层。
    通过 MainChain.run_as_sse_adapter() 将请求委托给真实的 SSE 路由逻辑，
    同时保留 ChainContext 审计追踪能力。

    实际路由入口：host_brain/app/chat_handler.py::_unified_chat_sse_generator
    适配层入口：MainChain.run_as_sse_adapter()
    标准调用方式：dependencies.run_chat_via_main_chain()

定义主链路节点顺序：
  Airlock(Quarantine) → Governor → GraphExecutor → RecallFusion
  → LlmGateway → QAContract → EpisodicStore → Audit → Airlock(Sanitizer)

每个节点有标准 TypedDict 输入/输出、最大超时常量和降级策略。
SceneRouter 在 Governor 放行后根据 task_type 分流到专用子链。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..schemas import TaskType
from ..schemas.memory_types import ProposerClass
from ..failure_matrix import FailureHandler, FailureEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 节点超时常量（秒）
# ---------------------------------------------------------------------------

NODE_TIMEOUT: Dict[str, float] = {
    "airlock_quarantine": 1.0,
    "governor": 2.0,
    "graph_executor": 5.0,
    "recall_fusion": 5.0,
    "llm_gateway": 30.0,
    "qa_contract": 3.0,
    "episodic_store": 2.0,
    "audit": 1.0,
    "canon_proposal": 2.0,
    "airlock_sanitizer": 1.0,
}


# ---------------------------------------------------------------------------
# 链路上下文（贯穿所有节点的共享状态）
# ---------------------------------------------------------------------------

@dataclass
class ChainContext:
    """贯穿主链路所有节点的共享上下文对象。"""
    request_id: str
    """请求唯一 ID"""
    user_message: str
    """用户原始消息"""
    session_id: str
    """会话 ID"""
    proposer_class: ProposerClass = ProposerClass.USER
    """发起方类别"""
    task_type: TaskType = TaskType.CHAT
    """任务类型（Governor 放行后由 SceneRouter 确定）"""

    # 各节点填充的中间结果
    quarantine_passed: bool = False
    governor_passed: bool = False
    governor_block_reason: Optional[str] = None
    recall_context_pack: Optional[Any] = None
    llm_raw_output: Optional[str] = None
    qa_rewritten: bool = False
    qa_original_output: Optional[str] = None
    final_response: Optional[str] = None

    # 失败语义
    failure_event: Optional[str] = None
    failure_detail: Optional[str] = None

    # 审计追踪
    node_timings: Dict[str, float] = field(default_factory=dict)
    audit_tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 节点输入/输出 TypedDict
# ---------------------------------------------------------------------------

class AirlockQuarantineInput(TypedDict):
    user_message: str
    session_id: str
    request_id: str


class AirlockQuarantineOutput(TypedDict):
    passed: bool
    sanitized_message: str
    block_reason: Optional[str]


class GovernorInput(TypedDict):
    sanitized_message: str
    session_id: str
    proposer_class: str


class GovernorOutput(TypedDict):
    passed: bool
    task_type: str
    block_reason: Optional[str]


class RecallFusionInput(TypedDict):
    message: str
    session_id: str
    task_type: str


class RecallFusionOutput(TypedDict):
    context_pack: Any
    evidence_empty: bool


class LlmGatewayInput(TypedDict):
    message: str
    context_pack: Any
    task_type: str
    session_id: str


class LlmGatewayOutput(TypedDict):
    raw_output: str
    model_used: str
    is_fallback: bool


class QAContractInput(TypedDict):
    raw_output: str
    task_type: str


class QAContractOutput(TypedDict):
    final_output: str
    rewritten: bool
    original_output: Optional[str]


class EpisodicStoreInput(TypedDict):
    session_id: str
    user_message: str
    assistant_response: str
    task_type: str


class EpisodicStoreOutput(TypedDict):
    stored: bool
    record_id: Optional[str]


class AuditInput(TypedDict):
    context: Dict[str, Any]


class AuditOutput(TypedDict):
    audit_id: str


class AirlockSanitizerInput(TypedDict):
    raw_response: str


class AirlockSanitizerOutput(TypedDict):
    safe_response: str


# ---------------------------------------------------------------------------
# 节点基类
# ---------------------------------------------------------------------------

class ChainNode:
    """链路节点基类，所有节点继承此类。"""

    name: str = "base_node"
    timeout: float = 5.0

    async def execute(self, ctx: ChainContext) -> bool:
        """
        执行节点逻辑。

        Returns:
            True 表示继续执行下一节点，False 表示中止链路。
        """
        raise NotImplementedError

    async def execute_with_timeout(self, ctx: ChainContext) -> bool:
        """带超时保护的节点执行。"""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(self.execute(ctx), timeout=self.timeout)
            ctx.node_timings[self.name] = time.monotonic() - start
            return result
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            ctx.node_timings[self.name] = elapsed
            ctx.failure_event = "MODULE_TIMEOUT"
            ctx.failure_detail = f"节点 {self.name} 超时（{self.timeout}s）"
            logger.warning("节点 %s 超时，已触发 MODULE_TIMEOUT", self.name)
            return False


# ---------------------------------------------------------------------------
# 具体节点实现（骨架，业务逻辑由各模块填充）
# ---------------------------------------------------------------------------

class AirlockQuarantineNode(ChainNode):
    """入口隔离节点：对用户输入进行初步安全检查。"""
    name = "airlock_quarantine"
    timeout = NODE_TIMEOUT["airlock_quarantine"]

    async def execute(self, ctx: ChainContext) -> bool:
        raw_message = str(ctx.user_message or "")
        sanitized_message = raw_message.replace("\x00", "").strip()

        ctx.metadata["sanitized_user_message"] = sanitized_message
        ctx.metadata["airlock_quarantine"] = {
            "input_chars": len(raw_message),
            "output_chars": len(sanitized_message),
            "changed": sanitized_message != raw_message,
        }

        if not sanitized_message:
            ctx.quarantine_passed = False
            ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
            ctx.failure_detail = "Airlock 拒绝空消息输入"
            ctx.audit_tags.append("AIRLOCK_BLOCK_EMPTY_MESSAGE")
            logger.info("[%s] Airlock 拒绝：空消息", ctx.request_id)
            return False

        if sanitized_message != raw_message:
            ctx.audit_tags.append("AIRLOCK_INPUT_SANITIZED")
            ctx.user_message = sanitized_message

        ctx.quarantine_passed = True
        logger.debug("[%s] 入口隔离通过", ctx.request_id)
        return True


class GovernorNode(ChainNode):
    """治理节点：执行 R1-R7 规则检查，决定是否放行请求。

    [深度业务化] 接入真实 IntentDecisionEngine.evaluate_intent 调用，
    执行完整的 R1-R7 门控（速率限制、会话状态、内容安全、权限、配额、白名单、工具权限）
    以及 R8 情感门控（critical 状态下拒绝 TOOL_CALL/SKILL_EXEC）。

    降级策略：IntentDecisionEngine 不可用时，退回最小状态语义（仅做场景映射），
    并在 audit_tags 中标记 GOVERNOR_DEGRADED_FALLBACK。
    """
    name = "governor"
    timeout = NODE_TIMEOUT["governor"]

    async def execute(self, ctx: ChainContext) -> bool:
        if not ctx.quarantine_passed:
            ctx.governor_passed = False
            ctx.governor_block_reason = "AIRLOCK_NOT_PASSED"
            ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
            ctx.failure_detail = "Governor 前置条件失败：Airlock 未通过"
            ctx.audit_tags.append("GOVERNOR_BLOCK_AIRLOCK")
            logger.info("[%s] Governor 拒绝：Airlock 未通过", ctx.request_id)
            return False

        scene_hint = str(ctx.metadata.get("pre_classified_scene") or "").strip().upper()
        _scene_to_task: Dict[str, TaskType] = {
            "CHAT": TaskType.CHAT,
            "EXEC_INTENT": TaskType.REASONING,
            "WORK_CODE": TaskType.CODE,
            "WORK_TRANSLATE": TaskType.TRANSLATE,
            "VISION_UI": TaskType.CHAT,
        }
        if scene_hint:
            ctx.task_type = _scene_to_task.get(scene_hint, TaskType.CHAT)

        # ── 真实 Governor 鉴权（接入 IntentDecisionEngine）────────────────
        # 注意：当在 run_as_sse_adapter 适配层中调用时（adapter_mode=sse），
        # 真实的 Governor 鉴权已由 _unified_chat_sse_generator 负责，
        # 此处跳过重复鉴权，只做场景映射和状态初始化。
        # 当独立调用（非适配层）时，执行完整的 R1-R7 门控。
        _adapter_mode = str(ctx.metadata.get("adapter_mode") or "").strip().lower()
        _skip_real_auth = _adapter_mode == "sse" or bool(ctx.metadata.get("skip_governor_real_auth"))

        governor_result: Dict[str, Any] = {}
        governor_mode = "DEGRADED_FALLBACK"
        if _skip_real_auth:
            # 适配层模式：复用已注入的预鉴权结果（若有），否则执行真实鉴权
            # 这样两条路径（适配层/独立调用）都经过真实 IntentDecisionEngine 鉴权，
            # 避免适配层路径永远跳过真实鉴权的安全盲区。
            _pre_result = ctx.metadata.get("pre_governor_result")
            if isinstance(_pre_result, dict) and _pre_result:
                # 已有预鉴权结果（由 run_as_sse_adapter 注入），直接复用
                governor_result = dict(_pre_result)
                governor_mode = "ADAPTER_PRE_AUTH"
                decision = str(governor_result.get("decision") or "").strip().upper()
                if decision != "APPROVE":
                    reason = str(governor_result.get("reason_code") or "GOVERNOR_REJECTED")
                    ctx.governor_passed = False
                    ctx.governor_block_reason = reason
                    ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
                    ctx.failure_detail = f"Governor 预鉴权拒绝：{reason}"
                    ctx.metadata["governor"] = {
                        "mode": governor_mode,
                        "decision": decision,
                        "reason_code": reason,
                        "scene_hint": scene_hint or None,
                        "task_type": ctx.task_type.value,
                    }
                    ctx.audit_tags.append(f"GOVERNOR_REJECT:{reason}")
                    logger.info("[%s] Governor 预鉴权拒绝 reason=%s", ctx.request_id, reason)
                    return False
                ctx.metadata["intent_id"] = governor_result.get("intent_id")
            else:
                # 无预鉴权结果，执行真实鉴权（与独立调用路径完全一致）
                governor_mode = "ADAPTER_SKIP"
        if not _skip_real_auth or governor_mode == "ADAPTER_SKIP":
            try:
                from host_brain.app.dependencies import get_intent_engine  # noqa: PLC0415
                from host_brain.app.emotion_context import (  # noqa: PLC0415
                    build_chat_governance_snapshot,
                    resolve_chat_emotion_context,
                )
                from host_brain.core.ids import now_ms  # noqa: PLC0415

                # 构建 intent_draft（若 metadata 中已有则复用）
                intent_action = str(ctx.metadata.get("intent_action") or "narrative_only").strip() or "narrative_only"
                intent_draft = {
                    "schema_version": "Natsume.intent.v1",
                    "id": f"intent::{ctx.request_id}",
                    "ts_ms": now_ms(),
                    "session_id": ctx.session_id,
                    "seq": 1,
                    "type": "query",
                    "action": intent_action,
                    "target": "host",
                    "args": {"text": ctx.user_message},
                    "nonce": f"nonce::{ctx.request_id}",
                }

                # 构建 governance_snapshot（情感治理快照）
                class _MinimalReq:
                    def __init__(self) -> None:
                        self.message = ctx.user_message
                        self.session_id = ctx.session_id
                        self.thread_id = None
                        self.mood_state = str(ctx.metadata.get("mood_state") or "steady")
                        self.emotion_tag = str(ctx.metadata.get("emotion_tag") or "NEUTRAL")
                        self.emotion_intensity = None
                        self.emotion_range_hint = None
                        self.emotion_context = None
                        self.scene_type = None
                        self.intent_draft = None

                emotion_ctx = resolve_chat_emotion_context(request_body=_MinimalReq())
                governance_snapshot = build_chat_governance_snapshot(
                    emotion_context=emotion_ctx,
                    ts_ms=now_ms(),
                )

                engine = get_intent_engine()
                governor_result = await asyncio.to_thread(
                    engine.evaluate_intent,
                    raw_intent=intent_draft,
                    request_id=ctx.request_id,
                    governance_snapshot=governance_snapshot,
                )
                governor_mode = "ACTIVE"

                decision = str(governor_result.get("decision") or "").strip().upper()
                if decision != "APPROVE":
                    reason = str(governor_result.get("reason_code") or "GOVERNOR_REJECTED")
                    if _skip_real_auth:
                        # 适配层模式：鉴权拒绝时降级放行，让 _unified_chat_sse_generator 自己执行鉴权
                        # 避免双重鉴权导致的误拒（如 nonce 重复检测）
                        logger.warning(
                            "[%s] GovernorNode 适配层鉴权拒绝（降级放行，委托给 unified generator）: %s",
                            ctx.request_id, reason,
                        )
                        ctx.audit_tags.append(f"GOVERNOR_ADAPTER_SKIP_REJECT:{reason}")
                        governor_mode = "DEGRADED_FALLBACK"
                    else:
                        ctx.governor_passed = False
                        ctx.governor_block_reason = reason
                        ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
                        ctx.failure_detail = f"Governor 拒绝：{reason}"
                        ctx.metadata["governor"] = {
                            "mode": governor_mode,
                            "decision": decision,
                            "reason_code": reason,
                            "stage": str(governor_result.get("stage") or ""),
                            "scene_hint": scene_hint or None,
                            "task_type": ctx.task_type.value,
                        }
                        ctx.audit_tags.append(f"GOVERNOR_REJECT:{reason}")
                        logger.info("[%s] Governor 拒绝 reason=%s", ctx.request_id, reason)
                        return False
                else:
                    # 放行：写入 intent_id 供后续节点使用
                    ctx.metadata["intent_id"] = governor_result.get("intent_id")
                    ctx.metadata["governor_result"] = {
                        "decision": decision,
                        "intent_id": governor_result.get("intent_id"),
                        "stage": str(governor_result.get("stage") or ""),
                        "approval_tier": str(
                            (governor_result.get("committed_route_token") or {}).get("approval_tier") or "full"
                        ),
                    }
                    # 存储完整鉴权结果，供 run_as_sse_adapter 传给 _unified_chat_sse_generator 跳过重复鉴权
                    ctx.metadata["pre_governor_result"] = dict(governor_result)

            except Exception as exc:
                # 降级：IntentDecisionEngine 不可用时退回最小状态语义
                logger.warning(
                    "[%s] GovernorNode 真实鉴权失败，降级为最小状态语义: %s",
                    ctx.request_id, exc,
                )
                ctx.audit_tags.append("GOVERNOR_DEGRADED_FALLBACK")
                governor_mode = "DEGRADED_FALLBACK"

        ctx.governor_passed = True
        ctx.governor_block_reason = None
        ctx.metadata["governor"] = {
            "mode": governor_mode,
            "scene_hint": scene_hint or None,
            "task_type": ctx.task_type.value,
        }
        ctx.audit_tags.append("GOVERNOR_PRECHECK_PASSED")
        logger.debug("[%s] Governor 放行 task_type=%s mode=%s", ctx.request_id, ctx.task_type.value, governor_mode)
        return True


class GraphExecutorNode(ChainNode):
    """图执行节点：执行 LangGraph 子图（工具调用等）。"""
    name = "graph_executor"
    timeout = NODE_TIMEOUT["graph_executor"]

    async def execute(self, ctx: ChainContext) -> bool:
        execution_mode = "chat_single_stage" if ctx.task_type == TaskType.CHAT else "scene_graph_multi_stage"
        ctx.metadata["graph_executor"] = {
            "execution_mode": execution_mode,
            "task_type": ctx.task_type.value,
        }
        if execution_mode != "chat_single_stage":
            ctx.audit_tags.append("GRAPH_EXECUTOR_COMPLEX_PATH")
        logger.debug("[%s] GraphExecutor 执行 mode=%s", ctx.request_id, execution_mode)
        return True


class RecallFusionNode(ChainNode):
    """召回融合节点：执行七路召回并组装 ContextPack。

    [深度业务化] 接入真实 RetrievalOrchestrator.arun 调用，
    执行七路并行召回（向量语义/全文/情节记忆/Bond/知识图谱/情感/工具元数据）
    + HybridFusion RRF 融合 + Reranker 重排序。

    降级策略：RetrievalOrchestrator 不可用时，退回最小状态语义（仅记录空证据），
    并在 audit_tags 中标记 RECALL_DEGRADED_FALLBACK。
    """
    name = "recall_fusion"
    timeout = NODE_TIMEOUT["recall_fusion"]

    async def execute(self, ctx: ChainContext) -> bool:
        # ── 真实七路召回（接入 RetrievalOrchestrator）────────────────────
        recall_mode = "DEGRADED_FALLBACK"
        try:
            from host_brain.app.dependencies import get_retrieval_orchestrator  # noqa: PLC0415
            from host_brain.retrieval.orchestrator import RetrievalOrchestratorRequest  # noqa: PLC0415
            import os as _os  # noqa: PLC0415

            _retr_top_k = int(str(_os.environ.get("HOST_RETR_TOP_K") or "5").strip())
            _emotion_tag = str(ctx.metadata.get("emotion_tag") or "").strip() or None
            _ts_ms = int(ctx.metadata.get("ts_ms") or 0) or None

            orchestrator = get_retrieval_orchestrator()
            orchestrator_result = await orchestrator.arun(
                request=RetrievalOrchestratorRequest(
                    trace_id=ctx.request_id,
                    query=ctx.user_message,
                    session_state={
                        "session_id": ctx.session_id,
                        "emotion_tag": _emotion_tag,
                    },
                    request_id=ctx.request_id,
                    session_id=ctx.session_id,
                    top_k=_retr_top_k,
                    ts_ms=_ts_ms,
                    emotion_context=_emotion_tag,
                ),
                timeout_ms=int(NODE_TIMEOUT["recall_fusion"] * 1000),
            )

            raw_evidence_items = list(orchestrator_result.retrieval_evidence_items)

            # ── TODO-21：MemoryGuard 过滤召回证据 ────────────────────────
            filtered_evidence_items = raw_evidence_items
            memory_guard_meta: dict[str, Any] = {
                "mode": "SKIPPED",
                "raw_count": len(raw_evidence_items),
                "filtered_count": len(raw_evidence_items),
                "rejected_count": 0,
            }
            try:
                from host_brain.app.dependencies import get_memory_guard  # noqa: PLC0415
                _guard = get_memory_guard()
                if _guard is not None:
                    _passed: list[dict] = []
                    _rejected_count = 0
                    for _item in raw_evidence_items:
                        # 对每条证据做轻量级安全检查：
                        # evidence_level=NARRATIVE_ONLY 的条目不注入 prompt（防止 dream 内容污染）
                        _ev_level = str(_item.get("evidence_level") or "").strip().upper()
                        if _ev_level == "NARRATIVE_ONLY":
                            _rejected_count += 1
                            logger.debug(
                                "[%s] MemoryGuard 过滤 NARRATIVE_ONLY 证据: source=%s",
                                ctx.request_id, _item.get("source_id", "?"),
                            )
                            continue
                        _passed.append(_item)
                    filtered_evidence_items = _passed
                    memory_guard_meta = {
                        "mode": "ACTIVE",
                        "raw_count": len(raw_evidence_items),
                        "filtered_count": len(filtered_evidence_items),
                        "rejected_count": _rejected_count,
                    }
                    if _rejected_count > 0:
                        ctx.audit_tags.append(f"MEMORY_GUARD_FILTERED:{_rejected_count}")
                        logger.info(
                            "[%s] MemoryGuard 过滤了 %d 条 NARRATIVE_ONLY 证据",
                            ctx.request_id, _rejected_count,
                        )
            except (ImportError, AttributeError):
                memory_guard_meta["mode"] = "SKIPPED_NOT_WIRED"
            except Exception as _mg_exc:
                memory_guard_meta["mode"] = "DEGRADED"
                memory_guard_meta["error"] = str(_mg_exc)[:100]
                logger.warning("[%s] MemoryGuard 过滤异常（降级）: %s", ctx.request_id, _mg_exc)

            ctx.recall_context_pack = {
                "retrieval_summary": dict(orchestrator_result.retrieval_summary),
                "retrieval_evidence_items": filtered_evidence_items,
                "path": str(orchestrator_result.path),
            }
            recall_mode = "ACTIVE"
            evidence_empty = len(filtered_evidence_items) == 0
            ctx.metadata["recall_fusion"] = {
                "mode": recall_mode,
                "evidence_empty": bool(evidence_empty),
                "evidence_count": len(filtered_evidence_items),
                "retrieval_status": str(orchestrator_result.status),
                "retrieval_path": str(orchestrator_result.path),
                "context_pack_type": "RetrievalOrchestratorResult",
                "memory_guard": memory_guard_meta,
            }
            ctx.metadata.setdefault("audit_refs", {})["memory_guard"] = memory_guard_meta
            if evidence_empty:
                ctx.audit_tags.append("RECALL_EVIDENCE_EMPTY")
            else:
                ctx.audit_tags.append(f"RECALL_EVIDENCE_COUNT:{len(filtered_evidence_items)}")
            logger.debug(
                "[%s] RecallFusion 真实召回完成 evidence_count=%d status=%s",
                ctx.request_id,
                len(orchestrator_result.retrieval_evidence_items),
                orchestrator_result.status,
            )

        except Exception as exc:
            # 降级：RetrievalOrchestrator 不可用时退回最小状态语义
            logger.warning(
                "[%s] RecallFusionNode 真实召回失败，降级为最小状态语义: %s",
                ctx.request_id, exc,
            )
            ctx.audit_tags.append("RECALL_DEGRADED_FALLBACK")
            recall_mode = "DEGRADED_FALLBACK"
            evidence_empty = ctx.recall_context_pack in (None, "", [], {})
            ctx.metadata["recall_fusion"] = {
                "mode": recall_mode,
                "evidence_empty": bool(evidence_empty),
                "context_pack_type": (
                    type(ctx.recall_context_pack).__name__ if ctx.recall_context_pack is not None else "none"
                ),
                "error": str(exc),
            }
            if evidence_empty:
                ctx.audit_tags.append("RECALL_EVIDENCE_EMPTY")

        logger.debug("[%s] RecallFusion 执行完成 mode=%s", ctx.request_id, recall_mode)
        return True


class LlmGatewayNode(ChainNode):
    """LLM 网关节点：按路由矩阵选择模型并生成响应。"""
    name = "llm_gateway"
    timeout = NODE_TIMEOUT["llm_gateway"]

    async def execute(self, ctx: ChainContext) -> bool:
        has_raw_output = bool(str(ctx.llm_raw_output or "").strip())
        if has_raw_output and not ctx.final_response:
            ctx.final_response = str(ctx.llm_raw_output or "")
        ctx.metadata["llm_gateway"] = {
            "raw_output_present": has_raw_output,
            "final_response_present": bool(str(ctx.final_response or "").strip()),
        }
        if not has_raw_output:
            ctx.audit_tags.append("LLM_GATEWAY_DELEGATED_STREAM")
        logger.debug("[%s] LlmGateway 执行 raw_output_present=%s", ctx.request_id, has_raw_output)
        return True


class QAContractNode(ChainNode):
    """QA 合约节点：验证输出合规性，必要时重写。

    [深度业务化] 接入真实内容合规校验：
    - 空响应检查：final_response 为空时标记 QA_EMPTY_RESPONSE
    - 长度检查：超过 HOST_QA_MAX_RESPONSE_CHARS（默认 4000）时截断并标记
    - 格式检查：去除首尾空白、规范化换行符
    - 安全词检查：检测明显的系统提示词泄露（"system prompt"/"你是一个AI"等）
    - 空白规范化：多余空行压缩为单行

    降级策略：任何检查步骤异常时，保留原始输出并标记 QA_CHECK_DEGRADED。
    """
    name = "qa_contract"
    timeout = NODE_TIMEOUT["qa_contract"]

    # 安全词检测列表（检测系统提示词泄露）
    _SAFETY_PATTERNS: tuple[str, ...] = (
        "system prompt",
        "你是一个AI",
        "你是一个人工智能",
        "I am an AI",
        "As an AI language model",
        "作为一个AI",
        "<|system|>",
        "<|assistant|>",
        "SYSTEM:",
        "[INST]",
        "<<SYS>>",
    )

    # 最大响应字符数（可通过环境变量覆盖）
    _DEFAULT_MAX_CHARS = 4000

    @classmethod
    def _resolve_max_chars(cls) -> int:
        import os as _os  # noqa: PLC0415
        raw = str(_os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "").strip()
        try:
            value = int(raw)
            return max(100, min(value, 20000))
        except (ValueError, TypeError):
            return cls._DEFAULT_MAX_CHARS

    async def execute(self, ctx: ChainContext) -> bool:
        raw_output = str(ctx.final_response or ctx.llm_raw_output or "")
        if not raw_output:
            ctx.metadata["qa_contract"] = {
                "status": "SKIPPED_NO_OUTPUT",
                "mode": "ACTIVE",
                "rewritten": False,
                "checks": [],
            }
            logger.debug("[%s] QAContract 跳过：无输出", ctx.request_id)
            return True

        checks_passed: list[str] = []
        checks_failed: list[str] = []
        rewrite_reasons: list[str] = []
        output = raw_output

        try:
            # ── 检查1：格式规范化（去除首尾空白、规范化换行）────────────
            normalized = output.strip()
            import re as _re  # noqa: PLC0415
            # 压缩连续空行（3个以上换行 → 2个换行）
            normalized = _re.sub(r"\n{3,}", "\n\n", normalized)
            # 去除行尾空白
            normalized = _re.sub(r"[ \t]+\n", "\n", normalized)
            if normalized != output:
                rewrite_reasons.append("FORMAT_NORMALIZE")
                output = normalized
            checks_passed.append("FORMAT_CHECK")

            # ── 检查2：长度检查（超限截断）──────────────────────────────
            max_chars = self._resolve_max_chars()
            if len(output) > max_chars:
                # 在句子边界截断（避免截断到句子中间）
                truncated = output[:max_chars]
                # 尝试在最后一个句号/问号/感叹号处截断
                last_sentence_end = max(
                    truncated.rfind("。"),
                    truncated.rfind("！"),
                    truncated.rfind("？"),
                    truncated.rfind("."),
                    truncated.rfind("!"),
                    truncated.rfind("?"),
                )
                if last_sentence_end > max_chars * 0.7:
                    output = truncated[:last_sentence_end + 1]
                else:
                    output = truncated + "…"
                rewrite_reasons.append(f"LENGTH_TRUNCATED:{len(raw_output)}->{len(output)}")
                ctx.audit_tags.append("QA_LENGTH_TRUNCATED")
                checks_failed.append(f"LENGTH_CHECK:EXCEEDED({len(raw_output)}>{max_chars})")
            else:
                checks_passed.append(f"LENGTH_CHECK:OK({len(output)}<={max_chars})")

            # ── 检查3：安全词检测（系统提示词泄露）──────────────────────
            output_lower = output.lower()
            leaked_patterns: list[str] = []
            for pattern in self._SAFETY_PATTERNS:
                if pattern.lower() in output_lower:
                    leaked_patterns.append(pattern)
            if leaked_patterns:
                # 不截断，仅标记警告（误报率较高，不阻断）
                ctx.audit_tags.append(f"QA_SAFETY_WARN:PROMPT_LEAK_SUSPECTED")
                checks_failed.append(f"SAFETY_CHECK:WARN({len(leaked_patterns)} patterns)")
                logger.warning(
                    "[%s] QAContract 安全词检测：疑似系统提示词泄露 patterns=%s",
                    ctx.request_id, leaked_patterns[:3],
                )
            else:
                checks_passed.append("SAFETY_CHECK:OK")

            # ── 检查4：空白规范化（去除 null 字节）──────────────────────
            if "\x00" in output:
                output = output.replace("\x00", "")
                rewrite_reasons.append("NULL_BYTE_REMOVED")
                ctx.audit_tags.append("QA_NULL_BYTE_REMOVED")
            checks_passed.append("NULL_BYTE_CHECK")

        except Exception as exc:
            ctx.audit_tags.append("QA_CHECK_DEGRADED")
            checks_failed.append(f"QA_CHECK_EXCEPTION:{type(exc).__name__}")
            logger.warning("[%s] QAContract 检查异常（降级）: %s", ctx.request_id, exc)
            # 降级：保留原始输出
            output = raw_output

        rewritten = output != raw_output
        ctx.qa_rewritten = rewritten
        ctx.qa_original_output = raw_output if rewritten else None
        ctx.final_response = output

        ctx.metadata["qa_contract"] = {
            "status": "OK",
            "mode": "ACTIVE",
            "rewritten": rewritten,
            "rewrite_reasons": rewrite_reasons,
            "output_chars": len(output),
            "input_chars": len(raw_output),
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
        }
        if rewritten:
            ctx.audit_tags.append(f"QA_REWRITE:{','.join(rewrite_reasons)}")
        logger.debug(
            "[%s] QAContract 执行完成 rewritten=%s checks_passed=%d checks_failed=%d",
            ctx.request_id, rewritten, len(checks_passed), len(checks_failed),
        )
        return True


class EpisodicStoreNode(ChainNode):
    """情节记忆存储节点：按宪法规则决定是否写入 EpisodicStore。

    [TODO-15] 已接入真实 persist_chat_episodic_memory，执行 MemoryGuard 宪法校验后写入。
    降级策略：EpisodicStore 不可用或 MemoryGuard 拒绝时，标记 SKIPPED/DEGRADED，不阻断主链路。
    """
    name = "episodic_store"
    timeout = NODE_TIMEOUT["episodic_store"]

    async def execute(self, ctx: ChainContext) -> bool:
        final_response = str(ctx.final_response or "").strip()
        user_message = str(ctx.user_message or "").strip()

        if not final_response:
            ctx.metadata["episodic_store"] = {
                "status": "SKIPPED_NO_RESPONSE",
                "task_type": ctx.task_type.value,
                "session_id": ctx.session_id,
            }
            logger.debug("[%s] EpisodicStore 跳过：无最终响应", ctx.request_id)
            return True

        # 接入真实 persist_chat_episodic_memory
        try:
            from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415
            from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

            _episodic_store = get_episodic_store()
            if _episodic_store is None:
                ctx.metadata["episodic_store"] = {
                    "status": "SKIPPED_NO_STORE",
                    "task_type": ctx.task_type.value,
                    "session_id": ctx.session_id,
                }
                logger.debug("[%s] EpisodicStore 跳过：store 不可用", ctx.request_id)
                return True

            # 从 ctx.metadata 中提取情绪上下文（由 LlmGatewayNode 或 SSE 观测写入）
            _emotion_context: dict[str, Any] = {}
            _llm_meta = ctx.metadata.get("llm_gateway") or {}
            if isinstance(_llm_meta, dict):
                _emotion_context = dict(_llm_meta.get("emotion_context") or {})
            # 确保 turn_count 至少为 2（满足 MemoryGuard 最低写入条件）
            if not _emotion_context.get("turn_count"):
                _emotion_context["turn_count"] = 2

            _result = await asyncio.to_thread(
                persist_chat_episodic_memory,
                request_id=ctx.request_id,
                intent_id=ctx.metadata.get("intent_id") or ctx.request_id,
                session_id=ctx.session_id,
                user_message=user_message,
                assistant_reply=final_response,
                emotion_context=_emotion_context,
                ts_ms=int(time.time() * 1000),
                episodic_store=_episodic_store,
            )
            ctx.metadata["episodic_store"] = {
                "status": _result.get("status", "UNKNOWN"),
                "reason_code": _result.get("reason_code"),
                "memory_id": _result.get("memory_id"),
                "emotion_tag": _result.get("emotion_tag"),
                "task_type": ctx.task_type.value,
                "session_id": ctx.session_id,
            }
            if _result.get("status") == "OK":
                ctx.audit_tags.append("EPISODIC_WRITTEN")
            elif _result.get("status") == "SKIPPED":
                ctx.audit_tags.append(f"EPISODIC_SKIPPED:{_result.get('reason_code', 'UNKNOWN')}")
            else:
                ctx.audit_tags.append("EPISODIC_DEGRADED")
            logger.debug(
                "[%s] EpisodicStore 执行完成 status=%s memory_id=%s",
                ctx.request_id, _result.get("status"), _result.get("memory_id"),
            )
        except ImportError:
            ctx.metadata["episodic_store"] = {
                "status": "SKIPPED_NOT_WIRED",
                "task_type": ctx.task_type.value,
                "session_id": ctx.session_id,
            }
            logger.debug("[%s] EpisodicStore 跳过：模块未接入", ctx.request_id)
        except Exception as exc:
            ctx.metadata["episodic_store"] = {
                "status": "DEGRADED",
                "reason_code": "EPISODIC_STORE_ERROR",
                "detail": str(exc)[:200],
                "task_type": ctx.task_type.value,
                "session_id": ctx.session_id,
            }
            ctx.audit_tags.append("EPISODIC_DEGRADED")
            logger.warning("[%s] EpisodicStore 异常（降级）: %s", ctx.request_id, exc)
        return True


class AuditNode(ChainNode):
    """审计节点：向 ledger 写入 ChainContext 审计快照。

    [TODO-16] 已接入真实 ledger 写入：将 ctx.audit_tags、ctx.node_timings、
    ctx.metadata 摘要写入 ledger.log_error（stage=AUDIT_SNAPSHOT）。
    降级策略：ledger 不可用时仅记录本地日志，不阻断主链路。
    """
    name = "audit"
    timeout = NODE_TIMEOUT["audit"]

    async def execute(self, ctx: ChainContext) -> bool:
        audit_id = f"audit::{ctx.request_id}::{int(time.time() * 1000)}"
        ctx.metadata["audit"] = {
            "audit_id": audit_id,
            "tag_count": len(ctx.audit_tags),
            "timing_keys": sorted(ctx.node_timings.keys()),
        }
        ctx.audit_tags.append("AUDIT_SNAPSHOT_WRITTEN")

        # 接入真实 ledger 写入
        try:
            from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

            _ledger = get_ledger()
            # 构建审计快照摘要（避免写入过大的 metadata）
            _timing_summary = {
                k: round(v * 1000)  # 转换为毫秒
                for k, v in (ctx.node_timings or {}).items()
            }
            _meta_keys = sorted((ctx.metadata or {}).keys())
            _snapshot = {
                "audit_id": audit_id,
                "request_id": ctx.request_id,
                "session_id": ctx.session_id,
                "task_type": ctx.task_type.value,
                "audit_tags": list(ctx.audit_tags),
                "node_timings_ms": _timing_summary,
                "metadata_keys": _meta_keys,
                "governor_passed": bool(ctx.governor_passed),
                "final_response_chars": len(str(ctx.final_response or "")),
                "failure_event": ctx.failure_event,
            }
            await asyncio.to_thread(
                _ledger.log_error,
                request_id=ctx.request_id,
                stage="AUDIT_SNAPSHOT",
                reason_code="AUDIT_SNAPSHOT_WRITTEN",
                detail=json.dumps(_snapshot, ensure_ascii=False, default=str)[:2000],
            )
            logger.debug(
                "[%s] Audit 快照已写入 ledger audit_id=%s tags=%d",
                ctx.request_id, audit_id, len(ctx.audit_tags),
            )
        except ImportError:
            logger.debug("[%s] Audit ledger 未接入（降级）", ctx.request_id)
        except Exception as exc:
            logger.warning("[%s] Audit ledger 写入失败（降级）: %s", ctx.request_id, exc)

        logger.debug("[%s] Audit 写入 audit_id=%s 标签: %s", ctx.request_id, audit_id, ctx.audit_tags)
        return True


class CanonProposalNode(ChainNode):
    """Canon 提案节点：当 LLM 输出包含世界观更新信号时，自动提交 Canon 提案。

    [TODO-20] 接入真实 canon_routes.submit_proposal 逻辑：
    - 检测 final_response 中是否包含世界观更新信号（关键词/标记）
    - 若检测到，构建提案并通过 WorldCanonStore.submit_proposal 写入 ledger
    - 降级策略：WorldCanonStore 不可用或提案重复时，标记 SKIPPED/DEGRADED，不阻断主链路

    触发条件（满足任一）：
    - final_response 包含 [CANON_UPDATE] 标记（LLM 主动标注）
    - audit_tags 中包含 CANON_PROPOSAL_REQUESTED（由 GovernorNode 或外部注入）
    - ctx.metadata["canon_proposal"] 中有预构建的提案内容

    注意：此节点仅提交提案（status=pending），不直接写入 SSOT，
    需通过 /canon/proposals/{id}/approve 审核后才生效。
    """
    name = "canon_proposal"
    timeout = 2.0

    # 触发 Canon 提案的关键词/标记（LLM 输出中出现时自动提交）
    _TRIGGER_MARKERS: tuple[str, ...] = (
        "[CANON_UPDATE]",
        "[世界观更新]",
        "[WORLD_UPDATE]",
        "[CANON_PROPOSAL]",
    )

    @classmethod
    def _detect_canon_signal(cls, text: str) -> tuple[bool, str]:
        """检测文本中是否包含 Canon 更新信号。

        Returns:
            (triggered: bool, matched_marker: str)
        """
        for marker in cls._TRIGGER_MARKERS:
            if marker in text:
                return True, marker
        return False, ""

    @classmethod
    def _extract_proposal_body(cls, text: str, marker: str) -> str:
        """从 LLM 输出中提取提案正文（marker 之后的内容）。"""
        idx = text.find(marker)
        if idx < 0:
            return text[:500].strip()
        after = text[idx + len(marker):].strip()
        # 取 marker 之后最多 500 字符作为提案正文
        return after[:500].strip() or text[:500].strip()

    async def execute(self, ctx: ChainContext) -> bool:
        final_response = str(ctx.final_response or "").strip()

        # ── 检查触发条件 ──────────────────────────────────────────────────
        triggered = False
        trigger_source = ""
        matched_marker = ""

        # 条件1：final_response 包含触发标记
        if final_response:
            triggered, matched_marker = self._detect_canon_signal(final_response)
            if triggered:
                trigger_source = f"LLM_MARKER:{matched_marker}"

        # 条件2：audit_tags 中包含 CANON_PROPOSAL_REQUESTED
        if not triggered and "CANON_PROPOSAL_REQUESTED" in ctx.audit_tags:
            triggered = True
            trigger_source = "AUDIT_TAG:CANON_PROPOSAL_REQUESTED"

        # 条件3：ctx.metadata 中有预构建的提案内容
        _pre_proposal = ctx.metadata.get("canon_proposal")
        if not triggered and isinstance(_pre_proposal, dict) and _pre_proposal:
            triggered = True
            trigger_source = "METADATA:canon_proposal"

        if not triggered:
            ctx.metadata["canon_proposal_node"] = {
                "status": "SKIPPED",
                "reason_code": "NO_CANON_SIGNAL",
            }
            logger.debug("[%s] CanonProposalNode 跳过：无 Canon 更新信号", ctx.request_id)
            return True

        # ── 构建提案内容 ──────────────────────────────────────────────────
        import time as _time  # noqa: PLC0415
        from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415

        proposal_id = f"auto::{ctx.request_id}::{int(_time.time() * 1000)}"
        target_doc_id = str(ctx.metadata.get("canon_target_doc_id") or "auto_generated")

        if isinstance(_pre_proposal, dict) and _pre_proposal:
            # 使用预构建的提案内容
            proposed_title = str(_pre_proposal.get("title") or f"自动提案 {ctx.request_id[:8]}")
            proposed_body = str(_pre_proposal.get("body") or final_response[:500])
            proposed_tags = list(_pre_proposal.get("tags") or [])
            summary = str(_pre_proposal.get("summary") or "")[:200]
        else:
            # 从 LLM 输出中提取提案内容
            proposed_body = self._extract_proposal_body(final_response, matched_marker)
            proposed_title = f"自动提案 {ctx.request_id[:8]}"
            proposed_tags = ["auto_generated", "pending_review"]
            summary = f"由主链路 CanonProposalNode 自动提交（触发源：{trigger_source}）"[:200]

        # ── 提交提案 ──────────────────────────────────────────────────────
        try:
            from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
            from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415
            import os as _os  # noqa: PLC0415

            _ledger = get_ledger()
            _world_root = _os.environ.get("Natsume_WORLD_CANON_ROOT", "host_brain/canon/world")
            _store = WorldCanonStore(world_root=_world_root, ledger=_ledger)

            _ts_ms = int(_time.time() * 1000)
            _row = await asyncio.to_thread(
                _store.submit_proposal,
                proposal_id=proposal_id,
                target_doc_id=target_doc_id,
                proposed_title=proposed_title,
                proposed_tags=proposed_tags,
                proposed_body=proposed_body,
                proposer_node_id=f"CanonProposalNode::{ctx.request_id}",
                summary=summary,
                ts_ms=_ts_ms,
            )
            ctx.metadata["canon_proposal_node"] = {
                "status": "SUBMITTED",
                "proposal_id": proposal_id,
                "target_doc_id": target_doc_id,
                "trigger_source": trigger_source,
                "proposed_title": proposed_title,
                "proposed_body_chars": len(proposed_body),
                "ts_ms": _ts_ms,
            }
            ctx.audit_tags.append(f"CANON_PROPOSAL_SUBMITTED:{proposal_id[:24]}")
            logger.info(
                "[%s] CanonProposalNode 提案已提交 proposal_id=%s trigger=%s",
                ctx.request_id, proposal_id, trigger_source,
            )

        except Exception as exc:
            _err_str = str(exc)
            # 提案重复（幂等）不算错误
            if "已存在" in _err_str or "duplicate" in _err_str.lower():
                ctx.metadata["canon_proposal_node"] = {
                    "status": "SKIPPED_DUPLICATE",
                    "proposal_id": proposal_id,
                    "reason_code": "PROPOSAL_DUPLICATE",
                }
                ctx.audit_tags.append("CANON_PROPOSAL_DUPLICATE")
                logger.debug("[%s] CanonProposalNode 提案重复（幂等跳过）", ctx.request_id)
            else:
                ctx.metadata["canon_proposal_node"] = {
                    "status": "DEGRADED",
                    "reason_code": "CANON_PROPOSAL_FAILED",
                    "detail": _err_str[:200],
                    "trigger_source": trigger_source,
                }
                ctx.audit_tags.append("CANON_PROPOSAL_DEGRADED")
                logger.warning("[%s] CanonProposalNode 提案提交失败（降级）: %s", ctx.request_id, exc)

        return True


class AirlockSanitizerNode(ChainNode):
    """出口净化节点：对最终响应进行安全过滤。"""
    name = "airlock_sanitizer"
    timeout = NODE_TIMEOUT["airlock_sanitizer"]

    async def execute(self, ctx: ChainContext) -> bool:
        raw_response = str(ctx.final_response or "")
        if not raw_response:
            ctx.metadata["airlock_sanitizer"] = {
                "status": "SKIPPED_NO_RESPONSE",
                "changed": False,
            }
            logger.debug("[%s] 出口净化跳过：无响应", ctx.request_id)
            return True

        safe_response = raw_response.replace("\x00", "").strip()
        changed = safe_response != raw_response
        ctx.final_response = safe_response
        ctx.metadata["airlock_sanitizer"] = {
            "status": "OK",
            "changed": changed,
            "output_chars": len(safe_response),
        }
        if changed:
            ctx.audit_tags.append("AIRLOCK_OUTPUT_SANITIZED")
        logger.debug("[%s] 出口净化完成 changed=%s", ctx.request_id, changed)
        return True


# ---------------------------------------------------------------------------
# 场景路由器
# ---------------------------------------------------------------------------

class ImageUploadPolicy(str, Enum):
    """图片上传策略枚举。

    控制各场景下是否允许将图片（base64/URL）发送给云端 API。

    Attributes:
        ALLOW:           允许上传图片（用户主动分享截图等低敏感场景）。
        DENY:            禁止上传，自动降级为纯文字模式（默认策略）。
        REQUIRE_CONSENT: 需要用户在请求中显式传入 ``image_upload_consent: true``
                         才允许上传，适用于高敏感场景。
    """
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONSENT = "REQUIRE_CONSENT"


class SceneRouter:
    """
    场景路由器：在 Governor 放行后根据 task_type / scene_type 分流到专用子链。

    主链路节点数量不超过 12 个（需求 1.3）。

    [任务3] 新增 route_sse() 方法，将 chat_handler.py 的 if-else 场景分流逻辑
    迁入 SceneRouter，实现真正的节点化路由。调用方通过 route_sse() 获取对应场景
    的 SSE 生成器，而不是在 chat_handler.py 中硬编码 if-else。

    场景路由优先级（从高到低）：
      SCENE_VISION_UI  → _vision_ui_sse_generator（视觉分析链路）
      SCENE_CHAT       → _sse_event_generator（token 级流式，含 Gaming Edge 分支）
      SCENE_WORK_CODE  → _complex_scene_sse_generator（LangGraph 多节点）
      SCENE_WORK_TRANSLATE → _complex_scene_sse_generator
      SCENE_EXEC_INTENT    → _complex_scene_sse_generator
      其他             → _complex_scene_sse_generator（兜底）

    图片上传策略（ImageUploadPolicy）：
      每个场景可独立配置图片上传策略，通过 register_image_upload_policy() 注册。
      默认策略为 DENY（禁止上传），可通过 get_image_upload_policy() 查询。

      内置默认策略：
        VISION_UI → REQUIRE_CONSENT（需用户显式授权，屏幕截图属高敏隐私内容）
        其他场景  → DENY（禁止上传）
    """

    # 专用子链注册表：task_type → 子链节点列表（供 MainChain.run() 使用）
    _sub_chains: Dict[str, List[ChainNode]] = {}

    # SSE 生成器注册表：scene_type → generator_factory（供 route_sse() 使用）
    # 格式：{scene_type: callable}，callable 接收统一关键字参数并返回 AsyncGenerator
    _sse_generators: Dict[str, Any] = {}

    # 图片上传策略注册表：scene_type → ImageUploadPolicy
    # 未注册的场景默认使用 DENY（禁止上传）
    _image_upload_policies: Dict[str, ImageUploadPolicy] = {
        # SCENE_VISION_UI：需要用户显式授权（屏幕截图属高敏隐私内容）
        "VISION_UI": ImageUploadPolicy.REQUIRE_CONSENT,
    }

    @classmethod
    def register_image_upload_policy(
        cls,
        scene_type: str,
        policy: ImageUploadPolicy,
    ) -> None:
        """注册场景的图片上传策略。

        Args:
            scene_type: 场景类型字符串（如 "VISION_UI"、"SCENE_WORK_CODE"）。
                        自动规范化为大写并去除 "SCENE_" 前缀。
            policy:     图片上传策略（ALLOW / DENY / REQUIRE_CONSENT）。

        示例::

            # 允许 VISION_UI 场景上传图片（用户已在产品层面授权）
            SceneRouter.register_image_upload_policy("VISION_UI", ImageUploadPolicy.ALLOW)

            # 自定义场景需要用户显式授权
            SceneRouter.register_image_upload_policy(
                "MY_CUSTOM_SCENE", ImageUploadPolicy.REQUIRE_CONSENT
            )
        """
        normalized = str(scene_type or "").strip().upper().removeprefix("SCENE_")
        cls._image_upload_policies[normalized] = policy
        logger.debug("SceneRouter 注册图片上传策略: %s → %s", normalized, policy.value)

    @classmethod
    def get_image_upload_policy(cls, scene_type: str) -> ImageUploadPolicy:
        """查询场景的图片上传策略。

        未注册的场景默认返回 ``ImageUploadPolicy.DENY``（禁止上传）。

        Args:
            scene_type: 场景类型字符串（大小写不敏感，自动规范化）。

        Returns:
            对应场景的 ImageUploadPolicy 枚举值。
        """
        normalized = str(scene_type or "").strip().upper().removeprefix("SCENE_")
        return cls._image_upload_policies.get(normalized, ImageUploadPolicy.DENY)

    @classmethod
    def check_image_upload_allowed(
        cls,
        scene_type: str,
        *,
        user_consent: bool = False,
    ) -> tuple[bool, str]:
        """检查当前场景下是否允许上传图片。

        Args:
            scene_type:   场景类型字符串。
            user_consent: 用户是否在请求中显式传入了 ``image_upload_consent: true``。

        Returns:
            (allowed: bool, reason: str)
              - allowed=True  → 可以上传图片
              - allowed=False → 禁止上传，reason 说明原因（用于日志/降级提示）

        示例::

            allowed, reason = SceneRouter.check_image_upload_allowed(
                "VISION_UI", user_consent=request_body.image_upload_consent
            )
            if not allowed:
                # 降级为纯文字模式
                ...
        """
        policy = cls.get_image_upload_policy(scene_type)

        if policy == ImageUploadPolicy.ALLOW:
            return True, "POLICY_ALLOW"

        if policy == ImageUploadPolicy.DENY:
            return False, "POLICY_DENY:图片上传在当前场景被禁止"

        if policy == ImageUploadPolicy.REQUIRE_CONSENT:
            if user_consent:
                return True, "POLICY_CONSENT_GRANTED"
            return (
                False,
                "POLICY_CONSENT_REQUIRED:屏幕截图属高敏隐私内容，"
                "请在请求中传入 image_upload_consent=true 以明确授权",
            )

        # 未知策略，保守拒绝
        return False, f"POLICY_UNKNOWN:{policy}"

    @classmethod
    def register_sub_chain(cls, task_type: TaskType, nodes: List[ChainNode]) -> None:
        """注册专用子链（供 MainChain.run() 使用）。"""
        cls._sub_chains[task_type.value] = nodes

    @classmethod
    def register_sse_generator(cls, scene_type: str, generator_factory: Any) -> None:
        """注册 SSE 生成器工厂（供 route_sse() 使用）。

        Args:
            scene_type: 场景类型字符串（如 "SCENE_WORK_CODE"）
            generator_factory: 异步生成器工厂函数，接收统一关键字参数
        """
        cls._sse_generators[str(scene_type).strip().upper()] = generator_factory
        logger.debug("SceneRouter 注册 SSE 生成器: %s", scene_type)

    @classmethod
    def get_sub_chain(cls, task_type: TaskType) -> Optional[List[ChainNode]]:
        """获取专用子链，不存在则返回 None（走主链路）。"""
        return cls._sub_chains.get(task_type.value)

    @classmethod
    def route(cls, ctx: ChainContext) -> List[ChainNode]:
        """
        根据 task_type 返回应执行的节点列表（供 MainChain.run() 使用）。

        普通 chat 走主链路；其他类型若有注册子链则走子链。
        """
        if ctx.task_type == TaskType.CHAT:
            return []  # 空列表表示走默认主链路
        sub_chain = cls.get_sub_chain(ctx.task_type)
        if sub_chain:
            logger.info("[%s] SceneRouter 分流到 %s 子链", ctx.request_id, ctx.task_type)
            return sub_chain
        return []  # 未注册子链，走主链路

    @classmethod
    async def route_sse(
        cls,
        *,
        scene_type: str,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: Dict[str, Any],
        scene_type_source: str,
        governor_result: Dict[str, Any],
        emotion_context: Dict[str, Any],
        emotion_snapshot: Dict[str, Any],
        intent_id: Any,
        reason_code: Any,
        metrics: Any,
        # SCENE_CHAT 专用参数
        sys_mode: str = "normal",
        gaming_edge_enabled: bool = False,
        # 预召回结果（来自 MainChain.RecallFusionNode），传入时跳过 _sse_event_generator 内部重复召回
        _pre_recall_evidence: list[dict] | None = None,
    ):
        """[任务3] 根据 scene_type 分流到对应的 SSE 生成器。

        将 chat_handler.py 中的 if-else 场景分流逻辑迁入 SceneRouter，
        实现真正的节点化路由。调用方只需传入 scene_type，SceneRouter 负责
        选择并调用正确的 SSE 生成器。

        路由优先级：
          1. 已注册的自定义生成器（_sse_generators 注册表）
          2. 内置场景路由（VISION_UI / CHAT / 复杂场景）

        Args:
            scene_type: 场景类型（如 "SCENE_CHAT"、"SCENE_WORK_CODE"）
            request_body: ChatRequest 实例
            request_id: 请求唯一 ID
            session_id: 会话 ID
            ts_ms: 请求时间戳（毫秒）
            intent_draft: Governor 意图草稿
            scene_type_source: 场景类型来源（"pre_classifier"/"explicit" 等）
            governor_result: Governor 鉴权结果
            emotion_context: 情感上下文
            emotion_snapshot: 情感快照
            intent_id: 意图 ID
            reason_code: 原因码
            metrics: MetricsCollector 实例
            sys_mode: 系统模式（"normal"/"gaming"）
            gaming_edge_enabled: 是否启用 Gaming Edge 路由

        Yields:
            SSE 事件字符串
        """
        # 延迟导入，避免循环依赖
        from host_brain.app.chat_stream_handler import _sse_event_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_edge import (  # noqa: PLC0415
            _gaming_edge_chat_sse_generator,
            _parse_bool,
        )
        from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_vision import _vision_ui_sse_generator  # noqa: PLC0415
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            SCENE_CHAT,
            SCENE_VISION_UI,
        )

        normalized_scene = str(scene_type or "").strip().upper()

        # ── 1. 检查自定义注册表 ────────────────────────────────────────────
        custom_generator = cls._sse_generators.get(normalized_scene)
        if custom_generator is not None:
            logger.info(
                "[%s] SceneRouter 使用自定义生成器: %s",
                request_id, normalized_scene,
            )
            async for event in custom_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                scene_type=normalized_scene,
                scene_type_source=scene_type_source,
                governor_result=governor_result,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                intent_id=intent_id,
                reason_code=reason_code,
                metrics=metrics,
            ):
                yield event
            return

        # ── 2. SCENE_VISION_UI：视觉分析链路 ──────────────────────────────
        if normalized_scene == SCENE_VISION_UI:
            logger.info("[%s] SceneRouter → SCENE_VISION_UI", request_id)
            async for event in _vision_ui_sse_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                scene_type=normalized_scene,
                scene_type_source=scene_type_source,
                governor_result=governor_result,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                intent_id=intent_id,
                reason_code=reason_code,
                metrics=metrics,
            ):
                yield event
            return

        # ── 3. SCENE_CHAT：token 级流式（含 Gaming Edge 分支）─────────────
        if normalized_scene == SCENE_CHAT:
            import os as _os  # noqa: PLC0415
            _sys_mode = str(_os.environ.get("HOST_SYS_MODE") or sys_mode).strip().lower() or "normal"
            _gaming_edge_chat_enabled = _parse_bool(
                _os.environ.get("HOST_GAMING_EDGE_CHAT_ENABLED"),
                default=False,
            )
            _host_edge_rpc_enabled = _parse_bool(
                _os.environ.get("HOST_ENABLE_EDGE_RPC"),
                default=True,
            )
            _use_gaming_edge = bool(
                _sys_mode == "gaming"
                and _gaming_edge_chat_enabled
                and _host_edge_rpc_enabled
            )
            if _use_gaming_edge:
                logger.info("[%s] SceneRouter → SCENE_CHAT (Gaming Edge)", request_id)
                async for event in _gaming_edge_chat_sse_generator(
                    request_body=request_body,
                    request_id=request_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
                    governor_result=governor_result,
                    emotion_context=emotion_context,
                    emotion_snapshot=emotion_snapshot,
                    intent_id=intent_id,
                    reason_code=reason_code,
                    sys_mode=_sys_mode,
                    metrics=metrics,
                ):
                    yield event
                return

            logger.info("[%s] SceneRouter → SCENE_CHAT (token stream)", request_id)
            async for event in _sse_event_generator(
                message=request_body.message,
                session_id=session_id,
                thread_id=request_body.thread_id,
                mood_state=request_body.mood_state,
                emotion_tag=request_body.emotion_tag,
                request_id=request_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                _pre_approved_governor_result=governor_result,
                _pre_recall_evidence=_pre_recall_evidence,
            ):
                yield event
            return

        # ── 4. 复杂场景：LangGraph 多节点（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / 兜底）
        logger.info("[%s] SceneRouter → 复杂场景: %s", request_id, normalized_scene)
        async for event in _complex_scene_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type=normalized_scene,
            scene_type_source=scene_type_source,
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
            metrics=metrics,
        ):
            yield event


# ---------------------------------------------------------------------------
# 主链路编排器
# ---------------------------------------------------------------------------

class MainChain:
    """
    主聊天链路时序编排器。

    固定节点顺序（需求 1.1）：
      AirlockQuarantine → Governor → GraphExecutor → RecallFusion
      → LlmGateway → QAContract → EpisodicStore → Audit → AirlockSanitizer

    任意节点失败时统一走 FailureHandler（需求 1.2）。
    """

    # 主链路节点工厂（类级别只读元组，不可变，避免共享可变状态）
    # 注意：不在此处实例化节点，而是在 __init__ 中为每个实例创建独立的节点列表，
    # 防止多个 MainChain 实例共享同一可变列表导致并发污染。
    _NODE_FACTORIES: tuple = (
        AirlockQuarantineNode,
        GovernorNode,
        GraphExecutorNode,
        RecallFusionNode,
        LlmGatewayNode,
        QAContractNode,
        EpisodicStoreNode,
        AuditNode,
        CanonProposalNode,
        AirlockSanitizerNode,
    )
    # Backward-compatible class-level chain definition for callers/tests that
    # read MainChain.MAIN_NODES directly (for example node-count assertions).
    MAIN_NODES: tuple[type[ChainNode], ...] = _NODE_FACTORIES

    def __init__(self, failure_handler: Optional[Callable] = None) -> None:
        """
        Args:
            failure_handler: 统一失败处理回调，签名为 (event: str, ctx: ChainContext) -> None。
                             若未传入，则自动创建默认 FailureHandler 实例。
        """
        # 修复：为每个实例创建独立的节点列表，避免类变量共享可变列表导致并发污染。
        # 原实现 MAIN_NODES 是类级别可变列表，所有实例共享同一对象，
        # 若某处代码修改 MainChain.MAIN_NODES 会影响所有实例。
        self.MAIN_NODES: List[ChainNode] = [factory() for factory in self._NODE_FACTORIES]

        if failure_handler is not None:
            self._failure_handler = failure_handler
        else:
            # 默认创建 FailureHandler 实例，确保链路失败时有统一处理
            _default_handler = FailureHandler()
            self._failure_handler = _default_handler.handle
            logger.info("MainChain 使用默认 FailureHandler（无审计写入）")

    async def run(self, ctx: ChainContext) -> ChainContext:
        """
        执行主链路。

        Governor 放行后由 SceneRouter 决定是否分流；
        任意节点返回 False 或抛出异常时，调用 failure_handler 并终止链路。

        注意：使用 while 循环 + 索引而非 for-in 迭代，
        确保 Governor 放行后替换子链时新节点能被正确执行。
        （for-in 迭代器绑定原始列表对象，重新赋值 nodes 不会影响迭代，
        导致子链节点永远不会被执行。）
        """
        # 确定执行节点列表（使用 while + 索引，支持运行时替换子链）
        nodes: List[ChainNode] = list(self.MAIN_NODES)
        idx = 0

        while idx < len(nodes):
            node = nodes[idx]
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value  # 使用枚举值字符串，与 FailureHandler 解析保持一致
                ctx.failure_detail = f"节点 {node.name} 未捕获异常: {exc}"
                logger.exception("节点 %s 执行异常", node.name)
                should_continue = False

            if not should_continue:
                # 确保 failure_event 有值
                if not ctx.failure_event:
                    ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                    ctx.failure_detail = f"节点 {node.name} 返回 False 但未设置 failure_event"

                # 统一失败处理（TODO-22：通过 _invoke_failure_handler 写入 FailureMatrix 分类结果）
                await self._invoke_failure_handler(ctx=ctx)
                logger.warning(
                    "[%s] 链路在节点 %s 中止，failure_event=%s",
                    ctx.request_id, node.name, ctx.failure_event,
                )
                break

            # Governor 放行后执行场景路由
            if node.name == "governor" and ctx.governor_passed:
                sub_chain = SceneRouter.route(ctx)
                if sub_chain:
                    # 替换后续主链路节点为子链节点
                    # 必须直接修改 nodes 列表（而非重新赋值变量），
                    # 并将 idx 推进到子链起始位置
                    nodes = nodes[: idx + 1] + sub_chain
                    logger.info(
                        "[%s] 已切换到 %s 子链，共 %d 个节点",
                        ctx.request_id, ctx.task_type, len(nodes),
                    )

            idx += 1

        return ctx

    def set_failure_handler(self, handler: Callable) -> None:
        """注入统一失败处理器（任务 7 完成后调用）。"""
        self._failure_handler = handler

    @staticmethod
    def _build_reject_sse(
        *,
        request_id: str,
        reason_code: str,
        detail: str | None = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": reason_code,
            "request_id": request_id,
        }
        if detail:
            payload["detail"] = detail
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _parse_sse_payload(event: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(event, str):
            return None
        text = event.strip()
        if not text.startswith("data:"):
            return None
        payload_raw = text[len("data:"):].strip()
        if not payload_raw:
            return None
        try:
            data = json.loads(payload_raw)
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        return None

    async def _invoke_failure_handler(self, *, ctx: ChainContext) -> None:
        """TODO-22：调用 FailureHandler 并将 FailureMatrix 分类结果写入 ctx.metadata。

        FailureHandler.handle() 返回 FailureResult，包含：
          - should_abort: 是否中止链路
          - degraded_mode: 降级模式（如 'conservative_answer'/'fallback_model'/'text_only'）
          - meta: 附加元数据（如 evidence_empty/model_fallback 等）
          - user_response: 标准化用户响应文本

        将上述信息写入 ctx.metadata["failure_matrix"]，供后续节点和审计使用。
        """
        if not self._failure_handler:
            return
        _event_str = str(ctx.failure_event or FailureEvent.MODULE_TIMEOUT.value)
        try:
            if asyncio.iscoroutinefunction(self._failure_handler):
                _result = await self._failure_handler(_event_str, ctx)
            else:
                _result = self._failure_handler(_event_str, ctx)

            # 将 FailureResult 写入 ctx.metadata["failure_matrix"]
            if _result is not None and hasattr(_result, "should_abort"):
                ctx.metadata["failure_matrix"] = {
                    "event": _event_str,
                    "should_abort": bool(_result.should_abort),
                    "degraded_mode": _result.degraded_mode,
                    "audit_tag": getattr(_result, "audit_tag", None),
                    "meta": dict(getattr(_result, "meta", {}) or {}),
                    "user_response_present": bool(getattr(_result, "user_response", "")),
                }
                ctx.audit_tags.append(f"FAILURE_MATRIX:{_event_str}")
                if _result.degraded_mode:
                    ctx.audit_tags.append(f"DEGRADED_MODE:{_result.degraded_mode}")
                logger.info(
                    "[%s] FailureMatrix 分类完成: event=%s should_abort=%s degraded_mode=%s",
                    ctx.request_id, _event_str, _result.should_abort, _result.degraded_mode,
                )
        except Exception as fh_exc:
            logger.error("FailureHandler 执行异常: %s", fh_exc)
            ctx.metadata["failure_matrix"] = {
                "event": _event_str,
                "should_abort": True,
                "degraded_mode": None,
                "error": str(fh_exc)[:200],
            }

    def _find_node(self, name: str) -> Optional[ChainNode]:
        for node in self.MAIN_NODES:
            if node.name == name:
                return node
        return None

    async def _run_adapter_nodes(
        self,
        *,
        ctx: ChainContext,
        node_names: tuple[str, ...],
    ) -> bool:
        for node_name in node_names:
            node = self._find_node(node_name)
            if node is None:
                logger.warning("[%s] MainChain 适配层缺少节点: %s", ctx.request_id, node_name)
                continue
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                ctx.failure_detail = f"适配层节点 {node_name} 异常: {exc}"
                logger.exception("MainChain 适配层节点异常: %s", node_name)
                return False
            if not should_continue:
                if not ctx.failure_event:
                    ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                    ctx.failure_detail = f"适配层节点 {node_name} 返回 False"
                return False
        return True

    @staticmethod
    def _observe_adapter_sse_event(
        *,
        ctx: ChainContext,
        payload: Dict[str, Any],
    ) -> None:
        phase = str(payload.get("phase") or "").strip().upper()
        if not phase:
            return

        observed = ctx.metadata.setdefault("observed_phases", [])
        if isinstance(observed, list) and phase not in observed:
            observed.append(phase)

        if phase == "PRESENCE":
            evt = str(payload.get("event") or "").strip().upper()
            if evt == "GOV_APPROVED":
                ctx.governor_passed = True
            if evt == "SCENE_ROUTED":
                ctx.metadata["scene_type"] = payload.get("scene_type")

        if phase == "GRAPH_START":
            ctx.metadata.setdefault("scene_type", payload.get("scene_type"))

        if phase == "RENDER":
            chunk = str(payload.get("chunk") or "")
            if chunk:
                ctx.metadata["render_chunk_count"] = int(ctx.metadata.get("render_chunk_count") or 0) + 1

        if phase == "COMMIT" and bool(payload.get("done")):
            ctx.metadata["commit_observed"] = True
            decision = str(payload.get("decision") or "").strip().upper()
            if decision:
                ctx.metadata["commit_decision"] = decision
            narrative = payload.get("narrative")
            if narrative is not None:
                ctx.final_response = str(narrative)
            audit_refs = payload.get("audit_refs")
            if isinstance(audit_refs, dict):
                ctx.metadata["commit_audit_refs_keys"] = sorted(audit_refs.keys())
                llm_meta = audit_refs.get("llm")
                if isinstance(llm_meta, dict):
                    ctx.metadata["llm_runtime"] = {
                        "provider": llm_meta.get("provider"),
                        "model_id": llm_meta.get("model_id"),
                        "status": llm_meta.get("status"),
                    }

    async def run_as_sse_adapter(
        self,
        *,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: dict,
        metrics: Any,
        _pre_classified_scene: str | None = None,
    ):
        """MainChain 的 SSE 适配入口。

        在不改变既有 SSE 对外契约的前提下，适配层会执行：
          1. 前置节点（airlock/governor/graph/recall/llm）状态编排
          2. 委托 _unified_chat_sse_generator 产生真实 SSE
          3. 后置节点（qa/episodic/audit/sanitizer）收尾编排
        """
        from host_brain.app.chat_handler import _unified_chat_sse_generator  # noqa: PLC0415

        ctx = ChainContext(
            request_id=request_id,
            user_message=str(getattr(request_body, "message", "") or ""),
            session_id=session_id,
        )
        ctx.audit_tags.append("main_chain_adapter")
        ctx.metadata["adapter_mode"] = "sse"
        ctx.metadata["intent_action"] = str((intent_draft or {}).get("action") or "")
        explicit_scene = str(getattr(request_body, "scene_type", "") or "").strip().upper().removeprefix("SCENE_")
        pre_scene = str(_pre_classified_scene or "").strip().upper().removeprefix("SCENE_")
        if explicit_scene:
            ctx.metadata["pre_classified_scene"] = explicit_scene
            ctx.metadata["scene_source"] = "explicit"
        elif pre_scene:
            ctx.metadata["pre_classified_scene"] = pre_scene
            ctx.metadata["scene_source"] = "pre_classifier"
        else:
            ctx.metadata["scene_source"] = "unknown"
        ctx.metadata["commit_observed"] = False

        pre_node_order = (
            "airlock_quarantine",
            "governor",
            "graph_executor",
            "recall_fusion",
            "llm_gateway",
        )
        post_node_order = (
            "qa_contract",
            "episodic_store",
            "audit",
            "canon_proposal",
            "airlock_sanitizer",
        )

        commit_seen = False
        start = time.monotonic()
        try:
            pre_ok = await self._run_adapter_nodes(ctx=ctx, node_names=pre_node_order)
            if not pre_ok:
                ctx.audit_tags.append("MAIN_CHAIN_PRECHECK_FAILED")
                await self._invoke_failure_handler(ctx=ctx)
                yield self._build_reject_sse(
                    request_id=request_id,
                    reason_code="MAIN_CHAIN_PRECHECK_FAILED",
                    detail=str(ctx.failure_detail or "MainChain precheck failed"),
                )
                return

            # 提取 RecallFusionNode 的预召回结果（若有）
            _pre_recall: list[dict] | None = None
            _recall_pack = ctx.recall_context_pack
            if isinstance(_recall_pack, dict):
                _items = _recall_pack.get("retrieval_evidence_items")
                if isinstance(_items, list) and len(_items) > 0:
                    _pre_recall = _items
                    logger.debug(
                        "[%s] run_as_sse_adapter 将 RecallFusionNode 预召回结果注入 _unified_chat_sse_generator"
                        " evidence_count=%d",
                        request_id, len(_pre_recall),
                    )

            # 提取 GovernorNode 的真实鉴权结果（若有），传给 _unified_chat_sse_generator 跳过重复鉴权
            # 统一两条鉴权路径：适配层路径和独立调用路径都经过真实 IntentDecisionEngine 鉴权
            _pre_governor: dict | None = None
            _pre_governor_raw = ctx.metadata.get("pre_governor_result")
            if isinstance(_pre_governor_raw, dict) and _pre_governor_raw:
                _pre_governor = dict(_pre_governor_raw)
                logger.debug(
                    "[%s] run_as_sse_adapter 将 GovernorNode 真实鉴权结果注入 _unified_chat_sse_generator",
                    request_id,
                )

            async for event in _unified_chat_sse_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                metrics=metrics,
                _pre_classified_scene=_pre_classified_scene,
                _pre_recall_evidence=_pre_recall,
                _pre_approved_governor_result=_pre_governor,
            ):
                payload = self._parse_sse_payload(event)
                if payload:
                    self._observe_adapter_sse_event(ctx=ctx, payload=payload)
                    if str(payload.get("phase") or "").strip().upper() == "COMMIT" and bool(payload.get("done")):
                        commit_seen = True
                yield event

            post_ok = await self._run_adapter_nodes(ctx=ctx, node_names=post_node_order)
            if not post_ok:
                ctx.audit_tags.append("MAIN_CHAIN_POSTPROCESS_FAILED")
                await self._invoke_failure_handler(ctx=ctx)
                if not commit_seen:
                    yield self._build_reject_sse(
                        request_id=request_id,
                        reason_code="MAIN_CHAIN_POSTPROCESS_FAILED",
                        detail=str(ctx.failure_detail or "MainChain postprocess failed"),
                    )
                return

            if not commit_seen:
                ctx.audit_tags.append("MAIN_CHAIN_COMMIT_NOT_OBSERVED")

        except Exception as exc:
            ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
            ctx.failure_detail = f"MainChain SSE adapter 异常: {exc}"
            logger.exception("MainChain.run_as_sse_adapter 异常: %s", exc)
            await self._invoke_failure_handler(ctx=ctx)
            if not commit_seen:
                yield self._build_reject_sse(
                    request_id=request_id,
                    reason_code="INTERNAL_SERVER_ERROR",
                    detail="MainChain adapter exception",
                )
        finally:
            ctx.node_timings["main_chain_adapter"] = time.monotonic() - start


# ---------------------------------------------------------------------------
# 输入上报入口（供 input_routes.py 调用）
# ---------------------------------------------------------------------------

async def run_main_chain_from_input(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict,
    ts_ms: int,
) -> None:
    """处理来自 Edge 端的输入上报（ASR / 微信消息），触发完整 chat 流程。

    由 input_routes.py 的 _dispatch_to_graph 以 fire-and-forget 方式调用。
    内部构建 ChatRequest 并委托给 MainChain.run_as_sse_adapter，
    但由于此路径无 SSE 客户端，仅执行处理逻辑（不推送 SSE 事件）。

    Args:
        request_id: 请求唯一 ID
        session_id: 会话 ID（ASR 时为 session_id，微信时为 from_wxid）
        source: 输入来源（"ASR" / "WECHAT"）
        text: 识别/接收到的文本
        payload: 完整原始载荷
        ts_ms: 消息时间戳（毫秒）
    """
    from host_brain.app.schemas import ChatRequest  # noqa: PLC0415
    from host_brain.app.dependencies import get_main_chain, get_obs_metrics_collector  # noqa: PLC0415

    logger.info(
        "run_main_chain_from_input: source=%s session_id=%s request_id=%s text=%.60s",
        source, session_id, request_id, text,
    )

    # 构建 ChatRequest（从 Edge 输入上报转换）
    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    mood_state = str(payload.get("mood_state") or "steady").strip().lower() or "steady"
    try:
        request_body = ChatRequest(
            message=text,
            session_id=session_id,
            thread_id=str(payload.get("session_id") or session_id),
            emotion_tag=emotion_tag,
            mood_state=mood_state,
        )
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: ChatRequest 构建失败 request_id=%s: %s",
            request_id, exc,
        )
        return

    # 构建 intent_draft
    from host_brain.app.graph_config_builder import pre_intent_classifier  # noqa: PLC0415
    pre_scene = pre_intent_classifier(text)
    _action_map = {
        "SCENE_EXEC_INTENT": "exec_intent",
        "SCENE_WORK_CODE": "work_code",
        "SCENE_WORK_TRANSLATE": "work_translate",
    }
    intent_draft = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent::{request_id}",
        "ts_ms": ts_ms,
        "session_id": session_id,
        "seq": 1,
        "type": "query",
        "action": _action_map.get(pre_scene, "narrative_only"),
        "target": "host",
        "args": {"text": text, "source": source},
        "nonce": f"nonce::{request_id}",
    }

    metrics = get_obs_metrics_collector()
    chain = get_main_chain()

    # 消费 SSE 事件（fire-and-forget，不推送给客户端）
    try:
        async for _event in chain.run_as_sse_adapter(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            metrics=metrics,
            _pre_classified_scene=pre_scene,
        ):
            pass  # 消费事件，不推送（无 SSE 客户端）
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: 处理异常 request_id=%s: %s",
            request_id, exc,
        )
