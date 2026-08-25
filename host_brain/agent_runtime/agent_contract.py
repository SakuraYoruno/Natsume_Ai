"""Agent dispatch risk and in-memory runtime metadata contracts.

Durable agent artifacts are owned by :mod:`host_brain.agent_runtime.artifact_store`.
This module only keeps the small runtime projection used by the dispatcher and
the shared risk-mode contract; it must not grow another artifact authority.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 风险模式常量
# ---------------------------------------------------------------------------

RISK_MODE_SAFE = "safe"          # 聊天 / 翻译等低风险
RISK_MODE_GUARDED = "guarded"    # 代码等中风险
RISK_MODE_STRICT = "strict"      # 执行意图等高风险

# ---------------------------------------------------------------------------
# AgentRuntime：agent_runtime payload 结构
# ---------------------------------------------------------------------------

@dataclass
class AgentRuntime:
    """agent_runtime 结构（M-MA3：统一存储在 GraphState 顶层字段）。

    由 Supervisor handler 初始化并维护，子 agent 只读不写。
    对应 GraphState 字段：active_agent_id / handoff_history / agent_budget / judge_result。
    持久化 agent_artifacts 由 ``AgentArtifactStore`` 独占，不在此运行态投影中复制。
    """
    plan_id: str
    active_agent_id: str
    agent_hops: int = 0
    max_hops: int = 2
    risk_mode: str = RISK_MODE_SAFE
    need_judge: bool = True
    budget_max_hops: int = 2
    budget_per_agent_timeout_ms: int = 12000
    budget_total_ms: int = 30000
    handoff_history: list[dict[str, Any]] = field(default_factory=list)
    multi_agent_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "active_agent_id": self.active_agent_id,
            "agent_hops": self.agent_hops,
            "max_hops": self.max_hops,
            "risk_mode": self.risk_mode,
            "need_judge": self.need_judge,
            "budget": {
                "max_hops": self.budget_max_hops,
                "per_agent_timeout_ms": self.budget_per_agent_timeout_ms,
                "total_budget_ms": self.budget_total_ms,
            },
            "handoff_history": list(self.handoff_history),
            "multi_agent_enabled": self.multi_agent_enabled,
        }

    def add_handoff(self, *, from_agent: str, to_agent: str, reason: str) -> None:
        """记录 handoff 历史。"""
        self.handoff_history.append({
            "from": from_agent,
            "to": to_agent,
            "reason": reason,
            "ts_ms": int(time.time() * 1000),
        })
        self.agent_hops += 1
