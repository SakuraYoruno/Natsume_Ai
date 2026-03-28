"""自适应 Prompt 预算分配器（Phase X 方案 2）。

职责：根据意图动态调整 RecallPack 各槽位的 token 预算。
复用 QueryPlanner 的意图分类结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from host_brain.app.schemas.recall_pack import (
    RECALL_PACK_TOKEN_BUDGET,
    RECALL_SLOT_TOKEN_BUDGET,
)

_logger = logging.getLogger("Natsume.retrieval.adaptive_budget")

# ---------------------------------------------------------------------------
# 意图 → 预算调整表
# ---------------------------------------------------------------------------
# 格式：{intent: {slot_name: delta_tokens}}
# 正值=增加预算，负值=减少预算，总和始终为 0（零和调整）
INTENT_BUDGET_ADJUSTMENTS: dict[str, dict[str, int]] = {
    "greeting": {},  # 跳过检索，不需要调整
    "chat": {
        "world_canon": -300,        # 闲聊不需要太多设定
        "artifact_vault": -100,     # 不需要制品
        "episodic_vector": +200,    # 对话历史更重要
        "persona_utterance": +100,  # 台词参考更重要
        "sqlite_ledger": +100,      # 意图日志可能有用
    },
    "recall_memory": {
        "world_canon": -400,        # 回忆场景不需要设定
        "artifact_vault": -100,     # 不需要制品
        "episodic_vector": +300,    # 情景记忆是核心
        "sqlite_ledger": +200,      # 事件日志是核心
    },
    "emotion": {
        "world_canon": -300,        # 情感场景不太需要设定
        "artifact_vault": -200,     # 不需要制品
        "episodic_vector": +200,    # 相关情感记忆
        "bond_graph": +200,         # Bond 历史更重要
        "persona_utterance": +100,  # 台词参考
    },
    "knowledge": {
        "world_canon": +200,        # 设定查询核心
        "style_canon": +100,        # 风格也可能相关
        "episodic_vector": -200,    # 不太需要对话历史
        "sqlite_ledger": -100,      # 不太需要事件日志
    },
    "tool": {
        "artifact_vault": +300,     # 制品库核心
        "world_canon": -200,        # 不需要设定
        "style_canon": -100,        # 不需要风格
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
    original_budget: dict[str, int]
    adjusted_budget: dict[str, int]
    total_budget: int
    adjustments_applied: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "original_budget": dict(self.original_budget),
            "adjusted_budget": dict(self.adjusted_budget),
            "total_budget": self.total_budget,
            "adjustments_applied": dict(self.adjustments_applied),
        }


# ---------------------------------------------------------------------------
# AdaptivePromptBudget
# ---------------------------------------------------------------------------
class AdaptivePromptBudget:
    """根据意图动态调整 RecallPack 各槽位 token 预算。

    使用方式::

        budget_mgr = AdaptivePromptBudget()
        result = budget_mgr.compute(intent="emotion")
        # result.adjusted_budget → {"world_canon": 500, "bond_graph": 500, ...}
    """

    def __init__(
        self,
        *,
        base_budget: dict[str, int] | None = None,
        total_budget: int = RECALL_PACK_TOKEN_BUDGET,
        adjustments: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self._base = dict(base_budget or RECALL_SLOT_TOKEN_BUDGET)
        self._total = int(total_budget)
        self._adjustments = dict(adjustments or INTENT_BUDGET_ADJUSTMENTS)

    def compute(self, intent: str) -> AdaptiveBudget:
        """根据意图计算调整后的预算。"""
        original = dict(self._base)
        deltas = self._adjustments.get(intent, {})

        if not deltas:
            return AdaptiveBudget(
                intent=intent,
                original_budget=original,
                adjusted_budget=dict(original),
                total_budget=self._total,
                adjustments_applied={},
            )

        # 应用调整
        adjusted = dict(original)
        applied: dict[str, int] = {}
        for slot, delta in deltas.items():
            if slot in adjusted:
                new_val = max(50, adjusted[slot] + delta)  # 最低 50 tokens
                applied[slot] = new_val - adjusted[slot]
                adjusted[slot] = new_val

        # 归一化：确保总和不超过总预算
        current_total = sum(adjusted.values())
        if current_total > self._total:
            scale = self._total / current_total
            adjusted = {k: max(50, int(v * scale)) for k, v in adjusted.items()}

        _logger.debug(
            "AdaptiveBudget: intent=%s adjustments=%s total=%d",
            intent, applied, sum(adjusted.values()),
        )

        return AdaptiveBudget(
            intent=intent,
            original_budget=original,
            adjusted_budget=adjusted,
            total_budget=self._total,
            adjustments_applied=applied,
        )
