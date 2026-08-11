"""Versioned, runtime-neutral contracts for Seven Seas' affective life.

The contracts deliberately describe three different things:

* observations about the relationship subject;
* Natsume's appraised, self-owned affect; and
* a regulation decision about how that affect may be expressed.

They contain only bounded, privacy-safe summaries and opaque evidence
references.  Raw conversation, audio, image and model-chain material never
belongs in this contract surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SELF_ID = "natsume_ai"
_MAX_COMPONENTS = 8
_MAX_ACTIVE_EPISODES = 4
_MAX_EVIDENCE_REFS = 16
_MAX_OBSERVATION_REFS = 8
_MODALITIES = frozenset({"TEXT", "SELF_REPORT", "ACOUSTIC", "VISUAL"})
_REGULATION_MODES = frozenset(
    {
        "EXPRESS",
        "CONTAIN",
        "DEFER",
        "GROUND",
        "SEEK_REPAIR",
        "GIVE_SPACE",
        "QUIET_PRESENCE",
    }
)


def _required(value: Any, name: str, *, limit: int = 240) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return normalized


def _optional(value: Any, name: str, *, limit: int = 240) -> str | None:
    if value in (None, ""):
        return None
    return _required(value, name, limit=limit)


def _unit(value: Any, name: str, *, default: float = 0.0) -> float:
    try:
        normalized = float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return round(normalized, 6)


def _signed_unit(value: Any, name: str, *, default: float = 0.0) -> float:
    try:
        normalized = float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not -1.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between -1 and 1")
    return round(normalized, 6)


def _non_negative_int(value: Any, name: str, *, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if normalized < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return normalized


def _opaque_refs(
    value: Any,
    name: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a list")
    normalized = tuple(
        _required(item, f"{name}[{index}]", limit=512)
        for index, item in enumerate(value)
    )
    if len(normalized) > maximum:
        raise ValueError(f"{name} cannot contain more than {maximum} items")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


def _distribution(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("emotion_distribution must be an object")
    if not value or len(value) > _MAX_COMPONENTS:
        raise ValueError(
            f"emotion_distribution must contain 1 to {_MAX_COMPONENTS} entries"
        )
    normalized: dict[str, float] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = _required(raw_key, "emotion_distribution key", limit=48).upper()
        normalized[key] = _unit(value[raw_key], f"emotion_distribution.{key}")
    return normalized


@dataclass(frozen=True)
class UserAffectObservationV1:
    """A bounded observation about the user, not Natsume's own affect."""

    observation_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    modality: str
    emotion_distribution: Mapping[str, float]
    valence: float
    arousal: float
    confidence: float
    observed_at_ms: int
    expires_at_ms: int
    evidence_refs: Sequence[str] = ()
    conflict_refs: Sequence[str] = ()

    def __post_init__(self) -> None:
        modality = _required(self.modality, "modality", limit=48).upper()
        if modality not in _MODALITIES:
            raise ValueError(f"modality must be one of {sorted(_MODALITIES)}")
        observed_at = _non_negative_int(self.observed_at_ms, "observed_at_ms")
        expires_at = _non_negative_int(self.expires_at_ms, "expires_at_ms")
        if expires_at < observed_at:
            raise ValueError("expires_at_ms cannot precede observed_at_ms")
        object.__setattr__(self, "observation_id", _required(self.observation_id, "observation_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "modality", modality)
        object.__setattr__(self, "emotion_distribution", _distribution(self.emotion_distribution))
        object.__setattr__(self, "valence", _signed_unit(self.valence, "valence"))
        object.__setattr__(self, "arousal", _unit(self.arousal, "arousal"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "observed_at_ms", observed_at)
        object.__setattr__(self, "expires_at_ms", expires_at)
        object.__setattr__(
            self,
            "evidence_refs",
            _opaque_refs(self.evidence_refs, "evidence_refs", maximum=_MAX_EVIDENCE_REFS),
        )
        object.__setattr__(
            self,
            "conflict_refs",
            _opaque_refs(self.conflict_refs, "conflict_refs", maximum=_MAX_OBSERVATION_REFS),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "user_affect_observation.v1",
            "observation_id": self.observation_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "modality": self.modality,
            "emotion_distribution": dict(self.emotion_distribution),
            "valence": self.valence,
            "arousal": self.arousal,
            "confidence": self.confidence,
            "observed_at_ms": self.observed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "evidence_refs": list(self.evidence_refs),
            "conflict_refs": list(self.conflict_refs),
        }


@dataclass(frozen=True)
class EventAppraisalV1:
    """Natsume's causal interpretation of an event before self-affect changes."""

    appraisal_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    event_kind: str
    target_ref: str
    goal_congruence: float
    responsibility: float
    controllability: float
    relationship_significance: float
    boundary_relevance: float
    risk: float
    uncertainty: float
    evidence_refs: Sequence[str] = ()
    observation_refs: Sequence[str] = ()
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        object.__setattr__(self, "appraisal_id", _required(self.appraisal_id, "appraisal_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "event_kind", _required(self.event_kind, "event_kind", limit=96).lower())
        object.__setattr__(self, "target_ref", _required(self.target_ref, "target_ref", limit=512))
        object.__setattr__(self, "goal_congruence", _signed_unit(self.goal_congruence, "goal_congruence"))
        object.__setattr__(self, "responsibility", _unit(self.responsibility, "responsibility"))
        object.__setattr__(self, "controllability", _unit(self.controllability, "controllability"))
        object.__setattr__(
            self,
            "relationship_significance",
            _unit(self.relationship_significance, "relationship_significance"),
        )
        object.__setattr__(self, "boundary_relevance", _unit(self.boundary_relevance, "boundary_relevance"))
        object.__setattr__(self, "risk", _unit(self.risk, "risk"))
        object.__setattr__(self, "uncertainty", _unit(self.uncertainty, "uncertainty"))
        object.__setattr__(
            self,
            "evidence_refs",
            _opaque_refs(self.evidence_refs, "evidence_refs", maximum=_MAX_EVIDENCE_REFS),
        )
        object.__setattr__(
            self,
            "observation_refs",
            _opaque_refs(
                self.observation_refs,
                "observation_refs",
                maximum=_MAX_OBSERVATION_REFS,
            ),
        )
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "event_appraisal.v1",
            "appraisal_id": self.appraisal_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "event_kind": self.event_kind,
            "target_ref": self.target_ref,
            "goal_congruence": self.goal_congruence,
            "responsibility": self.responsibility,
            "controllability": self.controllability,
            "relationship_significance": self.relationship_significance,
            "boundary_relevance": self.boundary_relevance,
            "risk": self.risk,
            "uncertainty": self.uncertainty,
            "evidence_refs": list(self.evidence_refs),
            "observation_refs": list(self.observation_refs),
        }


@dataclass(frozen=True)
class EmotionComponentV1:
    """One component of a mixed, self-owned emotional state."""

    emotion: str
    intensity: float
    valence: float
    arousal: float
    action_tendency: str = "observe"

    def __post_init__(self) -> None:
        object.__setattr__(self, "emotion", _required(self.emotion, "emotion", limit=48).upper())
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        object.__setattr__(self, "valence", _signed_unit(self.valence, "valence"))
        object.__setattr__(self, "arousal", _unit(self.arousal, "arousal"))
        object.__setattr__(
            self,
            "action_tendency",
            _required(self.action_tendency, "action_tendency", limit=64).lower(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "emotion": self.emotion,
            "intensity": self.intensity,
            "valence": self.valence,
            "arousal": self.arousal,
            "action_tendency": self.action_tendency,
        }


@dataclass(frozen=True)
class EmotionEpisodeV1:
    """An appraised, decaying episode rather than a permanent trait."""

    episode_id: str
    appraisal_id: str
    relationship_subject_id: str
    trigger_ref: str
    components: Sequence[EmotionComponentV1]
    opened_at_ms: int
    decay_half_life_ms: int
    evidence_refs: Sequence[str] = ()
    unresolved: bool = True
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        components = tuple(self.components)
        if not components or len(components) > _MAX_COMPONENTS:
            raise ValueError(f"components must contain 1 to {_MAX_COMPONENTS} items")
        if not all(isinstance(component, EmotionComponentV1) for component in components):
            raise ValueError("components must contain EmotionComponentV1 values")
        if len({component.emotion for component in components}) != len(components):
            raise ValueError("components must not repeat an emotion")
        object.__setattr__(self, "episode_id", _required(self.episode_id, "episode_id"))
        object.__setattr__(self, "appraisal_id", _required(self.appraisal_id, "appraisal_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "trigger_ref", _required(self.trigger_ref, "trigger_ref", limit=512))
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "opened_at_ms", _non_negative_int(self.opened_at_ms, "opened_at_ms"))
        object.__setattr__(
            self,
            "decay_half_life_ms",
            _non_negative_int(self.decay_half_life_ms, "decay_half_life_ms", minimum=1_000),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _opaque_refs(self.evidence_refs, "evidence_refs", maximum=_MAX_EVIDENCE_REFS),
        )
        object.__setattr__(self, "unresolved", bool(self.unresolved))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "emotion_episode.v1",
            "episode_id": self.episode_id,
            "appraisal_id": self.appraisal_id,
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "trigger_ref": self.trigger_ref,
            "components": [component.as_dict() for component in self.components],
            "opened_at_ms": self.opened_at_ms,
            "decay_half_life_ms": self.decay_half_life_ms,
            "evidence_refs": list(self.evidence_refs),
            "unresolved": self.unresolved,
        }


@dataclass(frozen=True)
class MoodStateV1:
    label: str
    valence: float
    arousal: float
    energy: float
    updated_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _required(self.label, "label", limit=48).lower())
        object.__setattr__(self, "valence", _signed_unit(self.valence, "valence"))
        object.__setattr__(self, "arousal", _unit(self.arousal, "arousal"))
        object.__setattr__(self, "energy", _unit(self.energy, "energy"))
        object.__setattr__(self, "updated_at_ms", _non_negative_int(self.updated_at_ms, "updated_at_ms"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "mood_state.v1",
            "label": self.label,
            "valence": self.valence,
            "arousal": self.arousal,
            "energy": self.energy,
            "updated_at_ms": self.updated_at_ms,
        }


@dataclass(frozen=True)
class NeedStateV1:
    connection: float
    rest: float
    autonomy: float
    play: float
    repair: float
    safety: float
    updated_at_ms: int

    def __post_init__(self) -> None:
        for name in ("connection", "rest", "autonomy", "play", "repair", "safety"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        object.__setattr__(self, "updated_at_ms", _non_negative_int(self.updated_at_ms, "updated_at_ms"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "need_state.v1",
            "connection": self.connection,
            "rest": self.rest,
            "autonomy": self.autonomy,
            "play": self.play,
            "repair": self.repair,
            "safety": self.safety,
            "updated_at_ms": self.updated_at_ms,
        }


@dataclass(frozen=True)
class RegulationDecisionV1:
    regulation_id: str
    relationship_subject_id: str
    mode: str
    reason_refs: Sequence[str]
    decided_at_ms: int
    episode_id: str | None = None
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        mode = _required(self.mode, "mode", limit=48).upper()
        if mode not in _REGULATION_MODES:
            raise ValueError(f"mode must be one of {sorted(_REGULATION_MODES)}")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        object.__setattr__(self, "regulation_id", _required(self.regulation_id, "regulation_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "reason_refs",
            _opaque_refs(self.reason_refs, "reason_refs", maximum=_MAX_EVIDENCE_REFS),
        )
        object.__setattr__(self, "decided_at_ms", _non_negative_int(self.decided_at_ms, "decided_at_ms"))
        object.__setattr__(self, "episode_id", _optional(self.episode_id, "episode_id"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "regulation_decision.v1",
            "regulation_id": self.regulation_id,
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "mode": self.mode,
            "reason_refs": list(self.reason_refs),
            "decided_at_ms": self.decided_at_ms,
            "episode_id": self.episode_id,
        }


@dataclass(frozen=True)
class SelfAffectStateV2:
    """Bounded snapshot head; detailed causes remain in the append-only journal."""

    relationship_subject_id: str
    revision: int
    components: Sequence[EmotionComponentV1]
    active_episodes: Sequence[EmotionEpisodeV1]
    mood: MoodStateV1
    needs: NeedStateV1
    last_integrated_ms: int
    last_regulation: RegulationDecisionV1 | None = None
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        components = tuple(self.components)
        episodes = tuple(self.active_episodes)
        if len(components) > _MAX_COMPONENTS:
            raise ValueError(f"components cannot exceed {_MAX_COMPONENTS}")
        if len(episodes) > _MAX_ACTIVE_EPISODES:
            raise ValueError(f"active_episodes cannot exceed {_MAX_ACTIVE_EPISODES}")
        if not all(isinstance(component, EmotionComponentV1) for component in components):
            raise ValueError("components must contain EmotionComponentV1 values")
        if not all(isinstance(episode, EmotionEpisodeV1) for episode in episodes):
            raise ValueError("active_episodes must contain EmotionEpisodeV1 values")
        if not isinstance(self.mood, MoodStateV1):
            raise ValueError("mood must be MoodStateV1")
        if not isinstance(self.needs, NeedStateV1):
            raise ValueError("needs must be NeedStateV1")
        if self.last_regulation is not None and not isinstance(
            self.last_regulation,
            RegulationDecisionV1,
        ):
            raise ValueError("last_regulation must be RegulationDecisionV1 or None")
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "revision", _non_negative_int(self.revision, "revision"))
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "active_episodes", episodes)
        object.__setattr__(
            self,
            "last_integrated_ms",
            _non_negative_int(self.last_integrated_ms, "last_integrated_ms"),
        )
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "self_affect_state.v2",
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "revision": self.revision,
            "components": [component.as_dict() for component in self.components],
            "active_episodes": [episode.as_dict() for episode in self.active_episodes],
            "mood": self.mood.as_dict(),
            "needs": self.needs.as_dict(),
            "last_integrated_ms": self.last_integrated_ms,
            "last_regulation": (
                self.last_regulation.as_dict()
                if self.last_regulation is not None
                else None
            ),
        }


__all__ = [
    "EmotionComponentV1",
    "EmotionEpisodeV1",
    "EventAppraisalV1",
    "MoodStateV1",
    "NeedStateV1",
    "RegulationDecisionV1",
    "SelfAffectStateV2",
    "UserAffectObservationV1",
]
