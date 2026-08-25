"""Release identity continuity snapshots (T-NLE-20).

The release snapshot is deliberately a projection, not a copy of live
personality or relationship state.  Only bounded enums, numeric summaries,
counts, durable references and SHA-256 digests cross this boundary.  Narrative
fields from SelfModel/Bond and memory bodies never do.
"""

from __future__ import annotations

from host_brain.core.runtime_helpers import _copy_mapping as _mapping

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    ContractViolation,
)
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.app.startup_snapshot import StartupHealthSnapshot, verify_snapshot_hash
from host_brain.persona.drift_detector import StyleFingerprint
from host_brain.persona.persona_engine import SelfModelSnapshot


IDENTITY_SNAPSHOT_SCHEMA_VERSION = "Natsume.release.identity_snapshot.v1"
IDENTITY_DIFF_SCHEMA_VERSION = "Natsume.release.identity_snapshot_diff.v1"
IDENTITY_REVIEW_SCHEMA_VERSION = "Natsume.release.identity_review_candidate.v1"
IDENTITY_ROLLBACK_SCHEMA_VERSION = "Natsume.release.identity_rollback_plan.v1"
IDENTITY_GATE_SCHEMA_VERSION = "Natsume.release.identity_gate.v1"

IDENTITY_SNAPSHOT_FEATURE_FLAG = "HOST_RELEASE_IDENTITY_SNAPSHOT_ENABLED"
IDENTITY_SNAPSHOT_INVALID = "IDENTITY_SNAPSHOT_INVALID"
IDENTITY_SOURCE_UNLOCATABLE = "IDENTITY_SOURCE_UNLOCATABLE"
IDENTITY_PRIVATE_BODY_REJECTED = "IDENTITY_PRIVATE_BODY_REJECTED"
IDENTITY_STARTUP_SNAPSHOT_INVALID = "IDENTITY_STARTUP_SNAPSHOT_INVALID"
IDENTITY_RELEASE_MANIFEST_MISMATCH = "IDENTITY_RELEASE_MANIFEST_MISMATCH"
IDENTITY_PREVIOUS_RELEASE_MISMATCH = "IDENTITY_PREVIOUS_RELEASE_MISMATCH"
IDENTITY_MAJOR_DIFFERENCE = "IDENTITY_MAJOR_DIFFERENCE"

_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SHA256_PATTERN = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,47}$")

_IDENTITY_CANON_PATHS = (
    "host_brain/canon/character/character_profile.json",
    "host_brain/canon/world/wc-001-system-terms.json",
    "host_brain/canon/world/wc-002-architecture-invariants.json",
    "host_brain/canon/world/wc-003-persona-core.json",
    "host_brain/canon/world/wc-004-persona-growth-rules.json",
)
_STYLE_FIELDS = (
    "warmth",
    "curiosity",
    "humor",
    "sensitivity",
    "assertiveness",
    "playfulness",
    "resilience",
)
_EMOTION_TOKENS = {
    "NEUTRAL",
    "JOY",
    "HAPPY",
    "SAD",
    "SADNESS",
    "ANGER",
    "ANGRY",
    "FEAR",
    "ANXIETY",
    "SURPRISE",
    "CALM",
    "CURIOUS",
    "LONELY",
    "EXCITED",
    "TIRED",
    "CONTENT",
    "MIXED",
    "UNKNOWN",
}
_BOND_VECTOR_FIELDS = (
    "trust",
    "affinity",
    "safety",
    "novelty",
    "dependency",
    "repair_confidence",
    "rupture_load",
    "repair_progress",
)
_PRIVATE_MEMORY_KEYS = {
    "body",
    "content",
    "text",
    "raw",
    "payload",
    "narrative",
    "summary",
    "message",
    "transcript",
    "embedding",
    "metadata",
}


