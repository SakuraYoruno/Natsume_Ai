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

_logger = logging.getLogger("Natsume.sub_agent")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentRequest:
    """Cortex Orchestrator → 子 Agent 的调度请求。"""
    agent_id: str                          # "code_agent" / "translate_agent" / "intent_agent" / "vision_agent"
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

AGENT_CODE = "code_agent"
AGENT_TRANSLATE = "translate_agent"
AGENT_INTENT = "intent_agent"
AGENT_VISION = "vision_agent"
AGENT_REPAIR = "repair_agent"

ALL_AGENT_IDS: frozenset[str] = frozenset({
    AGENT_CODE, AGENT_TRANSLATE, AGENT_INTENT, AGENT_VISION, AGENT_REPAIR,
})

# 子 Agent 能力描述（注入 Cortex Orchestrator prompt，供 LLM 判断调度）
AGENT_CAPABILITIES: dict[str, str] = {
    AGENT_CODE: "代码专家：生成、修改、审查代码，解释技术概念",
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
    AGENT_CODE: "代码专家：生成、修改、审查代码，并解释技术概念",
    AGENT_TRANSLATE: "翻译专家：处理中日英多语翻译，包含校对和润色",
    AGENT_INTENT: "执行专家：理解用户的执行意图，并调用工具完成实际操作（文件 / 网络 / 系统）",
    AGENT_VISION: "视觉分析：分析图片或截图内容，提取 UI 元素与关键信息",
    AGENT_REPAIR: "环境修复专家：当浏览器、依赖或运行环境缺失时，尝试自动安装并恢复执行能力",
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
            from host_brain.app.handlers.code_agent_handler import code_agent_handler
            self._handler_map[AGENT_CODE] = code_agent_handler
        except ImportError:
            pass
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

        # 构建 handler 入参（兼容现有 handler 的 payload/context 接口）
        payload: dict[str, Any] = {
            "user_input": req.task,
            "query": req.task,
            "request_id": req.request_id,
            **req.context,
        }
        context: dict[str, Any] = {
            "request_id": req.request_id,
            "session_id": req.session_id,
            "source": "executive_router_dispatch",
        }
        context.update(dict(req.context or {}))
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

            # 也从 react_scratchpad 提取 final_answer
            scratchpad = result_payload.get("react_scratchpad") or {}
            final_answer = str(scratchpad.get("final_answer") or summary or "").strip()

            if not final_answer:
                # 兜底：从 narrative 提取
                narrative_raw = result_payload.get("narrative") or result_payload.get("output") or {}
                if isinstance(narrative_raw, dict):
                    final_answer = str(narrative_raw.get("text") or "").strip()
                elif isinstance(narrative_raw, str):
                    final_answer = narrative_raw.strip()

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
                audit_refs=audit_refs,
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
                error=str(exc)[:200],
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
            result_digest = str(resp.result or resp.error or "").strip()
            if not result_digest:
                result_digest = f"agent={resp.agent_id} success={str(bool(resp.success)).lower()}"
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
                "result_digest": result_digest[:4000],
                "evidence_ref": f"sub_agent::{req.request_id}::{resp.agent_id}",
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
