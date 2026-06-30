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
        model_config = ConfigDict(extra="forbid")

else:  # pragma: no cover - pydantic v1 compatibility

    class _StrictModel(BaseModel):
        class Config:
            extra = "forbid"


class ReplyControlPacket(_StrictModel):
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
    soul_md: str
    user_message: str
    control: ReplyControlPacket
    facts: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
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
    value = str(text or "").strip()
    if not value or not _CJK_CHAR_RE.search(value):
        return value
    value = re.sub(r"(?<=[\u4e00-\u9fff])\?(?=[\u4e00-\u9fff]|$)", "？", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])!(?=[\u4e00-\u9fff]|$)", "！", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])", "，", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff]|$)", "。", value)
    return value
