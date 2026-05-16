"""情感时间线模块。

从 metabolism.py 拆分，包含 EmotionTimeline、EmotionTimelineEntry 和 compute_emotion_echo。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

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

    def build_cross_day_comparison(
        self,
        *,
        today_window_ms: int = 86_400_000,
        yesterday_window_ms: int = 86_400_000,
    ) -> dict[str, Any]:
        """跨天情感对比——将今天与昨天的情感状态进行对比，生成自然语言描述。

        返回字典包含：
        - comparison_text: 第一人称跨天对比描述（自然语言）
        - today_dominant: 今天主导情感
        - yesterday_dominant: 昨天主导情感
        - today_avg_valence: 今天平均效价
        - yesterday_avg_valence: 昨天平均效价
        - valence_delta: 效价变化量（今天 - 昨天）
        - mood_shift: 情感变化方向（"better" / "worse" / "stable"）
        """
        from host_brain.core.ids import now_ms as _now_ms
        now_value = _now_ms()

        # 今天的条目（最近 24 小时）
        today_entries = self.query(since_ms=now_value - today_window_ms, limit=200)
        # 昨天的条目（24~48 小时前）
        yesterday_entries = self.query(
            since_ms=now_value - today_window_ms - yesterday_window_ms,
            until_ms=now_value - today_window_ms,
            limit=200,
        )

        # 如果昨天没有数据，返回空对比
        if not yesterday_entries:
            return {
                "comparison_text": "",
                "today_dominant": "NEUTRAL",
                "yesterday_dominant": "NEUTRAL",
                "today_avg_valence": 0.0,
                "yesterday_avg_valence": 0.0,
                "valence_delta": 0.0,
                "mood_shift": "stable",
            }

        def _dominant(entries: list) -> str:
            counts: dict[str, int] = {}
            for e in entries:
                counts[e.emotion_tag] = counts.get(e.emotion_tag, 0) + 1
            return max(counts, key=lambda k: counts[k]) if counts else "NEUTRAL"

        def _avg_valence(entries: list) -> float:
            if not entries:
                return 0.0
            return sum(e.valence for e in entries) / len(entries)

        today_dominant = _dominant(today_entries) if today_entries else "NEUTRAL"
        yesterday_dominant = _dominant(yesterday_entries)
        today_avg = _avg_valence(today_entries)
        yesterday_avg = _avg_valence(yesterday_entries)
        delta = today_avg - yesterday_avg

        if delta > 0.15:
            mood_shift = "better"
        elif delta < -0.15:
            mood_shift = "worse"
        else:
            mood_shift = "stable"

        comparison_text = _build_cross_day_text(
            today_dominant=today_dominant,
            yesterday_dominant=yesterday_dominant,
            today_avg=today_avg,
            yesterday_avg=yesterday_avg,
            mood_shift=mood_shift,
            has_today=bool(today_entries),
        )

        return {
            "comparison_text": comparison_text,
            "today_dominant": today_dominant,
            "yesterday_dominant": yesterday_dominant,
            "today_avg_valence": round(today_avg, 3),
            "yesterday_avg_valence": round(yesterday_avg, 3),
            "valence_delta": round(delta, 3),
            "mood_shift": mood_shift,
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
    personality_traits: Any = None,
) -> dict[str, Any]:
    """计算情感回声——Natsume 对用户情感的共鸣响应（M29-H OPT-12 / Phase 5 升级 / Phase II 人格耦合）。

    Phase 5 升级：
    - 用户情感映射扩展到 16 种标签（含 LOVING/PROUD/GRATEFUL 等）
    - response_hint 根据关系深度（bond_stage）差异化
    - 新增 echo_intensity 字段（回声强度，0.0~1.0）

    Phase II（灵魂系统）升级：
    - 新增 personality_traits 参数，人格特质影响 echo_weight 调制

    Args:
        user_emotion_tag: 用户当前情感标签
        current_vector: Natsume 当前情绪向量
        echo_weight: 回声权重（0.0~1.0），默认 0.4
        bond_stage: 当前关系阶段（FRIEND~SOULMATE），为空时按 FRIEND 处理
        personality_traits: PersonalityTraits 人格特质（可选，Phase II 新增）

    Returns:
        包含 echo_emotion_tag、echo_vector、response_hint、echo_intensity 的字典
    """
    from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415

    # Phase II：人格特质调制 echo_weight
    _effective_echo_weight = echo_weight
    _persona_modifiers: dict[str, Any] = {}
    if personality_traits is not None:
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            reactivity_payload = dict(
                get_limbic_agent().resolve_emotion_reactivity_profile(
                    personality_traits=personality_traits,
                    user_emotion_tag=str(user_emotion_tag or ""),
                    bond_stage=str(bond_stage or ""),
                    echo_weight=float(echo_weight),
                )
                or {}
            )
            _effective_echo_weight = float(reactivity_payload.get("effective_echo_weight", echo_weight))
            _persona_modifiers = dict(reactivity_payload.get("persona_modifiers") or {})
        except (ImportError, Exception):
            pass  # 降级：不使用人格调制

    # Phase 5: 扩展到 16 种用户情感标签的 VAD 映射（使用共享常量）
    _tag = str(user_emotion_tag or "").upper()
    _shared_vad = _SHARED_VAD_MAP.get(_tag, (0.0, 0.0, 0.0))
    user_vad = {
        "valence": _shared_vad[0],
        "arousal": _shared_vad[1],
        "dominance": _shared_vad[2],
    }
    user_vec = EmotionVector(
        valence=user_vad["valence"],
        arousal=user_vad["arousal"],
        dominance=user_vad.get("dominance", 0.0),
        ts_ms=current_vector.ts_ms,
    )
    # 情感回声：当前向量向用户情感靠近（使用人格调制后的权重）
    echo_vec = current_vector.blend(user_vec, weight=_effective_echo_weight)
    echo_tag = echo_vec.to_emotion_tag()

    # Phase 5: 关系深度判定（shallow / medium / deep）
    _stage = str(bond_stage or "").strip().upper()
    _DEEP_STAGES = {"CLOSE_FRIEND", "CONFIDANT", "SOULMATE"}
    _MEDIUM_STAGES = {"FRIEND"}
    if _stage in _DEEP_STAGES:
        depth = "deep"
    elif _stage in _MEDIUM_STAGES:
        depth = "medium"
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

    result = {
        "echo_emotion_tag": echo_tag,
        "echo_valence": round(echo_vec.valence, 4),
        "echo_arousal": round(echo_vec.arousal, 4),
        "response_hint": response_hint,
        "echo_intensity": round(echo_intensity, 4),
        "bond_depth": depth,
    }
    # Phase II：附加人格调制信息
    if _persona_modifiers:
        result["persona_modifiers"] = _persona_modifiers
        result["effective_echo_weight"] = round(_effective_echo_weight, 4)
    return result


# ---------------------------------------------------------------------------
# 方向 D：情感日记辅助函数——使用共享常量模块
# ---------------------------------------------------------------------------

from host_brain.core.emotion_constants import (  # noqa: E402
    EMOTION_TAG_ZH as _EMOTION_ZH,
    EMOTION_TAG_TO_VAD as _SHARED_VAD_MAP,
)


def _build_cross_day_text(
    *,
    today_dominant: str,
    yesterday_dominant: str,
    today_avg: float,
    yesterday_avg: float,
    mood_shift: str,
    has_today: bool,
) -> str:
    """生成跨天情感对比的第一人称自然语言描述。"""
    today_zh = _EMOTION_ZH.get(today_dominant, "平静")
    yesterday_zh = _EMOTION_ZH.get(yesterday_dominant, "平静")

    if not has_today:
        return f"昨天整体是{yesterday_zh}的状态，今天还没有太多感受。"

    if mood_shift == "better":
        if today_dominant == yesterday_dominant:
            return f"今天和昨天一样都是{today_zh}的感觉，但整体比昨天好了一些。"
        return f"昨天感觉{yesterday_zh}，今天变成了{today_zh}，心情比昨天好多了。"
    elif mood_shift == "worse":
        if today_dominant == yesterday_dominant:
            return f"今天和昨天都是{today_zh}的状态，但感觉比昨天低落了一些。"
        return f"昨天还是{yesterday_zh}的，今天变成了{today_zh}，心情比昨天差了一点。"
    else:
        if today_dominant == yesterday_dominant:
            return f"今天和昨天差不多，都是{today_zh}的状态，挺稳定的。"
        return f"昨天是{yesterday_zh}的感觉，今天是{today_zh}，整体变化不大。"


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
