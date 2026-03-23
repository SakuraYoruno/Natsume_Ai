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

    @property
    def entries(self) -> list[EmotionTimelineEntry]:
        """只读访问所有条目（供外部读取）。"""
        return list(self._entries)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def build_daily_diary(self, *, window_ms: int = 86_400_000) -> dict[str, Any]:
        """方向 D：构建情感日记摘要——将时间窗口内的情感时间线生成可读的日记。

        默认时间窗口 24 小时。返回包含以下信息的字典：
        - diary_text: 第一人称的情感日记（自然语言）
        - dominant_emotion: 主导情感
        - emotion_arc: 情感弧线（开始→中间→结束）
        - avg_valence: 平均效价
        - total_entries: 条目数
        - time_range_ms: (start_ts, end_ts)

        如果窗口内无记录，返回空日记。
        """
        from host_brain.core.ids import now_ms as _now_ms
        now_value = _now_ms()
        entries = self.query(since_ms=now_value - window_ms, limit=200)
        if not entries:
            return {
                "diary_text": "",
                "dominant_emotion": "NEUTRAL",
                "emotion_arc": {"start": "NEUTRAL", "middle": "NEUTRAL", "end": "NEUTRAL"},
                "avg_valence": 0.0,
                "total_entries": 0,
                "time_range_ms": (0, 0),
            }

        # 统计主导情感
        tag_counts: dict[str, int] = {}
        for e in entries:
            tag_counts[e.emotion_tag] = tag_counts.get(e.emotion_tag, 0) + 1
        dominant = max(tag_counts, key=lambda k: tag_counts[k])

        # 情感弧线（三段式）
        n = len(entries)
        arc = {
            "start": entries[0].emotion_tag,
            "middle": entries[n // 2].emotion_tag if n > 1 else entries[0].emotion_tag,
            "end": entries[-1].emotion_tag,
        }

        # 平均效价
        avg_valence = sum(e.valence for e in entries) / n

        # 生成自然语言日记
        diary_text = _arc_to_diary_text(arc, dominant, avg_valence, n, tag_counts)

        return {
            "diary_text": diary_text,
            "dominant_emotion": dominant,
            "emotion_arc": arc,
            "avg_valence": round(avg_valence, 3),
            "total_entries": n,
            "time_range_ms": (entries[0].ts_ms, entries[-1].ts_ms),
        }

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
    bond_stage: str = "",
) -> dict[str, Any]:
    """计算情感回声——Natsume 对用户情感的共鸣响应（M29-H OPT-12 / Phase 5 升级）。

    Phase 5 升级：
    - 用户情感映射扩展到 16 种标签（含 LOVING/PROUD/GRATEFUL 等）
    - response_hint 根据关系深度（bond_stage）差异化
    - 新增 echo_intensity 字段（回声强度，0.0~1.0）

    Args:
        user_emotion_tag: 用户当前情感标签
        current_vector: Natsume 当前情绪向量
        echo_weight: 回声权重（0.0~1.0），默认 0.4
        bond_stage: 当前关系阶段（STRANGER~SOULMATE），为空时按 FRIEND 处理

    Returns:
        包含 echo_emotion_tag、echo_vector、response_hint、echo_intensity 的字典
    """
    from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415

    # Phase 5: 扩展到 16 种用户情感标签的 VAD 映射
    _USER_EMOTION_MAP: dict[str, dict[str, float]] = {
        "HAPPY":        {"valence":  0.6, "arousal":  0.4, "dominance":  0.2},
        "EXCITED":      {"valence":  0.7, "arousal":  0.7, "dominance":  0.3},
        "SAD":          {"valence": -0.5, "arousal": -0.3, "dominance": -0.3},
        "ANGRY":        {"valence": -0.6, "arousal":  0.5, "dominance":  0.5},
        "ANXIOUS":      {"valence": -0.3, "arousal":  0.4, "dominance": -0.3},
        "FRUSTRATED":   {"valence": -0.4, "arousal":  0.3, "dominance":  0.2},
        "CALM":         {"valence":  0.2, "arousal": -0.3, "dominance":  0.0},
        "NEUTRAL":      {"valence":  0.0, "arousal":  0.0, "dominance":  0.0},
        "THINKING":     {"valence":  0.0, "arousal":  0.1, "dominance":  0.3},
        "LOVING":       {"valence":  0.7, "arousal":  0.3, "dominance":  0.1},
        "EMBARRASSED":  {"valence": -0.2, "arousal":  0.4, "dominance": -0.4},
        "CONFUSED":     {"valence": -0.1, "arousal":  0.2, "dominance": -0.2},
        "BORED":        {"valence": -0.3, "arousal": -0.4, "dominance":  0.0},
        "PROUD":        {"valence":  0.6, "arousal":  0.4, "dominance":  0.5},
        "GRATEFUL":     {"valence":  0.6, "arousal":  0.2, "dominance": -0.2},
        "SURPRISED":    {"valence":  0.3, "arousal":  0.6, "dominance":  0.0},
    }
    user_vad = _USER_EMOTION_MAP.get(
        str(user_emotion_tag or "").upper(),
        {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
    )
    user_vec = EmotionVector(
        valence=user_vad["valence"],
        arousal=user_vad["arousal"],
        dominance=user_vad.get("dominance", 0.0),
        ts_ms=current_vector.ts_ms,
    )
    # 情感回声：当前向量向用户情感靠近
    echo_vec = current_vector.blend(user_vec, weight=echo_weight)
    echo_tag = echo_vec.to_emotion_tag()

    # Phase 5: 关系深度判定（shallow / medium / deep）
    _stage = str(bond_stage or "").strip().upper()
    _DEEP_STAGES = {"CLOSE_FRIEND", "CONFIDANT", "SOULMATE"}
    _MEDIUM_STAGES = {"FRIEND", "ACQUAINTANCE"}
    if _stage in _DEEP_STAGES:
        depth = "deep"
    elif _stage in _MEDIUM_STAGES:
        depth = "medium"
    elif _stage in {"STRANGER"}:
        depth = "shallow"
    else:
        depth = "medium"  # 默认按中等处理

    # Phase 5: 差异化响应提示（情绪方向 × 关系深度）
    _valence_dir = (
        "positive" if echo_vec.valence > 0.3
        else "negative" if echo_vec.valence < -0.3
        else "neutral"
    )
    _ECHO_HINTS: dict[tuple[str, str], str] = {
        ("positive", "shallow"):  "对方心情不错，给予适当的回应，但保持分寸",
        ("positive", "medium"):   "感受到对方的开心，也被感染了，可以自在地表达愉悦",
        ("positive", "deep"):     "被对方的快乐深深感染，可以更自在地分享喜悦，甚至撒个小娇",
        ("negative", "shallow"):  "对方似乎不太开心，表达基本的关心就好",
        ("negative", "medium"):   "能感受到对方的不开心，想好好安慰一下，语气温柔些",
        ("negative", "deep"):     "心疼对方，想好好陪着，可以说一些只有亲近的人才会说的话",
        ("neutral",  "shallow"):  "正常交流，保持友好的距离",
        ("neutral",  "medium"):   "自然平和地交流，像朋友一样轻松",
        ("neutral",  "deep"):     "虽然话题平淡，但这种日常感也很舒服，享受陪伴的感觉",
    }
    response_hint = _ECHO_HINTS.get(
        (_valence_dir, depth),
        "以自然真诚的语气回应",
    )

    # 回声强度（VAD 距离越大，共情越强）
    echo_intensity = min(1.0, (
        abs(echo_vec.valence - current_vector.valence) +
        abs(echo_vec.arousal - current_vector.arousal)
    ) / 2.0)

    return {
        "echo_emotion_tag": echo_tag,
        "echo_valence": round(echo_vec.valence, 4),
        "echo_arousal": round(echo_vec.arousal, 4),
        "response_hint": response_hint,
        "echo_intensity": round(echo_intensity, 4),
        "bond_depth": depth,
    }


# ---------------------------------------------------------------------------
# 方向 D：情感日记辅助函数
# ---------------------------------------------------------------------------

_EMOTION_ZH: dict[str, str] = {
    "HAPPY": "开心", "EXCITED": "兴奋", "SAD": "难过", "ANGRY": "生气",
    "CALM": "平静", "NEUTRAL": "平淡", "THINKING": "沉思", "LOVING": "充满爱意",
    "EMBARRASSED": "有些害羞", "CONFUSED": "困惑", "BORED": "无聊",
    "PROUD": "自豪", "GRATEFUL": "感激", "ANXIOUS": "焦虑",
    "FRUSTRATED": "沮丧", "SURPRISED": "惊喜",
}


def _arc_to_diary_text(
    arc: dict[str, str],
    dominant: str,
    avg_valence: float,
    entry_count: int,
    tag_counts: dict[str, int],
) -> str:
    """将情感弧线转为第一人称情感日记文本（方向 D）。"""
    start_zh = _EMOTION_ZH.get(arc.get("start", "NEUTRAL"), "平淡")
    mid_zh = _EMOTION_ZH.get(arc.get("middle", "NEUTRAL"), "平淡")
    end_zh = _EMOTION_ZH.get(arc.get("end", "NEUTRAL"), "平淡")
    dominant_zh = _EMOTION_ZH.get(dominant, "平淡")

    # 整体氛围
    if avg_valence > 0.3:
        mood_desc = "整体来说，今天的心情不错"
    elif avg_valence < -0.3:
        mood_desc = "今天的心情有些低落"
    else:
        mood_desc = "今天的心情比较平稳"

    # 情感丰富度
    unique_emotions = len(tag_counts)
    if unique_emotions >= 5:
        variety_desc = "情感变化很丰富，经历了好多种心情"
    elif unique_emotions >= 3:
        variety_desc = "心情有一些波动"
    else:
        variety_desc = "心情比较稳定"

    # 弧线描述
    if arc["start"] == arc["end"]:
        arc_desc = f"从头到尾都是{start_zh}的感觉"
    elif start_zh != end_zh:
        arc_desc = f"从{start_zh}慢慢变成了{end_zh}的状态"
    else:
        arc_desc = f"最终还是回到了{end_zh}的状态"

    parts = [mood_desc]
    if entry_count > 3:
        parts.append(variety_desc)
    parts.append(arc_desc)
    if dominant != arc.get("end"):
        parts.append(f"大部分时间都是{dominant_zh}的")

    return "。".join(parts) + "。"