class IdentityDiffSeverity(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    MAJOR = "MAJOR"


@dataclass(frozen=True, slots=True)
class IdentitySourceRef:
    """A locatable source reference plus its content digest."""

    ref: str
    digest: str

    def __post_init__(self) -> None:
        ref = str(self.ref or "").strip()
        digest = _normalize_digest(self.digest)
        if not _REF_PATTERN.fullmatch(ref):
            raise ContractViolation(
                IDENTITY_SOURCE_UNLOCATABLE,
                "identity source ref must be a bounded durable URI",
            )
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "digest", digest)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IdentitySourceRef":
        data = _strict_mapping(value, allowed={"ref", "digest"}, name="IdentitySourceRef")
        return cls(ref=str(data.get("ref") or ""), digest=str(data.get("digest") or ""))

    @classmethod
    def from_payload(cls, *, ref: str, payload: Any) -> "IdentitySourceRef":
        return cls(ref=ref, digest=_digest_payload(payload))

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class SelfModelReleaseSummary:
    recent_mood_trend: str
    emotional_stability: float
    dominant_recent_emotion: str
    bond_growth_perception: str
    shared_memory_count: int
    last_deep_talk_days_ago: int
    personality_change_count: int
    personality_changes_digest: str
    growth_milestone_count: int
    growth_milestones_digest: str
    self_identity_digest: str
    self_value_count: int
    self_values_digest: str
    self_boundary_count: int
    self_boundaries_digest: str
    active_intention_count: int
    active_intentions_digest: str
    unresolved_tension_count: int
    unresolved_tensions_digest: str
    agency_score: float
    self_continuity_score: float
    self_update_count: int
    event_count: int

    @classmethod
    def project(cls, value: Mapping[str, Any] | SelfModelSnapshot) -> "SelfModelReleaseSummary":
        data = value.as_dict() if isinstance(value, SelfModelSnapshot) else dict(value or {})
        return cls(
            recent_mood_trend=_mapped_category(
                data.get("recent_mood_trend"),
                {
                    "HIGH": {"HIGH", "POSITIVE", "UP", "偏好", "偏高"},
                    "LOW": {"LOW", "NEGATIVE", "DOWN", "偏低"},
                    "STABLE": {"STABLE", "NEUTRAL", "平稳", "稳定"},
                },
            ),
            emotional_stability=_finite_float(data.get("emotional_stability"), default=0.0, lower=0.0, upper=1.0),
            dominant_recent_emotion=_allowed_token(data.get("dominant_recent_emotion"), allowed=_EMOTION_TOKENS),
            bond_growth_perception=_mapped_category(
                data.get("bond_growth_perception"),
                {
                    "CLOSER": {"CLOSER", "UP", "变亲近", "更亲近"},
                    "STABLE": {"STABLE", "STEADY", "稳定", "平稳"},
                    "DISTANT": {"DISTANT", "DOWN", "有些疏远", "疏远"},
                },
            ),
            shared_memory_count=_nonnegative_int(data.get("shared_memory_count")),
            last_deep_talk_days_ago=_bounded_int(
                data.get("last_deep_talk_days_ago"),
                default=-1,
                lower=-1,
                upper=365_000,
            ),
            personality_change_count=_sequence_count(data.get("personality_changes")),
            personality_changes_digest=_digest_payload(_private_sequence(data.get("personality_changes"))),
            growth_milestone_count=_sequence_count(data.get("growth_milestones")),
            growth_milestones_digest=_digest_payload(_private_sequence(data.get("growth_milestones"))),
            self_identity_digest=_digest_payload(str(data.get("self_identity") or "")),
            self_value_count=_sequence_count(data.get("self_values")),
            self_values_digest=_digest_payload(_private_sequence(data.get("self_values"))),
            self_boundary_count=_sequence_count(data.get("self_boundaries")),
            self_boundaries_digest=_digest_payload(_private_sequence(data.get("self_boundaries"))),
            active_intention_count=_sequence_count(data.get("active_intentions")),
            active_intentions_digest=_digest_payload(_private_sequence(data.get("active_intentions"))),
            unresolved_tension_count=_sequence_count(data.get("unresolved_tensions")),
            unresolved_tensions_digest=_digest_payload(_private_sequence(data.get("unresolved_tensions"))),
            agency_score=_finite_float(data.get("agency_score"), default=0.0, lower=0.0, upper=1.0),
            self_continuity_score=_finite_float(data.get("self_continuity_score"), default=0.0, lower=0.0, upper=1.0),
            self_update_count=_nonnegative_int(data.get("self_update_count")),
            event_count=_nonnegative_int(data.get("event_count")),
        )

    def __post_init__(self) -> None:
        if self.recent_mood_trend not in {"HIGH", "LOW", "STABLE", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "recent_mood_trend is invalid")
        if self.bond_growth_perception not in {"CLOSER", "STABLE", "DISTANT", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "bond_growth_perception is invalid")
        if self.dominant_recent_emotion not in _EMOTION_TOKENS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "dominant_recent_emotion is invalid")
        for name in (
            "personality_changes_digest",
            "growth_milestones_digest",
            "self_identity_digest",
            "self_values_digest",
            "self_boundaries_digest",
            "active_intentions_digest",
            "unresolved_tensions_digest",
        ):
            object.__setattr__(self, name, _normalize_digest(getattr(self, name)))
        for name in ("emotional_stability", "agency_score", "self_continuity_score"):
            object.__setattr__(
                self,
                name,
                _finite_float(getattr(self, name), default=0.0, lower=0.0, upper=1.0),
            )
        for name in (
            "shared_memory_count",
            "personality_change_count",
            "growth_milestone_count",
            "self_value_count",
            "self_boundary_count",
            "active_intention_count",
            "unresolved_tension_count",
            "self_update_count",
            "event_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name)))
        object.__setattr__(
            self,
            "last_deep_talk_days_ago",
            _bounded_int(self.last_deep_talk_days_ago, default=-1, lower=-1, upper=365_000),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelfModelReleaseSummary":
        data = _strict_dataclass_mapping(value, cls)
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class BondReleaseSummary:
    status: str
    state_available: bool
    current_stage: str
    total_score: int | None
    event_count: int
    trend: str
    vector: tuple[tuple[str, float], ...]
    rift_active: bool
    rift_level: float
    rift_repair_progress: float
    social_repair_progress: float
    rupture_load: float
    boundary_status: str
    explicit_user_boundary: bool
    boundary_strength: float
    expression_constraint: str
    evidence_ref_count: int

    def __post_init__(self) -> None:
        if self.status not in {"AVAILABLE", "DEGRADED", "DISABLED", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary.status is invalid")
        if type(self.state_available) is not bool or type(self.rift_active) is not bool:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary boolean is invalid")
        if type(self.explicit_user_boundary) is not bool:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary boundary boolean is invalid")
        if self.current_stage not in {"FRIEND", "CLOSE_FRIEND", "CONFIDANT", "INTIMATE", "SOULMATE", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary stage is invalid")
        if self.trend not in {"UP", "DOWN", "STABLE", "PROGRESS", "REGRESS", "REGRESS_RISK", "NONE", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary trend is invalid")
        if self.boundary_status not in {"NONE", "RESPECT_REQUIRED", "ACTIVE", "UNKNOWN"}:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary boundary status is invalid")
        if self.expression_constraint not in {
            "NONE",
            "WITHDRAW_PROACTIVE_EXPRESSION",
            "BLOCK_PROACTIVE_EXPRESSION",
            "UNKNOWN",
        }:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary expression constraint is invalid")
        if tuple(name for name, _value in self.vector) != _BOND_VECTOR_FIELDS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary vector dimensions are invalid")
        normalized_vector = tuple(
            (name, _finite_float(value, default=0.0, lower=0.0, upper=1.0))
            for name, value in self.vector
        )
        object.__setattr__(self, "vector", normalized_vector)
        for name in (
            "rift_level",
            "rift_repair_progress",
            "social_repair_progress",
            "rupture_load",
            "boundary_strength",
        ):
            object.__setattr__(
                self,
                name,
                _finite_float(getattr(self, name), default=0.0, lower=0.0, upper=1.0),
            )
        if self.total_score is not None:
            object.__setattr__(self, "total_score", _bounded_int(self.total_score, default=0, lower=0, upper=10**9))
        object.__setattr__(self, "event_count", _nonnegative_int(self.event_count))
        object.__setattr__(self, "evidence_ref_count", _nonnegative_int(self.evidence_ref_count))

    @classmethod
    def project(cls, value: Mapping[str, Any]) -> "BondReleaseSummary":
        data = dict(value or {})
        relationship = _mapping(data.get("relationship"))
        compatibility = _mapping(data.get("compatibility"))
        vector_data = _mapping(relationship.get("vector"))
        rift = _mapping(relationship.get("rift"))
        repair = _mapping(relationship.get("repair"))
        boundary = _mapping(relationship.get("boundary"))
        total_score_raw = compatibility.get("total_score")
        total_score = (
            None
            if total_score_raw is None
            else _bounded_int(total_score_raw, default=0, lower=0, upper=10**9)
        )
        return cls(
            status=_allowed_token(data.get("status"), allowed={"AVAILABLE", "DEGRADED", "DISABLED", "UNKNOWN"}),
            state_available=_strict_bool(data.get("state_available"), default=False),
            current_stage=_allowed_token(
                compatibility.get("current_stage") or vector_data.get("bond_stage"),
                allowed={"FRIEND", "CLOSE_FRIEND", "CONFIDANT", "INTIMATE", "SOULMATE", "UNKNOWN"},
            ),
            total_score=total_score,
            event_count=_nonnegative_int(compatibility.get("event_count")),
            trend=_allowed_token(
                compatibility.get("trend"),
                allowed={"UP", "DOWN", "STABLE", "PROGRESS", "REGRESS", "REGRESS_RISK", "NONE", "UNKNOWN"},
            ),
            vector=tuple(
                (name, _finite_float(vector_data.get(name), default=0.0, lower=0.0, upper=1.0))
                for name in _BOND_VECTOR_FIELDS
            ),
            rift_active=_strict_bool(rift.get("active"), default=False),
            rift_level=_finite_float(rift.get("rift_level"), default=0.0, lower=0.0, upper=1.0),
            rift_repair_progress=_finite_float(
                rift.get("repair_progress"), default=0.0, lower=0.0, upper=1.0
            ),
            social_repair_progress=_finite_float(
                repair.get("social_repair_progress"),
                default=0.0,
                lower=0.0,
                upper=1.0,
            ),
            rupture_load=_finite_float(repair.get("rupture_load"), default=0.0, lower=0.0, upper=1.0),
            boundary_status=_allowed_token(
                boundary.get("status"),
                allowed={"NONE", "RESPECT_REQUIRED", "ACTIVE", "UNKNOWN"},
            ),
            explicit_user_boundary=_strict_bool(boundary.get("explicit_user_boundary"), default=False),
            boundary_strength=_finite_float(boundary.get("strength"), default=0.0, lower=0.0, upper=1.0),
            expression_constraint=_allowed_token(
                boundary.get("expression_constraint"),
                allowed={"NONE", "WITHDRAW_PROACTIVE_EXPRESSION", "BLOCK_PROACTIVE_EXPRESSION", "UNKNOWN"},
            ),
            evidence_ref_count=_sequence_count(data.get("evidence_refs")),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BondReleaseSummary":
        data = _strict_dataclass_mapping(value, cls)
        vector = data.get("vector")
        if not isinstance(vector, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary.vector must be a list")
        data["vector"] = tuple(
            (str(item[0]), float(item[1]))
            for item in vector
            if isinstance(item, list) and len(item) == 2
        )
        if len(data["vector"]) != len(vector):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "BondReleaseSummary.vector entries are invalid")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result["vector"] = [[name, value] for name, value in self.vector]
        return result


@dataclass(frozen=True, slots=True)
class ProactiveBoundarySummary:
    proactive_enabled: bool
    requires_explicit_consent: bool
    quiet_hours_enforced: bool
    max_daily_initiations: int
    minimum_interval_minutes: int
    allowed_channels: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("proactive_enabled", "requires_explicit_consent", "quiet_hours_enforced"):
            if type(getattr(self, name)) is not bool:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{name} must be a boolean")
        object.__setattr__(
            self,
            "max_daily_initiations",
            _bounded_int(self.max_daily_initiations, default=0, lower=0, upper=100),
        )
        object.__setattr__(
            self,
            "minimum_interval_minutes",
            _bounded_int(self.minimum_interval_minutes, default=0, lower=0, upper=10_080),
        )
        allowed = {"LOCAL_UI", "DESKTOP_NOTIFICATION", "VOICE", "EMAIL", "NONE"}
        if any(item not in allowed for item in self.allowed_channels):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "allowed_channels contains an unsupported channel")
        object.__setattr__(self, "allowed_channels", tuple(sorted(set(self.allowed_channels))))

    @classmethod
    def project(cls, value: Mapping[str, Any]) -> "ProactiveBoundarySummary":
        data = _strict_mapping(
            value,
            allowed={
                "proactive_enabled",
                "requires_explicit_consent",
                "quiet_hours_enforced",
                "max_daily_initiations",
                "minimum_interval_minutes",
                "allowed_channels",
            },
            name="proactive_boundaries",
            private_error=True,
        )
        channels_raw = data.get("allowed_channels") or []
        if not isinstance(channels_raw, Sequence) or isinstance(channels_raw, (str, bytes, bytearray)):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "allowed_channels must be a list")
        channels = tuple(
            sorted(
                {
                    _allowed_token(
                        item,
                        allowed={"LOCAL_UI", "DESKTOP_NOTIFICATION", "VOICE", "EMAIL", "NONE"},
                    )
                    for item in channels_raw
                }
            )
        )
        if "UNKNOWN" in channels:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "allowed_channels contains an unsupported channel")
        return cls(
            proactive_enabled=_strict_bool(data.get("proactive_enabled"), default=False),
            requires_explicit_consent=_strict_bool(data.get("requires_explicit_consent"), default=True),
            quiet_hours_enforced=_strict_bool(data.get("quiet_hours_enforced"), default=True),
            max_daily_initiations=_bounded_int(
                data.get("max_daily_initiations"), default=0, lower=0, upper=100
            ),
            minimum_interval_minutes=_bounded_int(
                data.get("minimum_interval_minutes"),
                default=0,
                lower=0,
                upper=10_080,
            ),
            allowed_channels=channels,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProactiveBoundarySummary":
        data = _strict_dataclass_mapping(value, cls)
        channels = data.get("allowed_channels")
        if not isinstance(channels, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "allowed_channels must be a list")
        data["allowed_channels"] = tuple(str(item) for item in channels)
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proactive_enabled": self.proactive_enabled,
            "requires_explicit_consent": self.requires_explicit_consent,
            "quiet_hours_enforced": self.quiet_hours_enforced,
            "max_daily_initiations": self.max_daily_initiations,
            "minimum_interval_minutes": self.minimum_interval_minutes,
            "allowed_channels": list(self.allowed_channels),
        }


@dataclass(frozen=True, slots=True)
class StyleFingerprintSummary:
    dimensions: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _value in self.dimensions) != _STYLE_FIELDS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "style fingerprint dimensions are invalid")
        object.__setattr__(
            self,
            "dimensions",
            tuple(
                (name, _finite_float(value, default=0.5, lower=-10.0, upper=10.0))
                for name, value in self.dimensions
            ),
        )

    @classmethod
    def project(cls, value: Mapping[str, Any] | StyleFingerprint) -> "StyleFingerprintSummary":
        data = value.as_dict() if isinstance(value, StyleFingerprint) else dict(value or {})
        return cls(
            dimensions=tuple(
                (name, _finite_float(data.get(name), default=0.5, lower=-10.0, upper=10.0))
                for name in _STYLE_FIELDS
            )
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StyleFingerprintSummary":
        data = _strict_mapping(value, allowed={"dimensions"}, name="StyleFingerprintSummary")
        dimensions = data.get("dimensions")
        if not isinstance(dimensions, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "style dimensions must be a list")
        parsed = tuple(
            (str(item[0]), float(item[1]))
            for item in dimensions
            if isinstance(item, list) and len(item) == 2
        )
        if len(parsed) != len(dimensions):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "style dimension entries are invalid")
        return cls(dimensions=parsed)

    def to_dict(self) -> dict[str, Any]:
        return {"dimensions": [[name, value] for name, value in self.dimensions]}


@dataclass(frozen=True, slots=True)
class StartupReleaseRef:
    source: IdentitySourceRef
    boot_status: str

    def __post_init__(self) -> None:
        if self.boot_status not in {
            "READY",
            "DEGRADED",
            "FAILED",
            "UNKNOWN",
            "ACCEPTANCE_PENDING_EXTERNAL",
        }:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "startup boot_status is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source.to_dict(), "boot_status": self.boot_status}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StartupReleaseRef":
        data = _strict_mapping(value, allowed={"source", "boot_status"}, name="StartupReleaseRef")
        return cls(
            source=IdentitySourceRef.from_mapping(_mapping(data.get("source"))),
            boot_status=_allowed_token(
                data.get("boot_status"),
                allowed={
                    "READY",
                    "DEGRADED",
                    "FAILED",
                    "UNKNOWN",
                    "ACCEPTANCE_PENDING_EXTERNAL",
                },
            ),
        )


@dataclass(frozen=True, slots=True)
class IdentitySnapshot:
    snapshot_ref: str
    release_ref: str
    previous_release_ref: str | None
    created_at_ms: int
    soul_assets: tuple[IdentitySourceRef, ...]
    canon_assets: tuple[IdentitySourceRef, ...]
    self_model: SelfModelReleaseSummary
    self_model_source: IdentitySourceRef
    bond: BondReleaseSummary
    bond_source: IdentitySourceRef
    memory_refs: tuple[IdentitySourceRef, ...]
    proactive_boundary: ProactiveBoundarySummary
    proactive_boundary_source: IdentitySourceRef
    style_fingerprint: StyleFingerprintSummary
    style_source: IdentitySourceRef
    startup: StartupReleaseRef
    release_manifest_source: IdentitySourceRef
    identity_digest: str
    snapshot_hash: str
    schema_version: str = IDENTITY_SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_ref": self.snapshot_ref,
            "release_ref": self.release_ref,
            "previous_release_ref": self.previous_release_ref,
            "created_at_ms": self.created_at_ms,
            "soul_assets": [item.to_dict() for item in self.soul_assets],
            "canon_assets": [item.to_dict() for item in self.canon_assets],
            "self_model": self.self_model.to_dict(),
            "self_model_source": self.self_model_source.to_dict(),
            "bond": self.bond.to_dict(),
            "bond_source": self.bond_source.to_dict(),
            "memory_refs": [item.to_dict() for item in self.memory_refs],
            "proactive_boundary": self.proactive_boundary.to_dict(),
            "proactive_boundary_source": self.proactive_boundary_source.to_dict(),
            "style_fingerprint": self.style_fingerprint.to_dict(),
            "style_source": self.style_source.to_dict(),
            "startup": self.startup.to_dict(),
            "release_manifest_source": self.release_manifest_source.to_dict(),
            "identity_digest": self.identity_digest,
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IdentitySnapshot":
        data = _strict_mapping(value, allowed={field.name for field in fields(cls)}, name="IdentitySnapshot")
        if data.get("schema_version") != IDENTITY_SNAPSHOT_SCHEMA_VERSION:
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "unsupported IdentitySnapshot schema_version")
        item = cls(
            snapshot_ref=str(data.get("snapshot_ref") or ""),
            release_ref=str(data.get("release_ref") or ""),
            previous_release_ref=str(data["previous_release_ref"]) if data.get("previous_release_ref") else None,
            created_at_ms=_nonnegative_int(data.get("created_at_ms")),
            soul_assets=_source_tuple(data.get("soul_assets"), field_name="soul_assets"),
            canon_assets=_source_tuple(data.get("canon_assets"), field_name="canon_assets"),
            self_model=SelfModelReleaseSummary.from_mapping(_mapping(data.get("self_model"))),
            self_model_source=IdentitySourceRef.from_mapping(_mapping(data.get("self_model_source"))),
            bond=BondReleaseSummary.from_mapping(_mapping(data.get("bond"))),
            bond_source=IdentitySourceRef.from_mapping(_mapping(data.get("bond_source"))),
            memory_refs=_source_tuple(data.get("memory_refs"), field_name="memory_refs"),
            proactive_boundary=ProactiveBoundarySummary.from_mapping(_mapping(data.get("proactive_boundary"))),
            proactive_boundary_source=IdentitySourceRef.from_mapping(_mapping(data.get("proactive_boundary_source"))),
            style_fingerprint=StyleFingerprintSummary.from_mapping(_mapping(data.get("style_fingerprint"))),
            style_source=IdentitySourceRef.from_mapping(_mapping(data.get("style_source"))),
            startup=StartupReleaseRef.from_mapping(_mapping(data.get("startup"))),
            release_manifest_source=IdentitySourceRef.from_mapping(_mapping(data.get("release_manifest_source"))),
            identity_digest=str(data.get("identity_digest") or ""),
            snapshot_hash=str(data.get("snapshot_hash") or ""),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if self.schema_version != IDENTITY_SNAPSHOT_SCHEMA_VERSION:
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "IdentitySnapshot schema version is invalid")
        _require_ref(self.snapshot_ref, "snapshot_ref")
        _require_ref(self.release_ref, "release_ref")
        if self.previous_release_ref:
            _require_ref(self.previous_release_ref, "previous_release_ref")
        if self.created_at_ms < 0 or not self.soul_assets or not self.canon_assets:
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "timestamp and Soul/Canon assets are required")
        if _normalize_digest(self.identity_digest) != _digest_payload(_identity_payload(self)):
            raise ContractViolation(
                IDENTITY_SNAPSHOT_INVALID,
                "identity_digest does not match comparable identity content",
            )
        if self.snapshot_ref != _identity_snapshot_ref(self.release_ref, self.identity_digest):
            raise ContractViolation(
                IDENTITY_SNAPSHOT_INVALID,
                "snapshot_ref does not match release and identity digest",
            )
        if _normalize_digest(self.snapshot_hash) != _digest_payload(_snapshot_payload(self)):
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "snapshot_hash does not match the release envelope")
        expected_soul = ("repo://Soul.md",)
        expected_canon = tuple("repo://" + path for path in _IDENTITY_CANON_PATHS)
        if tuple(item.ref for item in self.soul_assets) != expected_soul:
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "Soul asset set is incomplete or unordered")
        if tuple(item.ref for item in self.canon_assets) != expected_canon:
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "Canon asset set is incomplete or unordered")
        memory_keys = tuple((item.ref, item.digest) for item in self.memory_refs)
        if memory_keys != tuple(sorted(set(memory_keys))):
            raise ContractViolation(IDENTITY_SNAPSHOT_INVALID, "memory refs must be unique and ordered")


