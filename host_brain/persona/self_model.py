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
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.persona.offline_continuity import resolve_absence_band as resolve_offline_absence_band
from host_brain.persona.runtime_labels import (
    BOND_GROWTH_CLOSER,
    BOND_GROWTH_DISTANT,
    BOND_GROWTH_STABLE,
    MOOD_TREND_NEGATIVE,
    MOOD_TREND_POSITIVE,
    MOOD_TREND_STABLE,
    TIME_CONTEXT_FREQUENT_INTERACTION,
    TIME_CONTEXT_REUNION,
    TIME_CONTEXT_SHORT_ABSENCE,
    TIME_CONTEXT_SPARSE_INTERACTION,
)

_logger = logging.getLogger("Natsume.self_model")


def _normalize_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _resolve_prompt_template_json_localized(
    template_id: str,
    *,
    render_target_lang: str = "zh",
) -> Any:
    lang_key = _normalize_template_lang(render_target_lang)
    for candidate_id in (f"{template_id}.{lang_key}", template_id):
        try:
            raw = resolve_prompt_template_json_merged(candidate_id)
        except Exception:
            continue
        if isinstance(raw, Mapping):
            localized = raw.get(lang_key)
            if localized is not None:
                return localized
        if raw is not None:
            return raw
    return {}


def _load_prompt_templates(*, render_target_lang: str = "zh") -> dict[str, Any]:
    raw = _resolve_prompt_template_json_localized(
        "persona.self_narrative_prompts",
        render_target_lang=render_target_lang,
    )
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _load_narrative_text_pools(*, render_target_lang: str = "zh") -> dict[str, Any]:
    raw = _resolve_prompt_template_json_localized(
        "self_model.narrative_text_pools",
        render_target_lang=render_target_lang,
    )
    return dict(raw) if isinstance(raw, Mapping) else {}


def _coerce_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        values = [str(item) for item in raw if str(item or "").strip()]
        if values:
            return values
    return list(fallback)


def _coerce_text_pool_mapping(raw: Any, fallback: dict[str, list[str]]) -> dict[str, list[str]]:
    if isinstance(raw, Mapping):
        result: dict[str, list[str]] = {}
        for key, value in raw.items():
            if not isinstance(value, list):
                continue
            items = [str(item) for item in value if str(item or "").strip()]
            if items:
                result[str(key)] = items
        if result:
            return result
    return {str(key): list(value) for key, value in fallback.items()}


def _coerce_text_mapping(raw: Any, fallback: dict[str, str]) -> dict[str, str]:
    if isinstance(raw, Mapping):
        result = {
            str(key): str(value).strip()
            for key, value in raw.items()
            if str(value).strip()
        }
        if result:
            return result
    return {str(key): str(value) for key, value in fallback.items()}


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
            return MOOD_TREND_POSITIVE
        elif trend == "declining" or avg_valence < -0.3:
            return MOOD_TREND_NEGATIVE
        return MOOD_TREND_STABLE

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
            return BOND_GROWTH_CLOSER
        elif trend in _NEGATIVE_TRENDS:
            return BOND_GROWTH_DISTANT
        return BOND_GROWTH_STABLE

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

        narrative_text_pools = _load_narrative_text_pools()
        trait_cn_mapping = _coerce_text_mapping(
            narrative_text_pools.get("trait_cn"),
            _TRAIT_CN,
        )
        change_templates = _coerce_text_mapping(
            narrative_text_pools.get("personality_change_templates"),
            {},
        )
        trait_names = list(trait_cn_mapping.keys())

        results: list[str] = []
        for record in changes[-3:]:  # 最近 3 条变化
            delta = getattr(record, "delta", {}) if hasattr(record, "delta") else {}
            if isinstance(delta, dict):
                for name in trait_names:
                    d = float(delta.get(name, 0.0))
                    if abs(d) >= 0.01:
                        cn = trait_cn_mapping.get(name, name)
                        if d > 0:
                            results.append(
                                _mapping_text(
                                    change_templates,
                                    "positive",
                                    default="",
                                    trait_cn=cn,
                                )
                            )
                        else:
                            results.append(
                                _mapping_text(
                                    change_templates,
                                    "negative",
                                    default="",
                                    trait_cn=cn,
                                )
                            )
        return [text for text in results[:5] if text]  # 最多 5 条

    def _extract_growth_milestones(
        self,
        bond_state: Any,
        changes: list[Any] | None,
    ) -> list[str]:
        """提取成长里程碑。"""
        narrative_text_pools = _load_narrative_text_pools()
        raw_milestone_templates = narrative_text_pools.get("growth_milestone_templates")
        milestone_templates = dict(_GROWTH_MILESTONE_TEMPLATES)
        if isinstance(raw_milestone_templates, Mapping):
            milestone_templates.update(raw_milestone_templates)
        stage_labels = _coerce_text_mapping(
            milestone_templates.get("stage_labels"),
            dict(_GROWTH_MILESTONE_TEMPLATES.get("stage_labels") or {}),
        )
        milestones: list[str] = []

        # 从 Bond 状态提取
        if bond_state is not None:
            stage = str(getattr(bond_state, "current_stage", "") or "").strip()
            event_count = int(getattr(bond_state, "event_count", 0) or 0)

            if stage in stage_labels:
                text = _mapping_text(
                    milestone_templates,
                    "relationship_stage",
                    default="",
                    stage_cn=stage_labels[stage],
                )
                if text:
                    milestones.append(text)

            # 互动次数里程碑
            if event_count >= 100:
                text = _mapping_text(
                    milestone_templates,
                    "event_count_high",
                    default="",
                    event_count=event_count,
                )
                if text:
                    milestones.append(text)
            elif event_count >= 50:
                text = _mapping_text(
                    milestone_templates,
                    "event_count_mid",
                    default="",
                    threshold=50,
                )
                if text:
                    milestones.append(text)

        # 从特质变化提取
        if changes and len(changes) >= 3:
            text = str(milestone_templates.get("personality_growth") or "").strip()
            if text:
                milestones.append(text)

        return milestones[:3]  # 最多 3 条


