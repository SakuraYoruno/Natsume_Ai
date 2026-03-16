"""Agent 数据契约：artifact contract、agent_runtime 结构定义。

设计原则：
- 子 agent 只产出 AgentArtifact（工件），不直接控制流程
- handoff_recommendation 只是建议，真正下一跳由 handler 编译决定
- agent_runtime 第一阶段放入 payload["agent_runtime"]，不扩 GraphState 顶层
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.app.agents.agent_registry import (
    AGENT_ID_JUDGE,
    AGENT_ALLOWLIST,
    is_agent_allowed,
)

# ---------------------------------------------------------------------------
# 风险模式常量
# ---------------------------------------------------------------------------

RISK_MODE_SAFE = "safe"          # 聊天 / 翻译等低风险
RISK_MODE_GUARDED = "guarded"    # 代码等中风险
RISK_MODE_STRICT = "strict"      # 执行意图等高风险

VALID_RISK_MODES: frozenset[str] = frozenset({
    RISK_MODE_SAFE,
    RISK_MODE_GUARDED,
    RISK_MODE_STRICT,
})

# ---------------------------------------------------------------------------
# AgentArtifact：子 agent 输出工件
# ---------------------------------------------------------------------------

@dataclass
class AgentArtifact:
    """子 agent 输出的工件契约。

    子 agent 只产出此结构，不直接写 __next_node__ 或 committed_route_token。
    handoff_recommendation 只是建议，handler 负责最终决策。
    """
    agent_id: str
    artifact_kind: str                          # draft_answer / code_draft / translation / plan
    draft_summary: str
    artifact_ref: str                           # artifact://req_xxx/xxx
    confidence: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    handoff_recommendation: str = AGENT_ID_JUDGE  # 建议下一跳（不是命令）
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "artifact_kind": self.artifact_kind,
            "draft_summary": self.draft_summary,
            "artifact_ref": self.artifact_ref,
            "confidence": self.confidence,
            "risk_flags": list(self.risk_flags),
            "handoff_recommendation": self.handoff_recommendation,
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# AgentRuntime：agent_runtime payload 结构
# ---------------------------------------------------------------------------

@dataclass
class AgentRuntime:
    """agent_runtime payload 结构（第一阶段放入 payload["agent_runtime"]）。

    由 Supervisor handler 初始化并维护，子 agent 只读不写。
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
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
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
            "artifacts": dict(self.artifacts),
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

    def record_artifact(self, artifact: AgentArtifact) -> None:
        """记录子 agent 输出的工件。"""
        self.artifacts[artifact.agent_id] = {
            "artifact_ref": artifact.artifact_ref,
            "summary": artifact.draft_summary,
            "confidence": artifact.confidence,
            "risk_flags": list(artifact.risk_flags),
        }


# ---------------------------------------------------------------------------
# SupervisorProposal：Supervisor LLM 输出的分派提案
# ---------------------------------------------------------------------------

@dataclass
class SupervisorProposal:
    """Supervisor LLM 输出的分派提案（不直接控图）。

    Handler 校验此提案后，才写入 __next_node__ 和 agent_runtime.plan。
    """
    selected_agent: str
    reason: str
    risk_mode: str = RISK_MODE_SAFE
    need_judge: bool = True
    budget_max_hops: int = 2
    budget_timeout_ms: int = 12000

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_agent": self.selected_agent,
            "reason": self.reason,
            "risk_mode": self.risk_mode,
            "need_judge": self.need_judge,
            "budget": {
                "max_hops": self.budget_max_hops,
                "timeout_ms": self.budget_timeout_ms,
            },
        }


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def build_agent_runtime_default(
    *,
    request_id: str,
    active_agent_id: str,
    risk_mode: str = RISK_MODE_SAFE,
    max_hops: int = 2,
    need_judge: bool = True,
) -> AgentRuntime:
    """构建默认 agent_runtime 结构。"""
    plan_id = f"plan_{request_id}_{int(time.time() * 1000)}"
    return AgentRuntime(
        plan_id=plan_id,
        active_agent_id=active_agent_id,
        agent_hops=0,
        max_hops=max_hops,
        risk_mode=risk_mode,
        need_judge=need_judge,
        budget_max_hops=max_hops,
        budget_per_agent_timeout_ms=12000,
        budget_total_ms=30000,
    )


def build_artifact(
    *,
    agent_id: str,
    artifact_kind: str,
    draft_summary: str,
    request_id: str,
    confidence: float = 0.0,
    risk_flags: list[str] | None = None,
    handoff_recommendation: str = AGENT_ID_JUDGE,
) -> AgentArtifact:
    """构建子 agent 输出工件。"""
    artifact_ref = f"artifact://{request_id}/{agent_id}_{int(time.time() * 1000)}"
    return AgentArtifact(
        agent_id=agent_id,
        artifact_kind=artifact_kind,
        draft_summary=draft_summary,
        artifact_ref=artifact_ref,
        confidence=confidence,
        risk_flags=list(risk_flags or []),
        handoff_recommendation=handoff_recommendation,
    )


def validate_supervisor_proposal(proposal_dict: dict[str, Any]) -> tuple[bool, str]:
    """校验 Supervisor 分派提案合法性。

    Returns:
        (is_valid, reason)：is_valid=True 表示提案合法，可以编译为 agent_runtime.plan
    """
    selected_agent = str(proposal_dict.get("selected_agent") or "").strip()
    if not selected_agent:
        return False, "PROPOSAL_MISSING_AGENT"

    if not is_agent_allowed(selected_agent):
        return False, f"PROPOSAL_AGENT_NOT_ALLOWED:{selected_agent}"

    risk_mode = str(proposal_dict.get("risk_mode") or RISK_MODE_SAFE).strip()
    if risk_mode not in VALID_RISK_MODES:
        return False, f"PROPOSAL_INVALID_RISK_MODE:{risk_mode}"

    budget = proposal_dict.get("budget") or {}
    max_hops = int(budget.get("max_hops") or 2)
    if max_hops < 1 or max_hops > 5:
        return False, f"PROPOSAL_INVALID_MAX_HOPS:{max_hops}"

    return True, "PROPOSAL_VALID"
