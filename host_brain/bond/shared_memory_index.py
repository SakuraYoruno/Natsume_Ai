"""SharedMemoryIndex：共同记忆索引——Phase V。

职责：
  1. 从 EpisodicStore 中筛选满足"共同回忆"条件的记忆
  2. 按情感浓度、时间、深度互动等维度排序
  3. 输出可注入 prompt 的自然语言叙事片段（限制 ≤ 3 条）

筛选条件（满足任一即标记为共同回忆）：
  - emotion_valence > 0.5（高情感浓度）
  - emotion_tag 属于深度情感类（LOVING, GRATEFUL, NOSTALGIC 等）
  - 文本包含回忆类关键词（"还记得"、"那时候"等）

设计原则：
  - 纯查询逻辑，不写入任何数据
  - 返回结构化结果，由 BondNarrativeLayer 负责转化为自然语言
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.shared_memory_index")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 高情感浓度阈值
HIGH_VALENCE_THRESHOLD = 0.5

# 深度情感标签（被视为共同回忆的情感标签）
DEEP_EMOTION_TAGS: frozenset[str] = frozenset({
    "LOVING", "GRATEFUL", "NOSTALGIC", "MOVED",
    "JOYFUL", "HAPPY", "PROUD", "RELIEVED",
    "TOUCHED", "WARM",
})

# 回忆类关键词
RECALL_KEYWORDS: tuple[str, ...] = (
    "还记得", "那时候", "以前", "那次", "当时",
    "第一次", "想起", "回忆", "曾经", "记得",
)

# 默认最大返回条数
DEFAULT_MAX_SHARED = 3


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SharedMemoryEntry:
    """单条共同记忆条目。"""
    memory_id: str
    text: str
    ts_ms: int
    emotion_tag: str
    emotion_valence: float
    match_reason: str  # 匹配原因（"high_valence" / "deep_emotion" / "recall_keyword"）
    relevance_score: float  # 综合相关度分数

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "ts_ms": self.ts_ms,
            "emotion_tag": self.emotion_tag,
            "emotion_valence": round(self.emotion_valence, 4),
            "match_reason": self.match_reason,
            "relevance_score": round(self.relevance_score, 4),
        }


@dataclass
class SharedMemoryResult:
    """共同记忆查询结果。"""
    entries: list[SharedMemoryEntry] = field(default_factory=list)
    total_candidates: int = 0  # 满足条件的总候选数

    @property
    def count(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.as_dict() for e in self.entries],
            "count": self.count,
            "total_candidates": self.total_candidates,
        }


# ---------------------------------------------------------------------------
# SharedMemoryIndex
# ---------------------------------------------------------------------------

class SharedMemoryIndex:
    """共同记忆索引。

    用法：
        index = SharedMemoryIndex(store=episodic_store)
        result = index.query(max_entries=3)
    """

    def __init__(
        self,
        *,
        store: Any = None,
        valence_threshold: float = HIGH_VALENCE_THRESHOLD,
        deep_tags: frozenset[str] | None = None,
        recall_keywords: tuple[str, ...] | None = None,
    ) -> None:
        self._store = store
        self._valence_threshold = float(valence_threshold)
        self._deep_tags = deep_tags or DEEP_EMOTION_TAGS
        self._recall_keywords = recall_keywords or RECALL_KEYWORDS

    def query(
        self,
        *,
        max_entries: int = DEFAULT_MAX_SHARED,
        min_ts_ms: int = 0,
    ) -> SharedMemoryResult:
        """查询共同记忆。

        Args:
            max_entries: 最大返回条数
            min_ts_ms: 最早时间戳过滤

        Returns:
            SharedMemoryResult
        """
        if self._store is None:
            return SharedMemoryResult()

        records = self._get_all_records()
        candidates: list[SharedMemoryEntry] = []

        for rec in records:
            # 跳过已删除/已合并的记录
            if getattr(rec, "deleted_at", None) is not None:
                continue
            if getattr(rec, "merged_into", None) is not None:
                continue

            ts = int(getattr(rec, "ts_ms", 0))
            if min_ts_ms > 0 and ts < min_ts_ms:
                continue

            text = str(getattr(rec, "text", ""))
            tag = str(getattr(rec, "emotion_tag", "NEUTRAL")).upper()
            valence = float(getattr(rec, "emotion_valence", 0.0))
            memory_id = str(getattr(rec, "memory_id", ""))

            # 检查匹配条件
            match_reasons: list[str] = []
            score = 0.0

            if valence > self._valence_threshold:
                match_reasons.append("high_valence")
                score += valence  # 情感浓度越高，分数越高

            if tag in self._deep_tags:
                match_reasons.append("deep_emotion")
                score += 0.3  # 深度情感标签加分

            if any(kw in text for kw in self._recall_keywords):
                match_reasons.append("recall_keyword")
                score += 0.2  # 回忆关键词加分

            if not match_reasons:
                continue

            # 时间衰减加分：越近的记忆越相关（简化实现）
            if ts > 0:
                score += 0.1  # 基础分

            candidates.append(SharedMemoryEntry(
                memory_id=memory_id,
                text=text,
                ts_ms=ts,
                emotion_tag=tag,
                emotion_valence=valence,
                match_reason="+".join(match_reasons),
                relevance_score=round(score, 4),
            ))

        total = len(candidates)
        # 按相关度降序 + 时间降序排序
        candidates.sort(key=lambda e: (e.relevance_score, e.ts_ms), reverse=True)
        selected = candidates[:max_entries]

        _logger.debug(
            "共同记忆查询: candidates=%d selected=%d", total, len(selected),
        )

        return SharedMemoryResult(entries=selected, total_candidates=total)

    def _get_all_records(self) -> list[Any]:
        """获取所有情节记忆记录（从 EpisodicStore._records 读取）。"""
        records_dict = getattr(self._store, "_records", None)
        if records_dict is None:
            return []
        return list(records_dict.values())