# ---------------------------------------------------------------------------
# 自省内容生成 — 将 SelfModelSnapshot 转化为第一人称自然语言
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 多模板池 — 防止 LLM 因重复文本而忽略自省脉冲
# ---------------------------------------------------------------------------

# 情感感知模板池：每种趋势 6 条变体
_MOOD_TEXT_POOL: dict[str, list[str]] = {}

# 关系感知模板池：每种感知 6 条变体
_BOND_TEXT_POOL: dict[str, list[str]] = {}

# 事件锚点模板：结合互动次数/共同记忆生成具体感知
_EVENT_ANCHOR_TEMPLATES: list[str] = []

# 时间感知文本池：不同时间情境的自然语言描述
_TIME_CONTEXT_TEXT_POOL: dict[str, list[str]] = {}


# ---------------------------------------------------------------------------
# 矛盾感知模板池 — 当多个维度出现矛盾时生成深度自省内容
# ---------------------------------------------------------------------------

# 矛盾类型 → 第一人称矛盾感知文本
_CONTRADICTION_TEXT_POOL: dict[str, list[str]] = {}


def detect_contradictions(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """检测快照中多个维度之间的矛盾状态.

    返回矛盾列表，每项包含:
      - type: 矛盾类型标识
      - text: 第一人称矛盾感知文本

    矛盾检测规则:
      1. 心情偏低 + 关系变亲近 → mood_low_bond_up
      2. 心情偏好 + 关系疏远 → mood_up_bond_down
      3. 有成长变化 + 心情偏低 → growing_but_sad
      4. 情绪不稳定(stability<0.35) + 关系稳定 → volatile_but_bond_stable
      5. 情绪稳定(stability>0.7) + 关系疏远 → stable_but_bond_down
      6. 有成长变化 + 关系疏远 → growing_but_bond_down
    """
    mood_trend = str(snapshot.get("recent_mood_trend") or MOOD_TREND_STABLE)
    bond_perception = str(snapshot.get("bond_growth_perception") or BOND_GROWTH_STABLE)
    stability = float(snapshot.get("emotional_stability") or 0.5)
    changes = list(snapshot.get("personality_changes") or [])
    has_growth = len(changes) > 0
    contradiction_pools = _coerce_text_pool_mapping(
        _load_narrative_text_pools().get("contradiction_text_pool"),
        _CONTRADICTION_TEXT_POOL,
    )

    contradictions: list[dict[str, str]] = []

    # 规则1: 心情低落 × 关系变好
    if mood_trend == MOOD_TREND_NEGATIVE and bond_perception == BOND_GROWTH_CLOSER:
        pool = contradiction_pools.get("mood_low_bond_up") or []
        contradictions.append({"type": "mood_low_bond_up", "text": random.choice(pool)})

    # 规则2: 心情好 × 关系疏远
    if mood_trend == MOOD_TREND_POSITIVE and bond_perception == BOND_GROWTH_DISTANT:
        pool = contradiction_pools.get("mood_up_bond_down") or []
        contradictions.append({"type": "mood_up_bond_down", "text": random.choice(pool)})

    # 规则3: 在成长 × 心情低落
    if has_growth and mood_trend == MOOD_TREND_NEGATIVE:
        pool = contradiction_pools.get("growing_but_sad") or []
        contradictions.append({"type": "growing_but_sad", "text": random.choice(pool)})

    # 规则4: 情绪不稳定 × 关系稳定
    if stability < 0.35 and bond_perception == BOND_GROWTH_STABLE:
        pool = contradiction_pools.get(
            "volatile_but_bond_stable",
            [],
        )
        contradictions.append({"type": "volatile_but_bond_stable", "text": random.choice(pool)})

    # 规则5: 情绪稳定 × 关系疏远
    if stability > 0.7 and bond_perception == BOND_GROWTH_DISTANT:
        pool = contradiction_pools.get("stable_but_bond_down") or []
        contradictions.append({"type": "stable_but_bond_down", "text": random.choice(pool)})

    # 规则6: 在成长 × 关系疏远
    if has_growth and bond_perception == BOND_GROWTH_DISTANT:
        pool = contradiction_pools.get("growing_but_bond_down") or []
        contradictions.append({"type": "growing_but_bond_down", "text": random.choice(pool)})

    # 最多返回 2 条矛盾（避免自省过于复杂）
    return contradictions[:2]


def build_contradiction_awareness_text(contradictions: list[dict[str, str]]) -> str:
    """将矛盾列表转化为自然语言矛盾感知文本.

    Args:
        contradictions: detect_contradictions() 的返回值

    Returns:
        矛盾感知文本（可能为空字符串）
    """
    if not contradictions:
        return ""
    texts = [c["text"] for c in contradictions if c.get("text")]
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]
    return texts[0] + "。" + texts[1]