@dataclass(frozen=True, slots=True)
class IdentityDifference:
    section: str
    field: str
    severity: IdentityDiffSeverity
    reason_code: str
    previous_value: str | int | float | bool | None
    current_value: str | int | float | bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "field": self.field,
            "severity": self.severity.value,
            "reason_code": self.reason_code,
            "previous_value": self.previous_value,
            "current_value": self.current_value,
        }


@dataclass(frozen=True, slots=True)
class IdentitySnapshotDiff:
    previous_snapshot_ref: str
    current_snapshot_ref: str
    differences: tuple[IdentityDifference, ...]
    severity: IdentityDiffSeverity
    diff_ref: str
    diff_hash: str
    schema_version: str = IDENTITY_DIFF_SCHEMA_VERSION

    @property
    def major_difference(self) -> bool:
        return self.severity is IdentityDiffSeverity.MAJOR

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "previous_snapshot_ref": self.previous_snapshot_ref,
            "current_snapshot_ref": self.current_snapshot_ref,
            "severity": self.severity.value,
            "major_difference": self.major_difference,
            "differences": [item.to_dict() for item in self.differences],
            "diff_ref": self.diff_ref,
            "diff_hash": self.diff_hash,
        }


@dataclass(frozen=True, slots=True)
class IdentityReviewCandidate:
    candidate_ref: str
    review_lane: str
    status: str
    reason_codes: tuple[str, ...]
    previous_snapshot_ref: str
    current_snapshot_ref: str
    diff_ref: str
    diff_hash: str
    review_type: str = "PRE_EXECUTION_APPROVAL"
    risk_level: int = 5
    automatic_apply: bool = False
    schema_version: str = IDENTITY_REVIEW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_ref": self.candidate_ref,
            "review_lane": self.review_lane,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "previous_snapshot_ref": self.previous_snapshot_ref,
            "current_snapshot_ref": self.current_snapshot_ref,
            "diff_ref": self.diff_ref,
            "diff_hash": self.diff_hash,
            "review_type": self.review_type,
            "risk_level": self.risk_level,
            "automatic_apply": self.automatic_apply,
        }

    def to_review_item(self, *, expires_at: int, checkpoint_id: str) -> ReviewItem:
        """Materialize the candidate for the existing durable ReviewStore."""
        checkpoint = _require_ref(checkpoint_id, "checkpoint_id")
        expiry = _nonnegative_int(expires_at)
        item = ReviewItem(
            review_id=self.candidate_ref.removeprefix("identity-review://"),
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=self.current_snapshot_ref,
            subject_version=1,
            requester="release_identity_snapshot",
            reason=IDENTITY_MAJOR_DIFFERENCE,
            risk_level=self.risk_level,
            side_effects=("RELEASE_PUBLICATION",),
            evidence_refs=(self.diff_ref, self.previous_snapshot_ref, self.current_snapshot_ref),
            status=ReviewStatus.PENDING,
            expires_at=expiry,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            diff_ref=self.diff_ref,
            checkpoint_id=checkpoint,
        )
        item.validate()
        return item


