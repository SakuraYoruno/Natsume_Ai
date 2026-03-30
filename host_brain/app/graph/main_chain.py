"""
main_chain.py —— 主聊天链路时序编排器（瘦身版）

拆分说明（Phase XIV 代码质量打磨）：
  - chain_types.py: ChainContext / TypedDict / ChainNode 基类 / NODE_TIMEOUT
  - chain_nodes.py: 10 个具体节点实现
  - scene_router.py: SceneRouter + ImageUploadPolicy
  - main_chain.py（本文件）: MainChain 编排器 + run_main_chain_from_input 入口 + 向后兼容重导出

定义主链路节点顺序：
  Airlock(Quarantine) → Governor → GraphExecutor → RecallFusion
  → LlmGateway → QAContract → EpisodicStore → Audit → Airlock(Sanitizer)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict

from host_brain.app.intent_seq import allocate_intent_seq, sync_intent_seq

from ..schemas import TaskType
from ..schemas.memory_types import ProposerClass
from ..failure_matrix import FailureHandler, FailureEvent

# ── 向后兼容重导出（外部文件 import 不需要改动）────────────────────────
from .chain_types import (  # noqa: F401
    ChainContext,
    ChainNode,
    NODE_TIMEOUT,
    AirlockQuarantineInput,
    AirlockQuarantineOutput,
    GovernorInput,
    GovernorOutput,
    RecallFusionInput,
    RecallFusionOutput,
    LlmGatewayInput,
    LlmGatewayOutput,
    QAContractInput,
    QAContractOutput,
    EpisodicStoreInput,
    EpisodicStoreOutput,
    AuditInput,
    AuditOutput,
    AirlockSanitizerInput,
    AirlockSanitizerOutput,
)
from .chain_nodes import (  # noqa: F401
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
from .scene_router import SceneRouter, ImageUploadPolicy  # noqa: F401

logger = logging.getLogger(__name__)

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

                # 统一失败处理（Ref #22：通过 _invoke_failure_handler 写入 FailureMatrix 分类结果）
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
        """Ref #22：调用 FailureHandler 并将 FailureMatrix 分类结果写入 ctx.metadata。

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
            node_started = time.monotonic()
            logger.info("[%s] MainChain adapter node start: %s", ctx.request_id, node_name)
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                ctx.failure_detail = f"适配层节点 {node_name} 异常: {exc}"
                logger.exception("MainChain 适配层节点异常: %s", node_name)
                return False
            elapsed_ms = max(0, int((time.monotonic() - node_started) * 1000))
            logger.info(
                "[%s] MainChain adapter node done: %s continue=%s elapsed_ms=%d",
                ctx.request_id,
                node_name,
                should_continue,
                elapsed_ms,
            )
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
        _intent_seq_raw = (intent_draft or {}).get("seq")
        try:
            _intent_seq = int(_intent_seq_raw)
            if _intent_seq > 0:
                ctx.metadata["intent_seq"] = _intent_seq
                ctx.metadata["turn_index"] = max(0, _intent_seq - 1)
        except (TypeError, ValueError):
            pass

        # ── Skill Manifest 注入：将当前可用 skill 列表写入 metadata，供 tool_planner 节点使用 ──
        try:
            from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
            _skill_reg = get_skill_registry()
            ctx.metadata["skill_manifest"] = _skill_reg.build_skill_manifest(max_risk_level=3)
            logger.debug(
                "[%s] skill_manifest 已构建，共 %d 个 skill",
                request_id, len(ctx.metadata["skill_manifest"]),
            )
        except Exception as _sm_exc:
            ctx.metadata["skill_manifest"] = []
            logger.warning("[%s] skill_manifest 构建失败（降级为空列表）: %s", request_id, _sm_exc)
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
        try:
            from host_brain.app.prompt_builder import infer_render_target_lang  # noqa: PLC0415

            resolved_scene = str(ctx.metadata.get("pre_classified_scene") or "").strip().upper()
            if resolved_scene == "GATEWAY":
                ctx.metadata["render_target_lang"] = "zh"
            else:
                ctx.metadata["render_target_lang"] = infer_render_target_lang(user_message=ctx.user_message)
        except Exception:
            ctx.metadata["render_target_lang"] = (
                "zh"
                if str(ctx.metadata.get("pre_classified_scene") or "").strip().upper() == "GATEWAY"
                else "ja"
            )
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
            _recall_pack = ctx.recall_payload
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
    from host_brain.app.graph.input_source_strategy import (  # noqa: PLC0415
        CommitResult,
        InputSourceRuntimeContext,
        resolve_input_source_strategy,
    )

    logger.info(
        "run_main_chain_from_input: source=%s session_id=%s request_id=%s text=%.60s",
        source, session_id, request_id, text,
    )

    import os as _os  # noqa: PLC0415

    source_upper = str(source or "").strip().upper()

    def _parse_sse_payload(raw_event: Any) -> dict[str, Any] | None:
        if not isinstance(raw_event, str):
            return None
        text_event = raw_event.strip()
        if not text_event.startswith("data:"):
            return None
        payload_raw = text_event[len("data:"):].strip()
        if not payload_raw:
            return None
        try:
            parsed = json.loads(payload_raw)
        except Exception:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    mood_state = str(payload.get("mood_state") or "steady").strip().lower() or "steady"
    thread_id_value = str(
        payload.get("session_id")
        or payload.get("room_wxid")
        or session_id
    ).strip() or session_id

    runtime_context = InputSourceRuntimeContext(
        request_id=request_id,
        source_upper=source_upper,
        payload=payload,
        environ=_os.environ,
    )
    source_strategy = resolve_input_source_strategy(source_upper)
    source_policy = source_strategy.build_policy(context=runtime_context)
    if await source_strategy.pre_chain(context=runtime_context, policy=source_policy):
        return

    try:
        request_body = ChatRequest(
            message=text,
            session_id=session_id,
            thread_id=thread_id_value,
            emotion_tag=emotion_tag,
            mood_state=mood_state,
            input_source=source_upper,
            suppress_edge_tts=source_policy.suppress_edge_tts,
            suppress_edge_vts=source_policy.suppress_edge_vts,
            reply_channel=source_policy.reply_channel,
            reply_target=source_policy.reply_target,
        )
        logger.info(
            "run_main_chain_from_input: request_body policy input_source=%s reply_channel=%s suppress_tts_field=%r suppress_vts_field=%r",
            str(getattr(request_body, "input_source", "") or ""),
            str(getattr(request_body, "reply_channel", "") or ""),
            getattr(request_body, "suppress_edge_tts", None),
            getattr(request_body, "suppress_edge_vts", None),
        )
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: ChatRequest 构建失败 request_id=%s: %s",
            request_id, exc,
        )
        return

    from host_brain.app.graph_config_builder import pre_intent_classifier  # noqa: PLC0415
    from host_brain.app.prompt_builder import SCENE_GATEWAY as _SCENE_GATEWAY  # noqa: PLC0415

    # Gateway 输入强制走 SCENE_GATEWAY，不使用 pre_intent_classifier 的结果
    # SCENE_GATEWAY 内部会自行执行意图识别和子场景路由
    if source_upper == "GATEWAY":
        pre_scene = _SCENE_GATEWAY
        logger.info(
            "run_main_chain_from_input: gateway input forced scene=%s request_id=%s",
            pre_scene, request_id,
        )
    else:
        pre_scene = pre_intent_classifier(text)
    _action_map = {
        "SCENE_EXEC_INTENT": "exec_intent",
        "SCENE_WORK_CODE": "work_code",
        "SCENE_WORK_TRANSLATE": "work_translate",
    }
    seq_hint: int | None = None
    try:
        seq_hint_raw = payload.get("seq")
        if seq_hint_raw is not None:
            seq_hint = int(seq_hint_raw)
    except (TypeError, ValueError):
        seq_hint = None
    intent_seq = allocate_intent_seq(session_id=session_id, min_seq=seq_hint)
    intent_draft = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent::{request_id}",
        "ts_ms": ts_ms,
        "session_id": session_id,
        "seq": intent_seq,
        "type": "query",
        "action": _action_map.get(pre_scene, "narrative_only"),
        "target": "host",
        "args": {"text": text},
        "nonce": f"nonce::{request_id}",
    }

    from host_brain.app.dependencies import get_main_chain, get_obs_metrics_collector  # noqa: PLC0415

    metrics = get_obs_metrics_collector()
    chain = get_main_chain()

    commit_result = CommitResult()
    try:
        logger.info(
            "run_main_chain_from_input: entering main chain adapter request_id=%s source=%s session_id=%s",
            request_id,
            source_upper,
            session_id,
        )
        async for _event in chain.run_as_sse_adapter(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            metrics=metrics,
            _pre_classified_scene=pre_scene,
        ):
            event_payload = _parse_sse_payload(_event)
            if not event_payload:
                continue
            if str(event_payload.get("phase") or "").strip().upper() != "COMMIT":
                continue
            if not bool(event_payload.get("done")):
                continue
            commit_result.decision = str(event_payload.get("decision") or "").strip().upper()
            commit_result.reason_code = str(event_payload.get("reason_code") or "").strip().upper()
            commit_result.detail = str(event_payload.get("detail") or "").strip()
            candidate_narrative = str(event_payload.get("narrative") or "").strip()
            if candidate_narrative:
                commit_result.reply_narrative = candidate_narrative
            # 提取 TTS 执行状态（供 ASR post_chain 兜底判断）
            _audit_refs = event_payload.get("audit_refs")
            if isinstance(_audit_refs, dict):
                _tts_info = _audit_refs.get("tts")
                if isinstance(_tts_info, dict):
                    _tts_status = str(_tts_info.get("status") or "").strip().upper()
                    if _tts_status not in {"", "SKIPPED", "NOT_ATTEMPTED"}:
                        commit_result.tts_already_fired = True
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: 处理异常 request_id=%s: %s",
            request_id, exc,
        )
        return
    logger.info(
        "run_main_chain_from_input: adapter stream completed request_id=%s decision=%s narrative_len=%d",
        request_id,
        commit_result.decision or "",
        len(commit_result.reply_narrative or ""),
    )

    await source_strategy.post_chain(
        context=runtime_context,
        policy=source_policy,
        commit_result=commit_result,
    )
    logger.info(
        "run_main_chain_from_input: post_chain completed request_id=%s source=%s decision=%s",
        request_id,
        source_upper,
        commit_result.decision or "",
    )
