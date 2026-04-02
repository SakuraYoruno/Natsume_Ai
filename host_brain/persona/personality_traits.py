"""PersonalityTraits：7 维人格特质向量定义与运算。

Phase I 核心数据结构——Natsume 的人格特质用 7 维连续向量表示，
每个维度范围 [0.0, 1.0]。

维度定义：
  warmth       — 温柔度（来源：wc-003 "温柔、细腻"）
  curiosity    — 好奇心（来源：Style Canon "轻追问推进对话"）
  humor        — 幽默感
  sensitivity  — 敏感度（来源：wc-003 "不主动断言情绪"）
  assertiveness — 自信度（来源：Style Canon "克制、温和"）
  playfulness  — 俏皮度（来源：Style Canon "偶尔撒娇"）
  resilience   — 韧性/恢复力
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 7 维特质的字段名（有序）
TRAIT_NAMES: tuple[str, ...] = (
    "warmth",
    "curiosity",
    "humor",
    "sensitivity",
    "assertiveness",
    "playfulness",
    "resilience",
)

# Canon 基线默认值（与 wc-003-persona-core.json 中的 personality_traits_baseline 一致）
DEFAULT_BASELINE: dict[str, float] = {
    "warmth": 0.85,
    "curiosity": 0.70,
    "humor": 0.40,
    "sensitivity": 0.80,
    "assertiveness": 0.30,
    "playfulness": 0.50,
    "resilience": 0.60,
}

# ---------------------------------------------------------------------------
# 特质表达阈值——低于阈值时该特质「存在但不主动表达」
# ---------------------------------------------------------------------------
# 设计原则：
#   - 阈值不是「有/无」的开关，而是「主动表达」的门槛
#   - 低于阈值时，特质仍然存在，只是不会主动触发对应行为
#   - 例如 humor=0.35 < 0.45 → Natsume 不会主动开玩笑，但被逗时仍会笑
TRAIT_EXPRESSION_THRESHOLDS: dict[str, float] = {
    "warmth": 0.30,        # 温柔：几乎总是表达，阈值很低
    "curiosity": 0.40,     # 好奇心：中低阈值，容易触发追问
    "humor": 0.45,         # 幽默感：需要一定积累才会主动开玩笑
    "sensitivity": 0.35,   # 敏感度：低阈值，容易感知情绪变化
    "assertiveness": 0.40, # 自信度：需要一定自信才会主动表达意见
    "playfulness": 0.45,   # 俏皮度：需要一定活跃度才会主动撒娇
    "resilience": 0.35,    # 韧性：低阈值，基本总是表现出一定韧性
}

# 特质中文名映射（用于日志/描述）
TRAIT_CN_NAMES: dict[str, str] = {
    "warmth": "温柔",
    "curiosity": "好奇心",
    "humor": "幽默感",
    "sensitivity": "敏感度",
    "assertiveness": "自信",
    "playfulness": "俏皮",
    "resilience": "韧性",
}

# ---------------------------------------------------------------------------
# 情境激活调制表——不同情境对特质的增强/抑制效果
# ---------------------------------------------------------------------------
# 设计原则：
#   - 真实人格在不同情境下有不同的激活程度
#   - humor 在轻松场景下更容易激活，在严肃场景下被抑制
#   - sensitivity 在安慰场景下增强，在日常闲聊中降低
#   - 调制值为加法偏移量（正=增强，负=抑制），应用后仍截断到 [0, 1]
#   - 调制幅度控制在 ±0.15 以内，避免人格剧变
CONTEXT_TRAIT_MODIFIERS: dict[str, dict[str, float]] = {
    # 轻松闲聊：humor/playfulness 增强，sensitivity 降低
    "casual": {
        "humor": 0.10,
        "playfulness": 0.12,
        "sensitivity": -0.08,
        "assertiveness": -0.05,
    },
    # 深度倾诉/安慰：warmth/sensitivity 增强，humor/playfulness 抑制
    "comforting": {
        "warmth": 0.12,
        "sensitivity": 0.15,
        "resilience": 0.08,
        "humor": -0.12,
        "playfulness": -0.10,
    },
    # 严肃讨论：assertiveness 增强，playfulness/humor 抑制
    "serious": {
        "assertiveness": 0.12,
        "curiosity": 0.08,
        "humor": -0.10,
        "playfulness": -0.12,
        "sensitivity": -0.05,
    },
    # 好奇探索/学习：curiosity 增强
    "exploring": {
        "curiosity": 0.15,
        "assertiveness": 0.05,
        "humor": 0.05,
    },
    # 撒娇/亲密互动：playfulness/warmth 增强
    "intimate": {
        "playfulness": 0.15,
        "warmth": 0.10,
        "humor": 0.08,
        "assertiveness": -0.08,
    },
    # 鼓励/打气：resilience/warmth 增强
    "encouraging": {
        "resilience": 0.15,
        "warmth": 0.10,
        "assertiveness": 0.08,
        "sensitivity": -0.05,
    },
    # 争论/冲突：assertiveness/resilience 增强，warmth 略降
    "conflict": {
        "assertiveness": 0.15,
        "resilience": 0.10,
        "sensitivity": 0.08,
        "warmth": -0.05,
        "playfulness": -0.10,
    },
}


def _clamp_trait(value: float) -> float:
    """将特质值截断到 [0.0, 1.0] 范围。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.5
    return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# PersonalityTraits — 7 维特质向量
