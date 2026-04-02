"""主动记忆整合触发器（Phase X 方案 5）。

职责：根据记忆写入量和话题重复频次动态触发蒸馏，
而不是只依赖每天凌晨 3 点的定时任务。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.memory.consolidation_trigger")

# ---------------------------------------------------------------------------
# 触发条件阈值
# ---------------------------------------------------------------------------
DEFAULT_VOLUME_THRESHOLD = 50       # 24h 内累计 >50 条新记忆 → 紧急蒸馏
DEFAULT_TOPIC_REPEAT_THRESHOLD = 5  # 同一话题出现 >5 次 → 专项蒸馏
DEFAULT_COOLDOWN_HOURS = 4          # 两次触发之间最少间隔 4 小时
DEFAULT_MIN_MEMORIES_FOR_TOPIC = 3  # 话题蒸馏最少需要 3 条相关记忆
DEFAULT_MAX_TOPIC_KEYS = 200        # _topic_counter 最大条目数，防止无限增长

# ---------------------------------------------------------------------------
# 触发类型
# ---------------------------------------------------------------------------
TRIGGER_NONE = "none"
TRIGGER_VOLUME = "volume"           # 量级触发
TRIGGER_TOPIC = "topic"             # 话题触发
TRIGGER_SCHEDULED = "scheduled"     # 定时触发


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class ConsolidationDecision:
    """蒸馏触发决策。"""

    should_trigger: bool
    trigger_type: str  # none / volume / topic / scheduled
    reason: str
    topic_keyword: str | None = None  # topic 触发时的关键话题
    memory_count_24h: int = 0
    topic_counts: dict[str, int] = field(default_factory=dict)
    cooldown_remaining_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_trigger": self.should_trigger,
            "trigger_type": self.trigger_type,
            "reason": self.reason,
            "topic_keyword": self.topic_keyword,
            "memory_count_24h": self.memory_count_24h,
            "top_topics": dict(sorted(
                self.topic_counts.items(),
                key=lambda kv: -kv[1],
            )[:5]),
            "cooldown_remaining_s": round(self.cooldown_remaining_s, 1),
        }


# ---------------------------------------------------------------------------
# ConsolidationTrigger
# ---------------------------------------------------------------------------
class ConsolidationTrigger:
    """主动记忆整合触发器——动态判断是否需要提前蒸馏。

    维护两个计数器：
    1. 24h 记忆写入量（volume_counter）
    2. 话题频次表（topic_counter）

    触发后自动重置计数器 + 进入冷却期。
    """

    def __init__(
        self,
        *,
        volume_threshold: int = DEFAULT_VOLUME_THRESHOLD,
        topic_repeat_threshold: int = DEFAULT_TOPIC_REPEAT_THRESHOLD,
        cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
        max_topic_keys: int = DEFAULT_MAX_TOPIC_KEYS,
    ) -> None:
        self._volume_threshold = int(volume_threshold)
        self._topic_threshold = int(topic_repeat_threshold)
        self._cooldown_s = float(cooldown_hours) * 3600
        self._max_topic_keys = int(max_topic_keys)

        # 运行时状态
        self._memory_timestamps: list[float] = []  # 记忆写入时间戳
        self._topic_counter: dict[str, int] = {}    # 话题→频次
        self._last_trigger_ts: float = 0.0          # 上次触发时间

    def on_memory_written(
        self,
        *,
        tags: list[str] | None = None,
        emotion_tag: str = "",
        text_preview: str = "",
    ) -> None:
        """每次记忆成功写入后调用，更新计数器。"""
        now = time.time()
        self._memory_timestamps.append(now)

        # 清理 24h 之前的时间戳
        cutoff = now - 86400
        self._memory_timestamps = [ts for ts in self._memory_timestamps if ts > cutoff]

        # 更新话题计数（仅依赖语义 tags，不使用原始文本前缀）
        for tag in list(tags or []):
            tag_clean = str(tag).strip().lower()
            if tag_clean and tag_clean != "neutral":
                self._topic_counter[tag_clean] = self._topic_counter.get(tag_clean, 0) + 1

        # 容量保护：超出上限时淘汰频次最低的条目
        if len(self._topic_counter) > self._max_topic_keys:
            # 保留频次最高的 max_topic_keys 条
            sorted_items = sorted(self._topic_counter.items(), key=lambda kv: -kv[1])
            self._topic_counter = dict(sorted_items[: self._max_topic_keys])
            _logger.debug(
                "ConsolidationTrigger: topic_counter 已裁剪至 %d 条",
                self._max_topic_keys,
            )

    def check(self) -> ConsolidationDecision:
        """检查是否需要触发蒸馏。"""
        now = time.time()

        # 冷却期检查
        elapsed_since_last = now - self._last_trigger_ts
        if elapsed_since_last < self._cooldown_s:
            remaining = self._cooldown_s - elapsed_since_last
            return ConsolidationDecision(
                should_trigger=False,
                trigger_type=TRIGGER_NONE,
                reason=f"冷却中(剩余{remaining/3600:.1f}h)",
                memory_count_24h=len(self._memory_timestamps),
                topic_counts=dict(self._topic_counter),
                cooldown_remaining_s=remaining,
            )

        # 量级触发
        count_24h = len(self._memory_timestamps)
        if count_24h >= self._volume_threshold:
            self._last_trigger_ts = now
            self._reset_counters()
            _logger.info("ConsolidationTrigger: VOLUME 触发 (count=%d)", count_24h)
            return ConsolidationDecision(
                should_trigger=True,
                trigger_type=TRIGGER_VOLUME,
                reason=f"24h记忆量={count_24h}≥{self._volume_threshold}",
                memory_count_24h=count_24h,
                topic_counts=dict(self._topic_counter),
            )

        # 话题触发
        for topic, freq in sorted(self._topic_counter.items(), key=lambda kv: -kv[1]):
            if freq >= self._topic_threshold:
                self._last_trigger_ts = now
                _logger.info(
                    "ConsolidationTrigger: TOPIC 触发 (topic='%s' freq=%d)",
                    topic[:30], freq,
                )
                # 只重置该话题的计数，不重置全部
                self._topic_counter[topic] = 0
                return ConsolidationDecision(
                    should_trigger=True,
                    trigger_type=TRIGGER_TOPIC,
                    reason=f"话题'{topic[:20]}'重复{freq}次≥{self._topic_threshold}",
                    topic_keyword=topic,
                    memory_count_24h=count_24h,
                    topic_counts=dict(self._topic_counter),
                )

        return ConsolidationDecision(
            should_trigger=False,
            trigger_type=TRIGGER_NONE,
            reason=f"未达阈值(volume={count_24h}/{self._volume_threshold})",
            memory_count_24h=count_24h,
            topic_counts=dict(self._topic_counter),
        )

    def _reset_counters(self) -> None:
        """量级触发后重置所有计数器。"""
        self._memory_timestamps.clear()
        self._topic_counter.clear()