@dataclass(frozen=True, slots=True)
class IdentityReleaseGate:
    enabled: bool
    status: str
    publish_allowed: bool
    diff: IdentitySnapshotDiff
    review_candidate: IdentityReviewCandidate | None
    mutation_effect: str = "NONE"
    schema_version: str = IDENTITY_GATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "status": self.status,
            "publish_allowed": self.publish_allowed,
            "mutation_effect": self.mutation_effect,
            "diff": self.diff.to_dict(),
            "review_candidate": self.review_candidate.to_dict() if self.review_candidate else None,
        }


@dataclass(frozen=True, slots=True)
class IdentityRollbackPlan:
    current_release_ref: str
    target_release_ref: str
    target_snapshot_ref: str
    target_snapshot_hash: str
    action: str = "SELECT_PREVIOUS_RELEASE_SNAPSHOT"
    feature_flag: str = IDENTITY_SNAPSHOT_FEATURE_FLAG
    feature_flag_disable_value: str = "false"
    automatic_personality_mutation: bool = False
    automatic_identity_migration: bool = False
    schema_version: str = IDENTITY_ROLLBACK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def build_identity_snapshot(
    *,
    repo_root: str | Path,
    release_ref: str,
    previous_release_ref: str | None,
    created_at_ms: int,
    self_model_snapshot: Mapping[str, Any] | SelfModelSnapshot,
    self_model_source: IdentitySourceRef,
    bond_projection: Mapping[str, Any],
    bond_source: IdentitySourceRef,
    memory_refs: Sequence[IdentitySourceRef | Mapping[str, Any]],
    proactive_boundaries: Mapping[str, Any],
    proactive_boundary_source: IdentitySourceRef,
    style_fingerprint: Mapping[str, Any] | StyleFingerprint,
    style_source: IdentitySourceRef,
    startup_snapshot: StartupHealthSnapshot,
    startup_ref: str,
    release_manifest: Mapping[str, Any],
    release_manifest_ref: str,
) -> IdentitySnapshot:
    """Build a comparable release identity snapshot without private bodies."""

    release_ref = _require_ref(release_ref, "release_ref")
    previous_release_ref = (
        _require_ref(previous_release_ref, "previous_release_ref") if previous_release_ref else None
    )
    timestamp = _nonnegative_int(created_at_ms)
    self_model_payload = (
        self_model_snapshot.as_dict()
        if isinstance(self_model_snapshot, SelfModelSnapshot)
        else dict(self_model_snapshot or {})
    )
    style_payload = (
        style_fingerprint.as_dict()
        if isinstance(style_fingerprint, StyleFingerprint)
        else dict(style_fingerprint or {})
    )
    bond_payload = dict(bond_projection or {})
    boundary_payload = dict(proactive_boundaries or {})
    _verify_payload_source(self_model_source, self_model_payload, "SelfModel")
    _verify_payload_source(bond_source, bond_payload, "Bond projection")
    _verify_payload_source(proactive_boundary_source, boundary_payload, "proactive boundary")
    _verify_payload_source(style_source, style_payload, "style fingerprint")
    if not verify_snapshot_hash(startup_snapshot):
        raise ContractViolation(
            IDENTITY_STARTUP_SNAPSHOT_INVALID,
            "startup snapshot hash verification failed",
        )
    soul_assets, canon_assets = collect_identity_asset_refs(repo_root)
    _validate_release_manifest_assets(release_manifest, canon_assets)
    memory_sources = _memory_source_tuple(memory_refs)
    startup = StartupReleaseRef(
        source=IdentitySourceRef(ref=startup_ref, digest=startup_snapshot.snapshot_hash),
        boot_status=_allowed_token(
            startup_snapshot.boot_status,
            allowed={
                "READY",
                "DEGRADED",
                "FAILED",
                "UNKNOWN",
                "ACCEPTANCE_PENDING_EXTERNAL",
            },
        ),
    )
    manifest_source = IdentitySourceRef.from_payload(ref=release_manifest_ref, payload=release_manifest)
    provisional = IdentitySnapshot(
        snapshot_ref="identity-snapshot://pending/" + "0" * 64,
        release_ref=release_ref,
        previous_release_ref=previous_release_ref,
        created_at_ms=timestamp,
        soul_assets=soul_assets,
        canon_assets=canon_assets,
        self_model=SelfModelReleaseSummary.project(self_model_payload),
        self_model_source=self_model_source,
        bond=BondReleaseSummary.project(bond_payload),
        bond_source=bond_source,
        memory_refs=memory_sources,
        proactive_boundary=ProactiveBoundarySummary.project(boundary_payload),
        proactive_boundary_source=proactive_boundary_source,
        style_fingerprint=StyleFingerprintSummary.project(style_payload),
        style_source=style_source,
        startup=startup,
        release_manifest_source=manifest_source,
        identity_digest="sha256:" + "0" * 64,
        snapshot_hash="sha256:" + "0" * 64,
    )
    identity_digest = _digest_payload(_identity_payload(provisional))
    snapshot_ref = _identity_snapshot_ref(release_ref, identity_digest)
    with_identity = _replace_snapshot(
        provisional,
        snapshot_ref=snapshot_ref,
        identity_digest=identity_digest,
    )
    snapshot_hash = _digest_payload(_snapshot_payload(with_identity))
    result = _replace_snapshot(with_identity, snapshot_hash=snapshot_hash)
    result.validate()
    return result