def _pick_from_pool(pool: list[str], seed_key: str = "") -> str:
    """从模板池中选取一条，使用随机选取避免重复。"""
    if not pool:
        return ""
    # 使用 seed_key 的哈希做轻微偏移，但主要靠随机
    return random.choice(pool)


def build_self_awareness_text(
    snapshot: dict[str, Any],
    *,
    time_context: str = "",
    llm_fn: Any | None = None,
    strict_llm: bool = False,
    style_evolution: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> str:
    """将快照转化为第一人称自我感知描述。

    用于注入 prompt 的 self_reflection_pulse.self_awareness 字段。
    技术债务 10.1 修复：优先使用 llm_fn 动态生成真实自省文本，
    降级到多模板池（向后兼容）。
    v1.11 新增：style_evolution 参数，LLM prompt 中注入风格演变趋势。
    """
    mood_trend = str(snapshot.get("recent_mood_trend") or MOOD_TREND_STABLE)
    bond_perception = str(snapshot.get("bond_growth_perception") or BOND_GROWTH_STABLE)
    event_count = int(snapshot.get("event_count") or 0)
    milestones = list(snapshot.get("growth_milestones") or [])
    narrative_text_pools = _load_narrative_text_pools(render_target_lang=render_target_lang)
    mood_text_pool = _coerce_text_pool_mapping(
        narrative_text_pools.get("mood_text_pool"),
        _MOOD_TEXT_POOL,
    )
    bond_text_pool = _coerce_text_pool_mapping(
        narrative_text_pools.get("bond_text_pool"),
        _BOND_TEXT_POOL,
    )
    time_context_text_pool = _coerce_text_pool_mapping(
        narrative_text_pools.get("time_context_text_pool"),
        _TIME_CONTEXT_TEXT_POOL,
    )
    event_anchor_templates = _coerce_text_list(
        narrative_text_pools.get("event_anchor_templates"),
        _EVENT_ANCHOR_TEMPLATES,
    )
    emotion_cn_mapping = _coerce_text_mapping(
        narrative_text_pools.get("emotion_cn"),
        {},
    )
    trait_cn_mapping = _coerce_text_mapping(
        narrative_text_pools.get("trait_cn"),
        _TRAIT_CN,
    )

    # ── LLM 动态生成路径（技术债务 10.1 修复）──────────────────────────────────────────────────────
    if llm_fn is not None:
        try:
            dominant = str(snapshot.get("dominant_recent_emotion") or "NEUTRAL").upper()
            stability = float(snapshot.get("emotional_stability") or 0.5)
            changes = list(snapshot.get("personality_changes") or [])
            emotion_cn = str(emotion_cn_mapping.get(dominant) or dominant)
            stability_desc = "情绪比较稳定" if stability >= 0.7 else ("情绪有些波动" if stability <= 0.3 else "情绪还算平稳")
            time_hint = f"，时间情境：{time_context}" if time_context else ""
            change_hint = f"，最近的变化：{'、'.join(changes[:2])}" if changes else ""
            milestone_hint = f"，重要时刻：{milestones[0]}" if milestones else ""
            # v1.11：注入风格演变趋势
            style_hint = ""
            if style_evolution and isinstance(style_evolution, dict):
                trend = style_evolution.get("trend") or {}
                history_count = int(style_evolution.get("history_count") or 0)
                if history_count >= 20:
                    rising = [trait_cn_mapping.get(k, k) for k, v in trend.items() if v == "rising"]
                    falling = [trait_cn_mapping.get(k, k) for k, v in trend.items() if v == "falling"]
                    if rising:
                        style_hint += f"，风格趋势：{'、'.join(rising)}在上升"
                    if falling:
                        style_hint += f"，{'、'.join(falling)}在下降"
            prompt = _mapping_text(
                _load_prompt_templates(render_target_lang=render_target_lang),
                "self_awareness_prompt",
                mood_trend=mood_trend,
                emotion_cn=emotion_cn,
                stability_desc=stability_desc,
                bond_perception=bond_perception,
                time_hint=time_hint,
                change_hint=change_hint,
                milestone_hint=milestone_hint,
                style_hint=style_hint,
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 5:
                return result.strip()
        except Exception as _llm_exc:
            _logger.debug("LLM 自省生成失败: %s", _llm_exc)
            if strict_llm:
                return ""

    if strict_llm:
        return ""

    # ── 降级：模板池路径（向后兼容）────────────────────────────────────
    # 从多模板池随机选取情感感知
    mood_pool = mood_text_pool.get(mood_trend) or mood_text_pool.get(MOOD_TREND_STABLE) or []
    mood_text = _pick_from_pool(mood_pool, seed_key=mood_trend)

    # 从多模板池随机选取关系感知
    bond_pool = bond_text_pool.get(
        bond_perception,
        bond_text_pool.get(BOND_GROWTH_STABLE) or [],
    )
    bond_text = _pick_from_pool(bond_pool, seed_key=bond_perception)

    parts = [mood_text, bond_text]

    # 时间感知文本：久别重逢/短暂分离/频繁/稀少互动
    if time_context and time_context in time_context_text_pool:
        time_pool = time_context_text_pool[time_context]
        if time_pool and random.random() < 0.7:  # 70% 概率加入时间感知
            parts.append(random.choice(time_pool))
    elif event_count >= 20 and random.random() < 0.4:
        # 事件锚点：互动次数达到一定量时，随机插入具体数字感知
        anchor_tmpl = random.choice(event_anchor_templates)
        anchor = anchor_tmpl.format(event_count=event_count)
        parts.append(anchor)
    elif milestones and random.random() < 0.3:
        # 里程碑感知
        parts.append(milestones[0])

    return "，".join(parts)


# 情绪感知模板池：每种稳定性档位 4 条变体
_MOOD_AWARENESS_POOL: dict[str, list[str]] = {}


def build_mood_awareness_text(
    snapshot: dict[str, Any],
    *,
    llm_fn: Any | None = None,
    strict_llm: bool = False,
    render_target_lang: str = "zh",
) -> str:
    """将快照转化为情绪感知描述。

    技术债务 10.1 修复：优先使用 llm_fn 动态生成，降级到模板池。
    """
    dominant = str(snapshot.get("dominant_recent_emotion") or "NEUTRAL").upper()
    stability = float(snapshot.get("emotional_stability") or 0.5)
    mood_awareness_pool = _coerce_text_pool_mapping(
        _load_narrative_text_pools(render_target_lang=render_target_lang).get("mood_awareness_pool"),
        _MOOD_AWARENESS_POOL,
    )

    emotion_cn_mapping = _coerce_text_mapping(
        _load_narrative_text_pools(render_target_lang=render_target_lang).get("emotion_cn"),
        {},
    )
    emotion_cn = str(emotion_cn_mapping.get(dominant) or dominant)

    # ── LLM 动态生成路径 ────────────────────────────────────────────────
    if llm_fn is not None:
        try:
            stability_desc = "情绪很稳定" if stability >= 0.7 else ("情绪起伏较大" if stability <= 0.3 else "情绪还算平稳")
            prompt = _mapping_text(
                _load_prompt_templates(render_target_lang=render_target_lang),
                "mood_awareness_prompt",
                emotion_cn=emotion_cn,
                stability_desc=stability_desc,
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 5:
                return result.strip()
        except Exception as _llm_exc:
            _logger.debug("LLM 情绪感知生成失败: %s", _llm_exc)
            if strict_llm:
                return ""

    if strict_llm:
        return ""

    # ── 降级：模板池路径 ────────────────────────────────────────────────
    if stability >= 0.7:
        pool = mood_awareness_pool.get("stable") or []
    elif stability <= 0.3:
        pool = mood_awareness_pool.get("volatile") or []
    else:
        pool = mood_awareness_pool.get("normal") or []

    if not pool:
        return ""
    tmpl = random.choice(pool)
    return tmpl.format(emotion=emotion_cn)


# 特质中文名映射（v1.11 新增，供 build_self_awareness_text / build_growth_note_text 使用）
_TRAIT_CN: dict[str, str] = {}

_GROWTH_MILESTONE_TEMPLATES: dict[str, Any] = {}

# 成长感知模板池：包装 personality_changes 的多种表达
_GROWTH_NOTE_WRAPPERS: list[str] = []


def build_growth_note_text(
    snapshot: dict[str, Any],
    *,
    llm_fn: Any | None = None,
    strict_llm: bool = False,
    style_evolution: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> str:
    """将快照转化为成长感知描述。

    技术债务 10.1 修复：优先使用 llm_fn 动态生成，降级到模板包装。
    v1.11 新增：style_evolution 参数，将风格演变趋势融入成长感知。
    """
    changes = list(snapshot.get("personality_changes") or [])
    milestones = list(snapshot.get("growth_milestones") or [])
    growth_note_wrappers = _coerce_text_list(
        _load_narrative_text_pools(render_target_lang=render_target_lang).get("growth_note_wrappers"),
        _GROWTH_NOTE_WRAPPERS,
    )

    # v1.11：从 style_evolution 提取有意义的趋势描述
    style_trend_texts: list[str] = []
    trait_cn_mapping = _coerce_text_mapping(
        _load_narrative_text_pools(render_target_lang=render_target_lang).get("trait_cn"),
        _TRAIT_CN,
    )
    if style_evolution and isinstance(style_evolution, dict):
        trend = style_evolution.get("trend") or {}
        history_count = int(style_evolution.get("history_count") or 0)
        if history_count >= 20:
            for dim, direction in trend.items():
                if direction == "rising":
                    cn = trait_cn_mapping.get(dim, dim)
                    style_trend_texts.append(f"变得更{cn}了")
                elif direction == "falling":
                    cn = trait_cn_mapping.get(dim, dim)
                    style_trend_texts.append(f"{cn}稍有收敛")

    # ── LLM 动态生成路径 ────────────────────────────────────────────────
    all_changes = changes + style_trend_texts
    if llm_fn is not None and (all_changes or milestones):
        try:
            change_hint = f"最近的变化：{'、'.join(all_changes[:3])}" if all_changes else ""
            milestone_hint = f"重要时刻：{milestones[0]}" if milestones else ""
            context = "、".join(filter(None, [change_hint, milestone_hint]))
            prompt = _mapping_text(
                _load_prompt_templates(render_target_lang=render_target_lang),
                "growth_note_prompt",
                context=context,
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 5:
                return result.strip()
        except Exception as _llm_exc:
            _logger.debug("LLM 成长感知生成失败: %s", _llm_exc)
            if strict_llm:
                return ""

    if strict_llm:
        return ""

    # ── 降级：模板包装路径 ──────────────────────────────────────────────
    # 优先使用风格演变趋势（更真实），其次使用特质变化记录
    if style_trend_texts:
        wrapper = random.choice(growth_note_wrappers)
        return wrapper.format(change=style_trend_texts[0])
    if changes:
        wrapper = random.choice(growth_note_wrappers)
        return wrapper.format(change=changes[0])
    if milestones:
        return milestones[0]
    return ""


_DEEP_BOND_STAGES: set[str] = {
    "FRIEND",
    "CLOSE",
    "CLOSE_FRIEND",
    "CONFIDANT",
    "INTIMATE",
    "SOULMATE",
}


_DEFAULT_STATE_ALIGNMENT_TEXTS: dict[str, Any] = {}


def _load_state_alignment_texts(*, render_target_lang: str = "zh") -> dict[str, Any]:
    try:
        raw = _resolve_prompt_template_json_localized(
            "self_model.state_alignment_texts",
            render_target_lang=render_target_lang,
        )
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _coerce_state_score(raw: dict[str, Any], key: str) -> float:
    try:
        value = float(raw.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _resolve_alignment_absence_band(offline_state: dict[str, Any]) -> str:
    return resolve_offline_absence_band(offline_state)


def _resolve_alignment_mode(runtime_flags: dict[str, Any]) -> str:
    mode = str(runtime_flags.get("sys_mode") or "").strip().lower()
    if mode in {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}:
        return mode
    activity_monitor = runtime_flags.get("activity_monitor")
    if isinstance(activity_monitor, dict) and bool(activity_monitor.get("is_gaming")):
        return "gaming"
    return "normal"


def _bond_perception_anchor(
    bond_perception: str,
    *,
    deep_bond: bool,
    render_target_lang: str = "zh",
) -> str:
    templates = _load_state_alignment_texts(render_target_lang=render_target_lang)
    anchors = templates.get("bond_perception_anchor")
    source = anchors if isinstance(anchors, Mapping) else {}
    if bond_perception == BOND_GROWTH_CLOSER:
        return str(source.get("closer_deep" if deep_bond else "closer_default") or "")
    if bond_perception == BOND_GROWTH_DISTANT:
        if deep_bond:
            return str(source.get("distant_deep") or "")
        return str(source.get("distant_default") or "")
    if deep_bond:
        return str(source.get("steady_deep") or "")
    return str(source.get("steady_default") or "")


def _absence_anchor(absence_band: str, *, render_target_lang: str = "zh") -> str:
    templates = _load_state_alignment_texts(render_target_lang=render_target_lang)
    mapping = templates.get("absence_anchor")
    source = mapping if isinstance(mapping, Mapping) else {}
    return str(source.get(absence_band) or "")


def _reason_suffix(
    *,
    sys_mode: str,
    supportive_drive: float,
    retreat_drive: float,
    should_be_brief: bool,
    should_be_withdrawn: bool,
    absence_band: str,
    intent_key: str,
    render_target_lang: str = "zh",
) -> str:
    templates = _load_state_alignment_texts(render_target_lang=render_target_lang)
    suffix_mapping = templates.get("reason_suffix")
    source = suffix_mapping if isinstance(suffix_mapping, Mapping) else {}
    if sys_mode == "sleep":
        return str(source.get("sleep") or "")
    if sys_mode == "focus":
        return str(source.get("focus") or "")
    if should_be_withdrawn or retreat_drive >= supportive_drive + 0.08:
        return str(source.get("withdrawn") or "")
    if should_be_brief:
        return str(source.get("brief") or "")
    if sys_mode == "gaming" and intent_key in {"continuity", "care", "reach_out"}:
        return str(source.get("gaming_supportive") or "")
    if absence_band in {"24h_plus", "72h_plus", "7d_plus"} and intent_key in {"continuity", "care", "reach_out"}:
        return str(source.get("absence_supportive") or "")
    return ""


def _state_alignment_intent_text(
    intent_key: str,
    *,
    deep_bond: bool,
    render_target_lang: str = "zh",
) -> str:
    templates = _load_state_alignment_texts(render_target_lang=render_target_lang)
    intent_mapping = templates.get("intent_text")
    source = intent_mapping if isinstance(intent_mapping, Mapping) else {}
    if intent_key == "continuity":
        key = "continuity_deep" if deep_bond else "continuity_default"
        return str(source.get(key) or "")
    if intent_key == "care":
        key = "care_deep" if deep_bond else "care_default"
        return str(source.get(key) or "")
    if intent_key == "reach_out":
        key = "reach_out_deep" if deep_bond else "reach_out_default"
        return str(source.get(key) or "")
    if intent_key == "guarded":
        return str(source.get("guarded_default") or "")
    return str(source.get("steady_default") or "")


def build_state_aligned_self_awareness(
    snapshot: dict[str, Any],
    *,
    offline_state: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    render_policy: dict[str, Any] | None = None,
    runtime_flags: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    offline = dict(offline_state or {})
    bond = dict(bond_summary or {})
    motive = dict(motive_state or {})
    policy = dict(render_policy or {})
    runtime = dict(runtime_flags or {})

    bond_stage = str(bond.get("stage") or "STRANGER").strip().upper()
    deep_bond = bond_stage in _DEEP_BOND_STAGES
    bond_perception = str(snapshot.get("bond_growth_perception") or BOND_GROWTH_STABLE)
    absence_band = _resolve_alignment_absence_band(offline)
    sys_mode = _resolve_alignment_mode(runtime)

    reach_drive = _coerce_state_score(motive, "reach_out_drive")
    care_drive = _coerce_state_score(motive, "care_drive")
    continuity_drive = _coerce_state_score(motive, "continuity_drive")
    withdraw_drive = _coerce_state_score(motive, "withdraw_drive")
    self_protect_drive = _coerce_state_score(motive, "self_protect_drive")
    supportive_drive = max(reach_drive, care_drive, continuity_drive)
    retreat_drive = max(withdraw_drive, self_protect_drive)
    should_be_brief = bool(policy.get("should_be_brief") or False)
    should_be_withdrawn = bool(policy.get("should_be_withdrawn") or policy.get("withdrawal_guard") or False)

    intent_key = "steady"
    if continuity_drive >= max(reach_drive, care_drive) and continuity_drive >= 0.45:
        intent_key = "continuity"
    elif care_drive >= max(reach_drive, continuity_drive) and care_drive >= 0.45:
        intent_key = "care"
    elif reach_drive >= 0.45:
        intent_key = "reach_out"
    elif retreat_drive >= 0.45:
        intent_key = "guarded"

    intent_text = _state_alignment_intent_text(
        intent_key,
        deep_bond=deep_bond,
        render_target_lang=render_target_lang,
    )

    parts: list[str] = []
    absence_anchor = _absence_anchor(absence_band, render_target_lang=render_target_lang)
    if absence_anchor:
        parts.append(absence_anchor)
    parts.append(
        _bond_perception_anchor(
            bond_perception,
            deep_bond=deep_bond,
            render_target_lang=render_target_lang,
        )
    )
    parts.append(intent_text)

    suffix = _reason_suffix(
        sys_mode=sys_mode,
        supportive_drive=supportive_drive,
        retreat_drive=retreat_drive,
        should_be_brief=should_be_brief,
        should_be_withdrawn=should_be_withdrawn,
        absence_band=absence_band,
        intent_key=intent_key,
        render_target_lang=render_target_lang,
    )
    if suffix:
        parts.append(suffix)

    separator = "、" if _normalize_template_lang(render_target_lang) == "ja" else "，"
    strip_chars = "、，。 "
    text = separator.join(part.strip(strip_chars) for part in parts if str(part).strip(strip_chars))
    if text:
        text = text.rstrip(strip_chars) + "。"

    reason_tags: list[str] = []
    if bond_stage:
        reason_tags.append(f"bond:{bond_stage}")
    reason_tags.append(
        f"bond_perception:{'closer' if bond_perception == BOND_GROWTH_CLOSER else ('distant' if bond_perception == BOND_GROWTH_DISTANT else 'steady')}"
    )
    if absence_band != "recent":
        reason_tags.append(f"offline:{absence_band}")
    if sys_mode != "normal":
        reason_tags.append(f"mode:{sys_mode}")
    reason_tags.append(f"motive:{intent_key}")
    if should_be_brief:
        reason_tags.append("policy:brief")
    if should_be_withdrawn:
        reason_tags.append("policy:withdrawn")

    return {
        "self_awareness": text,
        "alignment_mode": intent_key,
        "reason_tags": reason_tags,
    }
