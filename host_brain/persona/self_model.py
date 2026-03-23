"""SelfModel：自我意识模型——Phase III 核心。

职责：
  1. 从 EmotionTimeline 提取情感趋势、稳定性、主导情感
  2. 从 BondStageEngine 提取关系成长感知
  3. 从 PersonaEngine 变化日志提取成长感知
  4. 综合生成 SelfModelSnapshot（Natsume 对自身状态的第一人称感知）

设计原则：
  - 纯函数式：输入各种数据源 → 输出 SelfModelSnapshot
  - 不持有状态，每次调用即时计算
  - 所有自然语言描述均为第一人称（"我感觉..."，"最近..."）
"""

from __future__ import annotations

import logging
import math
from typing import Any

_logger = logging.getLogger("Natsume.self_model")


# ---------------------------------------------------------------------------
# SelfModel — 自我意识模型核心
# ---------------------------------------------------------------------------

class SelfModel:
    """自我意识模型——综合多个数据源生成自省快照。

    用法：
        model = SelfModel()
        snapshot = model.build_snapshot(
            timeline_summary=timeline.rebuild_summary(),
            timeline_diary=timeline.build_daily_diary(),
            bond_state=bond_engine.get_state(user_id),
            persona_changes=engine.get_change_log(user_id),
        )
    """

    def build_snapshot(
        self,
        *,
        timeline_summary: dict[str, Any] | None = None,
        timeline_diary: dict[str, Any] | None = None,
        bond_state: Any | None = None,
        bond_summary: dict[str, Any] | None = None,
        persona_changes: list[Any] | None = None,
        last_deep_talk_ts_ms: int = 0,
        current_ts_ms: int = 0,
    ) -> dict[str, Any]:
        """构建完整的自我意识快照。

        Args:
            timeline_summary: EmotionTimeline.rebuild_summary() 的输出
            timeline_diary: EmotionTimeline.build_daily_diary() 的输出
            bond_state: BondScoreState（可为 None）
            bond_summary: BondScoreState.to_summary() 的输出
            persona_changes: TraitChangeRecord 列表
            last_deep_talk_ts_ms: 上次深度对话时间戳
            current_ts_ms: 当前时间戳

        Returns:
            SelfModelSnapshot.as_dict() 格式的字典
        """
        from host_brain.persona.persona_engine import SelfModelSnapshot

        ts_summary = dict(timeline_summary or {})
        ts_diary = dict(timeline_diary or {})

        # ── 情感层面 ─────────────────────────────────────────────────────
        recent_mood_trend = self._analyze_mood_trend(ts_summary, ts_diary)
        emotional_stability = self._compute_emotional_stability(ts_summary, ts_diary)
        dominant_recent_emotion = str(
            ts_summary.get("dominant_emotion")
            or ts_diary.get("dominant_emotion")
            or "NEUTRAL"
        ).upper()

        # ── 关系层面 ─────────────────────────────────────────────────────
        bond_growth_perception = self._analyze_bond_growth(bond_state, bond_summary)
        shared_memory_count = 0  # Phase V 将填充
        last_deep_talk_days_ago = self._compute_days_ago(
            last_deep_talk_ts_ms, current_ts_ms,
        )

        # ── 成长层面 ─────────────────────────────────────────────────────
        personality_changes = self._extract_personality_changes(persona_changes)
        growth_milestones = self._extract_growth_milestones(
            bond_state, persona_changes,
        )

        snapshot = SelfModelSnapshot(
            recent_mood_trend=recent_mood_trend,
            emotional_stability=emotional_stability,
            dominant_recent_emotion=dominant_recent_emotion,
            bond_growth_perception=bond_growth_perception,
            shared_memory_count=shared_memory_count,
            last_deep_talk_days_ago=last_deep_talk_days_ago,
            personality_changes=personality_changes,
            growth_milestones=growth_milestones,
        )
        return snapshot.as_dict()

    # ── 情感分析 ─────────────────────────────────────────────────────────

    def _analyze_mood_trend(
        self,
        summary: dict[str, Any],
        diary: dict[str, Any],
    ) -> str:
        """分析近期情感趋势。"""
        trend = str(summary.get("trend") or "stable").strip().lower()
        avg_valence = float(diary.get("avg_valence") or 0.0)

        if trend == "improving" or avg_valence > 0.3:
            return "偏好"
        elif trend == "declining" or avg_valence < -0.3:
            return "偏低"
        return "平稳"

    def _compute_emotional_stability(
        self,
        summary: dict[str, Any],
        diary: dict[str, Any],
    ) -> float:
        """计算情感稳定性（0~1）。

        稳定性 = 1 - 波动度。波动度由情感弧线的变化幅度推算。
        """
        arc = diary.get("emotion_arc") or {}
        start = str(arc.get("start") or "NEUTRAL")
        end = str(arc.get("end") or "NEUTRAL")

        # 简化的波动度计算：弧线起止不同 → 波动较大
        _VALENCE_MAP: dict[str, float] = {
            "HAPPY": 0.6, "EXCITED": 0.8, "CALM": 0.2, "THINKING": 0.0,
            "NEUTRAL": 0.0, "SAD": -0.6, "ANGRY": -0.7,
            "ANXIOUS": -0.4, "FRUSTRATED": -0.5,
            "LOVING": 0.8, "EMBARRASSED": -0.2, "CONFUSED": -0.1,
            "BORED": -0.3, "PROUD": 0.7, "GRATEFUL": 0.7, "SURPRISED": 0.3,
        }
        v_start = _VALENCE_MAP.get(start, 0.0)
        v_end = _VALENCE_MAP.get(end, 0.0)
        delta = abs(v_end - v_start)

        # 条目数越多，可能波动越多
        entry_count = int(diary.get("total_entries") or 0)
        count_factor = min(1.0, entry_count / 20.0)  # 20 条以上视为波动充分

        volatility = min(1.0, delta * 0.5 + count_factor * 0.2)
        stability = max(0.0, min(1.0, 1.0 - volatility))
        return round(stability, 3)

    # ── 关系分析 ─────────────────────────────────────────────────────────

    def _analyze_bond_growth(
        self,
        bond_state: Any,
        bond_summary: dict[str, Any] | None,
    ) -> str:
        """分析关系成长感知。"""
        summary = dict(bond_summary or {})
        trend = str(summary.get("trend") or "稳定").strip()

        # 中文趋势映射
        _POSITIVE_TRENDS = {"上升", "快速上升", "缓慢上升", "RISING"}
        _NEGATIVE_TRENDS = {"下降", "快速下降", "缓慢下降", "DECLINING"}

        if trend in _POSITIVE_TRENDS:
            return "变亲近"
        elif trend in _NEGATIVE_TRENDS:
            return "有些疏远"
        return "稳定"

    def _compute_days_ago(self, ts_ms: int, current_ts_ms: int) -> int:
        """计算距今天数。"""
        if ts_ms <= 0 or current_ts_ms <= 0:
            return -1
        delta_ms = max(0, current_ts_ms - ts_ms)
        days = delta_ms / (24 * 3600 * 1000)
        return max(0, int(days))

    # ── 成长分析 ─────────────────────────────────────────────────────────

    def _extract_personality_changes(
        self,
        changes: list[Any] | None,
    ) -> list[str]:
        """从特质变化记录中提取自然语言描述。"""
        if not changes:
            return []

        _CN_NAMES = {
            "warmth": "温柔", "curiosity": "好奇心",
            "humor": "幽默感", "sensitivity": "敏感",
            "assertiveness": "自信", "playfulness": "俏皮",
            "resilience": "韧性",
        }
        _TRAIT_NAMES = list(_CN_NAMES.keys())

        results: list[str] = []
        for record in changes[-3:]:  # 最近 3 条变化
            delta = getattr(record, "delta", {}) if hasattr(record, "delta") else {}
            if isinstance(delta, dict):
                for name in _TRAIT_NAMES:
                    d = float(delta.get(name, 0.0))
                    if abs(d) >= 0.01:
                        cn = _CN_NAMES.get(name, name)
                        if d > 0:
                            results.append(f"变得更{cn}了")
                        else:
                            results.append(f"{cn}稍有收敛")
        return results[:5]  # 最多 5 条

    def _extract_growth_milestones(
        self,
        bond_state: Any,
        changes: list[Any] | None,
    ) -> list[str]:
        """提取成长里程碑。"""
        milestones: list[str] = []

        # 从 Bond 状态提取
        if bond_state is not None:
            stage = str(getattr(bond_state, "current_stage", "") or "").strip()
            event_count = int(getattr(bond_state, "event_count", 0) or 0)

            _STAGE_CN = {
                "ACQUAINTANCE": "相识", "FRIEND": "朋友",
                "CLOSE_FRIEND": "好友", "CONFIDANT": "挚友",
                "SOULMATE": "灵魂伴侣",
            }
            if stage in _STAGE_CN:
                milestones.append(f"和 master 已经成为{_STAGE_CN[stage]}了")

            # 互动次数里程碑
            if event_count >= 100:
                milestones.append(f"已经有 {event_count} 次互动了")
            elif event_count >= 50:
                milestones.append("互动超过 50 次了")

        # 从特质变化提取
        if changes and len(changes) >= 3:
            milestones.append("人格在慢慢成长中")

        return milestones[:3]  # 最多 3 条