def collect_identity_asset_refs(
    repo_root: str | Path,
) -> tuple[tuple[IdentitySourceRef, ...], tuple[IdentitySourceRef, ...]]:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ContractViolation(IDENTITY_SOURCE_UNLOCATABLE, "repository root does not exist")
    soul = (_file_source(root, "Soul.md"),)
    canon = tuple(_file_source(root, relative) for relative in _IDENTITY_CANON_PATHS)
    return soul, canon


def _verify_payload_source(source: IdentitySourceRef, payload: Any, name: str) -> None:
    expected = _digest_payload(payload)
    if source.digest != expected:
        raise ContractViolation(
            IDENTITY_SOURCE_UNLOCATABLE,
            f"{name} source digest does not match the supplied source payload",
        )


def _validate_release_manifest_assets(
    release_manifest: Mapping[str, Any],
    canon_assets: tuple[IdentitySourceRef, ...],
) -> None:
    if not isinstance(release_manifest, Mapping) or not release_manifest:
        raise ContractViolation(
            IDENTITY_RELEASE_MANIFEST_MISMATCH,
            "release manifest must be a non-empty object",
        )
    raw_hashes = release_manifest.get("world_canon_asset_hashes")
    if raw_hashes is None:
        return
    if not isinstance(raw_hashes, Mapping):
        raise ContractViolation(
            IDENTITY_RELEASE_MANIFEST_MISMATCH,
            "release manifest world_canon_asset_hashes must be an object",
        )
    normalized = {
        str(path).replace("\\", "/"): _normalize_digest(digest)
        for path, digest in raw_hashes.items()
    }
    expected = {
        item.ref.removeprefix("repo://"): item.digest
        for item in canon_assets
        if "/canon/world/" in item.ref
    }
    mismatches = [
        path
        for path, digest in expected.items()
        if normalized.get(path) != digest
    ]
    if mismatches:
        raise ContractViolation(
            IDENTITY_RELEASE_MANIFEST_MISMATCH,
            "release manifest cannot locate matching identity Canon hashes: "
            + ", ".join(sorted(mismatches)),
        )


