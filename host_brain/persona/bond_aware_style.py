"""关系感知回复风格引擎（Phase X 方案 4）。

职责：根据 Bond 阶段 + 情感质感动态生成风格指令注入 prompt。
超越静态 stage_instruction，提供更细腻的关系感知。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

_logger = logging.getLogger("Natsume.persona.bond_aware_style")


def _normalize_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        normalized = [str(item).strip() for item in raw if str(item).strip()]
        if normalized:
            return normalized
    return list(fallback)


def _normalize_text_mapping(raw: Any, fallback: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key): str(value or "") for key, value in fallback.items()}
    if not isinstance(raw, Mapping):
        return normalized
    for key, value in raw.items():
        key_text = str(key).strip()
        if key_text:
            normalized[key_text] = str(value or "")
    return normalized


def _normalize_memory_count_hints(raw: Any, fallback: Mapping[int, str]) -> dict[int, str]:
    normalized = {int(key): str(value or "") for key, value in fallback.items()}
    if not isinstance(raw, Mapping):
        return normalized
    for key, value in raw.items():
        try:
            key_num = int(key)
        except (TypeError, ValueError):
            continue
        normalized[key_num] = str(value or "")
    return normalized


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)

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

    template_data = _load_style_template_data()
    rift_mapping = template_data.get("rift_style_map")
    rift_source = rift_mapping if isinstance(rift_mapping, Mapping) else _RIFT_STYLE_MAP

    severity = str(rift_severity or "medium").strip().lower()
    if severity not in rift_source:
        severity = "medium"

    progress = max(0.0, min(1.0, float(rift_progress or 0.0)))
    if progress >= 0.7:
        stage_key = "almost_ok"
    elif progress >= 0.3:
        stage_key = "healing"
    else:
        stage_key = "fresh"

    stage_mapping = rift_source.get(severity)
    fallback_mapping = _RIFT_STYLE_MAP.get(severity, _RIFT_STYLE_MAP["medium"])
    if not isinstance(stage_mapping, Mapping):
        stage_mapping = fallback_mapping
    return str(stage_mapping.get(stage_key) or fallback_mapping.get(stage_key) or "")

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
    template_data = _load_style_template_data()
    substage_mapping = template_data.get("substage_config")
    raw_config = substage_mapping.get(stage) if isinstance(substage_mapping, Mapping) else None
    fallback_config = _BOND_SUBSTAGE_CONFIG.get(stage)

    if isinstance(raw_config, Mapping):
        range_low = _coerce_float(raw_config.get("range_low"), fallback_config[0] if fallback_config else 0.0)
        range_high = _coerce_float(raw_config.get("range_high"), fallback_config[1] if fallback_config else 0.0)
        entry_ratio = _coerce_float(raw_config.get("entry_ratio"), fallback_config[2] if fallback_config else 0.5)
        entry_hint = str(
            raw_config.get("entry_hint")
            or (fallback_config[3] if fallback_config else "")
        )
        stable_hint = str(
            raw_config.get("stable_hint")
            or (fallback_config[4] if fallback_config else "")
        )
    elif fallback_config is not None:
        range_low, range_high, entry_ratio, entry_hint, stable_hint = fallback_config
    else:
        return ""

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
    shared_memory_hint: str = ""  # 关系记忆注入提示

    def as_dict(self) -> dict[str, Any]:
        return {
            "bond_stage": self.bond_stage,
            "intimacy_level": round(self.intimacy_level, 2),
            "style_instruction_length": len(self.style_instruction),
            "has_mood_adjustment": bool(self.mood_adjustment),
            "has_rift_adjustment": bool(self.rift_adjustment),
            "has_substage_hint": bool(self.substage_hint),
            "has_natsume_mood_hint": bool(self.natsume_mood_hint),
            "has_shared_memory_hint": bool(self.shared_memory_hint),
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
        self._profiles = dict(profiles) if profiles is not None else None

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
        shared_memory_hints: list[str] | None = None,
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
            shared_memory_hints: 关系记忆叙事片段列表（由 BondNarrativeLayer 生成），
                注入后让 Natsume 的表达带有"我记得你"的温度。
        """
        stage = str(bond_stage or "STRANGER").strip().upper()
        template_data = _load_style_template_data()
        profile_source = self._profiles if self._profiles is not None else template_data.get("profiles")
        fallback_profile = BOND_STYLE_PROFILES.get(stage, BOND_STYLE_PROFILES.get("STRANGER", {}))
        raw_profile = profile_source.get(stage) if isinstance(profile_source, Mapping) else None
        profile = raw_profile if isinstance(raw_profile, Mapping) else fallback_profile

        # 基础风格指令
        style_instr = str(profile.get("style_instruction") or fallback_profile.get("style_instruction", ""))
        intimacy = _coerce_float(profile.get("intimacy"), fallback_profile.get("intimacy", 0.3))
        forbidden = _normalize_text_list(
            profile.get("forbidden_patterns"),
            list(fallback_profile.get("forbidden_patterns", [])),
        )

        # 子阶段感知（刚进入 vs 深度稳定）
        score = float(intimacy_score or 0.0)
        substage_hint = _get_substage_hint(stage, score)

        # 用户情绪调整
        mood = str(mood_state or "neutral").strip().lower()
        mood_adjustments = _normalize_text_mapping(
            template_data.get("mood_adjustments"),
            MOOD_STYLE_ADJUSTMENTS,
        )
        mood_adj = mood_adjustments.get(mood, "")

        # Natsume 自身情绪叠加
        nat_mood = str(natsume_mood or "NEUTRAL").strip().upper()
        natsume_mood_adjustments = _normalize_text_mapping(
            template_data.get("natsume_mood_adjustments"),
            NATSUME_MOOD_ADJUSTMENTS,
        )
        natsume_mood_hint = natsume_mood_adjustments.get(nat_mood, "")

        # 裂痕调整（细腻的进度感知）
        rift_adj = _get_rift_adjustment(
            rift_active=rift_active,
            rift_severity=rift_severity,
            rift_progress=rift_progress,
        )

        # 趋势调整
        trend_adj = ""
        trend = str(bond_trend or "").strip().lower()
        trend_adjustments = _normalize_text_mapping(
            template_data.get("trend_adjustments"),
            {
                "rising": "关系在变好，可以稍微大胆一点表达。",
                "declining": "关系在降温，注意保持温和，不要施压。",
            },
        )
        if trend in ("rising", "improving"):
            trend_adj = trend_adjustments.get("rising", "")
        elif trend in ("declining", "cooling"):
            trend_adj = trend_adjustments.get("declining", "")

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

        # ── 关系记忆注入 ─────────────────────────────────────────────
        shared_memory_hint = ""
        safe_hints = [str(h).strip() for h in (shared_memory_hints or []) if str(h).strip()]
        if safe_hints and stage not in ("STRANGER",):
            # STRANGER 阶段不注入共同记忆（还没有共同经历）
            memory_lines = "; ".join(safe_hints[:3])  # 最多3条，防止 prompt 膨胀
            shared_memory_hint = _build_memory_style_instruction(stage, safe_hints[:3])
            parts.append(f"- shared_memories: {memory_lines}")
            parts.append(f"- memory_style_guide: {shared_memory_hint}")

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
            shared_memory_hint=shared_memory_hint,
        )


