"""情感时间线模块。

从 metabolism.py 拆分，包含 EmotionTimeline、EmotionTimelineEntry 和 compute_emotion_echo。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

def _clamp_float(value: float, low: float = -1.0, high: float = 1.0) -> float:
    try:
        return float(max(low, min(high, float(value))))
    except (TypeError, ValueError):
        return 0.0


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from host_brain.core.metabolism import EmotionVector

@dataclass
class EmotionTimelineEntry:
    """情感时间线条目（M29-E OPT-9）。"""
    ts_ms: int
    emotion_tag: str
    valence: float
    arousal: float
    source: str  # "user_input" / "visual" / "proactive" / "bond_event"


class EmotionTimeline:
    """情感时间线——记录情感状态变化历史（M29-E OPT-9）。

    支持：
    - 追加情感事件
    - 按时间窗口查询
    - 重建情感轨迹（用于 LLM 上下文注入）
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._entries: list[EmotionTimelineEntry] = []
        self._max_entries = max_entries

    def append(
        self,
        *,
        ts_ms: int,
        emotion_tag: str,
        valence: float = 0.0,
        arousal: float = 0.0,
        source: str = "user_input",
    ) -> None:
        """追加情感事件。"""
        entry = EmotionTimelineEntry(
            ts_ms=int(ts_ms),
            emotion_tag=str(emotion_tag).upper(),
            valence=_clamp_float(valence),
            arousal=_clamp_float(arousal),
            source=str(source),
        )
        self._entries.append(entry)
        # 超出最大条目数时，移除最旧的
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def query(
        self,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 10,
    ) -> list[EmotionTimelineEntry]:
        """按时间窗口查询情感时间线。"""
        result = self._entries
        if since_ms is not None:
            result = [e for e in result if e.ts_ms >= since_ms]
        if until_ms is not None:
            result = [e for e in result if e.ts_ms <= until_ms]
        return result[-limit:]

    def rebuild_summary(self, *, window_ms: int = 300_000) -> dict[str, Any]:
        """重建情感轨迹摘要（用于 LLM 上下文注入）（M29-E OPT-9）。

        Args:
            window_ms: 时间窗口（毫秒），默认 5 分钟

        Returns:
            情感轨迹摘要，包含主导情感、情感变化趋势等
        """
        from host_brain.core.ids import now_ms as _now_ms
        now_value = _now_ms()
        recent = self.query(since_ms=now_value - window_ms, limit=20)
        if not recent:
            return {"dominant_emotion": "NEUTRAL", "trend": "stable", "entries": []}
        # 统计主导情感
        tag_counts: dict[str, int] = {}
        for e in recent:
            tag_counts[e.emotion_tag] = tag_counts.get(e.emotion_tag, 0) + 1
        dominant = max(tag_counts, key=lambda k: tag_counts[k])
        # 情感趋势：比较前半段和后半段的 valence 均值
        mid = len(recent) // 2
        first_half = recent[:mid] if mid > 0 else recent
        second_half = recent[mid:] if mid > 0 else recent
        avg_first = sum(e.valence for e in first_half) / len(first_half)
        avg_second = sum(e.valence for e in second_half) / len(second_half)
        if avg_second - avg_first > 0.15:
            trend = "improving"
        elif avg_first - avg_second > 0.15:
            trend = "declining"
        else:
            trend = "stable"
        return {
            "dominant_emotion": dominant,
            "trend": trend,
            "entry_count": len(recent),
            "entries": [
                {"ts_ms": e.ts_ms, "emotion_tag": e.emotion_tag, "source": e.source}
                for e in recent[-5:]  # 最近 5 条
            ],
        }


# ---------------------------------------------------------------------------
# M29-H：情感回声机制（OPT-12）
# ---------------------------------------------------------------------------

def compute_emotion_echo(
    user_emotion_tag: str,
    *,
    current_vector: "EmotionVector",
    echo_weight: float = 0.4,
) -> dict[str, Any]:
    """计算情感回声——Natsume 对用户情感的共鸣响应（M29-H OPT-12）。

    情感回声：Natsume 的情感状态向用户情感靠近（共情），
    但保持一定的独立性（不完全镜像）。

    Args:
        user_emotion_tag: 用户当前情感标签
        current_vector: Natsume 当前情绪向量
        echo_weight: 回声权重（0.0~1.0），默认 0.4

    Returns:
        包含 echo_emotion_tag、echo_vector、response_hint 的字典
    """
    # 用户情感 → EmotionVector 映射
    _USER_EMOTION_MAP: dict[str, dict[str, float]] = {
        "HAPPY":      {"valence": 0.6, "arousal": 0.4},
        "EXCITED":    {"valence": 0.7, "arousal": 0.7},
        "SAD":        {"valence": -0.5, "arousal": -0.3},
        "ANGRY":      {"valence": -0.6, "arousal": 0.5},
        "ANXIOUS":    {"valence": -0.3, "arousal": 0.4},
        "FRUSTRATED": {"valence": -0.4, "arousal": 0.3},
        "CALM":       {"valence": 0.2, "arousal": -0.3},
        "NEUTRAL":    {"valence": 0.0, "arousal": 0.0},
        "THINKING":   {"valence": 0.0, "arousal": 0.1},
    }
    user_vad = _USER_EMOTION_MAP.get(str(user_emotion_tag or "").upper(), {"valence": 0.0, "arousal": 0.0})
    user_vec = EmotionVector(
        valence=user_vad["valence"],
        arousal=user_vad["arousal"],
        dominance=0.0,
        ts_ms=current_vector.ts_ms,
    )
    # 情感回声：当前向量向用户情感靠近
    echo_vec = current_vector.blend(user_vec, weight=echo_weight)
    echo_tag = echo_vec.to_emotion_tag()
    # 响应提示
    if echo_vec.valence > 0.3:
        response_hint = "以积极温暖的语气回应"
    elif echo_vec.valence < -0.3:
        response_hint = "以温柔关怀的语气回应，表达理解和支持"
    else:
        response_hint = "以平静自然的语气回应"
    return {
        "echo_emotion_tag": echo_tag,
        "echo_valence": round(echo_vec.valence, 4),
        "echo_arousal": round(echo_vec.arousal, 4),
        "response_hint": response_hint,
    }

