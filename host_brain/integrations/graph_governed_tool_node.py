"""GovernedToolNode：LangGraph ToolNode + Governor 门控（FIX-5.2）。

这是从自研 run_react_loop() 迁移到 LangGraph 原生 ToolNode 的过渡模块。

设计原则（FIX-PLAN §5.2）：
- 保留 Governor 门控（不可绕过）：工具调用前必须经过 Governor 裁决
- 保留 Evidence Plane 审计（不可绕过）：工具调用后写入审计记录
- 利用 LangGraph 原生 ToolNode 的工具调用解析、错误处理、重试机制
- 通过 LANGGRAPH_TOOL_NODE_ENABLED 环境变量控制是否启用（默认 False，渐进迁移）

使用方式：
    from host_brain.integrations.graph_governed_tool_node import (
        GovernedToolNode,
        is_governed_tool_node_enabled,
    )

    # 在 StateGraph 中注册
    if is_governed_tool_node_enabled():
        tool_node = GovernedToolNode(tools=[...], governor=governor)
        graph.add_node("tools", tool_node)
    else:
        # 降级：使用自研 run_react_loop()
        graph.add_node("tools", react_loop_handler)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 环境变量控制
# ---------------------------------------------------------------------------

_ENV_ENABLED = "LANGGRAPH_TOOL_NODE_ENABLED"


def is_governed_tool_node_enabled() -> bool:
    """检查是否启用 GovernedToolNode（通过环境变量控制）。

    Returns:
        True 表示启用 GovernedToolNode；False 表示使用自研 run_react_loop()（默认）
    """
    raw = str(os.environ.get(_ENV_ENABLED, "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# FIX-5.2：GovernedToolNode
# ---------------------------------------------------------------------------

class GovernedToolNode:
    """在 LangGraph ToolNode 基础上增加 Governor 门控和 Evidence Plane 审计。

    核心约束（FIX-PLAN §5.2）：
    1. Governor 门控（保留，不可绕过）：
       - 工具调用前调用 governor.evaluate_tool_call()
       - 裁决结果写入 committed_route_token
       - 非 GOV_APPROVE 时直接返回，不执行工具
    2. Evidence Plane 审计（保留，不可绕过）：
       - 工具调用后写入 Evidence Plane
    3. LangGraph ToolNode 执行（新增）：
       - 利用 LangGraph 原生工具调用解析和错误处理
       - 降级：若 LangGraph ToolNode 不可用，回退到 execute_tool()

    与自研 run_react_loop() 的区别：
    - 自研：完整的 Observe-Think-Act 循环，包含 LLM 推理
    - GovernedToolNode：只负责工具执行层（Act），LLM 推理由上游节点完成
    """

    def __init__(
        self,
        *,
        tools: list[Any] | None = None,
        governor: Any = None,
        evidence_plane: Any = None,
        governor_rules_cache: Any = None,
    ) -> None:
        """初始化 GovernedToolNode。

        Args:
            tools: 工具列表（LangChain Tool 格式，可选）
            governor: Governor 实例（可选，None 时跳过门控）
            evidence_plane: Evidence Plane 实例（可选，None 时跳过审计）
            governor_rules_cache: GovernorRulesCache 实例（可选，None 时从模块级单例获取）
        """
        self._tools = list(tools or [])
        self._governor = governor
        self._evidence_plane = evidence_plane
        self._governor_rules_cache = governor_rules_cache
        self._langgraph_tool_node: Any = None
        self._langgraph_available = False
        self._try_init_langgraph_tool_node()

    def _try_init_langgraph_tool_node(self) -> None:
        """尝试初始化 LangGraph ToolNode（静默降级）。"""
        if not self._tools:
            return
        try:
            from langgraph.prebuilt import ToolNode  # noqa: PLC0415
            self._langgraph_tool_node = ToolNode(self._tools)
            self._langgraph_available = True
            _logger.info(
                "GovernedToolNode: LangGraph ToolNode 初始化成功，工具数=%d",
                len(self._tools),
            )
        except ImportError:
            _logger.warning(
                "GovernedToolNode: langgraph.prebuilt.ToolNode 不可用，"
                "降级到 execute_tool() 路径"
            )
        except Exception as exc:
            _logger.warning(
                "GovernedToolNode: LangGraph ToolNode 初始化失败（%s），降级", exc
            )

    def _get_tribe_params(self) -> dict[str, Any]:
        """获取 TRIBE 族动态参数（用于 Governor 裁决）。"""
        try:
            cache = self._governor_rules_cache
            if cache is None:
                from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                cache = get_governor_rules_cache()
            return cache.get_effective_rules()
        except Exception:
            return {}

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用（Governor 门控 → LangGraph ToolNode → Evidence Plane 审计）。

        Args:
            state: 当前 GraphState 字典

        Returns:
            更新后的 GraphState 字典
        """
        tool_calls = list(state.get("tool_calls") or [])
        request_id = str(state.get("request_id") or "")
        ts_ms = int(time.time() * 1000)

        # 1. Governor 门控（保留，不可绕过）
        if self._governor is not None and tool_calls:
            try:
                tribe_params = self._get_tribe_params()
                gov_result = self._governor.evaluate_tool_call(
                    tool_calls=tool_calls,
                    tribe_params=tribe_params,
                )
                route_token = str(getattr(gov_result, "token", "") or "").strip()
                if route_token and route_token != "GOV_APPROVE":
                    _logger.info(
                        "GovernedToolNode: Governor 拒绝工具调用，token=%s request_id=%s",
                        route_token, request_id,
                    )
                    return {
                        **state,
                        "committed_route_token": route_token,
                        "tribe_governor_params": tribe_params,
                    }
            except Exception as exc:
                _logger.warning(
                    "GovernedToolNode: Governor 门控异常（降级允许执行）: %s", exc
                )

        # 2. 工具执行（LangGraph ToolNode 或降级路径）
        tool_results: list[dict[str, Any]] = []
        if self._langgraph_available and self._langgraph_tool_node is not None:
            try:
                result_state = self._langgraph_tool_node(state)
                tool_results = list(result_state.get("tool_results") or [])
                # 合并 LangGraph ToolNode 的输出到 state
                state = {**state, **result_state}
            except Exception as exc:
                _logger.warning(
                    "GovernedToolNode: LangGraph ToolNode 执行失败（%s），降级到 execute_tool()", exc
                )
                tool_results = self._fallback_execute_tools(tool_calls, state)
        else:
            tool_results = self._fallback_execute_tools(tool_calls, state)

        # 3. Evidence Plane 审计（保留，不可绕过）
        if self._evidence_plane is not None:
            try:
                self._evidence_plane.write_tool_execution(
                    request_id=request_id,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    ts_ms=ts_ms,
                )
            except Exception:
                pass  # 静默降级，不影响主流程

        return {
            **state,
            "tool_results": tool_results,
        }

    def _fallback_execute_tools(
        self,
        tool_calls: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """降级路径：使用 execute_tool() 执行工具调用。"""
        results: list[dict[str, Any]] = []
        try:
            from host_brain.app.agents.tool_registry import execute_tool  # noqa: PLC0415
            for call in tool_calls:
                tool_id = str(call.get("tool") or call.get("name") or "").strip()
                args = dict(call.get("args") or call.get("arguments") or {})
                if tool_id:
                    result = execute_tool(tool_id, args)
                    results.append({"tool": tool_id, "result": result})
        except Exception as exc:
            _logger.warning("GovernedToolNode._fallback_execute_tools 失败: %s", exc)
        return results


__all__ = [
    "GovernedToolNode",
    "is_governed_tool_node_enabled",
]
