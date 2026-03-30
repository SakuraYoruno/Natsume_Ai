"""自适应 Prompt 预算分配器（Phase X 方案 2 + Phase XIII 预算双路由）。

职责：
  1. 根据 LLM 路由目标（本地 14B / 云端 Gemini）选择基础预算档位
  2. 根据意图动态调整 RecallPack 各槽位的 token 预算
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from host_brain.app.schemas.recall_pack import (
    RECALL_BUDGET_CLOUD,
    RECALL_BUDGET_LOCAL,
    RECALL_PACK_TOKEN_BUDGET,
    RECALL_SLOT_BUDGET_CLOUD,
    RECALL_SLOT_BUDGET_LOCAL,
    RECALL_SLOT_TOKEN_BUDGET,
    get_budget_for_route,
)

_logger = logging.getLogger("Natsume.retrieval.adaptive_budget")

# ---------------------------------------------------------------------------
# 意图 → 预算调整表（比例制）
# ---------------------------------------------------------------------------
# Phase XIII: 改为比例调整（百分比），以适配不同基础预算档位。
# 正值=增加该比例，负值=减少该比例。
# 例如 "world_canon": -0.25 表示该 slot 预算缩减 25%。
INTENT_BUDGET_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "greeting": {},  # 跳过检索，不需要调整
    "phatic": {
        "world_canon": -0.40,       # 寒暄不需要设定
        "artifact_vault": -0.50,    # 不需要制品
        "episodic_vector": -0.15,   # 少量历史即可
        "sqlite_ledger": -0.20,     # 不太需要日志
        "bond_graph": +0.10,        # Bond 状态有用
    },
    "chat": {
        "world_canon": -0.20,       # 闲聊不需要太多设定
        "artifact_vault": -0.30,    # 不需要制品
        "episodic_vector": +0.15,   # 对话历史更重要
        "sqlite_ledger": +0.10,     # 意图日志可能有用
    },
    "recall_memory": {
        "world_canon": -0.30,       # 回忆场景不需要设定
        "artifact_vault": -0.50,    # 不需要制品
        "episodic_vector": +0.25,   # 情景记忆是核心
        "sqlite_ledger": +0.30,     # 事件日志是核心
    },
    "emotion": {
        "world_canon": -0.30,       # 情感场景不太需要设定
        "artifact_vault": -0.50,    # 不需要制品
        "episodic_vector": +0.20,   # 相关情感记忆
        "bond_graph": +0.30,        # Bond 历史更重要
    },
    "affection": {
        "world_canon": -0.40,       # 亲密对话不需要设定
        "artifact_vault": -0.50,    # 不需要制品
        "sqlite_ledger": -0.20,     # 不太需要日志
        "bond_graph": +0.40,        # Bond 关系是核心
        "episodic_vector": +0.20,   # 共同记忆重要
    },
    "knowledge": {
        "world_canon": +0.25,       # 设定查询核心
        "episodic_vector": -0.20,   # 不太需要对话历史
        "sqlite_ledger": -0.20,     # 不太需要事件日志
    },
    "tool": {
        "artifact_vault": +0.50,    # 制品库核心
        "world_canon": -0.30,       # 不需要设定
    },
    "complex": {},  # 复杂查询用默认预算
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class AdaptiveBudget:
    """自适应预算结果。"""

    intent: str
    route_target: str
    original_budget: dict[str, int]
    adjusted_budget: dict[str, int]
    total_budget: int
    adjustments_applied: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "route_target": self.route_target,
            "original_budget": dict(self.original_budget),
            "adjusted_budget": dict(self.adjusted_budget),
            "total_budget": self.total_budget,
            "adjustments_applied": dict(self.adjustments_applied),
        }


# ---------------------------------------------------------------------------
# AdaptivePromptBudget
# ---------------------------------------------------------------------------
class AdaptivePromptBudget:
    """根据 LLM 路由 + 意图动态调整 RecallPack 各槽位 token 预算。

    使用方式::

        budget_mgr = AdaptivePromptBudget()
        # 自动根据路由选择预算档位
        result = budget_mgr.compute(intent="emotion", route_target="host_14b")
        # result.adjusted_budget → 本地14B档位的情感预算
        result = budget_mgr.compute(intent="knowledge", route_target="cloud")
        # result.adjusted_budget → 云端Gemini档位的知识预算
    """

    def __init__(
        self,
        *,
        base_budget: dict[str, int] | None = None,
        total_budget: int | None = None,
        adjustments: dict[str, dict[str, float]] | None = None,
    ) -> None:
        # 如果显式传入 base_budget/total_budget，使用固定预算（向后兼容）
        self._fixed_base = dict(base_budget) if base_budget is not None else None
        self._fixed_total = int(total_budget) if total_budget is not None else None
        self._adjustments = dict(adjustments or INTENT_BUDGET_ADJUSTMENTS)

    def compute(self, intent: str, route_target: str = "") -> AdaptiveBudget:
        """根据路由目标和意图计算调整后的预算。

        Args:
            intent: 意图类型（greeting/chat/emotion/knowledge/tool/complex）
            route_target: LLM 路由目标（"host_14b" / "cloud" / ""）
        """
        # 选择基础预算档位
        if self._fixed_base is not None:
            base = dict(self._fixed_base)
            total = self._fixed_total or sum(base.values())
        else:
            base, total = get_budget_for_route(route_target)

        original = dict(base)
        deltas = self._adjustments.get(intent, {})

        if not deltas:
            return AdaptiveBudget(
                intent=intent,
                route_target=route_target or "default",
                original_budget=original,
                adjusted_budget=dict(original),
                total_budget=total,
                adjustments_applied={},
            )

        # 应用比例调整
        adjusted = dict(original)
        applied: dict[str, int] = {}
        for slot, ratio in deltas.items():
            if slot in adjusted:
                delta = int(adjusted[slot] * ratio)
                new_val = max(50, adjusted[slot] + delta)  # 最低 50 tokens
                applied[slot] = new_val - adjusted[slot]
                adjusted[slot] = new_val

        # 归一化：确保总和不超过总预算
        current_total = sum(adjusted.values())
        if current_total > total:
            scale = total / current_total
            adjusted = {k: max(50, int(v * scale)) for k, v in adjusted.items()}

        _logger.debug(
            "AdaptiveBudget: intent=%s route=%s base_total=%d adjusted_total=%d",
            intent, route_target, total, sum(adjusted.values()),
        )

        return AdaptiveBudget(
            intent=intent,
            route_target=route_target or "default",
            original_budget=original,
            adjusted_budget=adjusted,
            total_budget=total,
            adjustments_applied=applied,
        )
