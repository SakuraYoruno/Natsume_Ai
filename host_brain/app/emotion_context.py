"""情感上下文处理模块。

从 fastapi_app.py 拆分，负责情感状态的解析、归一化和治理快照构建。

升级（Phase 1/2/5）：
- Phase 1: 持久化情绪状态 + 时间感知衰减，让情绪"活着"
- Phase 2: VAD 映射精细化（16 种标签全覆盖 + dominance 维度）
- Phase 5: 情感回声差异化（关系深度 + 情绪类型）
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Mapping

from host_brain.core.metabolism import (
    build_governance_snapshot,
    build_hard_snapshot,
    normalize_hard_state,
    # 修复审计问题 #6：接入孤立的情感模块
    EmotionVector,
    EmotionTimeline,
    compute_emotion_echo,
    evaluate_emotion_triggers,
    apply_visual_emotion_hint,  # noqa: F401 — 供外部模块按需调用
)

logger = logging.getLogger(__name__)

# 修复审计问题 #6：全局 EmotionTimeline 实例，记录情感状态变化历史
_global_emotion_timeline = EmotionTimeline(max_entries=100)

# Phase 1: 全局持久化情绪向量（进程级单例，支持跨请求情绪连续性）
_global_emotion_vector: EmotionVector | None = None
_global_emotion_vector_last_ts: int = 0  # 上次更新时间戳（毫秒）


def get_emotion_timeline() -> EmotionTimeline:
    """获取全局情感时间线实例（供外部模块访问）。"""
    return _global_emotion_timeline


def get_persistent_emotion_vector() -> EmotionVector:
    """获取全局持久化情绪向量（Phase 1）。

    首次调用时初始化为中性偏微正的默认值（模拟"刚醒来，状态还不错"）。
    """
    global _global_emotion_vector, _global_emotion_vector_last_ts
    if _global_emotion_vector is None:
        _global_emotion_vector = EmotionVector(
            valence=0.1, arousal=0.2, dominance=0.0,
            ts_ms=int(time.time() * 1000),
        )
        _global_emotion_vector_last_ts = _global_emotion_vector.ts_ms
    return _global_emotion_vector


def _apply_time_aware_decay(vector: EmotionVector, now_ms: int) -> EmotionVector:
    """Phase 1: 时间感知情绪衰减。

    根据距离上次更新的时间差，自动衰减情绪：
    - 1 分钟内：几乎不衰减
    - 5 分钟：衰减约 15%
    - 30 分钟：衰减约 50%
    - 2 小时+：衰减约 85%，接近中性
    """
    global _global_emotion_vector_last_ts
    elapsed_ms = max(0, now_ms - _global_emotion_vector_last_ts)
    if elapsed_ms < 30_000:  # 30 秒内不衰减
        return vector
    # 衰减率随时间非线性增长：rate = min(0.85, elapsed_minutes / 120)
    elapsed_minutes = elapsed_ms / 60_000.0
    rate = min(0.85, elapsed_minutes / 120.0)
    decayed = vector.decay(rate=rate)
    logger.debug(
        "情绪时间衰减 elapsed=%.1f分钟 rate=%.3f valence=%.3f→%.3f arousal=%.3f→%.3f",
        elapsed_minutes, rate,
        vector.valence, decayed.valence,
        vector.arousal, decayed.arousal,
    )
    return decayed


def _update_persistent_emotion(new_vector: EmotionVector, now_ms: int) -> EmotionVector:
    """Phase 1: 更新全局持久化情绪向量。

    流程：先对旧情绪做时间衰减 → 再与新输入混合（weight=0.4）。
    这确保了：
    1. 长时间不聊天后情绪自然归于平静
    2. 新输入能显著影响当前情绪，但不会完全覆盖（保持连续性）
    """
    global _global_emotion_vector, _global_emotion_vector_last_ts
    current = get_persistent_emotion_vector()
    # 先衰减
    decayed = _apply_time_aware_decay(current, now_ms)
    # 再混合新输入（weight=0.4，新输入占 40%）
    blended = decayed.blend(new_vector, weight=0.4)
    # 更新时间戳
    blended = EmotionVector(
        valence=blended.valence,
        arousal=blended.arousal,
        dominance=blended.dominance,
        ts_ms=now_ms,
    )
    _global_emotion_vector = blended
    _global_emotion_vector_last_ts = now_ms
    logger.debug(
        "持久化情绪更新 new_input=(%.3f,%.3f) decayed=(%.3f,%.3f) blended=(%.3f,%.3f)",
        new_vector.valence, new_vector.arousal,
        decayed.valence, decayed.arousal,
        blended.valence, blended.arousal,
    )
    return blended


def normalize_chat_emotion_context(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    state = normalize_hard_state(
        {
            "mood_state": source.get("mood_state"),
            "ts_ms": source.get("emotion_ts_ms"),
        }
    )
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)

    emotion_tag = str(source.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags:
        emotion_tag = allowed_emotion_tags[0]

    emotion_range_hint = str(source.get("emotion_range_hint") or "").strip() or hard_snapshot.emotion_range_hint
    emotion_intensity = _coerce_emotion_intensity(
        source.get("emotion_intensity"),
        default=_coerce_emotion_intensity(os.environ.get("HOST_DEFAULT_EMOTION_INTENSITY"), default=0.5),
    )
    try:
        emotion_ts_ms = int(source.get("emotion_ts_ms") or hard_snapshot.emotion_ts_ms)
    except (TypeError, ValueError):
        emotion_ts_ms = int(hard_snapshot.emotion_ts_ms)

    # Phase 2: 精细化 VAD 映射（16 种标签 + dominance 维度）
    valence, arousal, dominance = map_emotion_tag_to_vad(emotion_tag)
    input_vector = EmotionVector(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        ts_ms=emotion_ts_ms,
    )
    # Phase 1: 与持久化情绪混合（时间衰减 + blend），实现情绪连续性
    current_vector = _update_persistent_emotion(input_vector, emotion_ts_ms)

    # 记录到全局情感时间线
    _global_emotion_timeline.append(
        ts_ms=emotion_ts_ms,
        emotion_tag=emotion_tag,
        valence=current_vector.valence,
        arousal=current_vector.arousal,
        source="normalize_chat_emotion_context",
    )
    # 计算情感回声（共情响应提示）
    echo_result = compute_emotion_echo(
        emotion_tag,
        current_vector=current_vector,
        echo_weight=0.4,
    )
    # 评估主动情感触发器
    triggered_emotions = evaluate_emotion_triggers({
        "emotion_tag": emotion_tag,
        "emotion_intensity": emotion_intensity,
        "mood_state": str(hard_snapshot.mood_state),
    })

    return {
        "mood_state": str(hard_snapshot.mood_state),
        "emotion_tag": str(emotion_tag),
        "emotion_range_hint": str(emotion_range_hint),
        "emotion_intensity": emotion_intensity,
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": emotion_ts_ms,
        # Phase 1+2：持久化 VAD 向量（含衰减 + 混合后的值）
        "vad_vector": current_vector.as_dict(),
        "emotion_echo": echo_result,
        "triggered_emotions": triggered_emotions,
        # Phase 1：内心独白（供 emotion_pulse 槽位使用）
        "inner_monologue": _vector_to_inner_monologue(current_vector),
    }


def resolve_chat_emotion_context(*, request_body: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(getattr(request_body, "emotion_context", None), Mapping):
        raw.update(dict(request_body.emotion_context))
    if getattr(request_body, "mood_state", None) is not None:
        raw["mood_state"] = request_body.mood_state
    if getattr(request_body, "emotion_tag", None) is not None:
        raw["emotion_tag"] = request_body.emotion_tag
    if getattr(request_body, "emotion_range_hint", None) is not None:
        raw["emotion_range_hint"] = request_body.emotion_range_hint
    if getattr(request_body, "emotion_intensity", None) is not None:
        raw["emotion_intensity"] = request_body.emotion_intensity
    if "mood_state" not in raw:
        raw["mood_state"] = str(os.environ.get("HOST_DEFAULT_MOOD_STATE") or "steady").strip().lower() or "steady"
    if "emotion_tag" not in raw:
        raw["emotion_tag"] = str(os.environ.get("HOST_DEFAULT_EMOTION_TAG") or "NEUTRAL").strip().upper() or "NEUTRAL"
    return normalize_chat_emotion_context(raw)


def build_chat_emotion_snapshot(
    *,
    emotion_context: Mapping[str, Any] | None,
    source: str = "chat_request",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    captured_ts_ms = _coerce_non_negative_int(ts_ms, default=int(time.time() * 1000))
    emotion_ts_ms = _coerce_non_negative_int(
        normalized.get("emotion_ts_ms"),
        default=captured_ts_ms,
    )
    return {
        "version": "chat_emotion_snapshot.v1",
        "source": str(source or "chat_request").strip() or "chat_request",
        "captured_ts_ms": captured_ts_ms,
        "emotion_ts_ms": emotion_ts_ms,
        "age_ms": max(0, captured_ts_ms - emotion_ts_ms),
        "emotion_context": {
            "mood_state": str(normalized.get("mood_state") or "steady"),
            "emotion_tag": str(normalized.get("emotion_tag") or "NEUTRAL"),
            "emotion_range_hint": str(normalized.get("emotion_range_hint") or ""),
            "emotion_intensity": _coerce_emotion_intensity(normalized.get("emotion_intensity"), default=0.5),
        },
        "allowed_emotion_tags": list(normalized.get("allowed_emotion_tags") or []),
    }


def build_chat_governance_snapshot(
    *,
    emotion_context: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    hard_state = normalize_hard_state(
        {
            "mood_state": normalized.get("mood_state"),
            "ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        }
    )
    snapshot = build_governance_snapshot(
        hard_state,
        overrides={
            "mood_state": normalized.get("mood_state"),
            "emotion_tag": normalized.get("emotion_tag"),
            "emotion_intensity": normalized.get("emotion_intensity", 0.5),
            "emotion_ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        },
    )
    snapshot["emotion_context"] = {
        "mood_state": normalized.get("mood_state"),
        "emotion_tag": normalized.get("emotion_tag"),
        "emotion_range_hint": normalized.get("emotion_range_hint"),
        "emotion_intensity": normalized.get("emotion_intensity", 0.5),
    }
    snapshot["emotion_snapshot"] = build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="governance_snapshot",
        ts_ms=ts_ms,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Phase 2: 精细化 VAD 映射（16 种标签全覆盖 + dominance 维度）
# ---------------------------------------------------------------------------
# (valence, arousal, dominance) — 三维情感空间
_EMOTION_TAG_TO_VAD: dict[str, tuple[float, float, float]] = {
    "HAPPY":        ( 0.60,  0.40,  0.30),  # 开心：积极但不太激动
    "EXCITED":      ( 0.80,  0.80,  0.50),  # 兴奋：高积极 + 高唤醒
    "SAD":          (-0.60, -0.20, -0.30),   # 悲伤：消极 + 低唤醒 + 被动
    "ANGRY":        (-0.70,  0.80,  0.60),   # 愤怒：消极 + 高唤醒 + 主导
    "CALM":         ( 0.20, -0.40,  0.10),   # 平静：微积极 + 低唤醒
    "THINKING":     ( 0.00,  0.20,  0.40),   # 思考：中性 + 微唤醒 + 主导
    "NEUTRAL":      ( 0.00,  0.00,  0.00),   # 中性
    "SURPRISED":    ( 0.30,  0.70,  0.00),   # 惊讶：微积极 + 高唤醒
    "LOVING":       ( 0.80,  0.30,  0.20),   # 爱意：高积极 + 温和唤醒
    "EMBARRASSED":  (-0.20,  0.50, -0.50),   # 尴尬：微消极 + 中唤醒 + 被动
    "CONFUSED":     (-0.10,  0.30, -0.30),   # 困惑：微消极 + 微唤醒 + 被动
    "BORED":        (-0.30, -0.50,  0.00),   # 无聊：消极 + 低唤醒
    "PROUD":        ( 0.70,  0.50,  0.70),   # 骄傲：积极 + 高主导
    "GRATEFUL":     ( 0.70,  0.20, -0.20),   # 感激：积极 + 温和 + 谦逊
    "ANXIOUS":      (-0.40,  0.70, -0.40),   # 焦虑：消极 + 高唤醒 + 被动
    "FRUSTRATED":   (-0.50,  0.60,  0.30),   # 沮丧：消极 + 高唤醒
}


def map_emotion_tag_to_vad(emotion_tag: str) -> tuple[float, float, float]:
    """将情感标签映射为 VAD 三维向量（Phase 2 精细化）。

    Returns:
        (valence, arousal, dominance) 三元组
    """
    tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    return _EMOTION_TAG_TO_VAD.get(tag, (0.0, 0.0, 0.0))


# ---------------------------------------------------------------------------
# Phase 3: 内心独白——将 VAD 向量转为第一人称感受描述
# ---------------------------------------------------------------------------

def _vector_to_inner_monologue(v: EmotionVector) -> str:
    """将 VAD 向量转为细腻的第一人称内心独白（Phase 3）。

    根据 valence/arousal/dominance 的不同区间组合，生成差异化的感受描述，
    替代原来机械的 4 种 style_hint 模板。
    """
    val, aro, dom = v.valence, v.arousal, v.dominance

    # 高正面 + 高唤醒：兴奋/雀跃
    if val > 0.6 and aro > 0.6:
        return "现在心里特别开心，有种想要分享一切的冲动"
    # 高正面 + 低唤醒：温暖/安心
    if val > 0.5 and aro < 0.3:
        return "心里暖暖的，感觉很安心，这种平静的幸福感真好"
    # 中正面 + 中唤醒：小雀跃
    if val > 0.3 and aro > 0.3:
        return "心情不错，有些小雀跃，想和对方多聊聊"
    # 中正面 + 低唤醒：恬淡满足
    if val > 0.2 and aro <= 0.0:
        return "很舒服的状态，不需要太多言语，安静待着也很好"
    # 高负面 + 高唤醒：难受但克制
    if val < -0.5 and aro > 0.5:
        return "心里有些难受，但还在努力消化这种感觉，不想让对方担心"
    # 高负面 + 低唤醒：低落沉默
    if val < -0.5 and aro < 0.0:
        return "有点低落，不太想说话，只想安静待一会儿"
    # 中负面：有点沉
    if val < -0.3:
        return "心情有点沉，不过还好，也许聊聊会好一些"
    # 高唤醒 + 中性效价：紧张/期待
    if aro > 0.5 and abs(val) < 0.3:
        return "有些紧张，心跳比平时快一些，不知道在期待什么"
    # 低唤醒 + 中性效价：平静如水
    if aro < -0.3 and abs(val) < 0.3:
        return "很平静，像水面一样，思绪慢慢地流淌"
    # 高支配感：自信/掌控
    if dom > 0.4:
        return "状态不错，心里很有底，知道自己在做什么"
    # 低支配感：柔软/顺从
    if dom < -0.3:
        return "心态比较柔软，愿意倾听和配合对方"
    # 默认：平稳自然
    return "状态还不错，心里很平稳，可以自然地聊下去"


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def _coerce_emotion_intensity(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return float(parsed)