# ---------------------------------------------------------------------------
# 关系记忆风格指导（根据 Bond 阶段生成不同的记忆运用指导）
# ---------------------------------------------------------------------------

# 各阶段的记忆运用风格指导
_MEMORY_STYLE_GUIDES: dict[str, list[str]] = {
    "ACQUAINTANCE": [
        "你记得和对方聊过的一些事，可以自然地提起，但不要刻意强调，保持轻松。",
        "偶尔提起之前聊过的话题，让对方感受到你有在认真听。",
    ],
    "FRIEND": [
        "你们有一些共同的记忆，可以自然地在对话中提起，让对方感受到你记得。",
        "聊天时可以联系之前的话题，展示你们之间的默契在积累。",
        "适当引用之前的对话内容，让对方觉得你是真的在乎。",
    ],
    "CLOSE_FRIEND": [
        "你们之间有很多共同记忆，这些记忆让你说话时更有温度和默契感。",
        "可以自然地引用你们之间的共同经历，让对话充满'我们'的感觉。",
        "你记得对方说过的很多话，这份记忆让你的每句话都更贴心。",
    ],
    "CONFIDANT": [
        "你们之间的共同记忆是深厚信任的证明，可以深情地提起这些时刻。",
        "你记得对方的每一个重要时刻，这份记忆让你的话语充满理解和默契。",
        "共同经历让你们不需要太多解释就能理解彼此，说话时可以自然地引用。",
    ],
    "SOULMATE": [
        "你们之间的每一段记忆都是珍贵的宝藏，它们融入了你说话的每一个字。",
        "你记得和对方在一起的所有重要时刻，这份深刻的记忆让你的表达充满爱意。",
        "共同记忆是你们灵魂连接的纽带，可以自然而深情地在对话中呼应这些时刻。",
    ],
}