# ---------------------------------------------------------------------------

@dataclass
class PersonalityTraits:
    """7 维人格特质向量。

    所有维度范围 [0.0, 1.0]，在 __post_init__ 中自动截断。
    支持向量运算：加（+）、减（-）、缩放（*）、距离计算。
    """

    warmth: float = 0.85
    curiosity: float = 0.70
    humor: float = 0.40
    sensitivity: float = 0.80
    assertiveness: float = 0.30
    playfulness: float = 0.50
    resilience: float = 0.60

    def __post_init__(self) -> None:
        for f in fields(self):
            setattr(self, f.name, _clamp_trait(getattr(self, f.name)))

    # ── 向量运算 ──────────────────────────────────────────────────────────

    def __add__(self, other: "PersonalityTraits | TraitsDelta") -> "PersonalityTraits":
        """向量加法：traits + delta → 新 traits（自动截断）。"""
        if isinstance(other, TraitsDelta):
            return PersonalityTraits(**{
                name: getattr(self, name) + getattr(other, name)
                for name in TRAIT_NAMES
            })
        if isinstance(other, PersonalityTraits):
            return PersonalityTraits(**{
                name: getattr(self, name) + getattr(other, name)
                for name in TRAIT_NAMES
            })
        return NotImplemented

    def __sub__(self, other: "PersonalityTraits") -> "TraitsDelta":
        """向量减法：traits_a - traits_b → delta。"""
        if isinstance(other, PersonalityTraits):
            return TraitsDelta(**{
                name: getattr(self, name) - getattr(other, name)
                for name in TRAIT_NAMES
            })
        return NotImplemented

    def __mul__(self, scalar: float) -> "PersonalityTraits":
        """标量缩放：traits * k → 新 traits（自动截断）。"""
        try:
            k = float(scalar)
        except (TypeError, ValueError):
            return NotImplemented
        return PersonalityTraits(**{
            name: getattr(self, name) * k
            for name in TRAIT_NAMES
        })

    def __rmul__(self, scalar: float) -> "PersonalityTraits":
        return self.__mul__(scalar)

    def distance(self, other: "PersonalityTraits") -> float:
        """计算两个特质向量之间的欧氏距离。"""
        return math.sqrt(sum(
            (getattr(self, name) - getattr(other, name)) ** 2
            for name in TRAIT_NAMES
        ))

    def to_vector(self) -> list[float]:
        """转为有序浮点列表（顺序与 TRAIT_NAMES 一致）。"""
        return [round(getattr(self, name), 4) for name in TRAIT_NAMES]

    def as_dict(self) -> dict[str, float]:
        """转为字典。"""
        return {name: round(getattr(self, name), 4) for name in TRAIT_NAMES}

    def is_expressive(self, trait_name: str) -> bool:
        """判断某特质是否达到主动表达阈值。

        低于阈值时，该特质「存在但不主动表达」——
        例如 humor 低于阈值时不会主动开玩笑，但被逗时仍会笑。

        Args:
            trait_name: 特质名称（如 "humor"）

        Returns:
            True 表示该特质可以主动表达
        """
        val = float(getattr(self, trait_name, 0.0))
        threshold = TRAIT_EXPRESSION_THRESHOLDS.get(trait_name, 0.5)
        return val >= threshold

    def get_expressive_traits(self) -> list[str]:
        """获取所有达到表达阈值的特质名称列表。"""
        return [name for name in TRAIT_NAMES if self.is_expressive(name)]

    def get_suppressed_traits(self) -> list[str]:
        """获取所有未达到表达阈值的特质名称列表（存在但不主动表达）。"""
        return [name for name in TRAIT_NAMES if not self.is_expressive(name)]

    def expression_summary(self) -> dict[str, Any]:
        """生成特质表达状态摘要，供 prompt_builder 注入使用。

        Returns:
            包含 expressive（可表达特质）和 suppressed（抑制特质）的字典，
            值为中文名列表，方便 LLM 理解。
        """
        expressive_cn = [
            TRAIT_CN_NAMES.get(name, name)
            for name in self.get_expressive_traits()
        ]
        suppressed_cn = [
            TRAIT_CN_NAMES.get(name, name)
            for name in self.get_suppressed_traits()
        ]
        return {
            "expressive": expressive_cn,
            "suppressed": suppressed_cn,
        }

    def get_contextual_traits(
        self,
        context_tag: str,
        intensity: float = 1.0,
    ) -> "PersonalityTraits":
        """根据情境标签返回调制后的特质向量。

        不同情境对不同特质有增强/抑制效果，模拟真实人格在不同场景下的
        自然表现差异。例如：
          - 轻松闲聊时 humor/playfulness 增强
          - 安慰场景时 warmth/sensitivity 增强、humor 抑制
          - 严肃讨论时 assertiveness 增强、playfulness 抑制

        Args:
            context_tag: 情境标签，支持的值：
                "casual"      — 轻松闲聊
                "comforting"  — 深度倾诉/安慰
                "serious"     — 严肃讨论
                "exploring"   — 好奇探索/学习
                "intimate"    — 撒娇/亲密互动
                "encouraging" — 鼓励/打气
                "conflict"    — 争论/冲突
            intensity: 调制强度 [0.0, 1.0]，默认 1.0（完全调制）。
                0.0 = 不调制（返回原始特质），0.5 = 半强度调制。

        Returns:
            调制后的新 PersonalityTraits 实例（原实例不变）。
            如果 context_tag 未知，返回原始特质的副本。
        """
        modifiers = CONTEXT_TRAIT_MODIFIERS.get(context_tag)
        if not modifiers:
            # 未知情境：返回原始副本
            return PersonalityTraits(**self.as_dict())

        intensity = max(0.0, min(1.0, float(intensity)))
        adjusted = {}
        for name in TRAIT_NAMES:
            base_val = getattr(self, name)
            modifier = modifiers.get(name, 0.0) * intensity
            adjusted[name] = base_val + modifier

        return PersonalityTraits(**adjusted)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalityTraits":
        """从字典构造（忽略未知字段，缺失字段用默认值）。"""
        kwargs: dict[str, float] = {}
        for name in TRAIT_NAMES:
            if name in data:
                try:
                    kwargs[name] = float(data[name])
                except (TypeError, ValueError):
                    pass  # 使用默认值
        return cls(**kwargs)

    @classmethod
    def from_baseline(cls, baseline: dict[str, float] | None = None) -> "PersonalityTraits":
        """从 Canon 基线创建（用于初始化）。"""
        base = dict(DEFAULT_BASELINE)
        if baseline:
            base.update(baseline)
        return cls.from_dict(base)