# ---------------------------------------------------------------------------
# 自省内容生成 — 将 SelfModelSnapshot 转化为第一人称自然语言
# ---------------------------------------------------------------------------

def build_self_awareness_text(snapshot: dict[str, Any]) -> str:
    """将快照转化为第一人称自我感知描述。

    用于注入 prompt 的 self_reflection_pulse.self_awareness 字段。
    """
    parts: list[str] = []

    mood_trend = str(snapshot.get("recent_mood_trend") or "平稳")
    dominant = str(snapshot.get("dominant_recent_emotion") or "NEUTRAL")
    bond_perception = str(snapshot.get("bond_growth_perception") or "稳定")

    # 情感感知
    _MOOD_TEXTS: dict[str, str] = {
        "偏好": "最近心情挺好的",
        "偏低": "最近心情有些低落",
        "平稳": "最近心情还算平稳",
    }
    parts.append(_MOOD_TEXTS.get(mood_trend, "最近心情还算平稳"))

    # 关系感知
    _BOND_TEXTS: dict[str, str] = {
        "变亲近": "感觉和 master 的关系在慢慢变好",
        "有些疏远": "感觉和 master 有些疏远了，有点担心",
        "稳定": "和 master 的关系一直很稳定",
    }
    parts.append(_BOND_TEXTS.get(bond_perception, "和 master 的关系一直很稳定"))

    return "，".join(parts)


