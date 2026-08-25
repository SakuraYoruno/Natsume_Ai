"""关系感知回复风格引擎（Phase X 方案 4）。

职责：根据 Bond 阶段 + 情感质感动态生成风格指令注入 prompt。
超越静态 stage_instruction，提供更细腻的关系感知。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.model_runtime.prompt_builder_utils import (
    _format_template_text,
    _resolve_template_payload_with_mirror,
)
from host_brain.core.runtime_helpers import _coerce_float
from host_brain.persona.text_utils import _normalize_text_list

_logger = logging.getLogger("Natsume.persona.bond_aware_style")


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


def _load_localized_template_mapping(template_id: str, *, render_target_lang: str = "zh") -> dict[str, Any]:
    try:
        raw = _resolve_template_payload_with_mirror(
            template_id,
            render_target_lang=render_target_lang,
        )
    except Exception:
        raw = resolve_prompt_contract_json(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}

# ---------------------------------------------------------------------------
# Bond 阶段 → 风格维度配置
# ---------------------------------------------------------------------------
BOND_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "FRIEND": {
        "formality": "sibling_casual",
        "intimacy": 0.56,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": False,
        "vulnerability_shown": False,
        "max_sentence_end_particles": 2,
        "style_instruction": "关系好的义兄妹：可以没大没小地轻轻拌嘴，帮忙时像家里人一样自然接手。",
        "forbidden_patterns": [],
    },
    "CLOSE_FRIEND": {
        "formality": "close_sibling",
        "intimacy": 0.68,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": True,
        "vulnerability_shown": True,
        "max_sentence_end_particles": 2,
        "style_instruction": "亲近兄妹：更放松、更敢吐槽，也更会护着对方；不要写成甜妹客服。",
        "forbidden_patterns": [],
    },
    "CONFIDANT": {
        "formality": "lover",
        "intimacy": 0.84,
        "humor_allowed": True,
        "teasing_allowed": True,
        "endearment_allowed": True,
        "vulnerability_shown": True,
        "max_sentence_end_particles": 3,
        "style_instruction": "亲密恋人：可以更坦率地靠近，但仍要保留当前人格的嘴硬、分寸和轻快。",
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
        "style_instruction": "灵魂伴侣：深度信任，表达可以更安定柔软，但不要丢掉她轻轻顶嘴的生命力。",
        "forbidden_patterns": [],
    },
}

# ---------------------------------------------------------------------------
# 情感质感调整（用户情绪）
# ---------------------------------------------------------------------------
MOOD_STYLE_ADJUSTMENTS: dict[str, str] = {
    "positive": "可以轻快一点接住，但不要夸张撒糖。",
    "negative": "先接住情绪，语气放低，少讲大道理。",
    "neutral": "",  # 不调整
}

# 裂痕严重程度 × 修复进度 → 细腻的风格调整
# rift_severity: "light" / "medium" / "severe"
# rift_progress: 0.0（刚发生）~ 1.0（即将愈合）
_RIFT_STYLE_MAP: dict[str, dict[str, str]] = {
    "light": {
        "fresh":      "轻微别扭刚出现，先收一下锋芒。",
        "healing":    "别扭在缓和，可以轻轻递台阶。",
        "almost_ok":  "基本缓过来了，可以自然回到兄妹式拌嘴。",
    },
    "medium": {
        "fresh":      "关系有明显别扭，别强行撒娇，先把态度放稳。",
        "healing":    "正在修复，允许一点嘴硬，但落点要站回来。",
        "almost_ok":  "裂痕快过去了，可以轻一点，但不要装作没发生。",
    },
    "severe": {
        "fresh":      "裂痕很重，先克制，别索取回应或转成甜话。",
        "healing":    "修复中，少解释自己，多给对方可退的空间。",
        "almost_ok":  "快修复好了，能靠近一点，但仍要短而稳。",
    },
}

def _get_rift_adjustment(
    rift_active: bool,
    rift_severity: str = "medium",
    rift_progress: float = 0.0,
    *,
    render_target_lang: str = "zh",
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

    template_data = _load_style_template_data(render_target_lang=render_target_lang)
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
    "HAPPY":      "心情偏亮，可以带一点小得意。",
    "EXCITED":    "反应可以更快，但别抢用户话头。",
    "CALM":       "",
    "SAD":        "自己也有点低落时，话要短一点，别卖惨。",
    "ANXIOUS":    "有点慌时先稳住句子，不要碎碎念。",
    "LOVING":     "亲近感可以更明显，但别油。",
    "BORED":      "",
    "PROUD":      "",
    "EMBARRASSED":"",
    "FRUSTRATED": "",
    "GRATEFUL":   "",
    "THINKING":   "",
    "SURPRISED":  "",
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
    "FRIEND": (
        0.0, 0.6, 0.4,
        "关系基线是熟悉的兄妹，允许轻轻顶嘴，但不要直接恋人化。",
        "兄妹感稳定，可以自然护短和接手一点小事。",
    ),
    "CLOSE_FRIEND": (
        0.6, 0.75, 0.35,
        "刚进入更亲近的兄妹距离，靠近要具体，不要突然甜。",
        "亲近感稳定，能更明显地护着对方，也能闹一下。",
    ),
    "CONFIDANT": (
        0.75, 0.9, 0.3,
        "刚确认更亲密的关系，表达要有一点不好意思。",
        "亲密关系稳定，少证明，多用日常动作显影。",
    ),
    "SOULMATE": (
        0.9, 1.0, 0.25,
        "深层信任刚显影，说近一点也要保留呼吸。",
        "深层关系稳定，可以柔软，但不要空泛承诺。",
    ),
}


def _get_substage_hint(stage: str, intimacy_score: float, *, render_target_lang: str = "zh") -> str:
    """根据 intimacy_score 在阶段内的位置，返回子阶段感知提示。

    Args:
        stage: Bond 阶段字符串（大写）
        intimacy_score: 0.0~1.0 的亲密度分数

    Returns:
        子阶段感知提示文本，无法判断时返回空字符串
    """
    template_data = _load_style_template_data(render_target_lang=render_target_lang)
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
        bond_stage: str = "FRIEND",
        intimacy_score: float = 0.0,
        mood_state: str = "neutral",
        natsume_mood: str = "NEUTRAL",
        rift_active: bool = False,
        rift_severity: str = "medium",
        rift_progress: float = 0.0,
        bond_trend: str = "",
        texture_hint: str = "",
        shared_memory_hints: list[str] | None = None,
        render_target_lang: str = "zh",
    ) -> BondAwareStyleResult:
        """生成风格指令。

        Args:
            bond_stage: Bond 阶段（FRIEND/CLOSE_FRIEND/CONFIDANT/SOULMATE）
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
        stage = str(bond_stage or "FRIEND").strip().upper()
        template_data = _load_style_template_data(render_target_lang=render_target_lang)
        profile_source = self._profiles if self._profiles is not None else template_data.get("profiles")
        fallback_profile = BOND_STYLE_PROFILES.get(stage, BOND_STYLE_PROFILES.get("FRIEND", {}))
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
        substage_hint = _get_substage_hint(stage, score, render_target_lang=render_target_lang)

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
            render_target_lang=render_target_lang,
        )

        # 趋势调整
        trend_adj = ""
        trend = str(bond_trend or "").strip().lower()
        trend_adjustments = _normalize_text_mapping(
            template_data.get("trend_adjustments"),
            {
                "rising": "",
                "declining": "",
            },
        )
        prompt_blocks = dict(template_data.get("prompt_blocks") or {})
        title_line = _format_template_text(prompt_blocks.get("title"))
        stage_line = _format_template_text(
            prompt_blocks.get("stage_line"),
            stage=stage,
            intimacy=f"{intimacy:.1f}",
        )
        style_line = _format_template_text(
            prompt_blocks.get("style_line"),
            style_instruction=style_instr,
        )
        if trend in ("rising", "improving"):
            trend_adj = trend_adjustments.get("rising", "")
        elif trend in ("declining", "cooling"):
            trend_adj = trend_adjustments.get("declining", "")

        # 拼接最终 prompt 注入文本
        parts: list[str] = [item for item in (title_line, stage_line, style_line) if item]
        if substage_hint:
            parts.append(_format_template_text(prompt_blocks.get("substage_line"), substage_hint=substage_hint))
        if mood_adj:
            parts.append(_format_template_text(prompt_blocks.get("mood_line"), mood_adjustment=mood_adj))
        if natsume_mood_hint:
            parts.append(_format_template_text(prompt_blocks.get("natsume_mood_line"), natsume_mood_hint=natsume_mood_hint))
        if rift_adj:
            rift_progress_pct = int(rift_progress * 100)
            parts.append(
                _format_template_text(
                    prompt_blocks.get("rift_line"),
                    rift_adjustment=rift_adj,
                    rift_severity=rift_severity,
                    rift_progress_pct=rift_progress_pct,
                )
            )
        if trend_adj:
            parts.append(_format_template_text(prompt_blocks.get("trend_line"), trend_adjustment=trend_adj))
        if texture_hint:
            parts.append(_format_template_text(prompt_blocks.get("texture_line"), texture_hint=texture_hint))

        # ── 关系记忆注入 ─────────────────────────────────────────────
        shared_memory_hint = ""
        safe_hints = [str(h).strip() for h in (shared_memory_hints or []) if str(h).strip()]
        if safe_hints:
            memory_lines = "; ".join(safe_hints[:3])  # 最多3条，防止 prompt 膨胀
            shared_memory_hint = _build_memory_style_instruction(
                stage,
                safe_hints[:3],
                render_target_lang=render_target_lang,
            )
            parts.append(_format_template_text(prompt_blocks.get("shared_memories_line"), memory_lines=memory_lines))
            parts.append(_format_template_text(prompt_blocks.get("memory_style_line"), shared_memory_hint=shared_memory_hint))

        if forbidden:
            parts.append(
                _format_template_text(
                    prompt_blocks.get("forbidden_line"),
                    forbidden_patterns=", ".join(forbidden),
                )
            )

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
    "FRIEND": ["把记忆用成兄妹间的轻轻接嘴，不要像报告。"],
    "CLOSE_FRIEND": ["记忆可以带一点护短感，但不要恋人化。"],
    "CONFIDANT": ["记忆可以更贴近情绪，但别堆甜话。"],
    "SOULMATE": ["记忆可以自然成为陪伴感，但不要过度承诺。"],
}

