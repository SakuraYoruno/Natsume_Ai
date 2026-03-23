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

    def as_dict(self) -> dict[str, Any]:
        return {
            "style_hint": self.style_hint,
            "tone": self.tone,
            "echo_weight_modifier": round(self.echo_weight_modifier, 4),
            "follow_up_tendency": round(self.追问倾向, 4),
        }
