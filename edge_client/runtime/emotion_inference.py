"""Lightweight text emotion hinting for Edge ASR events.

This module does not infer acoustic emotion from voice. Current ASR upstreams
only provide transcript text, so this is a conservative text bootstrap hint
used when no dedicated voice-affect model is available.
"""

from __future__ import annotations

import math
from typing import Any

from natsume_contracts.edge.v1 import EmotionVector, EMOTION_TAG_TO_VAD


# ASR keyword matching lists (edge-specific, multilingual)
_EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "HAPPY": ("happy", "开心", "高兴", "うれしい", "嬉しい", "楽しい", "好き"),
    "EXCITED": ("excited", "兴奋", "激动", "最高", "やばい", "すごい"),
    "SAD": ("sad", "难过", "伤心", "悲しい", "つらい", "寂しい"),
    "ANGRY": ("angry", "生气", "烦死", "烦", "むかつく", "腹立つ"),
    "ANXIOUS": ("anxious", "紧张", "担心", "不安", "心配", "こわい", "怖い"),
    "CALM": ("calm", "安心", "平静", "冷静", "落ち着", "ゆっくり"),
    "THINKING": ("think", "maybe", "想想", "考虑", "怎么", "为什么", "どう", "かな"),
    "SURPRISED": ("surprised", "惊讶", "居然", "まさか", "えっ", "真的假的"),
}

# Edge 侧刻意缩小从契约 VAD 值导出的幅度：这只是没有声学模型时的文本提示，不能以
# 与 Host 规范情绪相同的强度驱动具身表现或覆盖更高权威的情感判断。
EDGE_VAD_SCALE: float = 0.85


def infer_asr_emotion(
    *,
    text: str,
    confidence: float | None = None,
    final: bool | None = None,
) -> dict[str, Any]:
    # 本函数只从转写文本推导保守 hint，绝不宣称读取了声学情绪；返回值始终携带来源、
    # 置信度和退化标记，调用方据此决定是否仅作展示而非决策依据。
    raw_text = str(text or "").strip()
    if not raw_text:
        # 空转写不能伪造中性“识别成功”：明确标记不可用，使后续状态机能够保持当前
        # 情绪或安全退化，而不是把噪声/静音解释为用户情绪变化。
        return {
            "emotion_tag": "NEUTRAL",
            "emotion_intensity": 0.0,
            "mood_state": "calm",
            "emotion_source": "asr_text_unavailable",
            "emotion_modality": "asr_text",
            "emotion_confidence": 0.0,
            "emotion_inference_degraded": True,
            "acoustic_emotion_available": False,
            "inference_note": "asr_transcript_empty_no_acoustic_emotion_model",
            "matched_terms": [],
        }

    normalized = raw_text.lower()
    valence = 0.0
    arousal = 0.0
    dominance = 0.0
    matched_terms: list[str] = []

    for tag, keywords in _EMOTION_KEYWORDS.items():
        # 关键词只累积版本化标签对应的 VAD 向量，不将匹配词本身传入动作执行面；
        # 多语言词表是轻量 bootstrap，不承担语义理解或意图判定责任。
        canonical_vector = EMOTION_TAG_TO_VAD.get(tag, (0.0, 0.0, 0.0))
        vector = tuple(v * EDGE_VAD_SCALE for v in canonical_vector)
        for term in keywords:
            if str(term).lower() not in normalized:
                continue
            matched_terms.append(str(term))
            valence += float(vector[0])
            arousal += float(vector[1])
            dominance += float(vector[2])

    exclamation_count = raw_text.count("!") + raw_text.count("！")
    question_count = raw_text.count("?") + raw_text.count("？")
    # 标点仅提供微弱的唤醒/疑问线索，后续增量有上限，防止重复符号被用来放大情绪
    # 强度或干扰实时表情。
    if exclamation_count:
        arousal += min(0.35, 0.12 * exclamation_count)
    if question_count:
        dominance += min(0.35, 0.10 * question_count)
        if not matched_terms:
            arousal += 0.08

    if not matched_terms and question_count:
        dominance += 0.22
    elif not matched_terms and exclamation_count:
        arousal += 0.18

    # 显式夹紧三个维度后才构造契约向量，保证下游获得的是可序列化、可比较的有限状态，
    # 而不是关键词命中次数随文本长度无界增长的原始和。
    vector = EmotionVector(
        valence=max(-1.0, min(1.0, valence)),
        arousal=max(-1.0, min(1.0, arousal)),
        dominance=max(-1.0, min(1.0, dominance)),
        ts_ms=0,
    )
    emotion_tag = vector.to_emotion_tag()
    mood_state = vector.to_mood_state()
    intensity = math.sqrt((vector.valence ** 2) + (vector.arousal ** 2) + (vector.dominance ** 2))
    if confidence is not None:
        # ASR 置信度只削弱文本 hint，保留最低系数避免极小值造成数值不稳定；它不提升
        # 关键词引擎的可信度上限。
        intensity *= max(0.45, min(1.0, float(confidence)))
    if final is False:
        intensity *= 0.9
    intensity = max(0.0, min(1.0, round(intensity / 1.35, 4)))

    if emotion_tag == "NEUTRAL" and intensity < 0.18:
        intensity = 0.0
    confidence_score = _confidence_from_signal(
        matched_terms=matched_terms,
        exclamation_count=exclamation_count,
        question_count=question_count,
        asr_confidence=confidence,
        final=final,
    )
    # 没有词项时仍可根据标点给出极弱提示，但必须显式声明 degraded，避免消费者把它
    # 误用作声学模型或完整 NLU 的输出。
    degraded = not matched_terms

    return {
        "emotion_tag": emotion_tag,
        "emotion_intensity": intensity,
        "mood_state": mood_state,
        "emotion_source": "asr_text_bootstrap",
        "emotion_modality": "asr_text",
        "emotion_confidence": confidence_score,
        "emotion_inference_degraded": degraded,
        "acoustic_emotion_available": False,
        "inference_note": "text_semantic_hint_no_acoustic_emotion_model",
        "inference_level": 1,  # 方向 G：标记为 Level 1（关键词引擎）
        "matched_terms": matched_terms[:6],
    }


def _confidence_from_signal(
    *,
    matched_terms: list[str],
    exclamation_count: int,
    question_count: int,
    asr_confidence: float | None,
    final: bool | None,
) -> float:
    # 置信度描述“此 hint 的可用程度”而非用户真实情绪概率，并被严格限制在 0.95，
    # 给后续更高质量模型/人工输入留下可覆盖空间。
    if matched_terms:
        confidence = 0.45 + min(0.30, 0.10 * len(set(matched_terms)))
    elif question_count or exclamation_count:
        confidence = 0.24
    else:
        confidence = 0.0
    if asr_confidence is not None:
        confidence *= max(0.45, min(1.0, float(asr_confidence)))
    if final is False:
        confidence *= 0.9
    return max(0.0, min(0.95, round(confidence, 4)))