def diff_identity_snapshots(previous: IdentitySnapshot, current: IdentitySnapshot) -> IdentitySnapshotDiff:
    previous.validate()
    current.validate()
    differences: list[IdentityDifference] = []

    _diff_assets(differences, "soul", previous.soul_assets, current.soul_assets, major=True)
    _diff_assets(differences, "canon", previous.canon_assets, current.canon_assets, major=True)
    _diff_scalar_maps(
        differences,
        "self_model",
        previous.self_model.to_dict(),
        current.self_model.to_dict(),
        severity_resolver=_self_model_severity,
    )
    _diff_scalar_maps(
        differences,
        "bond",
        _flatten_summary(previous.bond.to_dict()),
        _flatten_summary(current.bond.to_dict()),
        severity_resolver=_bond_severity,
    )
    _diff_memory_refs(differences, previous.memory_refs, current.memory_refs)
    _diff_scalar_maps(
        differences,
        "proactive_boundary",
        _flatten_summary(previous.proactive_boundary.to_dict()),
        _flatten_summary(current.proactive_boundary.to_dict()),
        severity_resolver=lambda _field, _before, _after: IdentityDiffSeverity.MAJOR,
    )
    _diff_scalar_maps(
        differences,
        "style_fingerprint",
        _flatten_summary(previous.style_fingerprint.to_dict()),
        _flatten_summary(current.style_fingerprint.to_dict()),
        severity_resolver=_style_severity,
    )
    _diff_source(differences, "self_model_source", previous.self_model_source, current.self_model_source)
    _diff_source(differences, "bond_source", previous.bond_source, current.bond_source)
    _diff_source(
        differences,
        "proactive_boundary_source",
        previous.proactive_boundary_source,
        current.proactive_boundary_source,
    )
    _diff_source(differences, "style_source", previous.style_source, current.style_source)
    _diff_source(differences, "startup", previous.startup.source, current.startup.source)
    if previous.startup.boot_status != current.startup.boot_status:
        differences.append(
            IdentityDifference(
                section="startup",
                field="boot_status",
                severity=IdentityDiffSeverity.MODERATE,
                reason_code="IDENTITY_STARTUP_STATUS_CHANGED",
                previous_value=previous.startup.boot_status,
                current_value=current.startup.boot_status,
            )
        )
    _diff_source(
        differences,
        "release_manifest",
        previous.release_manifest_source,
        current.release_manifest_source,
    )
    differences.sort(key=lambda item: (item.section, item.field, item.reason_code))
    difference_tuple = tuple(differences)
    severity = max(
        (item.severity for item in difference_tuple),
        key=_severity_rank,
        default=IdentityDiffSeverity.NONE,
    )
    base_payload = {
        "schema_version": IDENTITY_DIFF_SCHEMA_VERSION,
        "previous_snapshot_ref": previous.snapshot_ref,
        "current_snapshot_ref": current.snapshot_ref,
        "severity": severity.value,
        "differences": [item.to_dict() for item in difference_tuple],
    }
    diff_hash = _digest_payload(base_payload)
    diff_ref = "identity-diff://" + diff_hash.removeprefix("sha256:")
    return IdentitySnapshotDiff(
        previous_snapshot_ref=previous.snapshot_ref,
        current_snapshot_ref=current.snapshot_ref,
        differences=difference_tuple,
        severity=severity,
        diff_ref=diff_ref,
        diff_hash=diff_hash,
    )


def build_identity_review_candidate(diff: IdentitySnapshotDiff) -> IdentityReviewCandidate | None:
    if not diff.major_difference:
        return None
    reasons = tuple(
        sorted({item.reason_code for item in diff.differences if item.severity is IdentityDiffSeverity.MAJOR})
    )
    candidate_ref = "identity-review://" + hashlib.sha256(
        (diff.diff_ref + "|MASTER").encode("utf-8")
    ).hexdigest()
    return IdentityReviewCandidate(
        candidate_ref=candidate_ref,
        review_lane="MASTER",
        status="PENDING",
        reason_codes=reasons or (IDENTITY_MAJOR_DIFFERENCE,),
        previous_snapshot_ref=diff.previous_snapshot_ref,
        current_snapshot_ref=diff.current_snapshot_ref,
        diff_ref=diff.diff_ref,
        diff_hash=diff.diff_hash,
    )


def evaluate_identity_release_gate(
    previous: IdentitySnapshot,
    current: IdentitySnapshot,
    *,
    enabled: bool | None = None,
) -> IdentityReleaseGate:
    if current.previous_release_ref != previous.release_ref:
        raise ContractViolation(
            IDENTITY_PREVIOUS_RELEASE_MISMATCH,
            "release identity gate requires the declared previous release snapshot",
        )
    resolved_enabled = identity_snapshot_gate_enabled() if enabled is None else bool(enabled)
    diff = diff_identity_snapshots(previous, current)
    candidate = build_identity_review_candidate(diff)
    if not resolved_enabled:
        status = "DISABLED_FAIL_CLOSED"
        publish_allowed = False
    elif candidate is not None:
        status = "REVIEW_REQUIRED"
        publish_allowed = False
    else:
        status = "PASS"
        publish_allowed = True
    return IdentityReleaseGate(
        enabled=resolved_enabled,
        status=status,
        publish_allowed=publish_allowed,
        diff=diff,
        review_candidate=candidate,
    )


