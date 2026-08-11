"""VTS 外显确定性映射（M14-B）。

emotion_tag → expression_id 固定枚举映射；拒绝非法 tag 或自由动作参数；
同版本同输入输出一致（可回放）。

覆盖需求：FR-AVATAR-001
测试编号：T-VTS-01~05
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from natsume_contracts.edge.v1 import (
    VTS_DURATION_INVALID,
    VTS_EXPRESSION_INVALID,
    VTS_INTENSITY_OUT_OF_RANGE,
    VTS_TAG_UNKNOWN,
    new_request_id,
    now_ms,
)


# ---------------------------------------------------------------------------
# 固定枚举（不允许自由扩展）
# ---------------------------------------------------------------------------

class EmotionTag(str, Enum):
    """合法 emotion_tag 枚举（固定集合，不接受自由字符串）。"""
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    FEARFUL   = "fearful"
    DISGUSTED = "disgusted"
    EXCITED   = "excited"
    CALM      = "calm"


# 固定映射表（同版本同输入输出一致，不可运行时修改）
EXPRESSION_MAP: dict[EmotionTag, str] = {
    EmotionTag.NEUTRAL:   "exp_neutral_01",
    EmotionTag.HAPPY:     "exp_happy_01",
    EmotionTag.SAD:       "exp_sad_01",
    EmotionTag.ANGRY:     "exp_angry_01",
    EmotionTag.SURPRISED: "exp_surprised_01",
    EmotionTag.FEARFUL:   "exp_fearful_01",
    EmotionTag.DISGUSTED: "exp_disgusted_01",
    EmotionTag.EXCITED:   "exp_excited_01",
    EmotionTag.CALM:      "exp_calm_01",
}

SUB_EMOTION_HOTKEY_MAP: dict[str, str] = {
    "blush": "hotkey_exp_blush",
    "star_eye": "hotkey_exp_star_eye",
    "heart_eye": "hotkey_exp_heart_eye",
    "cry": "hotkey_exp_cry",
    "scissors_eye": "hotkey_exp_scissors_eye",
    "dizzy": "hotkey_exp_dizzy",
    "disdain": "hotkey_exp_disdain_dark",
    "peace_sign": "hotkey_exp_peace_sign",
}


def sub_emotion_to_hotkey(sub_emotion: str | None) -> str | None:
    normalized = str(sub_emotion or "").strip().lower()
    return SUB_EMOTION_HOTKEY_MAP.get(normalized) if normalized else None

# 合法 tag 字符串集合（大小写不敏感查找用）
_VALID_TAGS_LOWER: dict[str, EmotionTag] = {tag.value.lower(): tag for tag in EmotionTag}


# ---------------------------------------------------------------------------
# ExpressionTrigger
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpressionTrigger:
    """一次外显触发的完整描述（可回放）。"""
    emotion_tag: EmotionTag
    expression_id: str          # 来自 EXPRESSION_MAP，固定值
    intensity: float            # 0.0~1.0
    duration_ms: int            # > 0
    request_id: str
    ts_ms: int


# ---------------------------------------------------------------------------
# VTSExpressionMap
# ---------------------------------------------------------------------------

class VTSExpressionMap:
    """
    VTS 外显确定性映射器。

    - 只接受 EmotionTag 枚举值（大小写不敏感）
    - 拒绝自由动作参数或未知 tag
    - 同版本同输入输出一致（EXPRESSION_MAP 不可变）
    """

    def resolve(
        self,
        emotion_tag: str,
        *,
        intensity: float = 1.0,
        duration_ms: int = 2000,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> ExpressionTrigger:
        """
        将 emotion_tag 映射为 ExpressionTrigger。

        Raises:
            ValueError: 携带 reason_code 字段，指示具体错误类型。
        """
        req_id = request_id or new_request_id()
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # 校验 intensity
        if not isinstance(intensity, (int, float)) or not (0.0 <= float(intensity) <= 1.0):
            err = ValueError(f"intensity must be in [0.0, 1.0], got {intensity!r}")
            err.reason_code = VTS_INTENSITY_OUT_OF_RANGE  # type: ignore[attr-defined]
            raise err

        # 校验 duration_ms
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            err = ValueError(f"duration_ms must be > 0, got {duration_ms!r}")
            err.reason_code = VTS_DURATION_INVALID  # type: ignore[attr-defined]
            raise err

        # 校验 emotion_tag 类型（拒绝非字符串的自由动作参数）
        if not isinstance(emotion_tag, str):
            err = ValueError(f"emotion_tag must be a string, got {type(emotion_tag).__name__!r}")
            err.reason_code = VTS_EXPRESSION_INVALID  # type: ignore[attr-defined]
            raise err

        # 查找枚举（大小写不敏感）
        tag_enum = _VALID_TAGS_LOWER.get(emotion_tag.strip().lower())
        if tag_enum is None:
            err = ValueError(f"unknown emotion_tag: {emotion_tag!r}")
            err.reason_code = VTS_TAG_UNKNOWN  # type: ignore[attr-defined]
            raise err

        expression_id = EXPRESSION_MAP[tag_enum]

        return ExpressionTrigger(
            emotion_tag=tag_enum,
            expression_id=expression_id,
            intensity=float(intensity),
            duration_ms=duration_ms,
            request_id=req_id,
            ts_ms=now_value,
        )

    def list_tags(self) -> list[str]:
        """返回所有合法 emotion_tag 枚举值（小写）。"""
        return [tag.value for tag in EmotionTag]
