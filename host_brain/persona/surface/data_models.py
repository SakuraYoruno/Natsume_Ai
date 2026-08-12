"""Data models for isolated surface response generation.

Extracted from isolated_surface_harness.py to improve single-responsibility.
This module contains only pure data definitions with minimal logic dependencies.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - pydantic v1 compatibility
    ConfigDict = None  # type: ignore[assignment]


if ConfigDict is not None:

    class _StrictModel(BaseModel):
        # 入口模型禁止额外字段，配合 input_validation 的递归检查形成两道边界：
        # 调用方无法通过新字段悄悄把未压缩上下文传给 Surface。
        model_config = ConfigDict(extra="forbid")

else:  # pragma: no cover - pydantic v1 compatibility

    class _StrictModel(BaseModel):
        class Config:
            extra = "forbid"


class ReplyControlPacket(_StrictModel):
    """生成器唯一接受的回复控制平面。

    事实、禁令与风格锚点是已压缩的不同语义通道；内在信号仅调节表层气质，
    不可被当作关于用户或现实世界的可断言内容。
    """
    response_move: str = "reply_current_turn"
    task_mode: str = "daily_chat"
    emotional_tone: str = "neutral"
    relationship_distance: str = "familiar"
    information_budget: str = "short"
    question_budget: int = 0
    must_include: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    style_anchor: list[str] = Field(default_factory=list)
    affective_continuity_kind: str = ""
    companion_utterance_frame: dict[str, Any] = Field(default_factory=dict)

    # --- Inner-state leakage fields (life-feel renovation) ---
    # Abstracted inner-life signals so the surface LLM can feel the character's
    # current inner weather without seeing raw private thoughts.
    inner_weather: str = Field(
        default="",
        description="1-2 sentence natural-language description of the character's "
        "current inner state (e.g. '有点闷闷的，想说点什么又不确定该不该开口').",
    )
    body_state_hint: str = Field(
        default="",
        description="Body-state hint derived from fatigue/drowsiness/loneliness "
        "(e.g. '困了，脑子转得慢').",
    )
    care_signal: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Care drive intensity [0.0, 1.0]. High values nudge the "
        "response toward warmth and concern.",
    )
    continuity_signal: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Continuity drive intensity [0.0, 1.0]. High values suggest "
        "picking up unfinished threads.",
    )
    tension_hint: str = Field(
        default="",
        description="Short description of active inner tension "
        "(e.g. '想靠近又怕打扰').",
    )
    liveliness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Overall liveliness / energy [0.0, 1.0]. Low values suggest "
        "slower, shorter, lower-energy replies.",
    )
    active_loops: int = Field(
        default=0,
        ge=0,
        description="Number of open conversation loops still on the character's mind.",
    )


class IsolatedSurfaceInput(_StrictModel):
    """一次隔离 Surface 生成的最小不可扩展请求。"""
    soul_md: str
    user_message: str
    control: ReplyControlPacket
    facts: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    # 规范任务结果独立于记忆和普通检索，只是暂时提示材料；权威副本已在 Task
    # ledger，模型序列化/trace 不得复制完整文本，避免工件扩散或成为长期记忆。
    accepted_task_results: list[str] = Field(
        default_factory=list,
        exclude=True,
        repr=False,
    )
    case_id: str = ""
    # --- Inner-state leakage fields (mirrored from ReplyControlPacket) ---
    inner_weather: str = ""
    body_state_hint: str = ""
    care_signal: float = 0.0
    continuity_signal: float = 0.0
    tension_hint: str = ""
    liveliness: float = 0.5
    active_loops: int = 0


@dataclass(frozen=True)
class IsolatedCandidateScore:
    """候选文本的质量判定快照，保留拒绝理由以支持可解释回退。"""
    text: str
    score: float
    passed: bool
    hard_fail: bool
    reason_codes: tuple[str, ...]
    control_violations: tuple[str, ...]
    dimensions: Mapping[str, float]
    quality: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": round(float(self.score or 0.0), 3),
            "passed": bool(self.passed),
            "hard_fail": bool(self.hard_fail),
            "reason_codes": list(self.reason_codes),
            "control_violations": list(self.control_violations),
            "dimensions": {
                key: round(float(value), 3)
                for key, value in dict(self.dimensions).items()
            },
            "quality": dict(self.quality),
        }


@dataclass(frozen=True)
class IsolatedSurfaceRunResult:
    """隔离生成的原始输出、候选与最终选择，用于运行时审计而非再次提示模型。"""
    messages: tuple[Mapping[str, str], ...]
    raw_output: str
    candidates: tuple[IsolatedCandidateScore, ...]
    selected: IsolatedCandidateScore | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "messages": [dict(item) for item in self.messages],
            "raw_output": self.raw_output,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "selected": self.selected.as_dict() if self.selected else None,
        }


@dataclass(frozen=True)
class RelationshipStyleProfile:
    stage: str
    prompt_lines: tuple[str, ...]
    prefer_markers: tuple[str, ...] = ()
    overstep_markers: tuple[str, ...] = ()
    underplay_markers: tuple[str, ...] = ()
    affection_markers: tuple[str, ...] = ()
    strong_affection_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipControlAxes:
    stage: str
    progress: int
    distance: str
    initiative: str
    tease_permission: str
    affection_visibility: str
    care_intensity: str
    vulnerability: str
    nickname_policy: str
    physical_intimacy_allowed: bool
    explicit_romance_allowed: bool
    commitment_language_allowed: bool
    generation_notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "progress": int(self.progress),
            "distance": self.distance,
            "initiative": self.initiative,
            "tease_permission": self.tease_permission,
            "affection_visibility": self.affection_visibility,
            "care_intensity": self.care_intensity,
            "vulnerability": self.vulnerability,
            "nickname_policy": self.nickname_policy,
            "physical_intimacy_allowed": bool(self.physical_intimacy_allowed),
            "explicit_romance_allowed": bool(self.explicit_romance_allowed),
            "commitment_language_allowed": bool(self.commitment_language_allowed),
            "generation_notes": list(self.generation_notes),
        }


GenerateSurface = Callable[[list[dict[str, str]]], str | Awaitable[str]]

_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…\u201c\u201d\"'：:；;（）()\[\]{}]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_surface_chat_punctuation(text: str) -> str:
    # 仅在含中文时修正中英标点混排，避免改变代码、URL 或其它非中文载荷；这是
    # 展示层规范化，不能承担内容修复或绕过质量门。
    value = str(text or "").strip()
    if not value or not _CJK_CHAR_RE.search(value):
        return value
    value = re.sub(r"(?<=[\u4e00-\u9fff])\?(?=[\u4e00-\u9fff]|$)", "？", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])!(?=[\u4e00-\u9fff]|$)", "！", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])", "，", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff]|$)", "。", value)
    return value
