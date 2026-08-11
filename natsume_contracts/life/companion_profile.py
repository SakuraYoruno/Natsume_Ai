"""Bounded, reversible companion-expression profile contracts.

These contracts deliberately describe only *how* Natsume may express an
already-frozen feeling.  They cannot change Soul, consent, relationship
identity, self-affect, or any safety boundary.  The canonical Digital Life
journal records lifecycle transitions; this module is pure validation and
deterministic identity only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


_SELF_ID = "natsume_ai"
_MAX_EVIDENCE_REFS = 16
_MAX_KNOWN_PROFILES = 8
_CANARY_INTERACTION_COUNT = 5

_ADDRESSING_STYLES = frozenset(
    {"DEFAULT", "WARM_FAMILIAR", "PLAYFUL_LIGHT", "GENTLE_DIRECT"}
)
_RESPONSE_LENGTHS = frozenset({"COMPACT", "BALANCED", "EXPANDED"})
_HUMOR_LEVELS = frozenset({"LOW", "LIGHT", "MEDIUM"})
_FOLLOWUP_RATES = frozenset({"NONE", "OCCASIONAL", "GENTLE"})
_COMFORT_MODES = frozenset({"ASK_FIRST", "QUIET_FIRST", "PRACTICAL_GENTLE"})
_PROACTIVE_WINDOWS = frozenset({"OFF", "DAYTIME", "EVENING", "FLEXIBLE"})
_COMPANION_PROFILE_CONTROL_REQUEST_SCHEMA = "companion_profile_control_request.v1"
_COMPANION_PROFILE_ROLLBACK_REQUEST_SCHEMA = "companion_profile_rollback_request.v1"


def _required(value: Any, field_name: str, *, limit: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _optional(value: Any, field_name: str, *, limit: int = 512) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return result


def _unit(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite value in [0, 1]") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{field_name} must be a finite value in [0, 1]")
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be a value in [0, 1]")
    return round(result, 6)


def _bounded_float(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{field_name} must be a finite number")
    if not minimum <= result <= maximum:
        raise ValueError(f"{field_name} must be in [{minimum}, {maximum}]")
    return round(result, 6)


def _opaque_ref(value: Any, field_name: str) -> str:
    reference = _required(value, field_name)
    if "://" not in reference:
        raise ValueError(f"{field_name} must be an opaque reference")
    return reference


def _refs(value: Sequence[Any], field_name: str, *, minimum: int = 0) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence")
    refs = tuple(_opaque_ref(item, field_name) for item in value)
    if len(refs) < minimum or len(refs) > _MAX_EVIDENCE_REFS:
        raise ValueError(f"{field_name} must contain {minimum}..{_MAX_EVIDENCE_REFS} refs")
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field_name} must not contain duplicates")
    return refs


def _days(value: Sequence[Any], field_name: str, *, minimum: int = 0) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence")
    days = tuple(_required(item, field_name, limit=16) for item in value)
    if len(days) < minimum or len(days) > _MAX_EVIDENCE_REFS:
        raise ValueError(f"{field_name} must contain {minimum}..{_MAX_EVIDENCE_REFS} days")
    if len(set(days)) != len(days):
        raise ValueError(f"{field_name} must not contain duplicates")
    for day in days:
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{field_name} contains an invalid UTC day") from exc
    return days


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CompanionProfilePreferencesV1:
    """Low-risk expression preferences; never a persona or consent patch."""

    addressing_style: str = "DEFAULT"
    response_length: str = "BALANCED"
    humor_level: str = "LIGHT"
    followup_rate: str = "GENTLE"
    comfort_mode: str = "ASK_FIRST"
    proactive_window: str = "FLEXIBLE"
    tts_rate_scale: float = 1.0
    avatar_motion_intensity: float = 0.5

    def __post_init__(self) -> None:
        values = {
            "addressing_style": (_ADDRESSING_STYLES, self.addressing_style),
            "response_length": (_RESPONSE_LENGTHS, self.response_length),
            "humor_level": (_HUMOR_LEVELS, self.humor_level),
            "followup_rate": (_FOLLOWUP_RATES, self.followup_rate),
            "comfort_mode": (_COMFORT_MODES, self.comfort_mode),
            "proactive_window": (_PROACTIVE_WINDOWS, self.proactive_window),
        }
        for field_name, (allowed, raw_value) in values.items():
            normalized = _required(raw_value, field_name, limit=64).upper()
            if normalized not in allowed:
                raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(
            self,
            "tts_rate_scale",
            _bounded_float(
                self.tts_rate_scale,
                "tts_rate_scale",
                minimum=0.85,
                maximum=1.15,
            ),
        )
        object.__setattr__(
            self,
            "avatar_motion_intensity",
            _bounded_float(
                self.avatar_motion_intensity,
                "avatar_motion_intensity",
                minimum=0.2,
                maximum=0.8,
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProfilePreferencesV1":
        if not isinstance(value, Mapping):
            raise ValueError("preferences must be an object")
        return cls(
            addressing_style=value.get("addressing_style", "DEFAULT"),
            response_length=value.get("response_length", "BALANCED"),
            humor_level=value.get("humor_level", "LIGHT"),
            followup_rate=value.get("followup_rate", "GENTLE"),
            comfort_mode=value.get("comfort_mode", "ASK_FIRST"),
            proactive_window=value.get("proactive_window", "FLEXIBLE"),
            tts_rate_scale=value.get("tts_rate_scale", 1.0),
            avatar_motion_intensity=value.get("avatar_motion_intensity", 0.5),
        )

    @classmethod
    def from_strict_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "CompanionProfilePreferencesV1":
        """Parse an explicit external preference control without defaults.

        The internal reducer may use ``from_mapping`` while replaying older
        snapshots.  A user-control wire request must name exactly the bounded
        preference surface, so an omitted/unknown field cannot silently alter
        how a later version is interpreted.
        """

        if not isinstance(value, Mapping):
            raise ValueError("preferences must be an object")
        expected = {
            "schema_version",
            "addressing_style",
            "response_length",
            "humor_level",
            "followup_rate",
            "comfort_mode",
            "proactive_window",
            "tts_rate_scale",
            "avatar_motion_intensity",
        }
        if set(value) != expected:
            raise ValueError("companion profile preference field set is invalid")
        if value.get("schema_version") != "companion_profile_preferences.v1":
            raise ValueError("companion profile preference schema is invalid")
        for field_name in (
            "addressing_style",
            "response_length",
            "humor_level",
            "followup_rate",
            "comfort_mode",
            "proactive_window",
        ):
            raw = value.get(field_name)
            if not isinstance(raw, str) or raw != raw.strip():
                raise ValueError(f"{field_name} must be an exact string")
        for field_name in ("tts_rate_scale", "avatar_motion_intensity"):
            raw = value.get(field_name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{field_name} must be a JSON number")
        return cls(
            addressing_style=value["addressing_style"],
            response_length=value["response_length"],
            humor_level=value["humor_level"],
            followup_rate=value["followup_rate"],
            comfort_mode=value["comfort_mode"],
            proactive_window=value["proactive_window"],
            tts_rate_scale=value["tts_rate_scale"],
            avatar_motion_intensity=value["avatar_motion_intensity"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_profile_preferences.v1",
            "addressing_style": self.addressing_style,
            "response_length": self.response_length,
            "humor_level": self.humor_level,
            "followup_rate": self.followup_rate,
            "comfort_mode": self.comfort_mode,
            "proactive_window": self.proactive_window,
            "tts_rate_scale": self.tts_rate_scale,
            "avatar_motion_intensity": self.avatar_motion_intensity,
        }


@dataclass(frozen=True, slots=True)
class CompanionProfileControlRequestV1:
    """Strict, user-authenticated request to trial bounded expression prefs.

    It carries no free-form rationale, raw conversation, self-affect patch,
    relationship consent, or personality material.  Secure Ingress supplies
    the caller identity and durable audit reference separately.
    """

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    preferences: CompanionProfilePreferencesV1
    schema_version: str = _COMPANION_PROFILE_CONTROL_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _COMPANION_PROFILE_CONTROL_REQUEST_SCHEMA:
            raise ValueError("companion profile control request schema is invalid")
        if not isinstance(self.preferences, CompanionProfilePreferencesV1):
            raise ValueError("preferences must be CompanionProfilePreferencesV1")
        object.__setattr__(
            self,
            "workspace_id",
            _required(self.workspace_id, "workspace_id", limit=240),
        )
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(
                self.relationship_subject_id,
                "relationship_subject_id",
                limit=240,
            ),
        )
        object.__setattr__(
            self,
            "session_id",
            _required(self.session_id, "session_id", limit=240),
        )
        object.__setattr__(
            self,
            "schema_version",
            _COMPANION_PROFILE_CONTROL_REQUEST_SCHEMA,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProfileControlRequestV1":
        if not isinstance(value, Mapping):
            raise ValueError("companion profile control request must be an object")
        expected = {
            "schema_version",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
            "preferences",
        }
        if set(value) != expected:
            raise ValueError("companion profile control request field set is invalid")
        for field_name in (
            "schema_version",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
        ):
            raw = value.get(field_name)
            if not isinstance(raw, str) or raw != raw.strip():
                raise ValueError(
                    f"companion profile control request {field_name} must be an exact string"
                )
        preferences = value.get("preferences")
        return cls(
            schema_version=value["schema_version"],
            workspace_id=value["workspace_id"],
            relationship_subject_id=value["relationship_subject_id"],
            session_id=value["session_id"],
            preferences=CompanionProfilePreferencesV1.from_strict_mapping(
                preferences if isinstance(preferences, Mapping) else {}
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "preferences": self.preferences.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CompanionProfileRollbackRequestV1:
    """Signed request to restore the exact predecessor of one active profile.

    The caller cannot choose an arbitrary historical version.  It only names
    the profile it currently sees; the canonical lifecycle reopens that active
    profile's immutable rollback reference and rejects any stale request.
    """

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    expected_active_profile_id: str
    schema_version: str = _COMPANION_PROFILE_ROLLBACK_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _COMPANION_PROFILE_ROLLBACK_REQUEST_SCHEMA:
            raise ValueError("companion profile rollback request schema is invalid")
        workspace = _required(self.workspace_id, "workspace_id", limit=240)
        subject = _required(
            self.relationship_subject_id,
            "relationship_subject_id",
            limit=240,
        )
        session = _required(self.session_id, "session_id", limit=240)
        profile_id = _required(
            self.expected_active_profile_id,
            "expected_active_profile_id",
            limit=240,
        )
        if not profile_id.startswith("companion-profile::"):
            raise ValueError(
                "expected_active_profile_id must name one companion profile"
            )
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "relationship_subject_id", subject)
        object.__setattr__(self, "session_id", session)
        object.__setattr__(self, "expected_active_profile_id", profile_id)
        object.__setattr__(
            self,
            "schema_version",
            _COMPANION_PROFILE_ROLLBACK_REQUEST_SCHEMA,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "CompanionProfileRollbackRequestV1":
        if not isinstance(value, Mapping):
            raise ValueError("companion profile rollback request must be an object")
        expected = {
            "schema_version",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
            "expected_active_profile_id",
        }
        if set(value) != expected:
            raise ValueError("companion profile rollback request field set is invalid")
        for field_name in expected:
            raw = value.get(field_name)
            if not isinstance(raw, str) or raw != raw.strip():
                raise ValueError(
                    "companion profile rollback request "
                    f"{field_name} must be an exact string"
                )
        return cls(
            schema_version=value["schema_version"],
            workspace_id=value["workspace_id"],
            relationship_subject_id=value["relationship_subject_id"],
            session_id=value["session_id"],
            expected_active_profile_id=value["expected_active_profile_id"],
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "expected_active_profile_id": self.expected_active_profile_id,
        }


@dataclass(frozen=True, slots=True)
class CompanionProfileVersionV1:
    """A content-addressed expression-profile version.

    A profile carries only opaque aftereffect evidence.  It is intentionally
    incapable of carrying Soul text, raw conversation, identity, consent, or
    an arbitrary prompt fragment.
    """

    profile_id: str
    workspace_id: str
    relationship_subject_id: str
    version: int
    preferences: CompanionProfilePreferencesV1
    source_aftereffect_refs: Sequence[str]
    evidence_days_utc: Sequence[str]
    confidence_lower_bound: float
    boundary_violation_count: int
    rollback_profile_id: str | None
    created_at_ms: int
    source_control_ref: str | None = None
    is_baseline: bool = False
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        if not isinstance(self.preferences, CompanionProfilePreferencesV1):
            raise ValueError("preferences must be CompanionProfilePreferencesV1")
        workspace_id = _required(self.workspace_id, "workspace_id", limit=240)
        subject_id = _required(
            self.relationship_subject_id,
            "relationship_subject_id",
            limit=240,
        )
        version = _non_negative_int(self.version, "version")
        baseline = bool(self.is_baseline)
        refs = _refs(
            self.source_aftereffect_refs,
            "source_aftereffect_refs",
            minimum=0 if baseline else 5,
        )
        days = _days(
            self.evidence_days_utc,
            "evidence_days_utc",
            minimum=0 if baseline else 3,
        )
        confidence = _unit(self.confidence_lower_bound, "confidence_lower_bound")
        violations = _non_negative_int(
            self.boundary_violation_count,
            "boundary_violation_count",
        )
        rollback_profile_id = _optional(
            self.rollback_profile_id,
            "rollback_profile_id",
            limit=240,
        )
        created_at_ms = _non_negative_int(self.created_at_ms, "created_at_ms")
        source_control_ref = _optional(
            self.source_control_ref,
            "source_control_ref",
            limit=512,
        )
        if source_control_ref is not None:
            source_control_ref = _opaque_ref(
                source_control_ref,
                "source_control_ref",
            )
        if baseline:
            if version != 0:
                raise ValueError("baseline profile version must be 0")
            if (
                refs
                or days
                or confidence != 0.0
                or violations != 0
                or rollback_profile_id
                or source_control_ref
            ):
                raise ValueError("baseline profile cannot claim learning evidence")
        else:
            if version < 1:
                raise ValueError("learned profile version must be positive")
            if len(refs) < 5 or len(days) < 3:
                raise ValueError("learned profile requires five aftereffects across three UTC days")
            if confidence < 0.85:
                raise ValueError("learned profile confidence lower bound must be at least 0.85")
            if violations != 0:
                raise ValueError("learned profile requires zero boundary violations")
            if not rollback_profile_id:
                raise ValueError("learned profile requires an exact rollback profile")
        identity = {
            "schema_version": "companion_profile_version.v1",
            "self_id": _SELF_ID,
            "workspace_id": workspace_id,
            "relationship_subject_id": subject_id,
            "version": version,
            "preferences": self.preferences.as_dict(),
            "source_aftereffect_refs": list(refs),
            "evidence_days_utc": list(days),
            "confidence_lower_bound": confidence,
            "boundary_violation_count": violations,
            "rollback_profile_id": rollback_profile_id,
            "created_at_ms": created_at_ms,
            "is_baseline": baseline,
        }
        if source_control_ref is not None:
            identity["source_control_ref"] = source_control_ref
        expected_id = f"companion-profile::{_digest(identity)[:40]}"
        if _required(self.profile_id, "profile_id", limit=240) != expected_id:
            raise ValueError("profile_id must match content-addressed profile identity")
        object.__setattr__(self, "profile_id", expected_id)
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "relationship_subject_id", subject_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "source_aftereffect_refs", refs)
        object.__setattr__(self, "evidence_days_utc", days)
        object.__setattr__(self, "confidence_lower_bound", confidence)
        object.__setattr__(self, "boundary_violation_count", violations)
        object.__setattr__(self, "rollback_profile_id", rollback_profile_id)
        object.__setattr__(self, "created_at_ms", created_at_ms)
        object.__setattr__(self, "source_control_ref", source_control_ref)
        object.__setattr__(self, "is_baseline", baseline)
        object.__setattr__(self, "self_id", _SELF_ID)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        version: Any,
        preferences: CompanionProfilePreferencesV1,
        source_aftereffect_refs: Sequence[Any],
        evidence_days_utc: Sequence[Any],
        confidence_lower_bound: Any,
        boundary_violation_count: Any,
        rollback_profile_id: Any,
        created_at_ms: Any,
        source_control_ref: Any = None,
        is_baseline: bool = False,
    ) -> "CompanionProfileVersionV1":
        """Construct a correctly content-addressed immutable profile."""

        workspace = _required(workspace_id, "workspace_id", limit=240)
        subject = _required(
            relationship_subject_id,
            "relationship_subject_id",
            limit=240,
        )
        normalized_version = _non_negative_int(version, "version")
        if not isinstance(preferences, CompanionProfilePreferencesV1):
            raise ValueError("preferences must be CompanionProfilePreferencesV1")
        refs = _refs(
            source_aftereffect_refs,
            "source_aftereffect_refs",
            minimum=0 if is_baseline else 5,
        )
        days = _days(
            evidence_days_utc,
            "evidence_days_utc",
            minimum=0 if is_baseline else 3,
        )
        confidence = _unit(confidence_lower_bound, "confidence_lower_bound")
        violations = _non_negative_int(
            boundary_violation_count,
            "boundary_violation_count",
        )
        rollback = _optional(rollback_profile_id, "rollback_profile_id", limit=240)
        created = _non_negative_int(created_at_ms, "created_at_ms")
        control_ref = _optional(source_control_ref, "source_control_ref", limit=512)
        if control_ref is not None:
            control_ref = _opaque_ref(control_ref, "source_control_ref")
        identity = {
            "schema_version": "companion_profile_version.v1",
            "self_id": _SELF_ID,
            "workspace_id": workspace,
            "relationship_subject_id": subject,
            "version": normalized_version,
            "preferences": preferences.as_dict(),
            "source_aftereffect_refs": list(refs),
            "evidence_days_utc": list(days),
            "confidence_lower_bound": confidence,
            "boundary_violation_count": violations,
            "rollback_profile_id": rollback,
            "created_at_ms": created,
            "is_baseline": bool(is_baseline),
        }
        if control_ref is not None:
            identity["source_control_ref"] = control_ref
        return cls(
            profile_id=f"companion-profile::{_digest(identity)[:40]}",
            workspace_id=workspace,
            relationship_subject_id=subject,
            version=normalized_version,
            preferences=preferences,
            source_aftereffect_refs=refs,
            evidence_days_utc=days,
            confidence_lower_bound=confidence,
            boundary_violation_count=violations,
            rollback_profile_id=rollback,
            created_at_ms=created,
            source_control_ref=control_ref,
            is_baseline=bool(is_baseline),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProfileVersionV1":
        if not isinstance(value, Mapping):
            raise ValueError("companion profile must be an object")
        return cls(
            profile_id=value.get("profile_id"),
            workspace_id=value.get("workspace_id"),
            relationship_subject_id=value.get("relationship_subject_id"),
            version=value.get("version"),
            preferences=CompanionProfilePreferencesV1.from_mapping(
                value.get("preferences") if isinstance(value.get("preferences"), Mapping) else {}
            ),
            source_aftereffect_refs=value.get("source_aftereffect_refs") or (),
            evidence_days_utc=value.get("evidence_days_utc") or (),
            confidence_lower_bound=value.get("confidence_lower_bound", 0.0),
            boundary_violation_count=value.get("boundary_violation_count", 0),
            rollback_profile_id=value.get("rollback_profile_id"),
            created_at_ms=value.get("created_at_ms", 0),
            source_control_ref=value.get("source_control_ref"),
            is_baseline=bool(value.get("is_baseline")),
            self_id=value.get("self_id", _SELF_ID),
        )

    @property
    def profile_digest(self) -> str:
        """Return the canonical digest serialized with this profile.

        ``as_dict`` deliberately computes the digest from the immutable
        profile payload (without recursively hashing its own envelope).  The
        planner, delivery protocol and SQLite projection must carry that same
        value; hashing the already-enveloped mapping here would create a
        second, incompatible identity for one profile version.
        """

        return str(self.as_dict()["profile_digest"])

    def as_dict(self) -> dict[str, Any]:
        digest_payload: dict[str, Any] = {
            "schema_version": "companion_profile_version.v1",
            "profile_id": self.profile_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "version": self.version,
            "preferences": self.preferences.as_dict(),
            "source_aftereffect_refs": list(self.source_aftereffect_refs),
            "evidence_days_utc": list(self.evidence_days_utc),
            "confidence_lower_bound": self.confidence_lower_bound,
            "boundary_violation_count": self.boundary_violation_count,
            "rollback_profile_id": self.rollback_profile_id,
            "created_at_ms": self.created_at_ms,
            "is_baseline": self.is_baseline,
        }
        if self.source_control_ref is not None:
            digest_payload["source_control_ref"] = self.source_control_ref
        result: dict[str, Any] = {
            "schema_version": "companion_profile_version.v1",
            "profile_id": self.profile_id,
            "profile_digest": _digest(digest_payload),
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "version": self.version,
            "preferences": self.preferences.as_dict(),
            "source_aftereffect_refs": list(self.source_aftereffect_refs),
            "evidence_days_utc": list(self.evidence_days_utc),
            "confidence_lower_bound": self.confidence_lower_bound,
            "boundary_violation_count": self.boundary_violation_count,
            "rollback_profile_id": self.rollback_profile_id,
            "created_at_ms": self.created_at_ms,
            "is_baseline": self.is_baseline,
        }
        if self.source_control_ref is not None:
            result["source_control_ref"] = self.source_control_ref
        return result


@dataclass(frozen=True, slots=True)
class CompanionProfileCanaryInteractionV1:
    """One accepted post-activation-style interaction observed during canary."""

    interaction_id: str
    profile_id: str
    aftereffect_id: str
    aftereffect_ref: str
    observed_at_ms: int

    def __post_init__(self) -> None:
        profile_id = _required(self.profile_id, "profile_id", limit=240)
        aftereffect_id = _required(self.aftereffect_id, "aftereffect_id", limit=240)
        aftereffect_ref = _opaque_ref(self.aftereffect_ref, "aftereffect_ref")
        observed_at_ms = _non_negative_int(self.observed_at_ms, "observed_at_ms")
        identity = {
            "schema_version": "companion_profile_canary_interaction.v1",
            "profile_id": profile_id,
            "aftereffect_id": aftereffect_id,
            "aftereffect_ref": aftereffect_ref,
            "observed_at_ms": observed_at_ms,
        }
        expected_id = f"companion-profile-canary::{_digest(identity)[:40]}"
        if _required(self.interaction_id, "interaction_id", limit=240) != expected_id:
            raise ValueError("interaction_id must match content-addressed canary identity")
        object.__setattr__(self, "interaction_id", expected_id)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "aftereffect_id", aftereffect_id)
        object.__setattr__(self, "aftereffect_ref", aftereffect_ref)
        object.__setattr__(self, "observed_at_ms", observed_at_ms)

    @classmethod
    def create(
        cls,
        *,
        profile_id: Any,
        aftereffect_id: Any,
        aftereffect_ref: Any,
        observed_at_ms: Any,
    ) -> "CompanionProfileCanaryInteractionV1":
        """Construct a deterministic canary interaction identity."""

        profile = _required(profile_id, "profile_id", limit=240)
        aftereffect = _required(aftereffect_id, "aftereffect_id", limit=240)
        reference = _opaque_ref(aftereffect_ref, "aftereffect_ref")
        observed = _non_negative_int(observed_at_ms, "observed_at_ms")
        identity = {
            "schema_version": "companion_profile_canary_interaction.v1",
            "profile_id": profile,
            "aftereffect_id": aftereffect,
            "aftereffect_ref": reference,
            "observed_at_ms": observed,
        }
        return cls(
            interaction_id=f"companion-profile-canary::{_digest(identity)[:40]}",
            profile_id=profile,
            aftereffect_id=aftereffect,
            aftereffect_ref=reference,
            observed_at_ms=observed,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProfileCanaryInteractionV1":
        if not isinstance(value, Mapping):
            raise ValueError("canary interaction must be an object")
        return cls(
            interaction_id=value.get("interaction_id"),
            profile_id=value.get("profile_id"),
            aftereffect_id=value.get("aftereffect_id"),
            aftereffect_ref=value.get("aftereffect_ref"),
            observed_at_ms=value.get("observed_at_ms", 0),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_profile_canary_interaction.v1",
            "interaction_id": self.interaction_id,
            "profile_id": self.profile_id,
            "aftereffect_id": self.aftereffect_id,
            "aftereffect_ref": self.aftereffect_ref,
            "observed_at_ms": self.observed_at_ms,
        }


@dataclass(frozen=True, slots=True)
class CompanionProfileStateV1:
    """Bounded head reconstructed solely from immutable Life events."""

    workspace_id: str
    relationship_subject_id: str
    revision: int
    active_profile: CompanionProfileVersionV1
    canary_profile: CompanionProfileVersionV1 | None = None
    canary_accepted_interaction_ids: Sequence[str] = ()
    known_profiles: Sequence[CompanionProfileVersionV1] = ()
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        if not isinstance(self.active_profile, CompanionProfileVersionV1):
            raise ValueError("active_profile must be CompanionProfileVersionV1")
        workspace_id = _required(self.workspace_id, "workspace_id", limit=240)
        subject_id = _required(
            self.relationship_subject_id,
            "relationship_subject_id",
            limit=240,
        )
        revision = _non_negative_int(self.revision, "revision")
        known = tuple(self.known_profiles)
        if len(known) > _MAX_KNOWN_PROFILES:
            raise ValueError(f"known_profiles cannot exceed {_MAX_KNOWN_PROFILES}")
        if not known:
            known = (self.active_profile,)
        if not all(isinstance(item, CompanionProfileVersionV1) for item in known):
            raise ValueError("known_profiles must contain CompanionProfileVersionV1")
        profile_ids = tuple(item.profile_id for item in known)
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("known_profiles must not contain duplicate profile ids")
        for profile in known:
            if (
                profile.workspace_id != workspace_id
                or profile.relationship_subject_id != subject_id
            ):
                raise ValueError("known profile scope drift")
        if self.active_profile.profile_id not in profile_ids:
            raise ValueError("active_profile must be present in known_profiles")
        candidate = self.canary_profile
        if candidate is not None:
            if not isinstance(candidate, CompanionProfileVersionV1):
                raise ValueError("canary_profile must be CompanionProfileVersionV1")
            if candidate.profile_id not in profile_ids:
                raise ValueError("canary_profile must be present in known_profiles")
            if candidate.profile_id == self.active_profile.profile_id:
                raise ValueError("canary_profile must differ from active_profile")
        interactions = tuple(
            _required(item, "canary_accepted_interaction_ids", limit=240)
            for item in self.canary_accepted_interaction_ids
        )
        if len(interactions) > _CANARY_INTERACTION_COUNT:
            raise ValueError(
                f"canary_accepted_interaction_ids cannot exceed {_CANARY_INTERACTION_COUNT}"
            )
        if len(set(interactions)) != len(interactions):
            raise ValueError("canary_accepted_interaction_ids must not contain duplicates")
        if candidate is None and interactions:
            raise ValueError("canary interactions require a canary profile")
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "relationship_subject_id", subject_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "known_profiles", known)
        object.__setattr__(self, "canary_accepted_interaction_ids", interactions)
        object.__setattr__(self, "self_id", _SELF_ID)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProfileStateV1":
        if not isinstance(value, Mapping):
            raise ValueError("companion profile state must be an object")
        active_raw = value.get("active_profile")
        if not isinstance(active_raw, Mapping):
            raise ValueError("companion profile state requires active_profile")
        candidate_raw = value.get("canary_profile")
        known_raw = value.get("known_profiles") or ()
        if not isinstance(known_raw, Sequence) or isinstance(
            known_raw, (str, bytes, bytearray)
        ):
            raise ValueError("known_profiles must be a sequence")
        return cls(
            workspace_id=value.get("workspace_id"),
            relationship_subject_id=value.get("relationship_subject_id"),
            revision=value.get("revision", 0),
            active_profile=CompanionProfileVersionV1.from_mapping(active_raw),
            canary_profile=(
                CompanionProfileVersionV1.from_mapping(candidate_raw)
                if isinstance(candidate_raw, Mapping)
                else None
            ),
            canary_accepted_interaction_ids=value.get("canary_accepted_interaction_ids") or (),
            known_profiles=tuple(
                CompanionProfileVersionV1.from_mapping(item)
                for item in known_raw
                if isinstance(item, Mapping)
            ),
            self_id=value.get("self_id", _SELF_ID),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_profile_state.v1",
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "revision": self.revision,
            "active_profile": self.active_profile.as_dict(),
            "canary_profile": (
                self.canary_profile.as_dict()
                if self.canary_profile is not None
                else None
            ),
            "canary_accepted_interaction_ids": list(
                self.canary_accepted_interaction_ids
            ),
            "known_profiles": [profile.as_dict() for profile in self.known_profiles],
        }


def baseline_companion_profile(
    *, workspace_id: Any, relationship_subject_id: Any
) -> CompanionProfileVersionV1:
    """Return the deterministic pre-learning expression baseline for one scope."""

    workspace = _required(workspace_id, "workspace_id", limit=240)
    subject = _required(
        relationship_subject_id,
        "relationship_subject_id",
        limit=240,
    )
    preferences = CompanionProfilePreferencesV1()
    return CompanionProfileVersionV1.create(
        workspace_id=workspace,
        relationship_subject_id=subject,
        version=0,
        preferences=preferences,
        source_aftereffect_refs=(),
        evidence_days_utc=(),
        confidence_lower_bound=0.0,
        boundary_violation_count=0,
        rollback_profile_id=None,
        created_at_ms=0,
        is_baseline=True,
    )


def utc_day_from_ms(value: Any) -> str:
    """Expose one deterministic day bucketing rule for evidence validation."""

    timestamp = _non_negative_int(value, "observed_at_ms")
    return datetime.fromtimestamp(timestamp / 1_000.0, tz=timezone.utc).date().isoformat()


__all__ = [
    "CompanionProfileCanaryInteractionV1",
    "CompanionProfileControlRequestV1",
    "CompanionProfilePreferencesV1",
    "CompanionProfileRollbackRequestV1",
    "CompanionProfileStateV1",
    "CompanionProfileVersionV1",
    "baseline_companion_profile",
    "utc_day_from_ms",
]
