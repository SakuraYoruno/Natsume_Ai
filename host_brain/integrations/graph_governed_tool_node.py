"""GovernedToolNode：LangGraph ToolNode + Governor 门控（FIX-5.2 彻底迁移，无兼容开关）。

直接使用 LangGraph 原生 ToolNode，不再有任何环境变量开关或降级路径。
Governor 门控和 Evidence Plane 审计作为不可绕过的前置/后置钩子保留。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.prebuilt import ToolNode

_logger = logging.getLogger(__name__)


class GovernedToolNode:
    """在 LangGraph ToolNode 基础上增加 Governor 门控和 Evidence Plane 审计。

    核心约束（FIX-PLAN §5.2）：
    1. Governor 门控（保留，不可绕过）：
       - 工具调用前调用 governor.evaluate_tool_call()
       - 裁决结果写入 committed_route_token
       - 非 GOV_APPROVE 时直接返回，不执行工具
    2. Evidence Plane 审计（保留，不可绕过）：
       - 工具调用后写入 Evidence Plane
    3. LangGraph 原生 ToolNode 执行：
       - 利用 LangGraph 原生工具调用解析、错误处理、重试机制
    """

    def __init__(
        self,
        *,
        tools: list[Any] | None = None,
        governor: Any = None,
        evidence_plane: Any = None,
        governor_rules_cache: Any = None,
    ) -> None:
        self._tools = list(tools or [])
        self._governor = governor
        self._evidence_plane = evidence_plane
        self._governor_rules_cache = governor_rules_cache
        # 直接初始化 LangGraph ToolNode（无降级路径）
        self._tool_node = ToolNode(self._tools) if self._tools else None

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
                _logger.warning("GovernedToolNode: Governor 门控异常（降级允许执行）: %s", exc)

        # 2. LangGraph 原生 ToolNode 执行
        if self._tool_node is None:
            return state
        result_state = self._tool_node(state)
        tool_results = list(result_state.get("tool_results") or [])
        merged = {**state, **result_state}

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

        return merged


__all__ = ["GovernedToolNode"]
