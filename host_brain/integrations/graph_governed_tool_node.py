"""GovernedToolNode：工具调用执行节点（Governor 门控 + 直接工具执行 + Evidence Plane 审计）。

直接执行工具调用，不依赖 LangGraph ToolNode（ToolNode 需要 messages 格式，与 react_loop 不兼容）。
Governor 门控和 Evidence Plane 审计作为不可绕过的前置/后置钩子保留。
"""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger(__name__)


class GovernedToolNode:
    """工具调用执行节点（Governor 门控 + 直接工具执行 + Evidence Plane 审计）。

    核心约束（FIX-PLAN §5.2）：
    1. Governor 门控（保留，不可绕过）：
       - 工具调用前调用 governor.evaluate_tool_call()
       - 裁决结果写入 committed_route_token
       - 非 GOV_APPROVE 时直接返回，不执行工具
    2. Evidence Plane 审计（保留，不可绕过）：
       - 工具调用后写入 Evidence Plane
    3. 直接工具执行：
       - 通过工具名称查找并调用工具
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
        # 构建工具名称到工具的映射
        self._tool_map: dict[str, Any] = {}
        for tool in self._tools:
            name = getattr(tool, "name", None) or str(tool)
            if name:
                self._tool_map[name] = tool

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

    def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """直接执行工具调用。"""
        tool = self._tool_map.get(tool_name)
        if tool is None:
            return {"error": f"工具 '{tool_name}' 未找到", "result": None}
        try:
            # 优先使用 .func(**kwargs)（LangChain StructuredTool 的底层函数）
            # 避免 .invoke() 的 Pydantic 参数解析问题
            if hasattr(tool, "func") and callable(tool.func):
                result = tool.func(**tool_args)
            elif hasattr(tool, "invoke"):
                result = tool.invoke(tool_args)
            elif callable(tool):
                result = tool(**tool_args)
            else:
                result = {"error": f"工具 '{tool_name}' 不可调用", "result": None}
            return result
        except Exception as exc:
            return {"error": str(exc), "result": None}

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用（Governor 门控 → 直接工具执行 → Evidence Plane 审计）。

        Args:
            state: 包含 tool_calls 列表的字典

        Returns:
            更新后的字典，包含 tool_results
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

        # 2. 直接执行工具
        tool_results = []
        for tc in tool_calls:
            tool_name = str(tc.get("tool") or "").strip()
            tool_args = dict(tc.get("args") or {})
            result = self._execute_tool(tool_name, tool_args)
            tool_results.append({
                "tool": tool_name,
                "args": tool_args,
                "result": result,
                "ts_ms": int(time.time() * 1000),
            })

        merged = {**state, "tool_results": tool_results}

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
