"""Versioned, privacy-bounded contracts for Seven Seas' relationship life.

These contracts deliberately separate relational evidence from an automatic
"bond score".  A response may open a provisional episode, but only a later
user interaction can provide an aftereffect.  Silence and technical delivery
failure are evidence about neither trust nor consent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SELF_ID = "natsume_ai"
_MAX_EVIDENCE_REFS = 16
_MAX_ACTIVE_EPISODES = 4
_EPISODE_SOURCES = frozenset({"CHAT_RESPONSE", "PROACTIVE_MESSAGE"})
_AFTEREFFECT_OUTCOMES = frozenset(
    {
        "HELPFUL",
        "MISUNDERSTOOD",
        "BOUNDARY",
        "REPAIR_ACCEPTED",
        "REPAIR_UNRESOLVED",
        "NEUTRAL",
        "SILENCE",
        "DELIVERY_FAILED",
    }
)
_PROACTIVE_PERMISSIONS = frozenset({"UNKNOWN", "ALLOWED", "DENIED"})
_CONSENT_STATES = frozenset({"NOT_GRANTED", "REVIEW_REQUIRED", "GRANTED"})


def _required(value: Any, name: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text


def _unit(value: Any, name: str, *, default: float = 0.0) -> float:
    try:
        number = float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return round(number, 6)


def _non_negative_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _refs(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a list")
    result = tuple(_required(item, f"{name}[{index}]", limit=512) for index, item in enumerate(value))
    if len(result) > _MAX_EVIDENCE_REFS:
        raise ValueError(f"{name} cannot contain more than {_MAX_EVIDENCE_REFS} items")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class RelationshipEpisodeV1:
    """A provisional, accepted response waiting for user-side evidence."""

    episode_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    source_kind: str
    response_receipt_ref: str
    response_digest: str
    opened_at_ms: int
    evidence_refs: Sequence[str] = ()
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        source_kind = _required(self.source_kind, "source_kind", limit=64).upper()
        if source_kind not in _EPISODE_SOURCES:
            raise ValueError(f"source_kind must be one of {sorted(_EPISODE_SOURCES)}")
        object.__setattr__(self, "episode_id", _required(self.episode_id, "episode_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "response_receipt_ref", _required(self.response_receipt_ref, "response_receipt_ref", limit=512))
        object.__setattr__(self, "response_digest", _required(self.response_digest, "response_digest", limit=128))
        object.__setattr__(self, "opened_at_ms", _non_negative_int(self.opened_at_ms, "opened_at_ms"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relationship_episode.v1",
            "episode_id": self.episode_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "source_kind": self.source_kind,
            "response_receipt_ref": self.response_receipt_ref,
            "response_digest": self.response_digest,
            "opened_at_ms": self.opened_at_ms,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class CompanionProactiveTargetV1:
    """One scope-bound destination registered by an accepted foreground reply.

    This is a routing capability for a relationship aggregate, not evidence of
    intimacy or permission to contact.  The relationship's explicit
    ``proactive_permission`` remains the separate authority that can allow a
    scheduler to use it.  No user or assistant body is retained here.
    """

    target_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    platform: str
    conversation_id: str
    source_receipt_ref: str
    registered_at_ms: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        platform = _required(self.platform, "platform", limit=80).lower()
        object.__setattr__(self, "target_id", _required(self.target_id, "target_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "platform", platform)
        object.__setattr__(
            self,
            "conversation_id",
            _required(self.conversation_id, "conversation_id", limit=512),
        )
        object.__setattr__(
            self,
            "source_receipt_ref",
            _required(self.source_receipt_ref, "source_receipt_ref", limit=512),
        )
        object.__setattr__(
            self,
            "registered_at_ms",
            _non_negative_int(self.registered_at_ms, "registered_at_ms"),
        )
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_proactive_target.v1",
            "target_id": self.target_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "conversation_id": self.conversation_id,
            "source_receipt_ref": self.source_receipt_ref,
            "registered_at_ms": self.registered_at_ms,
        }


@dataclass(frozen=True)
class RelationalAftereffectV1:
    """A later user-side outcome bound to one provisional response episode."""

    aftereffect_id: str
    episode_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    source_receipt_ref: str
    user_input_ref: str
    outcome: str
    helpfulness: float
    boundary_respected: bool
    repair_acknowledged: bool
    observed_at_ms: int
    evidence_refs: Sequence[str] = ()
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        outcome = _required(self.outcome, "outcome", limit=64).upper()
        if outcome not in _AFTEREFFECT_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_AFTEREFFECT_OUTCOMES)}")
        helpfulness = _unit(self.helpfulness, "helpfulness")
        if outcome in {"SILENCE", "DELIVERY_FAILED"} and helpfulness != 0.0:
            raise ValueError("silence or delivery failure cannot claim helpfulness")
        if self.repair_acknowledged and outcome != "REPAIR_ACCEPTED":
            raise ValueError("repair_acknowledged requires REPAIR_ACCEPTED")
        object.__setattr__(self, "aftereffect_id", _required(self.aftereffect_id, "aftereffect_id"))
        object.__setattr__(self, "episode_id", _required(self.episode_id, "episode_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "source_receipt_ref", _required(self.source_receipt_ref, "source_receipt_ref", limit=512))
        object.__setattr__(self, "user_input_ref", _required(self.user_input_ref, "user_input_ref", limit=512))
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "helpfulness", helpfulness)
        object.__setattr__(self, "boundary_respected", bool(self.boundary_respected))
        object.__setattr__(self, "repair_acknowledged", bool(self.repair_acknowledged))
        object.__setattr__(self, "observed_at_ms", _non_negative_int(self.observed_at_ms, "observed_at_ms"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relational_aftereffect.v1",
            "aftereffect_id": self.aftereffect_id,
            "episode_id": self.episode_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "source_receipt_ref": self.source_receipt_ref,
            "user_input_ref": self.user_input_ref,
            "outcome": self.outcome,
            "helpfulness": self.helpfulness,
            "boundary_respected": self.boundary_respected,
            "repair_acknowledged": self.repair_acknowledged,
            "observed_at_ms": self.observed_at_ms,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class RelationshipStateV2:
    """Bounded, consent-aware relationship projection.

    Trust, safety and attunement are separate from proactive permission and all
    intimacy-consent states.  None of the latter can be inferred from a score
    or a warm reply.
    """

    workspace_id: str
    relationship_subject_id: str
    revision: int
    trust: float
    safety: float
    attunement: float
    rupture_load: float
    repair_required: bool
    proactive_permission: str
    active_episodes: Sequence[RelationshipEpisodeV1] = ()
    last_aftereffect: RelationalAftereffectV1 | None = None
    romantic_consent: str = "NOT_GRANTED"
    exclusive_consent: str = "NOT_GRANTED"
    dependency_consent: str = "NOT_GRANTED"
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        permission = _required(self.proactive_permission, "proactive_permission", limit=64).upper()
        if permission not in _PROACTIVE_PERMISSIONS:
            raise ValueError(f"proactive_permission must be one of {sorted(_PROACTIVE_PERMISSIONS)}")
        episodes = tuple(self.active_episodes)
        if len(episodes) > _MAX_ACTIVE_EPISODES:
            raise ValueError(f"active_episodes cannot exceed {_MAX_ACTIVE_EPISODES}")
        if len({episode.episode_id for episode in episodes}) != len(episodes):
            raise ValueError("active_episodes must not contain duplicate episode ids")
        for consent_name in ("romantic_consent", "exclusive_consent", "dependency_consent"):
            value = _required(getattr(self, consent_name), consent_name, limit=64).upper()
            if value not in _CONSENT_STATES:
                raise ValueError(f"{consent_name} must be one of {sorted(_CONSENT_STATES)}")
            object.__setattr__(self, consent_name, value)
        for episode in episodes:
            if episode.workspace_id != str(self.workspace_id) or episode.relationship_subject_id != str(self.relationship_subject_id):
                raise ValueError("active episode scope drift")
        if self.last_aftereffect is not None and (
            self.last_aftereffect.workspace_id != str(self.workspace_id)
            or self.last_aftereffect.relationship_subject_id != str(self.relationship_subject_id)
        ):
            raise ValueError("last aftereffect scope drift")
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "revision", _non_negative_int(self.revision, "revision"))
        object.__setattr__(self, "trust", _unit(self.trust, "trust"))
        object.__setattr__(self, "safety", _unit(self.safety, "safety"))
        object.__setattr__(self, "attunement", _unit(self.attunement, "attunement"))
        object.__setattr__(self, "rupture_load", _unit(self.rupture_load, "rupture_load"))
        object.__setattr__(self, "repair_required", bool(self.repair_required))
        object.__setattr__(self, "proactive_permission", permission)
        object.__setattr__(self, "active_episodes", episodes)
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relationship_state.v2",
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "revision": self.revision,
            "trust": self.trust,
            "safety": self.safety,
            "attunement": self.attunement,
            "rupture_load": self.rupture_load,
            "repair_required": self.repair_required,
            "proactive_permission": self.proactive_permission,
            "romantic_consent": self.romantic_consent,
            "exclusive_consent": self.exclusive_consent,
            "dependency_consent": self.dependency_consent,
            "active_episodes": [episode.as_dict() for episode in self.active_episodes],
            "last_aftereffect": (
                self.last_aftereffect.as_dict()
                if self.last_aftereffect is not None
                else None
            ),
        }


__all__ = [
    "CompanionProactiveTargetV1",
    "RelationshipEpisodeV1",
    "RelationshipStateV2",
    "RelationalAftereffectV1",
]
