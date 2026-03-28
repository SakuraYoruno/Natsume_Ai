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
# 情感质感调整
# ---------------------------------------------------------------------------
MOOD_STYLE_ADJUSTMENTS: dict[str, str] = {
    "positive": "对方心情好，可以更活泼、俏皮一些，分享快乐。",
    "negative": "对方心情不好，先接住情绪，语气放柔，减少俏皮，增加温暖。",
    "neutral": "",  # 不调整
}

RIFT_STYLE_ADJUSTMENT = (
    "注意：你们之间有未修复的裂痕。语气要更温和谨慎，"
    "不要翻旧账，展示真诚的修复意愿。"
)


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "bond_stage": self.bond_stage,
            "intimacy_level": round(self.intimacy_level, 2),
            "style_instruction_length": len(self.style_instruction),
            "has_mood_adjustment": bool(self.mood_adjustment),
            "has_rift_adjustment": bool(self.rift_adjustment),
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
            mood_state="negative",
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
        mood_state: str = "neutral",
        rift_active: bool = False,
        bond_trend: str = "",
        texture_hint: str = "",
    ) -> BondAwareStyleResult:
        """生成风格指令。"""
        stage = str(bond_stage or "STRANGER").strip().upper()
        profile = self._profiles.get(stage, self._profiles.get("STRANGER", {}))

        # 基础风格指令
        style_instr = str(profile.get("style_instruction", ""))
        intimacy = float(profile.get("intimacy", 0.3))
        forbidden = list(profile.get("forbidden_patterns", []))

        # 情绪调整
        mood = str(mood_state or "neutral").strip().lower()
        mood_adj = MOOD_STYLE_ADJUSTMENTS.get(mood, "")

        # 裂痕调整
        rift_adj = RIFT_STYLE_ADJUSTMENT if rift_active else ""

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
        if mood_adj:
            parts.append(f"- mood_adjustment: {mood_adj}")
        if rift_adj:
            parts.append(f"- rift_adjustment: {rift_adj}")
        if trend_adj:
            parts.append(f"- trend_adjustment: {trend_adj}")
        if texture_hint:
            parts.append(f"- texture: {texture_hint}")
        if forbidden:
            parts.append(f"- forbidden_expressions: {', '.join(forbidden)}")

        prompt_injection = "\n".join(parts) + "\n"

        _logger.debug(
            "BondAwareStyle: stage=%s intimacy=%.1f mood=%s rift=%s",
            stage, intimacy, mood, rift_active,
        )

        return BondAwareStyleResult(
            bond_stage=stage,
            intimacy_level=intimacy,
            style_instruction=style_instr,
            mood_adjustment=mood_adj,
            rift_adjustment=rift_adj,
            forbidden_patterns=forbidden,
            prompt_injection=prompt_injection,
        )
