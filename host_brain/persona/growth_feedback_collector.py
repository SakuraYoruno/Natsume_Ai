"""GrowthFeedbackCollector：用户反馈驱动的人格成长修正——Phase IX-C。

收集用户的显式和隐式反馈信号，汇总后计算人格成长修正 delta。

反馈信号来源：
  显式（高信度）：
  - 用户文本含人格评价关键词（"你好可爱"/"你话太多了"）
  - WebUI 点赞/踩按钮
  隐式（低信度，需累积）：
  - 用户对特定风格回复的情感反应
  - 用户话题主动转向/回避

设计原则：
  - 显式信号权重 = confidence × 1.0
  - 隐式信号权重 = confidence × 0.3
  - 同方向信号累积 ≥ 3 条且加权均值 > 阈值才触发
  - 最终 delta 极小（0.01 级别），叠加到交互驱动 delta 之上
  - 全部参数可通过 Canon growth_params 覆盖
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.personality_traits import (
    TRAIT_NAMES,
    TraitsDelta,
)

_logger = logging.getLogger("Natsume.growth_feedback")


# ---------------------------------------------------------------------------
# 反馈信号来源标识
# ---------------------------------------------------------------------------

SOURCE_EXPLICIT_USER = "explicit_user"      # 用户文本中的显式评价
SOURCE_IMPLICIT_BEHAVIOR = "implicit_behavior"  # 行为推断
SOURCE_WEBUI_VOTE = "webui_vote"            # WebUI 点赞/踩

# 默认参数（可被 Canon growth_params 覆盖）
DEFAULT_EXPLICIT_WEIGHT = 1.0
DEFAULT_IMPLICIT_WEIGHT = 0.3
DEFAULT_MIN_SIGNALS = 3
DEFAULT_SIGNAL_THRESHOLD = 0.2
DEFAULT_FEEDBACK_DELTA = 0.01


# ---------------------------------------------------------------------------
# 用户文本 → 反馈信号 关键词规则
# ---------------------------------------------------------------------------

# (正则模式, 关联特质, 方向, 置信度)
FEEDBACK_KEYWORD_RULES: list[tuple[str, str, float, float]] = [
    # 正反馈
    (r"(你好可爱|好萌|可爱)", "playfulness", +1.0, 0.7),
    (r"(你好温柔|好温暖|暖心)", "warmth", +1.0, 0.8),
    (r"(好有趣|太有趣了|笑死|哈哈哈)", "humor", +1.0, 0.6),
    (r"(你好聪明|好厉害|你懂好多)", "curiosity", +1.0, 0.6),
    (r"(你好贴心|好体贴|懂我)", "sensitivity", +1.0, 0.7),
    (r"(好有安全感|靠谱|放心)", "resilience", +1.0, 0.6),
    # 负反馈
    (r"(你话好多|话太多|别说了|太啰嗦)", "assertiveness", -1.0, 0.8),
    (r"(太幼稚|别闹了|正经点)", "playfulness", -1.0, 0.7),
    (r"(太冷淡|冷冰冰|没感情)", "warmth", -1.0, 0.8),
    (r"(不好笑|一点都不好笑|尬)", "humor", -1.0, 0.7),
    (r"(太敏感了|别想太多|没那个意思)", "sensitivity", -1.0, 0.6),
    (r"(太强势|别命令我|谁要你管)", "assertiveness", -1.0, 0.7),
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class GrowthFeedback:
    """单条成长反馈信号。"""
    trait_name: str       # 关联的特质维度
    direction: float      # +1.0 正反馈 / -1.0 负反馈
    confidence: float     # 信号置信度 [0.0, 1.0]
    source: str           # explicit_user / implicit_behavior / webui_vote
    ts_ms: int
    text_hint: str = ""   # 触发文本摘要（审计用，最多 50 字）

    def as_dict(self) -> dict[str, Any]:
        return {
            "trait_name": self.trait_name,
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "ts_ms": self.ts_ms,
            "text_hint": self.text_hint[:50],
        }


@dataclass(frozen=True)
class FeedbackAdjustmentResult:
    """反馈汇总调整结果。"""
    delta: TraitsDelta
    total_signals: int
    trait_signal_counts: dict[str, int]
    trait_avg_signals: dict[str, float]
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "delta": self.delta.as_dict(),
            "total_signals": self.total_signals,
            "trait_signal_counts": dict(self.trait_signal_counts),
            "trait_avg_signals": {
                k: round(v, 4) for k, v in self.trait_avg_signals.items()
            },
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# GrowthFeedbackCollector — 核心收集器
# ---------------------------------------------------------------------------

class GrowthFeedbackCollector:
    """用户反馈驱动的人格成长修正收集器。

    用法：
        collector = GrowthFeedbackCollector()
        # 每轮对话后从用户文本提取反馈信号
        signals = collector.extract_from_text("你好可爱呀", ts_ms=now())
        for sig in signals:
            collector.add_feedback("user_1", sig)
        # 每周调度时计算调整 delta
        result = collector.compute_adjustment("user_1")
    """

    def __init__(
        self,
        *,
        explicit_weight: float = DEFAULT_EXPLICIT_WEIGHT,
        implicit_weight: float = DEFAULT_IMPLICIT_WEIGHT,
        min_signals: int = DEFAULT_MIN_SIGNALS,
        signal_threshold: float = DEFAULT_SIGNAL_THRESHOLD,
        feedback_delta: float = DEFAULT_FEEDBACK_DELTA,
        max_buffer_size: int = 200,
    ) -> None:
        self._explicit_weight = float(explicit_weight)
        self._implicit_weight = float(implicit_weight)
        self._min_signals = int(min_signals)
        self._signal_threshold = float(signal_threshold)
        self._feedback_delta = float(feedback_delta)
        self._max_buffer_size = int(max_buffer_size)
        # user_id → 反馈缓冲区
        self._buffers: dict[str, list[GrowthFeedback]] = {}

    @classmethod
    def from_canon_params(cls, params: dict[str, Any] | None = None) -> "GrowthFeedbackCollector":
        """从 Canon growth_params 创建收集器。"""
        p = dict(params or {})
        return cls(
            explicit_weight=float(p.get("feedback_explicit_weight", DEFAULT_EXPLICIT_WEIGHT)),
            implicit_weight=float(p.get("feedback_implicit_weight", DEFAULT_IMPLICIT_WEIGHT)),
            min_signals=int(p.get("feedback_min_signals", DEFAULT_MIN_SIGNALS)),
            signal_threshold=float(p.get("feedback_signal_threshold", DEFAULT_SIGNAL_THRESHOLD)),
            feedback_delta=float(p.get("base_delta", DEFAULT_FEEDBACK_DELTA)),
        )

    # ── 信号提取 ──────────────────────────────────────────────────────────

    @staticmethod
    def extract_from_text(
        user_text: str,
        *,
        ts_ms: int = 0,
    ) -> list[GrowthFeedback]:
        """从用户文本中提取显式反馈信号。

        Args:
            user_text: 用户输入文本
            ts_ms: 时间戳

        Returns:
            提取到的反馈信号列表（可能为空）
        """
        text = str(user_text or "").strip()
        if not text:
            return []

        ts = ts_ms or int(time.time() * 1000)
        signals: list[GrowthFeedback] = []

        for pattern, trait_name, direction, confidence in FEEDBACK_KEYWORD_RULES:
            if re.search(pattern, text):
                signals.append(GrowthFeedback(
                    trait_name=trait_name,
                    direction=direction,
                    confidence=confidence,
                    source=SOURCE_EXPLICIT_USER,
                    ts_ms=ts,
                    text_hint=text[:50],
                ))

        return signals

    # ── 信号收集 ──────────────────────────────────────────────────────────

    def add_feedback(self, user_id: str, feedback: GrowthFeedback) -> None:
        """追加反馈信号到用户缓冲区。"""
        uid = str(user_id).strip()
        buf = self._buffers.setdefault(uid, [])
        buf.append(feedback)
        # 超出上限时淘汰最旧的
        if len(buf) > self._max_buffer_size:
            self._buffers[uid] = buf[-self._max_buffer_size:]

    def add_webui_vote(
        self,
        user_id: str,
        *,
        trait_name: str,
        is_positive: bool,
        ts_ms: int = 0,
    ) -> None:
        """WebUI 点赞/踩 → 反馈信号。"""
        if trait_name not in TRAIT_NAMES:
            return
        self.add_feedback(user_id, GrowthFeedback(
            trait_name=trait_name,
            direction=1.0 if is_positive else -1.0,
            confidence=0.9,  # WebUI 明确操作，高置信度
            source=SOURCE_WEBUI_VOTE,
            ts_ms=ts_ms or int(time.time() * 1000),
            text_hint="webui_vote",
        ))

    def add_implicit_signal(
        self,
        user_id: str,
        *,
        trait_name: str,
        direction: float,
        confidence: float = 0.3,
        text_hint: str = "",
        ts_ms: int = 0,
    ) -> None:
        """隐式行为推断 → 反馈信号。"""
        if trait_name not in TRAIT_NAMES:
            return
        self.add_feedback(user_id, GrowthFeedback(
            trait_name=trait_name,
            direction=max(-1.0, min(1.0, direction)),
            confidence=max(0.0, min(1.0, confidence)),
            source=SOURCE_IMPLICIT_BEHAVIOR,
            ts_ms=ts_ms or int(time.time() * 1000),
            text_hint=str(text_hint)[:50],
        ))

    # ── 汇总计算 ──────────────────────────────────────────────────────────

    def compute_adjustment(
        self,
        user_id: str,
        *,
        ts_ms: int = 0,
    ) -> FeedbackAdjustmentResult:
        """汇总反馈信号，计算调整 delta。

        规则：
        - 只有同特质信号累积 ≥ min_signals 条才计算
        - 加权均值的绝对值 > signal_threshold 才触发
        - 最终 delta = 方向 × feedback_delta × 加权信号强度
        """
        uid = str(user_id).strip()
        feedbacks = self._buffers.get(uid, [])
        ts = ts_ms or int(time.time() * 1000)

        if not feedbacks:
            return FeedbackAdjustmentResult(
                delta=TraitsDelta(),
                total_signals=0,
                trait_signal_counts={n: 0 for n in TRAIT_NAMES},
                trait_avg_signals={n: 0.0 for n in TRAIT_NAMES},
                ts_ms=ts,
            )

        # 按特质汇总加权信号
        trait_weighted_sum: dict[str, float] = {n: 0.0 for n in TRAIT_NAMES}
        trait_weight_total: dict[str, float] = {n: 0.0 for n in TRAIT_NAMES}
        trait_counts: dict[str, int] = {n: 0 for n in TRAIT_NAMES}

        for fb in feedbacks:
            if fb.trait_name not in TRAIT_NAMES:
                continue

            # 权重 = 置信度 × 来源权重系数
            if fb.source in (SOURCE_EXPLICIT_USER, SOURCE_WEBUI_VOTE):
                source_weight = self._explicit_weight
            else:
                source_weight = self._implicit_weight

            w = fb.confidence * source_weight
            trait_weighted_sum[fb.trait_name] += fb.direction * w
            trait_weight_total[fb.trait_name] += w
            trait_counts[fb.trait_name] += 1

        # 计算加权平均 + 阈值过滤
        trait_avg: dict[str, float] = {}
        deltas: dict[str, float] = {}

        for name in TRAIT_NAMES:
            count = trait_counts[name]
            if count < self._min_signals:
                trait_avg[name] = 0.0
                deltas[name] = 0.0
                continue

            w_total = trait_weight_total[name]
            if w_total <= 0:
                trait_avg[name] = 0.0
                deltas[name] = 0.0
                continue

            avg_signal = trait_weighted_sum[name] / w_total
            trait_avg[name] = avg_signal

            if abs(avg_signal) < self._signal_threshold:
                deltas[name] = 0.0  # 信号太弱
                continue

            # delta = 方向 × base_delta × 信号强度（取 tanh 压缩防止极端值）
            import math
            compressed = math.tanh(avg_signal)
            deltas[name] = round(compressed * self._feedback_delta, 4)

        result_delta = TraitsDelta(**deltas)

        _logger.info(
            "反馈汇总: user=%s total_signals=%d delta=%s",
            uid, len(feedbacks), result_delta.as_dict(),
        )

        return FeedbackAdjustmentResult(
            delta=result_delta,
            total_signals=len(feedbacks),
            trait_signal_counts=trait_counts,
            trait_avg_signals=trait_avg,
            ts_ms=ts,
        )

    # ── 缓冲区管理 ────────────────────────────────────────────────────────

    def clear_buffer(self, user_id: str) -> int:
        """清空用户反馈缓冲区，返回清除的信号数。"""
        uid = str(user_id).strip()
        count = len(self._buffers.get(uid, []))
        self._buffers.pop(uid, None)
        return count

    def get_buffer_size(self, user_id: str) -> int:
        """获取用户反馈缓冲区大小。"""
        return len(self._buffers.get(str(user_id).strip(), []))

    def get_recent_feedbacks(
        self, user_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """获取用户最近的反馈信号（审计用）。"""
        buf = self._buffers.get(str(user_id).strip(), [])
        return [fb.as_dict() for fb in buf[-limit:]]
