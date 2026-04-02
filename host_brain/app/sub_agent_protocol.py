"""sub_agent_protocol.py — 子 Agent 调度协议（Phase XIV v4）。

定义顶级 Agent 与子 Agent 之间的通信协议：
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
from typing import Any

_logger = logging.getLogger("Natsume.sub_agent")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentRequest:
    """顶级 Agent → 子 Agent 的调度请求。"""
    agent_id: str                          # "code_agent" / "translate_agent" / "intent_agent" / "vision_agent"
    task: str                              # 自然语言任务描述
    user_message: str = ""                 # 用户原始消息
    request_id: str = ""
    session_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 30_000


@dataclass
class SubAgentResponse:
    """子 Agent → 顶级 Agent 的执行结果。"""
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

# 子 Agent 能力描述（注入顶级 Agent prompt，供 LLM 判断调度）
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
        # vision_agent 暂未实现独立 handler，复用 intent_agent
        if AGENT_VISION not in self._handler_map and AGENT_INTENT in self._handler_map:
            self._handler_map[AGENT_VISION] = self._handler_map[AGENT_INTENT]
        try:
            from host_brain.app.handlers.repair_agent_handler import repair_agent_handler
            self._handler_map[AGENT_REPAIR] = repair_agent_handler
        except ImportError:
            pass

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
                error=f"unknown agent_id: {req.agent_id}",
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
            "source": "top_agent_dispatch",
        }
        context.update(dict(req.context or {}))

        try:
            timeout_sec = max(1.0, req.timeout_ms / 1000.0)
            result = await asyncio.wait_for(
                asyncio.to_thread(handler, payload, context),
                timeout=timeout_sec,
            )

            latency_ms = int(time.time() * 1000) - started_ms

            # 从 handler 返回的 state 中提取结果
            artifacts = result.get("agent_artifacts") or {}
            agent_artifact = artifacts.get(req.agent_id) or {}
            summary = str(agent_artifact.get("summary") or "").strip()

            # 也从 react_scratchpad 提取 final_answer
            scratchpad = result.get("react_scratchpad") or {}
            final_answer = str(scratchpad.get("final_answer") or summary or "").strip()

            if not final_answer:
                # 兜底：从 narrative 提取
                narrative_raw = result.get("narrative") or result.get("output") or {}
                if isinstance(narrative_raw, dict):
                    final_answer = str(narrative_raw.get("text") or "").strip()
                elif isinstance(narrative_raw, str):
                    final_answer = narrative_raw.strip()

            resp = SubAgentResponse(
                success=bool(final_answer),
                agent_id=req.agent_id,
                result=final_answer,
                audit_refs={
                    "agent_id": req.agent_id,
                    "confidence": agent_artifact.get("confidence"),
                    "risk_flags": agent_artifact.get("risk_flags"),
                },
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
                error=f"timeout after {req.timeout_ms}ms",
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
            ledger.log_skill_exec({
                "request_id": req.request_id,
                "session_id": req.session_id,
                "skill_name": f"sub_agent:{resp.agent_id}",
                "result_status": "OK" if resp.success else "FAILED",
                "reason_code": resp.error[:120] if resp.error else None,
                "latency_ms": resp.latency_ms,
                "payload": {
                    "task": req.task[:200],
                    "agent_id": resp.agent_id,
                    "success": resp.success,
                    "audit_refs": resp.audit_refs,
                },
                "ts_ms": int(time.time() * 1000),
            })
        except Exception:
            pass  # 审计不影响主流程

    async def dispatch_parallel(
        self, requests: list[SubAgentRequest],
    ) -> list[SubAgentResponse]:
        """并行调度多个子 Agent。"""
        return list(await asyncio.gather(
            *(self.dispatch(req) for req in requests),
            return_exceptions=False,
        ))


# 模块级单例
_dispatcher: SubAgentDispatcher | None = None


def get_sub_agent_dispatcher() -> SubAgentDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SubAgentDispatcher()
    return _dispatcher
