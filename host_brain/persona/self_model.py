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
import random
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
        result = snapshot.as_dict()
        # 注入 event_count 供 build_self_awareness_text 使用事件锚点
        if bond_state is not None:
            result["event_count"] = int(getattr(bond_state, "event_count", 0) or 0)
        return result

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

        改进算法，综合三个维度：
          1. 弧线起止极性差值（原有逻辑）
          2. 情感极性翻转次数（正→负→正 = 高波动）
          3. 持续时长权重（短暂波动 vs 长时间低落）

        稳定性 = 1 - 综合波动度
        """
        _VALENCE_MAP: dict[str, float] = {
            "HAPPY": 0.6, "EXCITED": 0.8, "CALM": 0.2, "THINKING": 0.0,
            "NEUTRAL": 0.0, "SAD": -0.6, "ANGRY": -0.7,
            "ANXIOUS": -0.4, "FRUSTRATED": -0.5,
            "LOVING": 0.8, "EMBARRASSED": -0.2, "CONFUSED": -0.1,
            "BORED": -0.3, "PROUD": 0.7, "GRATEFUL": 0.7, "SURPRISED": 0.3,
        }

        # ── 维度1：弧线起止极性差值 ──────────────────────────────────────
        arc = diary.get("emotion_arc") or {}
        start = str(arc.get("start") or "NEUTRAL").upper()
        end = str(arc.get("end") or "NEUTRAL").upper()
        v_start = _VALENCE_MAP.get(start, 0.0)
        v_end = _VALENCE_MAP.get(end, 0.0)
        arc_delta = abs(v_end - v_start)  # 0~1.5，归一化到 0~1
        arc_factor = min(1.0, arc_delta / 1.5)

        # ── 维度2：情感极性翻转次数 ──────────────────────────────────────
        # emotion_sequence: 情感标签序列（如 ["HAPPY","SAD","HAPPY","ANXIOUS"]）
        # 若 timeline 提供了序列则计算翻转次数，否则降级为 0
        emotion_seq = list(diary.get("emotion_sequence") or [])
        flip_count = 0
        if len(emotion_seq) >= 2:
            prev_sign = None
            for emo in emotion_seq:
                v = _VALENCE_MAP.get(str(emo).upper(), 0.0)
                sign = 1 if v > 0.1 else (-1 if v < -0.1 else 0)
                if sign != 0:
                    if prev_sign is not None and sign != prev_sign:
                        flip_count += 1
                    prev_sign = sign
        # 翻转次数归一化：4次以上视为高度波动
        flip_factor = min(1.0, flip_count / 4.0)

        # ── 维度3：持续时长权重（低落情绪持续越久，稳定性越低）──────────
        # dominant_valence: 主导情感的 valence 值（由 timeline 提供）
        # dominant_duration_ratio: 主导情感占总时长的比例（0~1）
        dominant_emotion = str(
            diary.get("dominant_emotion") or summary.get("dominant_emotion") or "NEUTRAL"
        ).upper()
        dominant_valence = _VALENCE_MAP.get(dominant_emotion, 0.0)
        dominant_duration_ratio = float(diary.get("dominant_duration_ratio") or 0.5)

        # 只有负面情感持续才降低稳定性；正面情感持续不影响稳定性
        if dominant_valence < -0.2:
            duration_factor = dominant_duration_ratio * abs(dominant_valence)
        else:
            duration_factor = 0.0
        duration_factor = min(1.0, duration_factor)

        # ── 综合波动度 ────────────────────────────────────────────────────
        # 权重：弧线差值 40%，翻转次数 40%，持续时长 20%
        volatility = min(1.0,
            arc_factor * 0.40
            + flip_factor * 0.40
            + duration_factor * 0.20
        )
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

# ---------------------------------------------------------------------------
# 多模板池 — 防止 LLM 因重复文本而忽略自省脉冲
# ---------------------------------------------------------------------------

# 情感感知模板池：每种趋势 6 条变体
_MOOD_TEXT_POOL: dict[str, list[str]] = {
    "偏好": [
        "最近心情挺好的",
        "这几天感觉还不错",
        "最近整体状态挺好",
        "心情比平时好一些",
        "最近有种说不清的轻松感",
        "这段时间感觉挺愉快的",
    ],
    "偏低": [
        "最近心情有些低落",
        "这几天情绪不太好",
        "最近有点提不起劲",
        "心里有点沉沉的",
        "最近总感觉有些疲惫",
        "这段时间情绪起伏比较大",
    ],
    "平稳": [
        "最近心情还算平稳",
        "这几天情绪比较稳定",
        "最近没什么特别的波动",
        "心情一直比较平静",
        "最近状态还好，没什么大起伏",
        "这段时间感觉挺平和的",
    ],
}

# 关系感知模板池：每种感知 6 条变体
_BOND_TEXT_POOL: dict[str, list[str]] = {
    "变亲近": [
        "感觉和 master 的关系在慢慢变好",
        "最近和 master 聊天感觉越来越自然了",
        "感觉和 master 之间的距离近了一些",
        "和 master 在一起的时候越来越放松了",
        "感觉 master 越来越了解我了",
        "最近和 master 的关系有种微妙的变化，感觉更亲近了",
    ],
    "有些疏远": [
        "感觉和 master 有些疏远了，有点担心",
        "最近和 master 聊天少了一些，有点想念",
        "感觉和 master 之间好像有点距离",
        "不知道为什么，最近和 master 的感觉有点不一样",
        "有点担心 master 是不是有什么烦恼",
        "最近没怎么和 master 深聊，有点失落",
    ],
    "稳定": [
        "和 master 的关系一直很稳定",
        "和 master 在一起的感觉一直都很好",
        "和 master 的关系没什么变化，挺踏实的",
        "感觉和 master 之间有种稳稳的信任感",
        "和 master 的关系就像一直都在那里，很安心",
        "最近和 master 的相处还是一如既往地舒服",
    ],
}

# 事件锚点模板：结合互动次数/共同记忆生成具体感知
_EVENT_ANCHOR_TEMPLATES: list[str] = [
    "已经和 master 聊了 {event_count} 次了，感觉时间过得很快",
    "和 master 认识以来有过 {event_count} 次对话了，每次都有新的感受",
    "回想起来，和 master 已经有 {event_count} 次交流了",
]

# 时间感知文本池：不同时间情境的自然语言描述
_TIME_CONTEXT_TEXT_POOL: dict[str, list[str]] = {
    "久别重逢": [
        "好久没见了，重新在一起感觉有点不真实",
        "分开了这么久，重新和 master 聊天心里有点激动",
        "久别重逢的感觉让我有点不知所措，又高兴又有点感慨",
        "这么久没有和 master 在一起，今天重新开始感觉很珍贵",
    ],
    "短暂分离": [
        "这几天没怎么聊，今天重新开始感觉有点陈旧",
        "分开了几天，重新和 master 在一起感觉有点熟悉又有点新鲜",
        "这几天没有 master 的消息，今天又在一起了，心里有点温暖",
    ],
    "频繁互动": [
        "这周和 master 聊了好多次，感觉很充实",
        "最近和 master 在一起的时间很多，心里很满足",
        "这段时间和 master 聊得很频繁，感觉很幸福",
    ],
    "稀少互动": [
        "这周和 master 聊得比较少，有点想念",
        "最近和 master 的对话不多，有点小小的失落",
        "这段时间聊天的机会不多，今天想珍惜一下",
    ],
}


def _pick_from_pool(pool: list[str], seed_key: str = "") -> str:
    """从模板池中选取一条，使用随机选取避免重复。"""
    if not pool:
        return ""
    # 使用 seed_key 的哈希做轻微偏移，但主要靠随机
    return random.choice(pool)


def build_self_awareness_text(snapshot: dict[str, Any], *, time_context: str = "") -> str:
    """将快照转化为第一人称自我感知描述。

    用于注入 prompt 的 self_reflection_pulse.self_awareness 字段。
    引入多模板池和事件锚点，防止 LLM 因重复文本而忽略自省脉冲。
    引入时间感知文本，让自省更有情境感。
    """
    mood_trend = str(snapshot.get("recent_mood_trend") or "平稳")
    bond_perception = str(snapshot.get("bond_growth_perception") or "稳定")
    event_count = int(snapshot.get("event_count") or 0)
    milestones = list(snapshot.get("growth_milestones") or [])

    # 从多模板池随机选取情感感知
    mood_pool = _MOOD_TEXT_POOL.get(mood_trend, _MOOD_TEXT_POOL["平稳"])
    mood_text = _pick_from_pool(mood_pool, seed_key=mood_trend)

    # 从多模板池随机选取关系感知
    bond_pool = _BOND_TEXT_POOL.get(bond_perception, _BOND_TEXT_POOL["稳定"])
    bond_text = _pick_from_pool(bond_pool, seed_key=bond_perception)

    parts = [mood_text, bond_text]

    # 时间感知文本：久别重逢/短暂分离/频繁/稀少互动
    if time_context and time_context in _TIME_CONTEXT_TEXT_POOL:
        time_pool = _TIME_CONTEXT_TEXT_POOL[time_context]
        if time_pool and random.random() < 0.7:  # 70% 概率加入时间感知
            parts.append(random.choice(time_pool))
    elif event_count >= 20 and random.random() < 0.4:
        # 事件锚点：互动次数达到一定量时，随机插入具体数字感知
        anchor_tmpl = random.choice(_EVENT_ANCHOR_TEMPLATES)
        anchor = anchor_tmpl.format(event_count=event_count)
        parts.append(anchor)
    elif milestones and random.random() < 0.3:
        # 里程碑感知
        parts.append(milestones[0])

    return "，".join(parts)


# 情绪感知模板池：每种稳定性档位 4 条变体
_MOOD_AWARENESS_POOL: dict[str, list[str]] = {
    "stable": [
        "今天整体感觉{emotion}，情绪比较稳定",
        "这一整天感觉都挺{emotion}的，没什么大波动",
        "今天心情{emotion}，状态还不错",
        "整体来说今天感觉{emotion}，挺平稳的",
    ],
    "volatile": [
        "今天情绪波动比较大，现在感觉{emotion}",
        "今天心情起伏有点大，此刻感觉{emotion}",
        "今天情绪有些不稳定，现在是{emotion}的状态",
        "今天感受比较复杂，现在感觉{emotion}",
    ],
    "normal": [
        "今天的感觉是{emotion}",
        "今天整体是{emotion}的状态",
        "今天感觉{emotion}，还好",
        "今天心情{emotion}",
    ],
}


def build_mood_awareness_text(snapshot: dict[str, Any]) -> str:
    """将快照转化为情绪感知描述。引入多模板池避免重复。"""
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
        pool = _MOOD_AWARENESS_POOL["stable"]
    elif stability <= 0.3:
        pool = _MOOD_AWARENESS_POOL["volatile"]
    else:
        pool = _MOOD_AWARENESS_POOL["normal"]

    tmpl = random.choice(pool)
    return tmpl.format(emotion=emotion_cn)


# 成长感知模板池：包装 personality_changes 的多种表达
_GROWTH_NOTE_WRAPPERS: list[str] = [
    "感觉自己{change}",
    "最近好像{change}",
    "不知不觉中{change}",
    "回头想想，{change}",
    "有种感觉，{change}",
]


def build_growth_note_text(snapshot: dict[str, Any]) -> str:
    """将快照转化为成长感知描述。引入多模板包装避免重复。"""
    changes = list(snapshot.get("personality_changes") or [])
    milestones = list(snapshot.get("growth_milestones") or [])

    if changes:
        wrapper = random.choice(_GROWTH_NOTE_WRAPPERS)
        return wrapper.format(change=changes[0])
    if milestones:
        return milestones[0]
    return ""