def build_mood_awareness_text(snapshot: dict[str, Any]) -> str:
    """将快照转化为情绪感知描述。"""
    dominant = str(snapshot.get("dominant_recent_emotion") or "NEUTRAL").upper()
    stability = float(snapshot.get("emotional_stability") or 0.5)

    _EMOTION_CN: dict[str, str] = {
        "HAPPY": "开心", "EXCITED": "兴奋", "CALM": "平静",
        "SAD": "有点难过", "ANXIOUS": "有些焦虑", "ANGRY": "有点生气",
        "LOVING": "充满爱意", "PROUD": "有点自豪", "GRATEFUL": "感到感激",
        "BORED": "有些无聊", "CONFUSED": "有点困惑", "NEUTRAL": "平静",
        "THINKING": "在思考", "FRUSTRATED": "有些沮丧",
        "EMBARRASSED": "有点害羞", "SURPRISED": "有些惊讶",
    }
    emotion_cn = _EMOTION_CN.get(dominant, "平静")

    if stability >= 0.7:
        return f"今天整体感觉{emotion_cn}，情绪比较稳定"
    elif stability <= 0.3:
        return f"今天情绪波动比较大，现在感觉{emotion_cn}"
    return f"今天的感觉是{emotion_cn}"


def build_growth_note_text(snapshot: dict[str, Any]) -> str:
    """将快照转化为成长感知描述。"""
    changes = list(snapshot.get("personality_changes") or [])
    milestones = list(snapshot.get("growth_milestones") or [])

    if changes:
        return f"感觉自己{changes[0]}"
    if milestones:
        return milestones[0]
    return ""