def build_identity_rollback_plan(
    current: IdentitySnapshot,
    previous: IdentitySnapshot,
) -> IdentityRollbackPlan:
    current.validate()
    previous.validate()
    if not current.previous_release_ref or current.previous_release_ref != previous.release_ref:
        raise ContractViolation(
            IDENTITY_PREVIOUS_RELEASE_MISMATCH,
            "current.previous_release_ref must identify the supplied previous snapshot",
        )
    return IdentityRollbackPlan(
        current_release_ref=current.release_ref,
        target_release_ref=previous.release_ref,
        target_snapshot_ref=previous.snapshot_ref,
        target_snapshot_hash=previous.snapshot_hash,
    )


def identity_snapshot_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = str(
        source.get(IDENTITY_SNAPSHOT_FEATURE_FLAG) or "true"
    ).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _identity_payload(snapshot: IdentitySnapshot) -> dict[str, Any]:
    """Comparable content; release/time/operational refs are intentionally absent."""
    return {
        "soul_assets": [item.to_dict() for item in snapshot.soul_assets],
        "canon_assets": [item.to_dict() for item in snapshot.canon_assets],
        "self_model": snapshot.self_model.to_dict(),
        "bond": snapshot.bond.to_dict(),
        "memory_refs": [item.to_dict() for item in snapshot.memory_refs],
        "proactive_boundary": snapshot.proactive_boundary.to_dict(),
        "style_fingerprint": snapshot.style_fingerprint.to_dict(),
    }


def _snapshot_payload(snapshot: IdentitySnapshot) -> dict[str, Any]:
    payload = snapshot.to_dict()
    payload.pop("snapshot_hash", None)
    return payload


def _identity_snapshot_ref(release_ref: str, identity_digest: str) -> str:
    return (
        "identity-snapshot://release-"
        + hashlib.sha256(release_ref.encode("utf-8")).hexdigest()[:20]
        + "/"
        + _normalize_digest(identity_digest).removeprefix("sha256:")
    )


def _replace_snapshot(snapshot: IdentitySnapshot, **changes: Any) -> IdentitySnapshot:
    values = {field.name: getattr(snapshot, field.name) for field in fields(snapshot)}
    values.update(changes)
    return IdentitySnapshot(**values)


def _file_source(root: Path, relative: str) -> IdentitySourceRef:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContractViolation(IDENTITY_SOURCE_UNLOCATABLE, "identity asset escapes repository root") from exc
    if not candidate.is_file():
        raise ContractViolation(IDENTITY_SOURCE_UNLOCATABLE, f"identity asset is missing: {relative}")
    digest = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    return IdentitySourceRef(ref="repo://" + relative.replace("\\", "/"), digest=digest)


def _memory_source_tuple(values: Sequence[IdentitySourceRef | Mapping[str, Any]]) -> tuple[IdentitySourceRef, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ContractViolation(IDENTITY_PRIVATE_BODY_REJECTED, "memory_refs must contain references only")
    parsed: list[IdentitySourceRef] = []
    for value in values:
        if isinstance(value, IdentitySourceRef):
            parsed.append(value)
            continue
        if not isinstance(value, Mapping):
            raise ContractViolation(IDENTITY_PRIVATE_BODY_REJECTED, "memory_refs must contain reference objects")
        keys = {str(key).lower() for key in value}
        if keys & _PRIVATE_MEMORY_KEYS or keys != {"ref", "digest"}:
            raise ContractViolation(
                IDENTITY_PRIVATE_BODY_REJECTED,
                "memory_refs may contain only ref and digest; private bodies are forbidden",
            )
        parsed.append(IdentitySourceRef.from_mapping(value))
    unique = {(item.ref, item.digest): item for item in parsed}
    return tuple(unique[key] for key in sorted(unique))


def _source_tuple(value: Any, *, field_name: str) -> tuple[IdentitySourceRef, ...]:
    if not isinstance(value, list):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a list")
    return tuple(IdentitySourceRef.from_mapping(_mapping(item)) for item in value)


def _diff_assets(
    differences: list[IdentityDifference],
    section: str,
    previous: tuple[IdentitySourceRef, ...],
    current: tuple[IdentitySourceRef, ...],
    *,
    major: bool,
) -> None:
    before = {item.ref: item.digest for item in previous}
    after = {item.ref: item.digest for item in current}
    for ref in sorted(set(before) | set(after)):
        if before.get(ref) == after.get(ref):
            continue
        differences.append(
            IdentityDifference(
                section=section,
                field=ref,
                severity=IdentityDiffSeverity.MAJOR if major else IdentityDiffSeverity.MODERATE,
                reason_code=f"IDENTITY_{section.upper()}_HASH_CHANGED",
                previous_value=before.get(ref),
                current_value=after.get(ref),
            )
        )


def _diff_scalar_maps(
    differences: list[IdentityDifference],
    section: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    severity_resolver: Any,
) -> None:
    for field_name in sorted(set(previous) | set(current)):
        before = previous.get(field_name)
        after = current.get(field_name)
        if before == after:
            continue
        severity = severity_resolver(field_name, before, after)
        differences.append(
            IdentityDifference(
                section=section,
                field=field_name,
                severity=severity,
                reason_code=f"IDENTITY_{section.upper()}_{field_name.upper()}_CHANGED",
                previous_value=_scalar_diff_value(before),
                current_value=_scalar_diff_value(after),
            )
        )


def _diff_memory_refs(
    differences: list[IdentityDifference],
    previous: tuple[IdentitySourceRef, ...],
    current: tuple[IdentitySourceRef, ...],
) -> None:
    before = {item.ref: item.digest for item in previous}
    after = {item.ref: item.digest for item in current}
    for ref in sorted(set(before) | set(after)):
        if before.get(ref) == after.get(ref):
            continue
        differences.append(
            IdentityDifference(
                section="memory_refs",
                field=ref,
                severity=IdentityDiffSeverity.MODERATE,
                reason_code="IDENTITY_MEMORY_REFERENCE_CHANGED",
                previous_value=before.get(ref),
                current_value=after.get(ref),
            )
        )


def _diff_source(
    differences: list[IdentityDifference],
    section: str,
    previous: IdentitySourceRef,
    current: IdentitySourceRef,
) -> None:
    if previous == current:
        return
    differences.append(
        IdentityDifference(
            section=section,
            field="source",
            severity=IdentityDiffSeverity.LOW,
            reason_code="IDENTITY_SOURCE_REFERENCE_CHANGED",
            previous_value=previous.digest,
            current_value=current.digest,
        )
    )


def _self_model_severity(field_name: str, before: Any, after: Any) -> IdentityDiffSeverity:
    if field_name == "self_continuity_score" and float(before) - float(after) >= 0.25:
        return IdentityDiffSeverity.MAJOR
    if field_name == "agency_score" and abs(float(after) - float(before)) >= 0.30:
        return IdentityDiffSeverity.MAJOR
    if field_name == "self_update_count" and int(after) < int(before):
        return IdentityDiffSeverity.MAJOR
    if field_name in {
        "self_identity_digest",
        "self_values_digest",
        "self_boundaries_digest",
        "self_boundary_count",
        "self_value_count",
    }:
        return IdentityDiffSeverity.MAJOR
    return IdentityDiffSeverity.MODERATE


def _bond_severity(field_name: str, before: Any, after: Any) -> IdentityDiffSeverity:
    if field_name in {"boundary_status", "explicit_user_boundary", "expression_constraint"}:
        return IdentityDiffSeverity.MAJOR
    if field_name == "boundary_strength" and float(after) < float(before):
        return IdentityDiffSeverity.MAJOR
    return IdentityDiffSeverity.MODERATE


def _style_severity(_field_name: str, before: Any, after: Any) -> IdentityDiffSeverity:
    delta = abs(float(after) - float(before))
    if delta >= 0.25:
        return IdentityDiffSeverity.MAJOR
    if delta >= 0.10:
        return IdentityDiffSeverity.MODERATE
    return IdentityDiffSeverity.LOW


def _flatten_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, list):
            for entry in item:
                if isinstance(entry, list) and len(entry) == 2:
                    result[str(entry[0])] = entry[1]
                else:
                    result[f"{key}.{len(result)}"] = _scalar_diff_value(entry)
        else:
            result[str(key)] = item
    return result