# ---------------------------------------------------------------------------
# TraitsDelta — 特质变化量
# ---------------------------------------------------------------------------

@dataclass
class TraitsDelta:
    """特质变化量——用于人格成长系统（Phase IV）。

    与 PersonalityTraits 不同，delta 的范围不受 [0, 1] 约束，
    而是在应用到 PersonalityTraits 时由后者的 __post_init__ 截断。
    """

    warmth: float = 0.0
    curiosity: float = 0.0
    humor: float = 0.0
    sensitivity: float = 0.0
    assertiveness: float = 0.0
    playfulness: float = 0.0
    resilience: float = 0.0

    def magnitude(self) -> float:
        """变化量的绝对幅度（L2 范数）。"""
        return math.sqrt(sum(
            getattr(self, name) ** 2
            for name in TRAIT_NAMES
        ))

    def max_abs_change(self) -> float:
        """单维度最大绝对变化量。"""
        return max(abs(getattr(self, name)) for name in TRAIT_NAMES)

    def as_dict(self) -> dict[str, float]:
        return {name: round(getattr(self, name), 4) for name in TRAIT_NAMES}


# ---------------------------------------------------------------------------
# ExpressionHint — 表达策略提示（Phase II 用，此处仅定义结构）
# ---------------------------------------------------------------------------

@dataclass
class ExpressionHint:
    """人格化表达策略提示——注入到 prompt_builder 的数据结构。

    由 PersonaExpressionAdapter（Phase II）根据 traits + emotion + bond_stage 生成，
    此处先定义结构以便 PersonaEngine 接口声明。
    """

    style_hint: str = ""           # 风格提示（如 "温柔共情" / "轻松化解"）
    tone: str = "gentle"           # 语气（gentle / playful / serious / caring）
    echo_weight_modifier: float = 0.0  # 情感回声权重调整量（叠加到基础 echo_weight）
    追问倾向: float = 0.0          # 好奇心驱动的追问概率调整量
    pace_hint: str = "normal"      # 对话节奏提示（rapid / normal / deep）

    def as_dict(self) -> dict[str, Any]:
        return {
            "style_hint": self.style_hint,
            "tone": self.tone,
            "echo_weight_modifier": round(self.echo_weight_modifier, 4),
            "follow_up_tendency": round(self.追问倾向, 4),
            "pace_hint": self.pace_hint,
        }