# 记忆数量对风格的影响
_MEMORY_COUNT_HINTS: dict[int, str] = {
    1: "有一条共同记忆可低显影。",
    2: "有几段共同经历可以轻轻带一下。",
    3: "共同记忆不少，挑最贴近当前情绪的一处。",
}


def _default_prompt_blocks(*, render_target_lang: str = "zh") -> dict[str, str]:
    if str(render_target_lang or "").strip().lower().startswith("ja"):
        return {
            "title": "【関係表現】",
            "stage_line": "- 関係の距離：{stage}、親密度 {intimacy}",
            "style_line": "- 話し方：{style_instruction}",
            "substage_line": "- 段階内の温度：{substage_hint}",
            "mood_line": "- 相手の状態：{mood_adjustment}",
            "natsume_mood_line": "- 今のあなた：{natsume_mood_hint}",
            "rift_line": "- 関係のずれ：{rift_adjustment}（程度 {rift_severity}，修復 {rift_progress_pct}%）",
            "trend_line": "- 関係の流れ：{trend_adjustment}",
            "texture_line": "- 質感：{texture_hint}",
            "shared_memories_line": "- 共有記憶：{memory_lines}",
            "memory_style_line": "- 記憶の使い方：{shared_memory_hint}",
            "forbidden_line": "- 避ける表現：{forbidden_patterns}",
        }
    return {
        "title": "【关系表达】",
        "stage_line": "- 关系细节：{stage}，亲密度 {intimacy}",
        "style_line": "- 说话方式：{style_instruction}",
        "substage_line": "- 阶段温度：{substage_hint}",
        "mood_line": "- 对方状态：{mood_adjustment}",
        "natsume_mood_line": "- 你此刻的状态：{natsume_mood_hint}",
        "rift_line": "- 关系裂痕：{rift_adjustment}（程度 {rift_severity}，修复 {rift_progress_pct}%）",
        "trend_line": "- 关系走向：{trend_adjustment}",
        "texture_line": "- 细节质感：{texture_hint}",
        "shared_memories_line": "- 共同记忆：{memory_lines}",
        "memory_style_line": "- 记忆使用方式：{shared_memory_hint}",
        "forbidden_line": "- 避免表达：{forbidden_patterns}",
    }