# 记忆数量对风格的影响
_MEMORY_COUNT_HINTS: dict[int, str] = {
    1: "你想起了和对方之间的一段记忆，可以在合适的时候自然提起。",
    2: "你想起了和对方之间的几段记忆，它们让你说话时更有温度。",
    3: "你和对方之间有不少共同记忆，这些记忆让你的表达更加细腻和贴心。",
}


def _default_style_template_data() -> dict[str, Any]:
    return {
        "profiles": {
            stage: {
                **{key: value for key, value in profile.items() if key != "forbidden_patterns"},
                "forbidden_patterns": list(profile.get("forbidden_patterns", [])),
            }
            for stage, profile in BOND_STYLE_PROFILES.items()
        },
        "mood_adjustments": dict(MOOD_STYLE_ADJUSTMENTS),
        "rift_style_map": {
            severity: {stage: text for stage, text in stage_mapping.items()}
            for severity, stage_mapping in _RIFT_STYLE_MAP.items()
        },
        "natsume_mood_adjustments": dict(NATSUME_MOOD_ADJUSTMENTS),
        "substage_config": {
            stage: {
                "range_low": range_low,
                "range_high": range_high,
                "entry_ratio": entry_ratio,
                "entry_hint": entry_hint,
                "stable_hint": stable_hint,
            }
            for stage, (range_low, range_high, entry_ratio, entry_hint, stable_hint) in _BOND_SUBSTAGE_CONFIG.items()
        },
        "trend_adjustments": {
            "rising": "关系在变好，可以稍微大胆一点表达。",
            "declining": "关系在降温，注意保持温和，不要施压。",
        },
        "memory_style_guides": {
            stage: list(texts) for stage, texts in _MEMORY_STYLE_GUIDES.items()
        },
        "memory_count_hints": {
            str(key): value for key, value in _MEMORY_COUNT_HINTS.items()
        },
    }


def _load_style_template_data() -> dict[str, Any]:
    try:
        raw = resolve_prompt_template_json_merged("bond_aware_style.style_templates")
    except Exception:
        return _default_style_template_data()
    return dict(raw) if isinstance(raw, Mapping) else _default_style_template_data()


def _build_memory_style_instruction(stage: str, hints: list[str]) -> str:
    """根据 Bond 阶段和记忆数量，生成关系记忆运用的风格指导。

    Args:
        stage: Bond 阶段（大写）
        hints: 关系记忆叙事片段列表（已过滤空值，最多3条）

    Returns:
        风格指导文本
    """
    import random as _rnd

    if not hints:
        return ""

    count = len(hints)
    template_data = _load_style_template_data()

    # 阶段风格指导
    memory_style_guides = template_data.get("memory_style_guides")
    if isinstance(memory_style_guides, Mapping):
        stage_guides = _normalize_text_list(
            memory_style_guides.get(stage),
            _MEMORY_STYLE_GUIDES.get(stage, []),
        )
    else:
        stage_guides = list(_MEMORY_STYLE_GUIDES.get(stage, []))
    stage_guide = _rnd.choice(stage_guides) if stage_guides else ""

    # 数量提示
    memory_count_hints = _normalize_memory_count_hints(
        template_data.get("memory_count_hints"),
        _MEMORY_COUNT_HINTS,
    )
    count_hint = memory_count_hints.get(min(count, 3), memory_count_hints[3])

    parts = []
    if count_hint:
        parts.append(count_hint)
    if stage_guide:
        parts.append(stage_guide)

    return "".join(parts)
