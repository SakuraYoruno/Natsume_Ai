"""预测性状态引擎（Phase XI 方案 3）。

职责：基于历史交互模式预测用户当前可能的状态，实现主动贴心。
设计：纯统计规则，从历史 emotion_tag + 时间戳提取周期性模式。
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.state.predictive")

# ---------------------------------------------------------------------------
# 预测置信度阈值
# ---------------------------------------------------------------------------
DEFAULT_MIN_SAMPLES = 5          # 某时段至少有 5 个样本才做预测
DEFAULT_MIN_CONFIDENCE = 0.4     # 主导情绪占比≥40% 才输出预测
DEFAULT_HISTORY_DAYS = 30        # 统计最近 30 天的历史


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class TimePeriod:
    """时间段定义。"""
    name: str           # e.g. "weekday_night"
    day_of_week: tuple[int, ...]  # 0=周一, 6=周日
    hour_start: int
    hour_end: int
    label: str          # 人类可读 e.g. "工作日深夜"


@dataclass
class PredictedState:
    """预测的用户状态。"""
    period_name: str
    period_label: str
    dominant_emotion: str
    confidence: float
    sample_count: int
    emotion_distribution: dict[str, float]
    proactive_hint: str  # 给 AI 的主动行为提示
    active: bool         # 当前是否处于该时段

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_name": self.period_name,
            "period_label": self.period_label,
            "dominant_emotion": self.dominant_emotion,
            "confidence": round(self.confidence, 3),
            "sample_count": self.sample_count,
            "top_emotions": dict(sorted(
                self.emotion_distribution.items(),
                key=lambda kv: -kv[1],
            )[:3]),
            "proactive_hint": self.proactive_hint,
            "active": self.active,
        }


@dataclass
class EmotionRecord:
    """历史情绪记录。"""
    emotion_tag: str
    ts_ms: int
    day_of_week: int  # 0=Mon, 6=Sun
    hour: int


# ---------------------------------------------------------------------------
# 预定义时段
# ---------------------------------------------------------------------------
DEFAULT_TIME_PERIODS: list[TimePeriod] = [
    TimePeriod("weekday_morning", (0, 1, 2, 3, 4), 6, 10, "工作日早晨"),
    TimePeriod("weekday_work", (0, 1, 2, 3, 4), 10, 18, "工作时间"),
    TimePeriod("weekday_evening", (0, 1, 2, 3, 4), 18, 22, "工作日晚间"),
    TimePeriod("weekday_night", (0, 1, 2, 3, 4), 22, 6, "工作日深夜"),
    TimePeriod("weekend_day", (5, 6), 8, 18, "周末白天"),
    TimePeriod("weekend_night", (5, 6), 18, 6, "周末夜晚"),
]

# 主导情绪 → 主动行为提示
EMOTION_PROACTIVE_HINTS: dict[str, str] = {
    "fatigue": "用户这个时段经常疲惫，语气要温柔，主动关心休息。",
    "sadness": "用户这个时段情绪容易低落，先接住情绪，减少追问。",
    "stress": "用户这个时段压力较大，不要增加负担，给简短温暖的回应。",
    "happiness": "用户这个时段通常心情好，可以更活泼俏皮。",
    "loneliness": "用户这个时段可能孤独，主动表达陪伴感。",
    "neutral": "用户这个时段状态平稳，正常互动即可。",
    "excitement": "用户这个时段通常兴奋，可以积极互动。",
}


# ---------------------------------------------------------------------------
# PredictiveStateEngine
# ---------------------------------------------------------------------------
class PredictiveStateEngine:
    """基于历史模式的用户状态预测。

    用法::

        engine = PredictiveStateEngine()

        # 喂入历史数据
        engine.record_emotion("fatigue", ts_ms=...)
        engine.record_emotion("stress", ts_ms=...)

        # 获取当前时段的预测
        prediction = engine.predict_current()
        if prediction and prediction.active:
            print(prediction.proactive_hint)
    """

    def __init__(
        self,
        *,
        time_periods: list[TimePeriod] | None = None,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        history_days: int = DEFAULT_HISTORY_DAYS,
    ) -> None:
        self._periods = list(time_periods or DEFAULT_TIME_PERIODS)
        self._min_samples = int(min_samples)
        self._min_confidence = float(min_confidence)
        self._history_ms = int(history_days) * 86400 * 1000

        # 历史记录
        self._records: list[EmotionRecord] = []

    def record_emotion(
        self,
        emotion_tag: str,
        *,
        ts_ms: int = 0,
    ) -> None:
        """记录一条情绪样本。"""
        ts = int(ts_ms or int(time.time() * 1000))
        # 转换为本地时间
        local_time = time.localtime(ts / 1000)
        record = EmotionRecord(
            emotion_tag=str(emotion_tag or "neutral").strip().lower(),
            ts_ms=ts,
            day_of_week=local_time.tm_wday,  # 0=Mon
            hour=local_time.tm_hour,
        )
        self._records.append(record)

        # 清理过期记录
        cutoff = ts - self._history_ms
        self._records = [r for r in self._records if r.ts_ms > cutoff]

    def predict_current(self) -> PredictedState | None:
        """预测当前时段的用户状态。"""
        now = time.localtime()
        current_dow = now.tm_wday
        current_hour = now.tm_hour

        # 找到当前匹配的时段
        current_period = self._match_period(current_dow, current_hour)
        if current_period is None:
            return None

        return self._predict_for_period(current_period, active=True)

    def predict_all_periods(self) -> list[PredictedState]:
        """预测所有时段的状态模式。"""
        results: list[PredictedState] = []
        now = time.localtime()
        current_period = self._match_period(now.tm_wday, now.tm_hour)

        for period in self._periods:
            pred = self._predict_for_period(
                period,
                active=(current_period is not None and period.name == current_period.name),
            )
            if pred is not None:
                results.append(pred)
        return results

    def _predict_for_period(
        self, period: TimePeriod, *, active: bool,
    ) -> PredictedState | None:
        """对单个时段做预测。"""
        # 筛选该时段的历史记录
        matching: list[str] = []
        for r in self._records:
            if r.day_of_week not in period.day_of_week:
                continue
            if period.hour_start <= period.hour_end:
                if not (period.hour_start <= r.hour < period.hour_end):
                    continue
            else:  # 跨午夜
                if not (r.hour >= period.hour_start or r.hour < period.hour_end):
                    continue
            matching.append(r.emotion_tag)

        if len(matching) < self._min_samples:
            return None

        # 统计分布
        counter = Counter(matching)
        total = len(matching)
        distribution = {k: v / total for k, v in counter.items()}

        # 主导情绪
        dominant, dominant_count = counter.most_common(1)[0]
        confidence = dominant_count / total

        if confidence < self._min_confidence:
            return None

        # 生成提示
        hint = EMOTION_PROACTIVE_HINTS.get(dominant, "")

        return PredictedState(
            period_name=period.name,
            period_label=period.label,
            dominant_emotion=dominant,
            confidence=confidence,
            sample_count=total,
            emotion_distribution=distribution,
            proactive_hint=hint,
            active=active,
        )

    def _match_period(self, dow: int, hour: int) -> TimePeriod | None:
        """匹配当前时间对应的时段。"""
        for period in self._periods:
            if dow not in period.day_of_week:
                continue
            if period.hour_start <= period.hour_end:
                if period.hour_start <= hour < period.hour_end:
                    return period
            else:  # 跨午夜
                if hour >= period.hour_start or hour < period.hour_end:
                    return period
        return None
