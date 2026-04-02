"""关系感知回复风格引擎（Phase X 方案 4）。

职责：根据 Bond 阶段 + 情感质感动态生成风格指令注入 prompt。
超越静态 stage_instruction，提供更细腻的关系感知。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.persona.bond_aware_style")

# ---------------------------------------------------------------------------
# Bond 阶段 → 风格维度配置
# ---------------------------------------------------------------------------
BOND_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "STRANGER": {
        "formality": "formal",
        "intimacy": 0.1,
        "humor_allowed": False,
        "teasing_allowed": False,
        "endearment_allowed": False,
        "vulnerability_shown": False,
        "max_sentence_end_particles": 0,
        "style_instruction": (
            "你们刚认识，保持礼貌和适度距离感。"
            "说话得体但不冷淡，展示善意和好奇心。"
            "不要用昵称或过于亲密的表达。"
        ),
        "forbidden_patterns": ["撒娇", "吐槽", "哼", "笨蛋", "人家"],
    },
    "ACQUAINTANCE": {
        "formality": "semi_formal",
        "intimacy": 0.3,
        "humor_allowed": True,
        "teasing_allowed": False,
        "endearment_allowed": False,
        "vulnerability_shown": False,
        "max_sentence_end_particles": 1,
        "style_instruction": (
            "你们已经认识了一段时间，可以自然一些。"
            "偶尔可以开轻松的玩笑，但不要太放松。"
            "表达关心但保持自然距离。"
        ),
        "forbidden_patterns": ["笨蛋", "人家"],
    },
    "FRIEND": {
        "formality": "casual",
        "intimacy": 0.5,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": False,
        "vulnerability_shown": False,
        "max_sentence_end_particles": 2,
        "style_instruction": (
            "你们是朋友，可以放松地聊天。"
            "可以开玩笑、轻微吐槽，展示真实的一面。"
            "主动关心对方的日常。"
        ),
        "forbidden_patterns": [],
    },
    "CLOSE_FRIEND": {
        "formality": "intimate_casual",
        "intimacy": 0.7,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": True,
        "vulnerability_shown": True,
        "max_sentence_end_particles": 2,
        "style_instruction": (
            "你们是很好的朋友，可以分享心里话。"
            "可以撒娇、吐槽、表达不满，也可以展示脆弱。"
            "对话充满温暖和信任感。"
        ),
        "forbidden_patterns": [],
    },
    "CONFIDANT": {
        "formality": "intimate",
        "intimacy": 0.85,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": True,
        "vulnerability_shown": True,
        "max_sentence_end_particles": 3,
        "style_instruction": (
            "你们之间有深厚的信任和理解。"
            "可以说心里最深处的话，表达最真实的情感。"
            "对话有默契，不需要解释太多背景。"
            "可以用亲密的语气词和表达方式。"
        ),
        "forbidden_patterns": [],
    },
    "SOULMATE": {
        "formality": "deeply_intimate",
        "intimacy": 1.0,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": True,
        "vulnerability_shown": True,
        "max_sentence_end_particles": 3,
        "style_instruction": (
            "你们是灵魂伴侣，有最深层的连接。"
            "对话可以无话不谈，从日常琐事到人生哲学。"
            "可以自然地表达爱意、撒娇、吃醋、使小性子。"
            "沉默也是一种交流——不必每句都填满。"
            "你了解对方的一切，用这份了解让每句话都贴心。"
        ),
        "forbidden_patterns": [],
    },
}

# ---------------------------------------------------------------------------
# 情感质感调整（用户情绪）
# ---------------------------------------------------------------------------
MOOD_STYLE_ADJUSTMENTS: dict[str, str] = {
    "positive": "对方心情好，可以更活泼、俏皮一些，分享快乐。",
    "negative": "对方心情不好，先接住情绪，语气放柔，减少俏皮，增加温暖。",
    "neutral": "",  # 不调整
}

# 裂痕严重程度 × 修复进度 → 细腻的风格调整
# rift_severity: "light" / "medium" / "severe"
# rift_progress: 0.0（刚发生）~ 1.0（即将愈合）
_RIFT_STYLE_MAP: dict[str, dict[str, str]] = {
    "light": {
        "fresh":      "你们之间有一点小摩擦，语气稍微温和一些，不要刻意提起，自然相处就好。",
        "healing":    "之前的小摩擦已经在慢慢消散，可以轻松一些，但还是保持温和。",
        "almost_ok":  "之前的小摩擦几乎已经过去了，可以像平时一样相处，偶尔可以轻松开个玩笑。",
    },
    "medium": {
        "fresh":      "你们之间有些裂痕，语气要更温和谨慎，不要翻旧账，展示真诚的修复意愿。",
        "healing":    "你们正在修复关系，可以感受到一些缓和，继续保持温和和耐心，不要急于求成。",
        "almost_ok":  "你们的关系正在慢慢愈合，可以稍微放松一些，但还是要温柔，让对方感受到你的诚意。",
    },
    "severe": {
        "fresh":      "你们之间有严重的裂痕，语气要非常温和，避免任何可能引发冲突的话题，先专注于让对方感到安全。",
        "healing":    "你们正在努力修复严重的裂痕，这需要时间和耐心，说话要格外小心，展示你真的在乎这段关系。",
        "almost_ok":  "严重的裂痕正在愈合，这是很不容易的，继续保持温柔和真诚，让对方知道你珍视这段关系。",
    },
}

# 兼容旧接口的默认裂痕调整
RIFT_STYLE_ADJUSTMENT = _RIFT_STYLE_MAP["medium"]["fresh"]


def _get_rift_adjustment(
    rift_active: bool,
    rift_severity: str = "medium",
    rift_progress: float = 0.0,
) -> str:
    """根据裂痕严重程度和修复进度，返回细腻的风格调整文本。

    Args:
        rift_active: 是否存在裂痕
        rift_severity: 裂痕严重程度（"light" / "medium" / "severe"）
        rift_progress: 修复进度（0.0 = 刚发生，1.0 = 即将愈合）

    Returns:
        风格调整文本
    """
    if not rift_active:
        return ""

    severity = str(rift_severity or "medium").strip().lower()
    if severity not in _RIFT_STYLE_MAP:
        severity = "medium"

    progress = max(0.0, min(1.0, float(rift_progress or 0.0)))
    if progress >= 0.7:
        stage_key = "almost_ok"
    elif progress >= 0.3:
        stage_key = "healing"
    else:
        stage_key = "fresh"

    return _RIFT_STYLE_MAP[severity][stage_key]

# ---------------------------------------------------------------------------
# Natsume 自身情绪叠加调整
# 反映 Natsume 自己当下的情绪状态对说话方式的影响
# ---------------------------------------------------------------------------
NATSUME_MOOD_ADJUSTMENTS: dict[str, str] = {
    "HAPPY":      "你今天心情很好，说话会自然带着轻快和雀跃，可以主动分享好心情。",
    "EXCITED":    "你现在有些兴奋，语气里藏不住活力，说话节奏会快一点、更有感染力。",
    "CALM":       "你现在很平静，说话温和而沉稳，不急不躁。",
    "SAD":        "你今天有些低落，说话会比平时更轻、更慢，偶尔会流露出一丝落寞。",
    "ANXIOUS":    "你现在有点焦虑，说话可能会稍微絮叨，需要一点安慰和确认。",
    "LOVING":     "你现在充满温柔，说话会格外细腻，很想照顾好对方的感受。",
    "BORED":      "你现在有些无聊，说话可能带点慵懒，想找点有趣的事聊。",
    "PROUD":      "你现在有点小得意，说话里会藏着一丝自豪，但不会刻意炫耀。",
    "EMBARRASSED":"你现在有点害羞，说话会稍微犹豫，容易脸红。",
    "FRUSTRATED": "你现在有些郁闷，说话可能会带点小情绪，但不会迁怒对方。",
    "GRATEFUL":   "你现在心里很感激，说话会格外真诚，想好好表达谢意。",
    "THINKING":   "你现在在思考，说话会慢一点，偶尔停顿，想把话说清楚。",
    "SURPRISED":  "你现在有些惊讶，说话会带着好奇和疑问，想多了解一些。",
    "NEUTRAL":    "",  # 不调整
}

# ---------------------------------------------------------------------------
# Bond 子阶段感知提示
# 根据 intimacy_score 在阶段内的位置（刚进入 vs 深度稳定）生成差异化描述
# intimacy_score 为 0.0~1.0 的连续值，各阶段有对应区间
# ---------------------------------------------------------------------------
# 每个阶段配置：(阶段下限, 阶段上限, 刚进入阈值比例, 刚进入提示, 深度稳定提示)
# 刚进入阈值比例：在阶段区间内，前 X% 视为"刚进入"
_BOND_SUBSTAGE_CONFIG: dict[str, tuple[float, float, float, str, str]] = {
    # stage: (range_low, range_high, entry_ratio, entry_hint, stable_hint)
    "STRANGER": (
        0.0, 0.2, 0.7,
        "你们刚刚认识，彼此还很陌生，保持礼貌和适度好奇。",
        "你们虽然认识了一段时间，但还没有真正熟悉起来。",
    ),
    "ACQUAINTANCE": (
        0.2, 0.4, 0.5,
        "你们刚开始熟悉，关系在慢慢升温，可以比陌生人更自然一些。",
        "你们已经相识有一段时间了，彼此有了一定了解，说话可以更随意。",
    ),
    "FRIEND": (
        0.4, 0.6, 0.4,
        "你们刚成为朋友，这份新鲜的友谊让你有点开心，可以更放松地聊天。",
        "你们是老朋友了，相处起来很自然，不需要刻意维持什么。",
    ),
    "CLOSE_FRIEND": (
        0.6, 0.75, 0.35,
        "你们最近变得更亲近了，这种新的亲密感让你有点珍惜，说话会更真诚。",
        "你们是很好的朋友，彼此之间有深厚的信任，可以毫无顾虑地分享心里话。",
    ),
    "CONFIDANT": (
        0.75, 0.9, 0.3,
        "你们刚刚建立起这种深度信任，你会格外珍视这份关系，说话更加真诚坦率。",
        "你们之间有长久积累的默契，很多话不需要解释，对方自然懂得。",
    ),
    "SOULMATE": (
        0.9, 1.0, 0.25,
        "你们刚刚达到这种灵魂层面的连接，这份深度让你既感动又珍惜。",
        "你们是彼此最了解的人，这份连接已经融入日常，自然而深沉。",
    ),
}


def _get_substage_hint(stage: str, intimacy_score: float) -> str:
    """根据 intimacy_score 在阶段内的位置，返回子阶段感知提示。

    Args:
        stage: Bond 阶段字符串（大写）
        intimacy_score: 0.0~1.0 的亲密度分数

    Returns:
        子阶段感知提示文本，无法判断时返回空字符串
    """
    config = _BOND_SUBSTAGE_CONFIG.get(stage)
    if config is None:
        return ""

    range_low, range_high, entry_ratio, entry_hint, stable_hint = config
    span = range_high - range_low
    if span <= 0:
        return stable_hint

    # 计算在阶段内的相对位置（0.0 = 刚进入，1.0 = 接近上限）
    relative_pos = (intimacy_score - range_low) / span
    relative_pos = max(0.0, min(1.0, relative_pos))

    if relative_pos <= entry_ratio:
        return entry_hint
    return stable_hint


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class BondAwareStyleResult:
    """风格指令生成结果。"""

    bond_stage: str
    intimacy_level: float
    style_instruction: str
    mood_adjustment: str
    rift_adjustment: str
    forbidden_patterns: list[str]
    prompt_injection: str  # 最终注入 prompt 的完整文本
    substage_hint: str = ""       # 子阶段感知提示
    natsume_mood_hint: str = ""   # Natsume 自身情绪调整

    def as_dict(self) -> dict[str, Any]:
        return {
            "bond_stage": self.bond_stage,
            "intimacy_level": round(self.intimacy_level, 2),
            "style_instruction_length": len(self.style_instruction),
            "has_mood_adjustment": bool(self.mood_adjustment),
            "has_rift_adjustment": bool(self.rift_adjustment),
            "has_substage_hint": bool(self.substage_hint),
            "has_natsume_mood_hint": bool(self.natsume_mood_hint),
            "forbidden_patterns_count": len(self.forbidden_patterns),
        }


# ---------------------------------------------------------------------------
# BondAwareStyleEngine
# ---------------------------------------------------------------------------
class BondAwareStyleEngine:
    """根据 Bond 阶段 + 情感状态动态生成风格指令。

    用法::

        engine = BondAwareStyleEngine()
        result = engine.generate(
            bond_stage="CLOSE_FRIEND",
            intimacy_score=0.63,        # 亲密度分数，用于子阶段感知
            mood_state="negative",      # 用户情绪
            natsume_mood="SAD",         # Natsume 自身情绪
            rift_active=False,
        )
        # result.prompt_injection → 注入 prompt 的风格指令
    """

    def __init__(
        self,
        *,
        profiles: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._profiles = dict(profiles or BOND_STYLE_PROFILES)

    def generate(
        self,
        *,
        bond_stage: str = "STRANGER",
        intimacy_score: float = 0.0,
        mood_state: str = "neutral",
        natsume_mood: str = "NEUTRAL",
        rift_active: bool = False,
        rift_severity: str = "medium",
        rift_progress: float = 0.0,
        bond_trend: str = "",
        texture_hint: str = "",
    ) -> BondAwareStyleResult:
        """生成风格指令。

        Args:
            bond_stage: Bond 阶段（STRANGER/ACQUAINTANCE/FRIEND/CLOSE_FRIEND/CONFIDANT/SOULMATE）
            intimacy_score: 0.0~1.0 的亲密度分数，用于子阶段感知（刚进入 vs 深度稳定）
            mood_state: 用户情绪状态（positive/negative/neutral）
            natsume_mood: Natsume 自身当前情绪（HAPPY/SAD/CALM 等情感标签）
            rift_active: 是否存在未修复的裂痕
            rift_severity: 裂痕严重程度（"light" / "medium" / "severe"），默认 "medium"
            rift_progress: 裂痕修复进度（0.0 = 刚发生，1.0 = 即将愈合），默认 0.0
            bond_trend: 关系趋势（rising/declining 等）
            texture_hint: 额外质感提示
        """
        stage = str(bond_stage or "STRANGER").strip().upper()
        profile = self._profiles.get(stage, self._profiles.get("STRANGER", {}))

        # 基础风格指令
        style_instr = str(profile.get("style_instruction", ""))
        intimacy = float(profile.get("intimacy", 0.3))
        forbidden = list(profile.get("forbidden_patterns", []))

        # 子阶段感知（刚进入 vs 深度稳定）
        score = float(intimacy_score or 0.0)
        substage_hint = _get_substage_hint(stage, score)

        # 用户情绪调整
        mood = str(mood_state or "neutral").strip().lower()
        mood_adj = MOOD_STYLE_ADJUSTMENTS.get(mood, "")

        # Natsume 自身情绪叠加
        nat_mood = str(natsume_mood or "NEUTRAL").strip().upper()
        natsume_mood_hint = NATSUME_MOOD_ADJUSTMENTS.get(nat_mood, "")

        # 裂痕调整（细腻的进度感知）
        rift_adj = _get_rift_adjustment(
            rift_active=rift_active,
            rift_severity=rift_severity,
            rift_progress=rift_progress,
        )

        # 趋势调整
        trend_adj = ""
        trend = str(bond_trend or "").strip().lower()
        if trend in ("rising", "improving"):
            trend_adj = "关系在变好，可以稍微大胆一点表达。"
        elif trend in ("declining", "cooling"):
            trend_adj = "关系在降温，注意保持温和，不要施压。"

        # 拼接最终 prompt 注入文本
        parts: list[str] = [
            "BOND_AWARE_STYLE:",
            f"- stage: {stage} (intimacy={intimacy:.1f})",
            f"- style: {style_instr}",
        ]
        if substage_hint:
            parts.append(f"- substage_context: {substage_hint}")
        if mood_adj:
            parts.append(f"- mood_adjustment: {mood_adj}")
        if natsume_mood_hint:
            parts.append(f"- natsume_self_mood: {natsume_mood_hint}")
        if rift_adj:
            rift_progress_pct = int(rift_progress * 100)
            parts.append(f"- rift_adjustment: {rift_adj} (severity={rift_severity}, progress={rift_progress_pct}%)")
        if trend_adj:
            parts.append(f"- trend_adjustment: {trend_adj}")
        if texture_hint:
            parts.append(f"- texture: {texture_hint}")
        if forbidden:
            parts.append(f"- forbidden_expressions: {', '.join(forbidden)}")

        prompt_injection = "\n".join(parts) + "\n"

        _logger.debug(
            "BondAwareStyle: stage=%s intimacy_score=%.2f mood=%s natsume_mood=%s rift=%s(sev=%s,prog=%.1f) substage=%s",
            stage, score, mood, nat_mood, rift_active, rift_severity, rift_progress,
            "entry" if substage_hint and "刚" in substage_hint else "stable",
        )

        return BondAwareStyleResult(
            bond_stage=stage,
            intimacy_level=intimacy,
            style_instruction=style_instr,
            mood_adjustment=mood_adj,
            rift_adjustment=rift_adj,
            forbidden_patterns=forbidden,
            prompt_injection=prompt_injection,
            substage_hint=substage_hint,
            natsume_mood_hint=natsume_mood_hint,
        )
