"""sub_agent_protocol.py — 子 Agent 调度协议（Phase XIV v4）。

定义 Cortex Orchestrator 与子 Agent 之间的通信协议：
  - SubAgentRequest / SubAgentResponse：调度数据结构
  - SubAgentDispatcher：统一调度器，根据 agent_id 路由到对应 handler
  - 子 Agent 内部保留 LangGraph 图引擎（翻译→校对→润色等多节点）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.sub_agent_context import (
    ContextEnvelopeError,
    build_agent_context_envelope,
    build_context_runtime_event_payload,
    project_legacy_context_for_sub_agent,
)
from host_brain.core.trace_redaction import (
    content_ref,
    public_decision_summary,
    public_output_text,
    read_decision_summary,
    read_public_agent_output,
    sanitize_public_payload,
)

_logger = logging.getLogger("Natsume.sub_agent")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentRequest:
    """Cortex Orchestrator → 子 Agent 的调度请求。"""
    agent_id: str                          # "translate_agent" / "intent_agent" / "vision_agent" / "repair_agent"
    task: str                              # 自然语言任务描述
    user_message: str = ""                 # 用户原始消息
    request_id: str = ""
    session_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 30_000


@dataclass
class SubAgentResponse:
    """子 Agent → Cortex Orchestrator 的执行结果。"""
    success: bool
    agent_id: str
    result: str = ""                       # 技术产出（代码/翻译/执行结果）
    error: str | None = None
    audit_refs: dict[str, Any] = field(default_factory=dict)  # 三联画审计摘要
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Agent ID 常量
# ---------------------------------------------------------------------------

AGENT_TRANSLATE = "translate_agent"
AGENT_INTENT = "intent_agent"
AGENT_VISION = "vision_agent"
AGENT_REPAIR = "repair_agent"
AGENT_PENTEST = "pentest_agent"
AGENT_PENTEST_ATTACK = "pentest_attack_agent"

ALL_AGENT_IDS: frozenset[str] = frozenset({
    AGENT_TRANSLATE, AGENT_INTENT, AGENT_VISION, AGENT_REPAIR, AGENT_PENTEST, AGENT_PENTEST_ATTACK,
})

# 子 Agent 能力描述（注入 Cortex Orchestrator prompt，供 LLM 判断调度）
AGENT_CAPABILITIES: dict[str, str] = {
    AGENT_TRANSLATE: "翻译专家：中日英多语言翻译，含校对和润色（多步流程）",
    AGENT_INTENT: "执行专家：理解用户的执行意图，调用工具完成实际操作（文件/网络/系统）",
    AGENT_VISION: "视觉分析：分析图片/截图内容，提取 UI 元素信息",
    AGENT_REPAIR: "环境修复专家：当缺少浏览器/依赖/运行环境时，尝试自动安装并恢复执行能力",
}


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


_DEFAULT_AGENT_CAPABILITIES: dict[str, str] = {
    AGENT_TRANSLATE: "翻译专家：处理中日英多语翻译，包含校对和润色",
    AGENT_INTENT: "执行专家：理解用户的执行意图，并调用工具完成实际操作（文件 / 网络 / 系统）",
    AGENT_VISION: "视觉分析：分析图片或截图内容，提取 UI 元素与关键信息",
    AGENT_REPAIR: "环境修复专家：当浏览器、依赖或运行环境缺失时，尝试自动安装并恢复执行能力",
    AGENT_PENTEST: "渗透测试专家：PTES 六阶段框架（被动侦察→主动侦察→漏洞评估→漏洞利用→后渗透→报告）",
    AGENT_PENTEST_ATTACK: "渗透攻击专家：全链路自主攻击（侦察→打点→提权→横移→域控→持久化），执行完整攻击链不限于测试",
}


def get_agent_capabilities() -> dict[str, str]:
    templates = _load_template_mapping("sub_agent_protocol.agent_capabilities")
    capabilities = dict(_DEFAULT_AGENT_CAPABILITIES)
    for agent_id in ALL_AGENT_IDS:
        resolved = str(templates.get(agent_id) or capabilities.get(agent_id) or "").strip()
        if resolved:
            capabilities[agent_id] = resolved
    return capabilities


def _runtime_text(key: str, **kwargs: Any) -> str:
    templates = _load_template_mapping("sub_agent_protocol.runtime_texts")
    return _mapping_text(templates, key, **kwargs)


AGENT_CAPABILITIES = get_agent_capabilities()


SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION = "Natsume.agent.sub_agent_registry_view.v1"


@dataclass(frozen=True, slots=True)
class SubAgentRegistryEntry:
    """Read-only compatibility view over the app-level sub-agent registry."""

    agent_id: str
    capability: str
    default_timeout_ms: int = 30_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "capability": self.capability,
            "default_timeout_ms": self.default_timeout_ms,
        }


def get_sub_agent_registry_view() -> dict[str, Any]:
    """Return a deterministic view; it does not load handlers or dispatch work."""

    capabilities = get_agent_capabilities()
    entries = [
        SubAgentRegistryEntry(
            agent_id=agent_id,
            capability=str(capabilities.get(agent_id) or "").strip(),
        )
        for agent_id in sorted(ALL_AGENT_IDS)
    ]
    return {
        "schema_version": SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION,
        "agents": [entry.to_dict() for entry in entries],
    }


def resolve_sub_agent_registry_entry(agent_id: str) -> SubAgentRegistryEntry:
    """Resolve an app agent without exposing mutable dispatcher internals."""

    normalized = str(agent_id or "").strip()
    view = get_sub_agent_registry_view()
    for payload in list(view.get("agents") or []):
        if str(payload.get("agent_id") or "").strip() == normalized:
            return SubAgentRegistryEntry(
                agent_id=normalized,
                capability=str(payload.get("capability") or "").strip(),
                default_timeout_ms=int(payload.get("default_timeout_ms") or 30_000),
            )
    from host_brain.agent_runtime.contract_errors import (  # noqa: PLC0415
        SUB_AGENT_ADAPTER_INVALID,
        ContractViolation,
    )

    raise ContractViolation(
        SUB_AGENT_ADAPTER_INVALID,
        f"sub-agent is not present in registry view: {normalized}",
    )


def build_sub_agent_request_from_child_work_order(
    *,
    child_work_order: Any,
    agent_id: str,
    request_id: str,
    session_id: str = "",
    context_refs: tuple[str, ...] | list[str] = (),
    timeout_ms: int | None = None,
    context: Mapping[str, Any] | None = None,
) -> SubAgentRequest:
    """Bridge a validated child WorkOrder into the legacy dispatcher shape.

    This is the only constructor used by the canonical planner path.  Existing
    direct ``SubAgentRequest`` construction remains available for legacy callers
    until their parent WorkOrder lineage exists.
    """

    from host_brain.agent_runtime.sub_agent_contract import (  # noqa: PLC0415
        SubAgentRequestAdapter,
        SubAgentSpec,
        validate_planner_target_binding,
    )
    from host_brain.agent_runtime.work_order import WorkOrderV3  # noqa: PLC0415

    child = (
        child_work_order
        if isinstance(child_work_order, WorkOrderV3)
        else WorkOrderV3.from_mapping(
            child_work_order if isinstance(child_work_order, Mapping) else {}
        )
    )
    entry = resolve_sub_agent_registry_entry(agent_id)
    if not entry.capability:
        from host_brain.agent_runtime.contract_errors import (  # noqa: PLC0415
            SUB_AGENT_ADAPTER_INVALID,
            ContractViolation,
        )

        raise ContractViolation(
            SUB_AGENT_ADAPTER_INVALID,
            f"sub-agent registry capability is empty: {entry.agent_id}",
        )
    validate_planner_target_binding(
        child,
        agent_id=entry.agent_id,
        require_binding=True,
    )
    resolved_timeout_ms = int(
        entry.default_timeout_ms if timeout_ms is None else timeout_ms
    )
    resolved_timeout_ms = max(1, resolved_timeout_ms)
    spec = SubAgentSpec(
        agent_id=entry.agent_id,
        capability_requirements=tuple(child.capability_requirements),
        allowed_actions=tuple(child.allowed_actions),
        timeout_ms=resolved_timeout_ms,
        max_attempts=1,
        context_contract={
            "task_source": "child_work_order",
            "requires_lineage": True,
        },
    )
    resolved_context_refs_list: list[str] = []
    for item in (*tuple(child.input_refs), *tuple(context_refs or ())):
        normalized = str(item or "").strip()
        if normalized and normalized not in resolved_context_refs_list:
            resolved_context_refs_list.append(normalized)
    resolved_context_refs = tuple(resolved_context_refs_list)
    contract = SubAgentRequestAdapter.adapt(
        child_work_order=child,
        spec=spec,
        request_id=request_id,
        session_id=session_id,
        context_refs=resolved_context_refs,
    )
    legacy = contract.to_legacy_mapping()
    protected_context = dict(legacy.get("context") or {})
    merged_context = dict(context or {})
    merged_context.update(protected_context)
    merged_context["canonical_sub_agent_request"] = contract.to_dict()
    return SubAgentRequest(
        agent_id=str(legacy.get("agent_id") or "").strip(),
        task=str(legacy.get("task") or "").strip(),
        user_message=str(legacy.get("user_message") or "").strip(),
        request_id=str(legacy.get("request_id") or "").strip(),
        session_id=str(legacy.get("session_id") or "").strip(),
        context=merged_context,
        timeout_ms=int(legacy.get("timeout_ms") or resolved_timeout_ms),
    )


class SubAgentDispatcher:
    """子 Agent 统一调度器。

    根据 agent_id 路由到对应的 handler，在 asyncio.to_thread 中执行
    （现有 handler 是同步函数），超时后返回降级结果。
    """

    def __init__(self) -> None:
        self._handler_map: dict[str, Any] = {}  # 延迟加载

    def _ensure_handlers(self) -> None:
        """延迟导入 handler，避免循环依赖。"""
        if self._handler_map:
            return
        try:
            from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
            self._handler_map[AGENT_TRANSLATE] = translate_agent_handler
        except ImportError:
            pass
        try:
            from host_brain.app.handlers.intent_agent_handler import intent_agent_handler
            self._handler_map[AGENT_INTENT] = intent_agent_handler
        except ImportError:
            pass
        # TODO(P3-7): vision_agent 暂未实现独立 handler，当前临时复用 intent_agent。
        # 独立 handler 需实现图片/截图内容分析、UI 元素提取等视觉能力。
        # 跟踪：创建 host_brain/app/handlers/vision_agent_handler.py，
        # 接入多模态 LLM（如 GPT-4V / Qwen-VL），替换此处的复用逻辑。
        if AGENT_VISION not in self._handler_map and AGENT_INTENT in self._handler_map:
            self._handler_map[AGENT_VISION] = self._handler_map[AGENT_INTENT]
        try:
            from host_brain.app.handlers.repair_agent_handler import repair_agent_handler
            self._handler_map[AGENT_REPAIR] = repair_agent_handler
        except ImportError:
            pass
        try:
            from host_brain.app.handlers.pentest_agent_handler import pentest_agent_handler
            self._handler_map[AGENT_PENTEST] = pentest_agent_handler
            from host_brain.app.handlers.pentest_attack_agent_handler import pentest_attack_agent_handler
            self._handler_map[AGENT_PENTEST_ATTACK] = pentest_attack_agent_handler
        except ImportError:
            pass

    @staticmethod
    def _extract_gateway(
        *,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Any | None:
        candidate = (
            context.get("gateway")
            or context.get("_gateway")
            or payload.get("gateway")
            or payload.get("_gateway")
        )
        return candidate

    async def _ensure_runtime_gateway(
        self,
        *,
        req: SubAgentRequest,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        # Reuse existing gateway first (propagated from parent route / tool context).
        existing_gateway = self._extract_gateway(payload=payload, context=context)
        if existing_gateway is not None:
            context.setdefault("gateway", existing_gateway)
            payload.setdefault("_gateway", existing_gateway)
            return

        # Build sub-agent gateway lazily from current runtime routing.
        try:
            from host_brain.app.graph_config_builder import (  # noqa: PLC0415
                resolve_host_llm_runtime_async,
                resolve_subagent_llm_runtime,
            )
            from host_brain.app.agent_runtime_shared import (  # noqa: PLC0415
                _build_subagent_gateway_from_runtime,
            )

            base_runtime = await resolve_host_llm_runtime_async(
                rollout_key=str(req.session_id or req.request_id or req.agent_id),
                scene_type="EXEC_INTENT",
            )
            subagent_runtime = resolve_subagent_llm_runtime(base_runtime=dict(base_runtime or {}))
            gateway = _build_subagent_gateway_from_runtime(subagent_runtime)
            if gateway is None:
                return
            context["gateway"] = gateway
            payload["_gateway"] = gateway
        except Exception as exc:
            _logger.debug(
                "[%s] sub_agent runtime gateway build skipped agent=%s: %s",
                req.request_id,
                req.agent_id,
                exc,
            )
            return

    async def dispatch(self, req: SubAgentRequest) -> SubAgentResponse:
        """调度子 Agent 执行任务。

        在独立线程中运行同步 handler，带超时保护。
        子 Agent 内部的 Governor + 审计在 handler 内完成。
        执行结果写入 skill_exec_log 供链路追踪。
        """
        self._ensure_handlers()

        handler = self._handler_map.get(req.agent_id)
        if handler is None:
            resp = SubAgentResponse(
                success=False,
                agent_id=req.agent_id,
                error=_runtime_text(
                    "unknown_agent",
                    agent_id=req.agent_id,
                ),
            )
            self._log_dispatch(req, resp)
            return resp

        started_ms = int(time.time() * 1000)

        raw_context = dict(req.context or {})
        try:
            agent_context_envelope = build_agent_context_envelope(
                agent_id=req.agent_id,
                task=req.task,
                user_message=req.user_message,
                request_id=req.request_id,
                session_id=req.session_id,
                context=raw_context,
                timeout_ms=req.timeout_ms,
            ).as_dict()
        except ContextEnvelopeError as exc:
            latency_ms = int(time.time() * 1000) - started_ms
            resp = SubAgentResponse(
                success=False,
                agent_id=req.agent_id,
                error=exc.reason_code,
                audit_refs={
                    "agent_id": req.agent_id,
                    "agent_context": {
                        "status": "REJECTED",
                        "reason_code": exc.reason_code,
                        "detail": public_decision_summary(exc.detail),
                    },
                },
                latency_ms=latency_ms,
            )
            self._log_dispatch(req, resp)
            return resp
        legacy_context = project_legacy_context_for_sub_agent(raw_context)

        # 构建 handler 入参（兼容现有 handler 的 payload/context 接口）。
        # 大块历史、召回行和证据只通过 agent_context_envelope 传递，避免
        # 子 agent 各自把 raw context 塞进 prompt。
        payload: dict[str, Any] = {
            "user_input": req.task,
            "query": req.task,
            "request_id": req.request_id,
            "agent_context_envelope": agent_context_envelope,
        }
        context: dict[str, Any] = {
            "request_id": req.request_id,
            "session_id": req.session_id,
            "source": "executive_router_dispatch",
            **legacy_context,
            "agent_context_envelope": agent_context_envelope,
        }
        await self._ensure_runtime_gateway(req=req, payload=payload, context=context)

        try:
            timeout_sec = max(1.0, req.timeout_ms / 1000.0)
            result = await asyncio.wait_for(
                asyncio.to_thread(handler, payload, context),
                timeout=timeout_sec,
            )

            latency_ms = int(time.time() * 1000) - started_ms

            # 从 handler 返回的 state 中提取结果
            result_payload = dict(result or {}) if isinstance(result, Mapping) else {}
            artifacts = result_payload.get("agent_artifacts") or {}
            agent_artifact = artifacts.get(req.agent_id) or {}
            summary = str(agent_artifact.get("summary") or "").strip()

            # The complete publishable result is independent from the trace.
            # Legacy scratchpads remain readable, but are centrally sanitized.
            output_envelope = result_payload.get("agent_output") or {}
            final_answer = read_public_agent_output(output_envelope).strip()
            scratchpad = result_payload.get("react_scratchpad") or {}
            if not final_answer:
                final_answer = read_public_agent_output(scratchpad).strip()
            if not final_answer:
                final_answer = read_decision_summary(scratchpad, fallback=summary).strip()

            if not final_answer:
                # 兜底：从 narrative 提取
                narrative_raw = result_payload.get("narrative") or result_payload.get("output") or {}
                if isinstance(narrative_raw, dict):
                    final_answer = public_output_text(narrative_raw.get("text")).strip()
                elif isinstance(narrative_raw, str):
                    final_answer = public_output_text(narrative_raw).strip()

            # repair_agent 的 success 语义特殊：
            # 不能用 final_answer 是否非空来判断（修复失败时也会有 summary 文本），
            # 必须读取 repair_agent_trace.can_retry 才能知道修复是否真正成功、是否允许重试。
            if req.agent_id == AGENT_REPAIR:
                repair_trace = result_payload.get("repair_agent_trace") or {}
                repair_success = bool(repair_trace.get("can_retry", repair_trace.get("success", bool(final_answer))))
            else:
                repair_success = bool(final_answer)

            subgraph_commit_summary = (
                dict(result_payload.get("subgraph_commit_summary") or {})
                if isinstance(result_payload.get("subgraph_commit_summary"), Mapping)
                else {}
            )
            subgraph_trace_raw = result_payload.get("subgraph_trace")
            subgraph_trace = (
                [dict(entry) for entry in subgraph_trace_raw if isinstance(entry, Mapping)]
                if isinstance(subgraph_trace_raw, list)
                else []
            )
            if len(subgraph_trace) > 64:
                subgraph_trace = subgraph_trace[-64:]
            intent_agent_trace = (
                dict(result_payload.get("intent_agent_trace") or {})
                if isinstance(result_payload.get("intent_agent_trace"), Mapping)
                else {}
            )
            audit_refs: dict[str, Any] = {
                "agent_id": req.agent_id,
                "confidence": agent_artifact.get("confidence"),
                "risk_flags": agent_artifact.get("risk_flags"),
                "agent_context": {
                    "schema_version": agent_context_envelope.get("schema_version"),
                    "runtime_event_summary": build_context_runtime_event_payload(agent_context_envelope),
                    "conversation_turns": len(
                        list(dict(agent_context_envelope.get("conversation_window") or {}).get("recent_turns") or [])
                    ),
                    "memory_fact_count": len(list(dict(agent_context_envelope.get("memory_packet") or {}).get("facts") or [])),
                    "evidence_item_count": len(list(dict(agent_context_envelope.get("evidence_pack") or {}).get("items") or [])),
                    "token_budget": dict(agent_context_envelope.get("token_budget") or {}),
                    "context_authorization": dict(agent_context_envelope.get("authorization") or {}),
                    "pollution_report": dict(agent_context_envelope.get("pollution_report") or {}),
                    "trace": dict(agent_context_envelope.get("trace") or {}),
                },
            }
            if subgraph_commit_summary:
                audit_refs["subgraph_commit_summary"] = subgraph_commit_summary
            if subgraph_trace:
                audit_refs["subgraph_trace"] = subgraph_trace
            if intent_agent_trace:
                audit_refs["intent_agent_trace"] = intent_agent_trace

            resp = SubAgentResponse(
                success=repair_success,
                agent_id=req.agent_id,
                result=final_answer,
                audit_refs=dict(sanitize_public_payload(audit_refs)),
                latency_ms=latency_ms,
            )
            self._log_dispatch(req, resp)
            return resp

        except asyncio.TimeoutError:
            latency_ms = int(time.time() * 1000) - started_ms
            _logger.warning(
                "[%s] sub_agent timeout agent=%s timeout_ms=%d",
                req.request_id, req.agent_id, req.timeout_ms,
            )
            resp = SubAgentResponse(
                success=False,
                agent_id=req.agent_id,
                error=_runtime_text(
                    "timeout_after",
                    timeout_ms=req.timeout_ms,
                ),
                latency_ms=latency_ms,
            )
            self._log_dispatch(req, resp)
            return resp
        except Exception as exc:
            latency_ms = int(time.time() * 1000) - started_ms
            _logger.warning(
                "[%s] sub_agent failed agent=%s: %s",
                req.request_id, req.agent_id, exc,
            )
            resp = SubAgentResponse(
                success=False,
                agent_id=req.agent_id,
                error=public_output_text(str(exc))[:200],
                latency_ms=latency_ms,
            )
            self._log_dispatch(req, resp)
            return resp

    def _log_dispatch(self, req: SubAgentRequest, resp: SubAgentResponse) -> None:
        """将子 Agent 调度结果写入 skill_exec_log 供链路追踪。"""
        try:
            from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
            ledger = get_ledger()
            result_code = "SUB_AGENT_OK" if resp.success else "SUB_AGENT_FAILED"
            raw_result = str(resp.result or resp.error or "").strip()
            result_digest = public_decision_summary(
                raw_result,
                fallback=f"agent={resp.agent_id} success={str(bool(resp.success)).lower()}",
            )
            result_ref = content_ref("sub-agent-output", raw_result or result_digest)
            ledger.log_skill_exec({
                "request_id": req.request_id,
                "intent_id": f"intent::{req.request_id}::{resp.agent_id}",
                "event_id": f"evt::{req.request_id}::{resp.agent_id}",
                "skill_name": f"sub_agent:{resp.agent_id}",
                "approval_token_ref": None,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": "OK" if resp.success else "FAILED",
                "result_code": result_code,
                "result_digest": result_digest,
                "evidence_ref": result_ref,
                "manifest_ref": None,
                "ts_ms": int(time.time() * 1000),
            })
        except Exception:
            pass  # 审计不影响主流程


# 模块级单例
_dispatcher: SubAgentDispatcher | None = None


def get_sub_agent_dispatcher() -> SubAgentDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SubAgentDispatcher()
    return _dispatcher
