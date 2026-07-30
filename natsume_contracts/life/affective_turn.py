"""Immutable turn plans for Natsume's affective Surface.

The model is allowed to choose wording only.  It must never manufacture the
character's current emotion, relationship permission, regulation decision, or
embodiment state.  Those decisions are frozen in an ``AffectiveTurnPlanV1``
before text generation starts and are safe to persist with a Life event.

The contracts intentionally contain only bounded identifiers, revisions and
expression controls.  Raw user messages, raw audio/video, chain-of-thought,
and private memory bodies do not belong here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SELF_ID = "natsume_ai"
_COMPANION_ACT_KINDS = frozenset(
    {
        "COMFORT",
        "PLAY",
        "REPAIR",
        "SET_BOUNDARY",
        "GIVE_SPACE",
        "SILENCE",
        "SOLVE",
        "ACKNOWLEDGE",
        "HOLD_COMPLEXITY",
    }
)
_TEXT_PACING = frozenset({"STEADY", "SOFT", "BRISK", "SPARSE"})
_GENERATION_MODES = frozenset({"STREAM_ONE", "BUFFERED_CANDIDATE_SELECTION"})
_AVATAR_GAZES = frozenset({"ATTENTIVE", "SOFT", "AVERTED", "OPEN", "STEADY"})
_AVATAR_POSTURES = frozenset(
    {"RELAXED", "OPEN_GENTLE", "CONTAINED", "GROUNDED", "LIGHT", "STILL"}
)
_PREVIOUS_EXPRESSION_SOURCES = frozenset(
    {"DURABLE_LAST_ACCEPTED", "FALLBACK_NEUTRAL", "UNAVAILABLE"}
)
_COMPANION_PROFILE_STAGES = frozenset(
    {"BASELINE", "CANARY", "ACTIVE", "COMPATIBILITY_UNBOUND"}
)


def _required(value: Any, field_name: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _optional(value: Any, field_name: str, *, limit: int = 240) -> str | None:
    if value in (None, ""):
        return None
    return _required(value, field_name, limit=limit)


def _unit(value: Any, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return round(normalized, 6)


def _signed_unit(value: Any, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not -1.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between -1 and 1")
    return round(normalized, 6)


def _bounded_float(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return round(normalized, 6)


def _bounded_int(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    normalized = _non_negative_int(value, field_name)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return normalized


def _refs(value: Any, field_name: str, *, maximum: int = 16) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a list")
    output = tuple(
        _required(item, f"{field_name}[{index}]", limit=512)
        for index, item in enumerate(value)
    )
    if len(output) > maximum:
        raise ValueError(f"{field_name} cannot contain more than {maximum} items")
    if len(set(output)) != len(output):
        raise ValueError(f"{field_name} must not contain duplicates")
    return output


def _sha256(value: Any, field_name: str) -> str:
    text = _required(value, field_name, limit=64).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field_name} must be a sha256 hex digest")
    return text


@dataclass(frozen=True, slots=True)
class CompanionActV1:
    """A bounded companionship action selected before prose generation."""

    companion_act_id: str
    relationship_subject_id: str
    kind: str
    intensity: float
    reason_refs: Sequence[str]
    created_at_ms: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        kind = _required(self.kind, "kind", limit=64).upper()
        if kind not in _COMPANION_ACT_KINDS:
            raise ValueError(f"kind must be one of {sorted(_COMPANION_ACT_KINDS)}")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        object.__setattr__(self, "companion_act_id", _required(self.companion_act_id, "companion_act_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        object.__setattr__(self, "reason_refs", _refs(self.reason_refs, "reason_refs"))
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_act.v1",
            "companion_act_id": self.companion_act_id,
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "kind": self.kind,
            "intensity": self.intensity,
            "reason_refs": list(self.reason_refs),
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ExpressionPlanV1:
    """One immutable expression revision shared by text, voice and avatar.

    The plan is intentionally an *instruction*, not a receipt that claims a
    remote TTS engine or avatar has already applied it.  Delivery receipts are
    recorded separately.  Keeping all three modalities here prevents a model
    response, a legacy text emotion parser, or a later channel failure from
    silently rewriting Natsume's frozen self-affect.
    """

    expression_plan_id: str
    synchronization_id: str
    self_affect_revision: int
    text_pacing: str
    voice_style: str
    avatar_expression: str
    regulation_mode: str
    created_at_ms: int
    voice_rate: float = 1.0
    voice_energy: float = 0.5
    voice_pitch: float = 0.0
    voice_pause_ms: int = 120
    avatar_gaze: str = "ATTENTIVE"
    avatar_posture: str = "RELAXED"
    avatar_transition_ms: int = 240
    avatar_behavior: str = "CALM_ATTENTIVE"
    avatar_motion_intensity: float = 0.5
    companion_profile_id: str = ""
    companion_profile_digest: str = ""
    previous_avatar_expression: str = "NEUTRAL"
    previous_expression_source: str = "FALLBACK_NEUTRAL"

    def __post_init__(self) -> None:
        pacing = _required(self.text_pacing, "text_pacing", limit=48).upper()
        if pacing not in _TEXT_PACING:
            raise ValueError(f"text_pacing must be one of {sorted(_TEXT_PACING)}")
        object.__setattr__(self, "expression_plan_id", _required(self.expression_plan_id, "expression_plan_id"))
        object.__setattr__(self, "synchronization_id", _required(self.synchronization_id, "synchronization_id"))
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "text_pacing", pacing)
        object.__setattr__(self, "voice_style", _required(self.voice_style, "voice_style", limit=64).upper())
        object.__setattr__(
            self,
            "avatar_expression",
            _required(self.avatar_expression, "avatar_expression", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "regulation_mode",
            _required(self.regulation_mode, "regulation_mode", limit=64).upper(),
        )
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(
            self,
            "voice_rate",
            _bounded_float(
                self.voice_rate,
                "voice_rate",
                minimum=0.7,
                maximum=1.3,
            ),
        )
        object.__setattr__(self, "voice_energy", _unit(self.voice_energy, "voice_energy"))
        object.__setattr__(self, "voice_pitch", _signed_unit(self.voice_pitch, "voice_pitch"))
        object.__setattr__(
            self,
            "voice_pause_ms",
            _bounded_int(
                self.voice_pause_ms,
                "voice_pause_ms",
                minimum=0,
                maximum=3_000,
            ),
        )
        gaze = _required(self.avatar_gaze, "avatar_gaze", limit=64).upper()
        if gaze not in _AVATAR_GAZES:
            raise ValueError(f"avatar_gaze must be one of {sorted(_AVATAR_GAZES)}")
        posture = _required(self.avatar_posture, "avatar_posture", limit=64).upper()
        if posture not in _AVATAR_POSTURES:
            raise ValueError(f"avatar_posture must be one of {sorted(_AVATAR_POSTURES)}")
        object.__setattr__(self, "avatar_gaze", gaze)
        object.__setattr__(self, "avatar_posture", posture)
        object.__setattr__(
            self,
            "avatar_transition_ms",
            _bounded_int(
                self.avatar_transition_ms,
                "avatar_transition_ms",
                minimum=0,
                maximum=5_000,
            ),
        )
        object.__setattr__(
            self,
            "avatar_behavior",
            _required(self.avatar_behavior, "avatar_behavior", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "avatar_motion_intensity",
            _unit(self.avatar_motion_intensity, "avatar_motion_intensity"),
        )
        profile_id = str(self.companion_profile_id or "").strip()
        profile_digest = str(self.companion_profile_digest or "").strip()
        if bool(profile_id) != bool(profile_digest):
            raise ValueError("expression companion profile id and digest must appear together")
        if profile_id:
            object.__setattr__(
                self,
                "companion_profile_id",
                _required(profile_id, "companion_profile_id", limit=240),
            )
            object.__setattr__(
                self,
                "companion_profile_digest",
                _sha256(profile_digest, "companion_profile_digest"),
            )
        else:
            object.__setattr__(self, "companion_profile_id", "")
            object.__setattr__(self, "companion_profile_digest", "")
        object.__setattr__(
            self,
            "previous_avatar_expression",
            _required(
                self.previous_avatar_expression,
                "previous_avatar_expression",
                limit=64,
            ).upper(),
        )
        previous_source = _required(
            self.previous_expression_source,
            "previous_expression_source",
            limit=64,
        ).upper()
        if previous_source not in _PREVIOUS_EXPRESSION_SOURCES:
            raise ValueError(
                "previous_expression_source must be one of "
                f"{sorted(_PREVIOUS_EXPRESSION_SOURCES)}"
            )
        object.__setattr__(self, "previous_expression_source", previous_source)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "expression_plan.v1",
            "expression_plan_id": self.expression_plan_id,
            "synchronization_id": self.synchronization_id,
            "self_affect_revision": self.self_affect_revision,
            "text_pacing": self.text_pacing,
            "voice_style": self.voice_style,
            "avatar_expression": self.avatar_expression,
            "regulation_mode": self.regulation_mode,
            "created_at_ms": self.created_at_ms,
            "voice_rate": self.voice_rate,
            "voice_energy": self.voice_energy,
            "voice_pitch": self.voice_pitch,
            "voice_pause_ms": self.voice_pause_ms,
            "avatar_gaze": self.avatar_gaze,
            "avatar_posture": self.avatar_posture,
            "avatar_transition_ms": self.avatar_transition_ms,
            "avatar_behavior": self.avatar_behavior,
            "avatar_motion_intensity": self.avatar_motion_intensity,
            "companion_profile_id": self.companion_profile_id,
            "companion_profile_digest": self.companion_profile_digest,
            "previous_avatar_expression": self.previous_avatar_expression,
            "previous_expression_source": self.previous_expression_source,
        }


@dataclass(frozen=True, slots=True)
class AffectiveTurnPlanV1:
    """Frozen causal plan for one user-visible Natsume turn."""

    plan_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    request_id: str
    self_affect_revision: int
    self_affect_digest: str
    self_emotion_tag: str
    relationship_revision: int
    regulation_ref: str | None
    companion_act: CompanionActV1
    expression_plan: ExpressionPlanV1
    generation_mode: str
    candidate_count: int
    created_at_ms: int
    self_id: str = _SELF_ID
    companion_profile_id: str = ""
    companion_profile_digest: str = ""
    companion_profile_revision: int = 0
    companion_profile_stage: str = "COMPATIBILITY_UNBOUND"

    def __post_init__(self) -> None:
        mode = _required(self.generation_mode, "generation_mode", limit=64).upper()
        if mode not in _GENERATION_MODES:
            raise ValueError(f"generation_mode must be one of {sorted(_GENERATION_MODES)}")
        candidate_count = _non_negative_int(self.candidate_count, "candidate_count")
        if candidate_count not in {1, 2}:
            raise ValueError("candidate_count must be one or two")
        if mode == "STREAM_ONE" and candidate_count != 1:
            raise ValueError("STREAM_ONE requires candidate_count=1")
        if mode == "BUFFERED_CANDIDATE_SELECTION" and candidate_count != 2:
            raise ValueError("BUFFERED_CANDIDATE_SELECTION requires candidate_count=2")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        if not isinstance(self.companion_act, CompanionActV1):
            raise ValueError("companion_act must be CompanionActV1")
        if not isinstance(self.expression_plan, ExpressionPlanV1):
            raise ValueError("expression_plan must be ExpressionPlanV1")
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "self_affect_digest", _sha256(self.self_affect_digest, "self_affect_digest"))
        object.__setattr__(
            self,
            "self_emotion_tag",
            _required(self.self_emotion_tag, "self_emotion_tag", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "relationship_revision",
            _non_negative_int(self.relationship_revision, "relationship_revision"),
        )
        object.__setattr__(self, "regulation_ref", _optional(self.regulation_ref, "regulation_ref", limit=512))
        object.__setattr__(self, "generation_mode", mode)
        object.__setattr__(self, "candidate_count", candidate_count)
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "self_id", _SELF_ID)
        profile_id = str(self.companion_profile_id or "").strip()
        profile_digest = str(self.companion_profile_digest or "").strip()
        stage = _required(
            self.companion_profile_stage,
            "companion_profile_stage",
            limit=64,
        ).upper()
        if stage not in _COMPANION_PROFILE_STAGES:
            raise ValueError(
                "companion_profile_stage must be one of "
                f"{sorted(_COMPANION_PROFILE_STAGES)}"
            )
        revision = _non_negative_int(
            self.companion_profile_revision,
            "companion_profile_revision",
        )
        if bool(profile_id) != bool(profile_digest):
            raise ValueError("turn companion profile id and digest must appear together")
        if not profile_id and stage != "COMPATIBILITY_UNBOUND":
            raise ValueError("bound companion profile stage requires profile identity")
        if profile_id:
            object.__setattr__(
                self,
                "companion_profile_id",
                _required(profile_id, "companion_profile_id", limit=240),
            )
            object.__setattr__(
                self,
                "companion_profile_digest",
                _sha256(profile_digest, "companion_profile_digest"),
            )
        else:
            object.__setattr__(self, "companion_profile_id", "")
            object.__setattr__(self, "companion_profile_digest", "")
        object.__setattr__(self, "companion_profile_revision", revision)
        object.__setattr__(self, "companion_profile_stage", stage)
        if self.companion_act.relationship_subject_id != self.relationship_subject_id:
            raise ValueError("companion_act relationship subject differs from turn plan")
        if self.expression_plan.self_affect_revision != self.self_affect_revision:
            raise ValueError("expression plan affect revision differs from turn plan")
        if (
            self.expression_plan.companion_profile_id
            != self.companion_profile_id
            or self.expression_plan.companion_profile_digest
            != self.companion_profile_digest
        ):
            raise ValueError("expression plan companion profile differs from turn plan")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "affective_turn_plan.v1",
            "plan_id": self.plan_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "self_affect_revision": self.self_affect_revision,
            "self_affect_digest": self.self_affect_digest,
            "self_emotion_tag": self.self_emotion_tag,
            "relationship_revision": self.relationship_revision,
            "regulation_ref": self.regulation_ref,
            "companion_act": self.companion_act.as_dict(),
            "expression_plan": self.expression_plan.as_dict(),
            "generation_mode": self.generation_mode,
            "candidate_count": self.candidate_count,
            "created_at_ms": self.created_at_ms,
            "companion_profile_id": self.companion_profile_id,
            "companion_profile_digest": self.companion_profile_digest,
            "companion_profile_revision": self.companion_profile_revision,
            "companion_profile_stage": self.companion_profile_stage,
        }


__all__ = [
    "AffectiveTurnPlanV1",
    "CompanionActV1",
    "ExpressionPlanV1",
]
