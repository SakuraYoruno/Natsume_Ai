"""已退役的嵌套 Graph/ReAct 工具执行兼容门面。

运行中 Agent 选出的工具只是提案，不拥有立即执行权。若要执行，规划器必须把
提案编译为新的不可变 TaskGraph 修订版，并重新走治理准入。本兼容对象刻意不
保留 Broker、描述符或回调，避免旧调用路径重新取得副作用能力。
"""

from __future__ import annotations

from typing import Any


REACT_TOOL_GOVERNED_ADMISSION_REQUIRED = (
    "REACT_TOOL_GOVERNED_ADMISSION_REQUIRED"
)


class GovernedToolNode:
    """对所有遗留嵌套工具调用 fail closed，保留原因码供上层重新规划。"""

    def __init__(
        self,
        *,
        tools: list[Any] | None = None,
        governor: Any = None,
        evidence_plane: Any = None,
        governor_rules_cache: Any = None,
        capability_broker: Any = None,
    ) -> None:
        # 兼容参数必须主动丢弃；保留回调或 Broker 都会让已退役路径重新拥有执行权。
        del tools, governor, evidence_plane, governor_rules_cache, capability_broker

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """将每个遗留调用转换为“需要治理准入”的无副作用结果。"""
        tool_calls = list(state.get("tool_calls") or [])
        results: list[dict[str, Any]] = []
        for raw in tool_calls:
            # 只回显可识别的工具名和统一拒绝原因；不解析参数、更不尝试调用目标。
            call = dict(raw) if isinstance(raw, dict) else {}
            tool_name = str(call.get("tool") or "").strip()
            results.append(
                {
                    "tool": tool_name,
                    "result": {
                        "error": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                        "reason_code": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                        "result": None,
                        "side_effects_executed": False,
                        "required_execution_surface": (
                            "new immutable TaskGraph revision"
                        ),
                    },
                }
            )
        return {
            # 令上层看见控制面拒绝，促使其创建新图修订而非沿当前图继续执行工具。
            "committed_route_token": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
            "tool_results": results,
        }


__all__ = ["GovernedToolNode", "REACT_TOOL_GOVERNED_ADMISSION_REQUIRED"]
