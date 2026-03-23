"""BondNarrativeLayer：关系叙事层——Phase V。

职责：
  1. 将 SharedMemoryIndex + MilestoneTracker 的数据转化为自然语言叙事
  2. 输出限制 ≤ 3 条叙事片段（防止 prompt 膨胀）
  3. 注入 prompt_context 的 bond_narrative 可选槽位

设计原则：
  - 叙事层是纯转换器，不持有状态
  - 叙事风格随 Bond 阶段变化
  - 空数据时返回空字典，不注入
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from host_brain.bond.shared_memory_index import (
    SharedMemoryIndex,
    SharedMemoryResult,
)
from host_brain.bond.milestone_tracker import (
    MilestoneTracker,
)

_logger = logging.getLogger("Natsume.bond_narrative")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 最大叙事片段数
MAX_NARRATIVE_ITEMS = 3

# 阶段叙事风格
STAGE_NARRATIVE_STYLE: dict[str, str] = {
    "STRANGER": "保持礼貌距离",
    "ACQUAINTANCE": "可以提及共同经历",
    "FRIEND": "自然地回忆共同时光",
    "CLOSE_FRIEND": "可以深情地提起共同回忆",
    "CONFIDANT": "用亲密的语气谈起你们的故事",
    "SOULMATE": "将共同记忆视为珍贵的宝藏",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeItem:
    """单条叙事片段。"""
    source: str      # "shared_memory" / "milestone"
    text: str        # 自然语言叙事
    emotion: str     # 情感标签（用于语气参考）
    weight: float    # 权重（用于排序）

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "text": self.text,
            "emotion": self.emotion,
            "weight": round(self.weight, 4),
        }


@dataclass
class BondNarrativeResult:
    """Bond 叙事结果。"""
    items: list[NarrativeItem] = field(default_factory=list)
    bond_stage: str = "STRANGER"
    narrative_style: str = ""
    shared_memory_count: int = 0
    milestone_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "items": [item.as_dict() for item in self.items],
            "bond_stage": self.bond_stage,
            "narrative_style": self.narrative_style,
            "shared_memory_count": self.shared_memory_count,
            "milestone_count": self.milestone_count,
        }
        return result

    def to_prompt_slot(self) -> dict[str, Any]:
        """转化为 prompt_context 的 bond_narrative 槽位值。"""
        if not self.items:
            return {}
        narratives = [item.text for item in self.items[:MAX_NARRATIVE_ITEMS]]
        return {
            "shared_memories": narratives,
            "milestone_count": self.milestone_count,
            "narrative_style": self.narrative_style,
        }


# ---------------------------------------------------------------------------
# BondNarrativeLayer
# ---------------------------------------------------------------------------

class BondNarrativeLayer:
    """关系叙事层。

    用法：
        layer = BondNarrativeLayer(
            memory_index=shared_memory_index,
            milestone_tracker=milestone_tracker,
        )
        result = layer.build_narrative(
            user_id="user_1",
            bond_stage="FRIEND",
        )
    """

    def __init__(
        self,
        *,
        memory_index: SharedMemoryIndex | None = None,
        milestone_tracker: MilestoneTracker | None = None,
        max_items: int = MAX_NARRATIVE_ITEMS,
    ) -> None:
        self._memory_index = memory_index
        self._milestone_tracker = milestone_tracker
        self._max_items = int(max_items)

    def build_narrative(
        self,
        *,
        user_id: str,
        bond_stage: str = "STRANGER",
    ) -> BondNarrativeResult:
        """构建 Bond 叙事。

        Args:
            user_id: 用户 ID
            bond_stage: 当前 Bond 阶段

        Returns:
            BondNarrativeResult
        """
        uid = str(user_id).strip()
        stage = str(bond_stage).strip().upper()
        style = STAGE_NARRATIVE_STYLE.get(stage, "保持真诚自然")

        items: list[NarrativeItem] = []

        # 1. 从共同记忆中生成叙事
        shared_count = 0
        if self._memory_index is not None:
            try:
                memory_result = self._memory_index.query(max_entries=self._max_items)
                shared_count = memory_result.total_candidates
                for entry in memory_result.entries:
                    narrative_text = self._memory_to_narrative(entry.text, stage)
                    items.append(NarrativeItem(
                        source="shared_memory",
                        text=narrative_text,
                        emotion=entry.emotion_tag,
                        weight=entry.relevance_score,
                    ))
            except Exception as exc:
                _logger.warning("共同记忆叙事构建失败: %s", exc)

        # 2. 从里程碑中生成叙事
        milestone_count = 0
        if self._milestone_tracker is not None:
            try:
                milestones = self._milestone_tracker.get_recent_milestones(
                    uid, limit=self._max_items,
                )
                milestone_count = self._milestone_tracker.get_milestone_count(uid)
                for ms in milestones:
                    items.append(NarrativeItem(
                        source="milestone",
                        text=ms.narrative,
                        emotion="NOSTALGIC",
                        weight=0.5,  # 里程碑默认权重
                    ))
            except Exception as exc:
                _logger.warning("里程碑叙事构建失败: %s", exc)

        # 按权重降序排序，取 top N
        items.sort(key=lambda x: x.weight, reverse=True)
        selected = items[:self._max_items]

        result = BondNarrativeResult(
            items=selected,
            bond_stage=stage,
            narrative_style=style,
            shared_memory_count=shared_count,
            milestone_count=milestone_count,
        )

        _logger.debug(
            "Bond叙事构建: user=%s stage=%s items=%d shared=%d milestones=%d",
            uid, stage, len(selected), shared_count, milestone_count,
        )

        return result

    @staticmethod
    def _memory_to_narrative(text: str, stage: str) -> str:
        """将共同记忆文本转化为叙事片段。

        对原始记忆文本进行简单裁剪（保留前 100 字），
        真正的叙事化由 LLM 在生成时完成（prompt 会指示风格）。
        """
        cleaned = str(text).strip()
        if len(cleaned) > 100:
            cleaned = cleaned[:100] + "…"
        return cleaned
