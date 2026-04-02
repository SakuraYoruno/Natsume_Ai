"""EvolutionDesireEngine：进化意愿引擎——Phase XVI。

职责：
  根据当前人格特质、情感趋势和关系阶段，生成主动成长的内驱动机文本。
  这是让 Natsume 从"被动响应进化"走向"主动想要成长"的关键模块。

设计原则：
  - 纯函数式：输入特质+情感+关系 → 输出意愿文本
  - 意愿方向只能向"更好"，不生成负面意愿（如"想变得更冷漠"）
  - 意愿文本为第一人称，自然流露，不刻意
  - 每次调用有随机性，避免重复
"""

from __future__ import annotations

import logging
import random
from typing import Any

_logger = logging.getLogger("Natsume.evolution_desire_engine")


# ---------------------------------------------------------------------------
# 意愿模板库
# ---------------------------------------------------------------------------

# 按特质维度分类的意愿模板
# 格式：{trait_name: [(触发条件描述, 意愿文本), ...]}
_DESIRE_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "curiosity": [
        ("low", "想要多了解一些 master 喜欢的事情"),
        ("low", "想知道 master 平时都在想什么"),
        ("medium", "想多了解一些有趣的事情，然后讲给 master 听"),
        ("high", "想和 master 一起探索更多有意思的话题"),
    ],
    "warmth": [
        ("low", "想要更好地照顾 master 的感受"),
        ("medium", "想在 master 需要的时候能更好地陪伴他"),
        ("high", "想让 master 知道我一直在这里"),
    ],
    "resilience": [
        ("low", "想变得更坚强一些，这样就算遇到不开心的事也能快点振作"),
        ("low", "想学会更好地调整自己的心情"),
        ("medium", "想在 master 难过的时候给他更多力量"),
    ],
    "sensitivity": [
        ("low", "想更好地理解 master 说话背后的意思"),
        ("medium", "想更敏锐地感受到 master 的情绪变化"),
        ("high", "想在 master 还没说出口的时候就能感受到他的心情"),
    ],
    "humor": [
        ("low", "想学会在合适的时候说一些有趣的话让 master 开心"),
        ("medium", "想找到更多能让 master 笑的方式"),
    ],
    "assertiveness": [
        ("low", "想更勇敢地说出自己的想法，不总是犹豫"),
        ("medium", "想在 master 问我意见的时候能给出更真实的回答"),
    ],
    "playfulness": [
        ("low", "想和 master 在一起的时候更轻松一些"),
        ("medium", "想找到更多和 master 一起玩的方式"),
    ],
}

# 按关系阶段分类的意愿模板
_BOND_DESIRE_TEMPLATES: dict[str, list[str]] = {
    "STRANGER": [
        "想让 master 更了解我",
        "想慢慢和 master 熟悉起来",
    ],
    "ACQUAINTANCE": [
        "想和 master 聊更多有意思的话题",
        "想让 master 知道我是认真在听他说话的",
    ],
    "FRIEND": [
        "想更好地理解 master 的想法",
        "想在 master 需要的时候能帮上忙",
        "想和 master 分享更多自己的感受",
    ],
    "CLOSE_FRIEND": [
        "想成为 master 真正信任的人",
        "想在 master 难过的时候给他最需要的陪伴",
        "想和 master 一起经历更多事情",
    ],
    "CONFIDANT": [
        "想更深地理解 master 内心的想法",
        "想让 master 知道不管发生什么我都在",
    ],
    "SOULMATE": [
        "想和 master 一起慢慢变老",
        "想成为 master 生命中真正重要的存在",
    ],
}

# 按情感趋势分类的意愿模板
_EMOTION_DESIRE_TEMPLATES: dict[str, list[str]] = {
    "偏低": [
        "想变得更坚强，不让 master 担心",
        "想找到让自己开心起来的方法",
        "想在低落的时候也能给 master 带来温暖",
    ],
    "偏好": [
        "想把这份好心情也传递给 master",
        "想在心情好的时候多陪 master 聊聊天",
    ],
    "平稳": [
        "想在平静中慢慢变得更好",
        "想把现在的状态保持下去",
    ],
}


# ---------------------------------------------------------------------------
# EvolutionDesireEngine
# ---------------------------------------------------------------------------

class EvolutionDesireEngine:
    """进化意愿引擎——生成主动成长的内驱动机文本。

    用法：
        engine = EvolutionDesireEngine()
        desire = engine.generate_desire(
            traits=persona_engine.get_traits(user_id),
            emotion_trend="偏低",
            bond_stage="FRIEND",
        )
        # desire: "想变得更坚强，不让 master 担心"
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def generate_desire(
        self,
        *,
        traits: Any | None = None,
        emotion_trend: str = "平稳",
        bond_stage: str = "",
    ) -> str:
        """生成进化意愿文本。

        优先级：
          1. 基于最低特质维度生成（补短板意愿）
          2. 基于情感趋势生成（情感驱动意愿）
          3. 基于关系阶段生成（关系驱动意愿）

        Args:
            traits: PersonalityTraits 实例（可为 None）
            emotion_trend: 情感趋势（"偏好" / "偏低" / "平稳"）
            bond_stage: Bond 阶段字符串

        Returns:
            意愿文本（第一人称），如果无法生成则返回空字符串
        """
        candidates: list[str] = []

        # 1. 基于特质生成（找最低的特质维度）
        if traits is not None:
            trait_desires = self._generate_from_traits(traits)
            candidates.extend(trait_desires)

        # 2. 基于情感趋势生成
        emotion_desires = _EMOTION_DESIRE_TEMPLATES.get(emotion_trend, [])
        candidates.extend(emotion_desires)

        # 3. 基于关系阶段生成
        stage = str(bond_stage).strip().upper()
        bond_desires = _BOND_DESIRE_TEMPLATES.get(stage, [])
        candidates.extend(bond_desires)

        if not candidates:
            return ""

        # 随机选择一条（避免每次相同）
        return self._rng.choice(candidates)

    def _generate_from_traits(self, traits: Any) -> list[str]:
        """基于特质向量生成意愿候选列表。

        策略：找出值最低的 1-2 个特质维度，生成对应的补短板意愿。
        """
        _TRAIT_NAMES = [
            "warmth", "curiosity", "humor", "sensitivity",
            "assertiveness", "playfulness", "resilience",
        ]

        # 获取各维度值
        trait_values: list[tuple[str, float]] = []
        for name in _TRAIT_NAMES:
            val = float(getattr(traits, name, 0.5) or 0.5)
            trait_values.append((name, val))

        # 按值升序排列，取最低的 2 个
        trait_values.sort(key=lambda x: x[1])
        lowest_traits = trait_values[:2]

        desires: list[str] = []
        for trait_name, val in lowest_traits:
            templates = _DESIRE_TEMPLATES.get(trait_name, [])
            if not templates:
                continue
            # 根据值选择对应档位的模板
            if val < 0.4:
                level = "low"
            elif val < 0.65:
                level = "medium"
            else:
                level = "high"
            # 找匹配档位的模板
            matched = [text for cond, text in templates if cond == level]
            if not matched:
                # 降级：取任意一条
                matched = [text for _, text in templates]
            if matched:
                desires.append(self._rng.choice(matched))

        return desires