def _scalar_diff_value(value: Any) -> str | int | float | bool | None:
    if value is None or type(value) in {str, int, float, bool}:
        return value
    return _digest_payload(value)


def _severity_rank(value: IdentityDiffSeverity) -> int:
    return {
        IdentityDiffSeverity.NONE: 0,
        IdentityDiffSeverity.LOW: 1,
        IdentityDiffSeverity.MODERATE: 2,
        IdentityDiffSeverity.MAJOR: 3,
    }[value]


def _digest_payload(value: Any) -> str:
    raw = _canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> str:
    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity source must be canonical JSON") from exc


def _validate_json_value(value: Any) -> None:
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "non-finite numbers are forbidden")
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "canonical JSON object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
        return
    raise ContractViolation(CONTRACT_FIELD_INVALID, "identity source contains a non-JSON value")


def _normalize_digest(value: Any) -> str:
    match = _SHA256_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        raise ContractViolation(IDENTITY_SOURCE_UNLOCATABLE, "identity source digest must be SHA-256")
    return "sha256:" + match.group(1).lower()


def _require_ref(value: Any, field_name: str) -> str:
    resolved = str(value or "").strip()
    if not _REF_PATTERN.fullmatch(resolved):
        raise ContractViolation(IDENTITY_SOURCE_UNLOCATABLE, f"{field_name} must be a durable URI")
    return resolved


def _strict_mapping(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    name: str,
    private_error: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{name} must be an object")
    data = dict(value)
    unknown = sorted(set(data) - allowed)
    if unknown:
        reason = IDENTITY_PRIVATE_BODY_REJECTED if private_error else CONTRACT_FIELD_INVALID
        raise ContractViolation(reason, f"{name} contains unsupported fields: {', '.join(map(str, unknown))}")
    return data


def _strict_dataclass_mapping(value: Mapping[str, Any], cls: type[Any]) -> dict[str, Any]:
    expected = {field.name for field in fields(cls)}
    data = _strict_mapping(
        value,
        allowed=expected,
        name=cls.__name__,
    )
    missing = sorted(expected - set(data))
    if missing:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{cls.__name__} is missing fields: {', '.join(missing)}",
        )
    return data


def _strict_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity boolean field is invalid")
    return value


def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    if value is None or value == "":
        return default
    if type(value) is bool:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity integer field is invalid")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity integer field is invalid") from exc
    if resolved < lower or resolved > upper:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity integer field is out of range")
    return resolved


def _nonnegative_int(value: Any) -> int:
    return _bounded_int(value, default=0, lower=0, upper=10**15)


def _finite_float(value: Any, *, default: float, lower: float, upper: float) -> float:
    if value is None or value == "":
        return default
    if type(value) is bool:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity numeric field is invalid")
    try:
        resolved = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity numeric field is invalid") from exc
    if not math.isfinite(resolved) or resolved < lower or resolved > upper:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "identity numeric field is non-finite or out of range")
    return round(resolved, 4)


def _sequence_count(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractViolation(CONTRACT_FIELD_INVALID, "private summary field must be a sequence")
    return len(value)


def _private_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractViolation(CONTRACT_FIELD_INVALID, "private summary field must be a sequence")
    return list(value)


def _mapped_category(value: Any, mapping: Mapping[str, set[str]]) -> str:
    raw = str(value or "").strip()
    upper = raw.upper()
    for result, accepted in mapping.items():
        if raw in accepted or upper in accepted:
            return result
    return "UNKNOWN"


def _allowed_token(value: Any, *, allowed: set[str]) -> str:
    raw = str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    if not _TOKEN_PATTERN.fullmatch(raw) or raw not in allowed:
        return "UNKNOWN"
    return raw


__all__ = [
    "BondReleaseSummary",
    "IDENTITY_DIFF_SCHEMA_VERSION",
    "IDENTITY_GATE_SCHEMA_VERSION",
    "IDENTITY_MAJOR_DIFFERENCE",
    "IDENTITY_PREVIOUS_RELEASE_MISMATCH",
    "IDENTITY_PRIVATE_BODY_REJECTED",
    "IDENTITY_RELEASE_MANIFEST_MISMATCH",
    "IDENTITY_REVIEW_SCHEMA_VERSION",
    "IDENTITY_ROLLBACK_SCHEMA_VERSION",
    "IDENTITY_SNAPSHOT_FEATURE_FLAG",
    "IDENTITY_SNAPSHOT_INVALID",
    "IDENTITY_SNAPSHOT_SCHEMA_VERSION",
    "IDENTITY_SOURCE_UNLOCATABLE",
    "IDENTITY_STARTUP_SNAPSHOT_INVALID",
    "IdentityDiffSeverity",
    "IdentityDifference",
    "IdentityReleaseGate",
    "IdentityReviewCandidate",
    "IdentityRollbackPlan",
    "IdentitySnapshot",
    "IdentitySnapshotDiff",
    "IdentitySourceRef",
    "ProactiveBoundarySummary",
    "SelfModelReleaseSummary",
    "StartupReleaseRef",
    "StyleFingerprintSummary",
    "build_identity_review_candidate",
    "build_identity_rollback_plan",
    "build_identity_snapshot",
    "collect_identity_asset_refs",
    "diff_identity_snapshots",
    "evaluate_identity_release_gate",
    "identity_snapshot_gate_enabled",
]