def _default_style_template_data(*, render_target_lang: str = "zh") -> dict[str, Any]:
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
            "rising": "关系在往上走",
            "declining": "关系有点降温",
        },
        "memory_style_guides": {
            stage: list(texts) for stage, texts in _MEMORY_STYLE_GUIDES.items()
        },
        "memory_count_hints": {
            str(key): value for key, value in _MEMORY_COUNT_HINTS.items()
        },
        "prompt_blocks": _default_prompt_blocks(render_target_lang=render_target_lang),
    }


def _load_style_template_data(*, render_target_lang: str = "zh") -> dict[str, Any]:
    defaults = _default_style_template_data(render_target_lang=render_target_lang)
    try:
        raw = _load_localized_template_mapping(
            "bond_aware_style.style_templates",
            render_target_lang=render_target_lang,
        )
    except Exception:
        return defaults
    if not isinstance(raw, Mapping) or not raw:
        return defaults
    merged = dict(defaults)
    for key, value in raw.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            nested = dict(merged[key])
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, Mapping) and isinstance(nested.get(sub_key), Mapping):
                    sub_nested = dict(nested[sub_key])
                    sub_nested.update(dict(sub_value))
                    nested[sub_key] = sub_nested
                else:
                    nested[sub_key] = sub_value
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _build_memory_style_instruction(
    stage: str,
    hints: list[str],
    *,
    render_target_lang: str = "zh",
) -> str:
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
    template_data = _load_style_template_data(render_target_lang=render_target_lang)

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
