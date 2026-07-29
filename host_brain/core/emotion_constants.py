"""情感系统共享常量——VAD 映射和标签集的唯一权威来源。

所有需要 VAD 映射或情感标签验证的模块都应从此处导入，
避免多处定义导致的数值不一致。

注意：如需修改 VAD 映射或标签集，只需修改此文件。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 权威 VAD 映射表（16 种标签 → Valence/Arousal/Dominance 三维向量）
# ---------------------------------------------------------------------------
# (valence, arousal, dominance) — 三维情感空间，所有维度 [-1.0, 1.0]

EMOTION_TAG_TO_VAD: dict[str, tuple[float, float, float]] = {
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


# ---------------------------------------------------------------------------
# 权威标签集（16 种）
# ---------------------------------------------------------------------------

VALID_EMOTION_TAGS: frozenset[str] = frozenset(EMOTION_TAG_TO_VAD.keys())


# ---------------------------------------------------------------------------
# 情感标签中文映射
# ---------------------------------------------------------------------------

EMOTION_TAG_ZH: dict[str, str] = {
    "HAPPY": "开心", "EXCITED": "兴奋", "SAD": "难过", "ANGRY": "生气",
    "CALM": "平静", "NEUTRAL": "平淡", "THINKING": "沉思", "LOVING": "充满爱意",
    "EMBARRASSED": "有些害羞", "CONFUSED": "困惑", "BORED": "无聊",
    "PROUD": "自豪", "GRATEFUL": "感激", "ANXIOUS": "焦虑",
    "FRUSTRATED": "沮丧", "SURPRISED": "惊喜",
}


# ---------------------------------------------------------------------------
# 权威别名表——将各种非规范标签映射到 16 种规范标签
# ---------------------------------------------------------------------------
# 合并自 emotion_context._SEMANTIC_EMOTION_TAG_ALIASES,
# vts_runtime_mapping.VTS_EMOTION_TAG_ALIASES,
# surface_affect_control._normalize_emotion_tag,
# The canonical Digital Life planner keeps normalization at the contract edge.
#
# 中间非规范目标（ANNOYED, EXHAUSTED, JOY, GUILTY, ASHAMED）
# 已全部解析为规范标签。

EMOTION_TAG_ALIASES: dict[str, str] = {
    # → HAPPY
    "HAPPINESS": "HAPPY",
    "JOY": "HAPPY",
    "JOYFUL": "HAPPY",
    "DELIGHT": "HAPPY",
    "PLAYFUL": "HAPPY",
    # → EXCITED
    "EXCITEMENT": "EXCITED",
    # → SAD
    "SADNESS": "SAD",
    "GRIEF": "SAD",
    "HURT": "SAD",
    # → CALM
    "COMFORT": "CALM",
    "SOOTHING": "CALM",
    "WARM": "CALM",
    # → THINKING
    "SERIOUS": "THINKING",
    # → ANGRY
    "ANGER": "ANGRY",
    # → FRUSTRATED
    "ANNOYED": "FRUSTRATED",
    "ANNOYANCE": "FRUSTRATED",
    "IRRITATED": "FRUSTRATED",
    # → LOVING
    "LOVE": "LOVING",
    "AFFECTION": "LOVING",
    "COQUETTISH": "LOVING",
    # → EMBARRASSED
    "GUILT": "EMBARRASSED",
    "GUILTY": "EMBARRASSED",
    "ASHAMED": "EMBARRASSED",
    "SHAME": "EMBARRASSED",
    "SHY": "EMBARRASSED",
    "EMBARRASSMENT": "EMBARRASSED",
    # → ANXIOUS
    "ANXIETY": "ANXIOUS",
    # → BORED
    "EXHAUSTION": "BORED",
    "EXHAUSTED": "BORED",
    "TIRED": "BORED",
}


def get_vad(emotion_tag: str) -> tuple[float, float, float]:
    """将情感标签映射为 VAD 三维向量。

    Args:
        emotion_tag: 情感标签（不区分大小写）

    Returns:
        (valence, arousal, dominance) 三元组，未知标签返回 (0, 0, 0)
    """
    tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    return EMOTION_TAG_TO_VAD.get(tag, (0.0, 0.0, 0.0))


def normalize_emotion_tag(value: str | None) -> str:
    """将任意情感标签归一化为 16 种规范标签之一。

    处理步骤：strip → upper → 替换非字母数字字符 → 解析别名。
    未知标签原样返回（大写形式）。
    """
    import re as _re
    text = str(value or "NEUTRAL").strip()
    if not text:
        return "NEUTRAL"
    text = text.upper().replace("-", "_").replace(" ", "_")
    text = _re.sub(r"[^A-Z0-9_]+", "_", text).strip("_")
    if not text:
        return "NEUTRAL"
    return EMOTION_TAG_ALIASES.get(text, text)
