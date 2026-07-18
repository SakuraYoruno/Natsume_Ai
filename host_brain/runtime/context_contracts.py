"""Typed, workspace-scoped World/Scene/Intent context contracts for NE-03."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONSTRAINT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,95}$")
_DURABLE_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[^\s\x00-\x1f\x7f]{1,511}$"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,239}$")


class ContextContractError(ValueError):
    """Context cannot be safely bound to a workspace and graph revision."""


def _text(value: Any, field: str, limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ContextContractError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise ContextContractError(f"{field} must be a SHA-256 digest")
    return result


def _durable_ref(value: Any, field: str) -> str:
    result = _text(value, field, 544)
    if _DURABLE_REF_RE.fullmatch(result) is None:
        raise ContextContractError(f"{field} must be a durable reference")
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field)
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise ContextContractError(f"{field} must be a canonical identifier")
    return result


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ContextContractError("context value must be JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class WorldFactV2:
    workspace_id: str
    subject_id: str
    key: str
    value: Any
    source_ref: str
    confidence: float
    observed_at_ms: int
    expires_at_ms: int
    conflict_group: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "key"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "source_ref",
            _durable_ref(self.source_ref, "source_ref"),
        )
        value = _json_copy(self.value)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ContextContractError("confidence must be within [0, 1]")
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in (self.observed_at_ms, self.expires_at_ms, self.revision)):
            raise ContextContractError("timestamps and revision must be non-negative integers")
        if self.expires_at_ms <= self.observed_at_ms:
            raise ContextContractError("expires_at_ms must exceed observed_at_ms")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "confidence", round(confidence, 6))
        object.__setattr__(self, "conflict_group", str(self.conflict_group or "").strip())

    @property
    def fact_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "key": self.key,
            "value": _json_copy(self.value),
            "source_ref": self.source_ref,
            "confidence": self.confidence,
            "observed_at_ms": self.observed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "conflict_group": self.conflict_group,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class WorldSnapshotV2:
    workspace_id: str
    subject_id: str
    revision: int
    facts: tuple[WorldFactV2, ...]
    snapshot_digest: str = ""

    def __post_init__(self) -> None:
        workspace = _text(self.workspace_id, "workspace_id")
        subject = _text(self.subject_id, "subject_id")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ContextContractError("snapshot revision must be non-negative")
        facts = tuple(self.facts)
        if not all(isinstance(item, WorldFactV2) for item in facts):
            raise ContextContractError("facts must contain WorldFactV2 values")
        if any(item.workspace_id != workspace or item.subject_id != subject for item in facts):
            raise ContextContractError("WorldFact scope differs from WorldSnapshot scope")
        ordered = tuple(sorted(facts, key=lambda item: (item.key, item.source_ref, item.fact_digest)))
        computed = _json_digest({"workspace_id": workspace, "subject_id": subject, "revision": self.revision, "facts": [item.to_dict() for item in ordered]})
        supplied = str(self.snapshot_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("snapshot_digest does not bind facts")
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "subject_id", subject)
        object.__setattr__(self, "facts", ordered)
        object.__setattr__(self, "snapshot_digest", computed)

    def competing_facts(self, key: str) -> tuple[WorldFactV2, ...]:
        """Return all sources for a key; confidence never hides conflicts."""

        return tuple(item for item in self.facts if item.key == key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.world.snapshot.v2",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "revision": self.revision,
            "facts": [item.to_dict() for item in self.facts],
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True, slots=True)
class WorldFactExpiryV1:
    """Explicit removal of one fact after its TTL becomes effective."""

    fact_digest: str
    authority_ref: str
    effective_at_ms: int
    reason_code: str = "TTL_EXPIRED"
    expiry_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_digest", _digest(self.fact_digest, "fact_digest"))
        object.__setattr__(
            self,
            "authority_ref",
            _durable_ref(self.authority_ref, "authority_ref"),
        )
        if (
            isinstance(self.effective_at_ms, bool)
            or not isinstance(self.effective_at_ms, int)
            or self.effective_at_ms < 0
        ):
            raise ContextContractError("effective_at_ms must be non-negative")
        reason = _text(self.reason_code, "reason_code", 96).upper()
        if reason != "TTL_EXPIRED":
            raise ContextContractError("WorldFactExpiryV1 only represents TTL_EXPIRED")
        object.__setattr__(self, "reason_code", reason)
        computed = _json_digest(self.to_dict(include_digest=False))
        supplied = str(self.expiry_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("World fact expiry digest drift")
        object.__setattr__(self, "expiry_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "natsume.world-fact-expiry.v1",
            "fact_digest": self.fact_digest,
            "authority_ref": self.authority_ref,
            "effective_at_ms": self.effective_at_ms,
            "reason_code": self.reason_code,
        }
        if include_digest:
            payload["expiry_digest"] = self.expiry_digest
        return payload


@dataclass(frozen=True, slots=True)
class WorldObservationBatchV1:
    """One monotonic workspace observation-journal transition."""

    workspace_id: str
    subject_id: str
    runtime_scope_id: str
    runtime_owner_id: str
    runtime_lease_id: str
    fencing_token: int
    revision: int
    previous_batch_digest: str
    source_event_ref: str
    source_event_digest: str
    observed_at_ms: int
    upserts: tuple[WorldFactV2, ...] = ()
    expiries: tuple[WorldFactExpiryV1, ...] = ()
    batch_digest: str = ""

    def __post_init__(self) -> None:
        workspace = _identifier(self.workspace_id, "workspace_id")
        subject = _identifier(self.subject_id, "subject_id")
        runtime_scope = _identifier(self.runtime_scope_id, "runtime_scope_id")
        runtime_owner = _identifier(self.runtime_owner_id, "runtime_owner_id")
        runtime_lease = _identifier(self.runtime_lease_id, "runtime_lease_id")
        if (
            isinstance(self.fencing_token, bool)
            or not isinstance(self.fencing_token, int)
            or self.fencing_token < 1
        ):
            raise ContextContractError("observation fencing_token must be positive")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ContextContractError("observation batch revision must be positive")
        previous = str(self.previous_batch_digest or "").strip().lower()
        if self.revision == 1:
            if previous:
                raise ContextContractError(
                    "first observation batch cannot have a previous digest"
                )
        elif not _DIGEST_RE.fullmatch(previous):
            raise ContextContractError(
                "observation batch previous digest is required after revision one"
            )
        if (
            isinstance(self.observed_at_ms, bool)
            or not isinstance(self.observed_at_ms, int)
            or self.observed_at_ms < 0
        ):
            raise ContextContractError("observed_at_ms must be non-negative")
        object.__setattr__(
            self,
            "source_event_ref",
            _durable_ref(self.source_event_ref, "source_event_ref"),
        )
        object.__setattr__(
            self,
            "source_event_digest",
            _digest(self.source_event_digest, "source_event_digest"),
        )
        upserts = tuple(self.upserts)
        if len(upserts) > 1024 or not all(
            isinstance(item, WorldFactV2) for item in upserts
        ):
            raise ContextContractError("observation upserts are invalid or unbounded")
        if any(
            item.workspace_id != workspace or item.subject_id != subject
            for item in upserts
        ):
            raise ContextContractError("observation upsert scope mismatch")
        ordered_upserts = tuple(
            sorted(
                {item.fact_digest: item for item in upserts}.values(),
                key=lambda item: item.fact_digest,
            )
        )
        expiries = tuple(self.expiries)
        if len(expiries) > 1024 or not all(
            isinstance(item, WorldFactExpiryV1) for item in expiries
        ):
            raise ContextContractError("observation expiries are invalid or unbounded")
        expiry_targets = [item.fact_digest for item in expiries]
        if len(set(expiry_targets)) != len(expiry_targets):
            raise ContextContractError(
                "one batch cannot expire the same fact more than once"
            )
        ordered_expiries = tuple(
            sorted(
                {item.expiry_digest: item for item in expiries}.values(),
                key=lambda item: item.expiry_digest,
            )
        )
        if {item.fact_digest for item in ordered_upserts}.intersection(
            item.fact_digest for item in ordered_expiries
        ):
            raise ContextContractError("one batch cannot upsert and expire the same fact")
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "subject_id", subject)
        object.__setattr__(self, "runtime_scope_id", runtime_scope)
        object.__setattr__(self, "runtime_owner_id", runtime_owner)
        object.__setattr__(self, "runtime_lease_id", runtime_lease)
        object.__setattr__(self, "previous_batch_digest", previous)
        object.__setattr__(self, "upserts", ordered_upserts)
        object.__setattr__(self, "expiries", ordered_expiries)
        computed = _json_digest(self.to_dict(include_digest=False))
        encoded_size = len(
            json.dumps(
                self.to_dict(include_digest=False),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        if encoded_size > 1_048_576:
            raise ContextContractError(
                "observation batch exceeds the durable journal payload limit"
            )
        supplied = str(self.batch_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("observation batch digest drift")
        object.__setattr__(self, "batch_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "natsume.world-observation-batch.v1",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "runtime_scope_id": self.runtime_scope_id,
            "runtime_owner_id": self.runtime_owner_id,
            "runtime_lease_id": self.runtime_lease_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "previous_batch_digest": self.previous_batch_digest,
            "source_event_ref": self.source_event_ref,
            "source_event_digest": self.source_event_digest,
            "observed_at_ms": self.observed_at_ms,
            "upserts": [item.to_dict() for item in self.upserts],
            "expiries": [item.to_dict() for item in self.expiries],
        }
        if include_digest:
            payload["batch_digest"] = self.batch_digest
        return payload


@dataclass(frozen=True, slots=True)
class WorldObservationHeadV1:
    """Stable terminal cursor captured around one World replay."""

    workspace_id: str
    subject_id: str
    revision: int
    batch_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_id",
            _identifier(self.workspace_id, "workspace_id"),
        )
        object.__setattr__(
            self,
            "subject_id",
            _identifier(self.subject_id, "subject_id"),
        )
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ContextContractError("observation head revision must be non-negative")
        digest = str(self.batch_digest or "").strip().lower()
        if self.revision == 0:
            if digest:
                raise ContextContractError("empty observation head cannot have a digest")
        elif _DIGEST_RE.fullmatch(digest) is None:
            raise ContextContractError("observation head digest is invalid")
        object.__setattr__(self, "batch_digest", digest)


@dataclass(frozen=True, slots=True)
class ContextPackV2:
    workspace_id: str
    subject_id: str
    task_graph_id: str
    graph_revision: int
    world_snapshot_digest: str
    facts: tuple[WorldFactV2, ...]

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "task_graph_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.graph_revision, bool) or not isinstance(self.graph_revision, int) or self.graph_revision < 1:
            raise ContextContractError("graph_revision must be positive")
        object.__setattr__(self, "world_snapshot_digest", _digest(self.world_snapshot_digest, "world_snapshot_digest"))
        facts = tuple(self.facts)
        if any(item.workspace_id != self.workspace_id or item.subject_id != self.subject_id for item in facts):
            raise ContextContractError("ContextPack fact scope mismatch")
        object.__setattr__(self, "facts", tuple(sorted(facts, key=lambda item: (item.key, item.source_ref))))

    @property
    def pack_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.context-pack.v2",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "world_snapshot_digest": self.world_snapshot_digest,
            "facts": [item.to_dict() for item in self.facts],
        }

    def verify_against_snapshot(self, snapshot: WorldSnapshotV2) -> None:
        if not isinstance(snapshot, WorldSnapshotV2):
            raise ContextContractError("ContextPack requires a typed WorldSnapshot")
        if (
            snapshot.workspace_id != self.workspace_id
            or snapshot.subject_id != self.subject_id
            or snapshot.snapshot_digest != self.world_snapshot_digest
            or tuple(snapshot.facts) != tuple(self.facts)
        ):
            raise ContextContractError("ContextPack does not bind the WorldSnapshot")


@dataclass(frozen=True, slots=True)
class ContextConstraintV1:
    """Typed, restrict-only constraint shared by Scene and Intent."""

    code: str
    disposition: str
    origin: str
    source_ref: str
    reason: str = ""

    def __post_init__(self) -> None:
        code = str(self.code or "").strip().upper()
        if _CONSTRAINT_CODE_RE.fullmatch(code) is None:
            raise ContextContractError("constraint code is invalid")
        disposition = str(self.disposition or "").strip().upper()
        if disposition not in {"RESTRICT", "REVIEW", "BLOCK"}:
            raise ContextContractError("constraint disposition is invalid")
        origin = str(self.origin or "").strip().upper()
        if origin not in {
            "SCENE",
            "TASK",
            "RELATIONSHIP",
            "LONG_TERM_GOAL",
            "RISK",
            "GRANT",
            "POLICY",
            "GUARDIAN",
        }:
            raise ContextContractError("constraint origin is invalid")
        reason = str(self.reason or "").strip()
        if len(reason) > 500:
            raise ContextContractError("constraint reason is too long")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "source_ref", _durable_ref(self.source_ref, "source_ref"))
        object.__setattr__(self, "reason", reason)

    @property
    def constraint_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.context-constraint.v1",
            "code": self.code,
            "disposition": self.disposition,
            "origin": self.origin,
            "source_ref": self.source_ref,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SceneResolutionInputV1:
    """Trusted signals from which SceneResolver derives the final scene."""

    workspace_id: str
    subject_id: str
    task_kind: str
    relationship_state: str
    user_state: str
    activity_state: str
    local_hour: int
    risk_level: int
    policy_constraints: tuple[ContextConstraintV1, ...] = ()

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        for name in (
            "task_kind",
            "relationship_state",
            "user_state",
            "activity_state",
        ):
            value = _text(getattr(self, name), name, 96).upper()
            object.__setattr__(self, name, value)
        if (
            isinstance(self.local_hour, bool)
            or not isinstance(self.local_hour, int)
            or not 0 <= self.local_hour <= 23
        ):
            raise ContextContractError("local_hour must be within [0, 23]")
        if (
            isinstance(self.risk_level, bool)
            or not isinstance(self.risk_level, int)
            or not 1 <= self.risk_level <= 5
        ):
            raise ContextContractError("risk_level must be within [1, 5]")
        constraints = tuple(self.policy_constraints)
        if not all(isinstance(item, ContextConstraintV1) for item in constraints):
            raise ContextContractError(
                "policy_constraints must contain ContextConstraintV1"
            )
        if any(item.origin != "POLICY" for item in constraints):
            raise ContextContractError(
                "policy_constraints must have POLICY origin"
            )
        if len(constraints) > 128:
            raise ContextContractError("policy_constraints exceed the scene limit")
        canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
        for item in constraints:
            identity = (item.origin, item.source_ref, item.code)
            existing = canonical.get(identity)
            if existing is not None and existing.constraint_digest != item.constraint_digest:
                raise ContextContractError("conflicting scene policy constraint identity")
            canonical[identity] = item
        object.__setattr__(
            self,
            "policy_constraints",
            tuple(sorted(canonical.values(), key=lambda item: item.constraint_digest)),
        )

    @property
    def context_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.scene-resolution-input.v1",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "task_kind": self.task_kind,
            "relationship_state": self.relationship_state,
            "user_state": self.user_state,
            "activity_state": self.activity_state,
            "local_hour": self.local_hour,
            "risk_level": self.risk_level,
            "policy_constraints": [
                item.to_dict() for item in self.policy_constraints
            ],
        }


@dataclass(frozen=True, slots=True)
class SceneDecisionV2:
    workspace_id: str
    subject_id: str
    scene_id: str
    revision: int
    world_snapshot_digest: str
    context_digest: str
    guardian_snapshot_digest: str
    constraints: tuple[ContextConstraintV1, ...]
    decision_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "scene_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContextContractError("scene revision must be positive")
        object.__setattr__(self, "world_snapshot_digest", _digest(self.world_snapshot_digest, "world_snapshot_digest"))
        object.__setattr__(self, "context_digest", _digest(self.context_digest, "context_digest"))
        object.__setattr__(
            self,
            "guardian_snapshot_digest",
            _digest(self.guardian_snapshot_digest, "guardian_snapshot_digest"),
        )
        constraints = tuple(self.constraints)
        if not all(isinstance(item, ContextConstraintV1) for item in constraints):
            raise ContextContractError(
                "scene constraints must contain ContextConstraintV1"
            )
        if len(constraints) > 128:
            raise ContextContractError("scene constraints exceed the decision limit")
        canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
        for item in constraints:
            identity = (item.origin, item.source_ref, item.code)
            existing = canonical.get(identity)
            if existing is not None and existing.constraint_digest != item.constraint_digest:
                raise ContextContractError("conflicting scene constraint identity")
            canonical[identity] = item
        constraints = tuple(
            sorted(canonical.values(), key=lambda item: item.constraint_digest)
        )
        computed = _json_digest(
            {
                "workspace_id": self.workspace_id,
                "subject_id": self.subject_id,
                "scene_id": self.scene_id,
                "revision": self.revision,
                "world_snapshot_digest": self.world_snapshot_digest,
                "context_digest": self.context_digest,
                "guardian_snapshot_digest": self.guardian_snapshot_digest,
                "constraints": [item.to_dict() for item in constraints],
            }
        )
        supplied = str(self.decision_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("scene decision digest drift")
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "decision_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.scene-decision.v2",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "scene_id": self.scene_id,
            "revision": self.revision,
            "world_snapshot_digest": self.world_snapshot_digest,
            "context_digest": self.context_digest,
            "guardian_snapshot_digest": self.guardian_snapshot_digest,
            "constraints": [item.to_dict() for item in self.constraints],
            "decision_digest": self.decision_digest,
        }


@dataclass(frozen=True, slots=True)
class SceneTransitionV1:
    """Append-only identity for a scene change."""

    workspace_id: str
    subject_id: str
    scene_id: str
    from_revision: int
    to_revision: int
    from_digest: str
    to_digest: str
    world_snapshot_digest: str
    context_digest: str
    observed_at_ms: int
    transition_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("workspace_id", "subject_id", "scene_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if (
            isinstance(self.from_revision, bool)
            or not isinstance(self.from_revision, int)
            or self.from_revision < 0
        ):
            raise ContextContractError("from_revision must be a non-negative integer")
        if (
            isinstance(self.to_revision, bool)
            or not isinstance(self.to_revision, int)
            or self.to_revision < 1
            or self.to_revision != self.from_revision + 1
        ):
            raise ContextContractError("to_revision must advance exactly one revision")
        previous = str(self.from_digest or "").strip().lower()
        if previous and not _DIGEST_RE.fullmatch(previous):
            raise ContextContractError("from_digest must be a SHA-256 digest when present")
        object.__setattr__(self, "from_digest", previous)
        object.__setattr__(self, "to_digest", _digest(self.to_digest, "to_digest"))
        object.__setattr__(self, "world_snapshot_digest", _digest(self.world_snapshot_digest, "world_snapshot_digest"))
        object.__setattr__(self, "context_digest", _digest(self.context_digest, "context_digest"))
        if (
            isinstance(self.observed_at_ms, bool)
            or not isinstance(self.observed_at_ms, int)
            or self.observed_at_ms < 0
        ):
            raise ContextContractError("observed_at_ms must be a non-negative integer")
        computed = _json_digest(
            {
                "workspace_id": self.workspace_id,
                "subject_id": self.subject_id,
                "scene_id": self.scene_id,
                "from_revision": self.from_revision,
                "to_revision": self.to_revision,
                "from_digest": self.from_digest,
                "to_digest": self.to_digest,
                "world_snapshot_digest": self.world_snapshot_digest,
                "context_digest": self.context_digest,
                "observed_at_ms": self.observed_at_ms,
            }
        )
        supplied = str(self.transition_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("scene transition digest drift")
        object.__setattr__(self, "transition_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.scene.transition.v1",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "scene_id": self.scene_id,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "from_digest": self.from_digest,
            "to_digest": self.to_digest,
            "world_snapshot_digest": self.world_snapshot_digest,
            "context_digest": self.context_digest,
            "observed_at_ms": self.observed_at_ms,
            "transition_digest": self.transition_digest,
        }


@dataclass(frozen=True, slots=True)
class IntentFrameV1:
    """Immutable input binding goal, graph, Scene, relationship, risk and Grant."""

    workspace_id: str
    subject_id: str
    principal_id: str
    intent_origin: str
    goal_ref: str
    goal_digest: str
    task_graph_id: str
    graph_revision: int
    task_node_id: str
    context_pack_digest: str
    scene_decision_digest: str
    scene_revision: int
    requested_action_ref: str
    requested_action_digest: str
    grant_ref: str
    grant_digest: str
    arbiter_policy_ref: str
    arbiter_policy_digest: str
    task_constraints: tuple[ContextConstraintV1, ...]
    relationship_constraints: tuple[ContextConstraintV1, ...]
    long_term_goal_refs: tuple[str, ...]
    long_term_goal_constraints: tuple[ContextConstraintV1, ...]
    risk_constraints: tuple[ContextConstraintV1, ...]
    grant_constraints: tuple[ContextConstraintV1, ...]
    frame_digest: str = ""

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "subject_id",
            "principal_id",
            "task_graph_id",
            "task_node_id",
        ):
            object.__setattr__(
                self,
                name,
                _identifier(getattr(self, name), name),
            )
        origin = _text(self.intent_origin, "intent_origin", 32).upper()
        if origin not in {"EXPLICIT_USER", "PROACTIVE", "SCHEDULED", "DELEGATED"}:
            raise ContextContractError("intent_origin is invalid")
        object.__setattr__(self, "intent_origin", origin)
        object.__setattr__(self, "goal_ref", _durable_ref(self.goal_ref, "goal_ref"))
        object.__setattr__(
            self,
            "goal_digest",
            _digest(self.goal_digest, "goal_digest"),
        )
        object.__setattr__(
            self,
            "context_pack_digest",
            _digest(self.context_pack_digest, "context_pack_digest"),
        )
        object.__setattr__(
            self,
            "scene_decision_digest",
            _digest(self.scene_decision_digest, "scene_decision_digest"),
        )
        for name in ("graph_revision", "scene_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContextContractError(f"{name} must be positive")
        expected_origins = {
            "task_constraints": "TASK",
            "relationship_constraints": "RELATIONSHIP",
            "long_term_goal_constraints": "LONG_TERM_GOAL",
            "risk_constraints": "RISK",
            "grant_constraints": "GRANT",
        }
        total_constraints = 0
        for name, expected_origin in expected_origins.items():
            values = tuple(getattr(self, name))
            if not all(isinstance(item, ContextConstraintV1) for item in values):
                raise ContextContractError(
                    f"{name} must contain ContextConstraintV1"
                )
            if len(values) > 128:
                raise ContextContractError(f"{name} exceeds the bounded limit")
            total_constraints += len(values)
            if any(item.origin != expected_origin for item in values):
                raise ContextContractError(
                    f"{name} must have {expected_origin} origin"
                )
            canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
            for item in values:
                identity = (item.origin, item.source_ref, item.code)
                existing = canonical.get(identity)
                if existing is not None and existing.constraint_digest != item.constraint_digest:
                    raise ContextContractError(
                        f"{name} contains a conflicting constraint identity"
                    )
                canonical[identity] = item
            object.__setattr__(
                self,
                name,
                tuple(sorted(canonical.values(), key=lambda item: item.constraint_digest)),
            )
        if total_constraints > 256:
            raise ContextContractError("intent constraints exceed the total limit")
        if len(self.long_term_goal_refs) > 64:
            raise ContextContractError("long_term_goal_refs exceeds the bounded limit")
        refs = tuple(
            sorted(
                {
                    _durable_ref(item, "long_term_goal_refs[]")
                    for item in self.long_term_goal_refs
                }
            )
        )
        object.__setattr__(self, "long_term_goal_refs", refs)
        if any(
            item.source_ref not in refs
            for item in self.long_term_goal_constraints
        ):
            raise ContextContractError(
                "long-term goal constraint lacks a bound goal reference"
            )
        object.__setattr__(
            self,
            "requested_action_ref",
            _durable_ref(self.requested_action_ref, "requested_action_ref"),
        )
        object.__setattr__(
            self,
            "requested_action_digest",
            _digest(self.requested_action_digest, "requested_action_digest"),
        )
        object.__setattr__(self, "grant_ref", _durable_ref(self.grant_ref, "grant_ref"))
        object.__setattr__(self, "grant_digest", _digest(self.grant_digest, "grant_digest"))
        if any(item.source_ref != self.grant_ref for item in self.grant_constraints):
            raise ContextContractError(
                "grant constraint must be bound to the exact grant_ref"
            )
        object.__setattr__(
            self,
            "arbiter_policy_ref",
            _durable_ref(self.arbiter_policy_ref, "arbiter_policy_ref"),
        )
        object.__setattr__(
            self,
            "arbiter_policy_digest",
            _digest(self.arbiter_policy_digest, "arbiter_policy_digest"),
        )
        computed = _json_digest(self.to_dict(include_digest=False))
        supplied = str(self.frame_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("intent frame digest drift")
        object.__setattr__(self, "frame_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "natsume.intent-frame.v1",
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "principal_id": self.principal_id,
            "intent_origin": self.intent_origin,
            "goal_ref": self.goal_ref,
            "goal_digest": self.goal_digest,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "task_node_id": self.task_node_id,
            "context_pack_digest": self.context_pack_digest,
            "scene_decision_digest": self.scene_decision_digest,
            "scene_revision": self.scene_revision,
            "requested_action_ref": self.requested_action_ref,
            "requested_action_digest": self.requested_action_digest,
            "grant_ref": self.grant_ref,
            "grant_digest": self.grant_digest,
            "arbiter_policy_ref": self.arbiter_policy_ref,
            "arbiter_policy_digest": self.arbiter_policy_digest,
            "task_constraints": [item.to_dict() for item in self.task_constraints],
            "relationship_constraints": [
                item.to_dict() for item in self.relationship_constraints
            ],
            "long_term_goal_refs": list(self.long_term_goal_refs),
            "long_term_goal_constraints": [
                item.to_dict() for item in self.long_term_goal_constraints
            ],
            "risk_constraints": [item.to_dict() for item in self.risk_constraints],
            "grant_constraints": [item.to_dict() for item in self.grant_constraints],
        }
        if include_digest:
            payload["frame_digest"] = self.frame_digest
        return payload


@dataclass(frozen=True, slots=True)
class IntentArbitrationV1:
    intent_frame_digest: str
    workspace_id: str
    subject_id: str
    principal_id: str
    intent_origin: str
    goal_ref: str
    goal_digest: str
    task_graph_id: str
    graph_revision: int
    task_node_id: str
    context_pack_digest: str
    scene_revision: int
    scene_decision_digest: str
    requested_action_ref: str
    requested_action_digest: str
    grant_ref: str
    grant_digest: str
    arbiter_policy_ref: str
    arbiter_policy_digest: str
    constraints: tuple[ContextConstraintV1, ...]
    decision: str
    goal_preserved: bool = True
    permission_effect: str = "RESTRICT_ONLY"
    execution_authorized: bool = False
    arbitration_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intent_frame_digest",
            _digest(self.intent_frame_digest, "intent_frame_digest"),
        )
        for name in (
            "workspace_id",
            "subject_id",
            "principal_id",
            "task_graph_id",
            "task_node_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        origin = _text(self.intent_origin, "intent_origin", 32).upper()
        if origin not in {"EXPLICIT_USER", "PROACTIVE", "SCHEDULED", "DELEGATED"}:
            raise ContextContractError("intent_origin is invalid")
        object.__setattr__(self, "intent_origin", origin)
        object.__setattr__(self, "goal_ref", _durable_ref(self.goal_ref, "goal_ref"))
        object.__setattr__(self, "goal_digest", _digest(self.goal_digest, "goal_digest"))
        object.__setattr__(
            self,
            "context_pack_digest",
            _digest(self.context_pack_digest, "context_pack_digest"),
        )
        object.__setattr__(
            self,
            "scene_decision_digest",
            _digest(self.scene_decision_digest, "scene_decision_digest"),
        )
        object.__setattr__(
            self,
            "requested_action_ref",
            _durable_ref(self.requested_action_ref, "requested_action_ref"),
        )
        object.__setattr__(
            self,
            "requested_action_digest",
            _digest(self.requested_action_digest, "requested_action_digest"),
        )
        object.__setattr__(self, "grant_ref", _durable_ref(self.grant_ref, "grant_ref"))
        object.__setattr__(self, "grant_digest", _digest(self.grant_digest, "grant_digest"))
        object.__setattr__(
            self,
            "arbiter_policy_ref",
            _durable_ref(self.arbiter_policy_ref, "arbiter_policy_ref"),
        )
        object.__setattr__(
            self,
            "arbiter_policy_digest",
            _digest(self.arbiter_policy_digest, "arbiter_policy_digest"),
        )
        for name in ("graph_revision", "scene_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContextContractError(f"intent {name} must be positive")
        constraints = tuple(self.constraints)
        if not all(isinstance(item, ContextConstraintV1) for item in constraints):
            raise ContextContractError(
                "intent constraints must contain ContextConstraintV1"
            )
        if len(constraints) > 384:
            raise ContextContractError("intent arbitration constraints exceed the limit")
        canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
        for item in constraints:
            identity = (item.origin, item.source_ref, item.code)
            existing = canonical.get(identity)
            if existing is not None and existing.constraint_digest != item.constraint_digest:
                raise ContextContractError(
                    "intent arbitration contains a conflicting constraint identity"
                )
            canonical[identity] = item
        object.__setattr__(
            self,
            "constraints",
            tuple(sorted(canonical.values(), key=lambda item: item.constraint_digest)),
        )
        dispositions = {item.disposition for item in self.constraints}
        expected_decision = (
            "BLOCK"
            if "BLOCK" in dispositions
            else "REVIEW"
            if "REVIEW" in dispositions
            else "RESTRICT"
            if "RESTRICT" in dispositions
            else "NO_ADDITIONAL_RESTRICTION"
        )
        decision = _text(self.decision, "decision", 120).upper()
        if decision != expected_decision:
            raise ContextContractError("intent decision does not match constraints")
        object.__setattr__(self, "decision", decision)
        if self.goal_preserved is not True:
            raise ContextContractError("intent arbitration must preserve the goal")
        permission_effect = _text(
            self.permission_effect,
            "permission_effect",
            32,
        ).upper()
        if permission_effect != "RESTRICT_ONLY":
            raise ContextContractError("intent arbitration may only restrict permission")
        object.__setattr__(self, "permission_effect", permission_effect)
        if self.execution_authorized is not False:
            raise ContextContractError("intent arbitration cannot authorize execution")
        computed = _json_digest(self.to_dict(include_digest=False))
        supplied = str(self.arbitration_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise ContextContractError("intent arbitration digest drift")
        object.__setattr__(self, "arbitration_digest", computed)

    def verify_against(
        self,
        *,
        frame: IntentFrameV1,
        scene: SceneDecisionV2,
        authoritative_constraints: tuple[ContextConstraintV1, ...] = (),
    ) -> None:
        """Reject a detached or re-minted result before it becomes authority."""

        if not isinstance(frame, IntentFrameV1) or not isinstance(scene, SceneDecisionV2):
            raise ContextContractError("typed frame and scene are required")
        exact = {
            "intent_frame_digest": frame.frame_digest,
            "workspace_id": frame.workspace_id,
            "subject_id": frame.subject_id,
            "principal_id": frame.principal_id,
            "intent_origin": frame.intent_origin,
            "goal_ref": frame.goal_ref,
            "goal_digest": frame.goal_digest,
            "task_graph_id": frame.task_graph_id,
            "graph_revision": frame.graph_revision,
            "task_node_id": frame.task_node_id,
            "context_pack_digest": frame.context_pack_digest,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "requested_action_ref": frame.requested_action_ref,
            "requested_action_digest": frame.requested_action_digest,
            "grant_ref": frame.grant_ref,
            "grant_digest": frame.grant_digest,
            "arbiter_policy_ref": frame.arbiter_policy_ref,
            "arbiter_policy_digest": frame.arbiter_policy_digest,
        }
        if any(getattr(self, name) != value for name, value in exact.items()):
            raise ContextContractError("intent arbitration binding drift")
        if (
            scene.workspace_id != frame.workspace_id
            or scene.subject_id != frame.subject_id
            or frame.scene_revision != scene.revision
            or frame.scene_decision_digest != scene.decision_digest
        ):
            raise ContextContractError("intent frame is not bound to the supplied scene")
        merged = (
            *scene.constraints,
            *frame.task_constraints,
            *frame.relationship_constraints,
            *frame.long_term_goal_constraints,
            *frame.risk_constraints,
            *frame.grant_constraints,
            *authoritative_constraints,
        )
        expected: dict[tuple[str, str, str], ContextConstraintV1] = {}
        for item in merged:
            if not isinstance(item, ContextConstraintV1):
                raise ContextContractError("authoritative constraint is not typed")
            identity = (item.origin, item.source_ref, item.code)
            previous = expected.get(identity)
            if previous is not None and previous.constraint_digest != item.constraint_digest:
                raise ContextContractError("authoritative constraint identity conflicts")
            expected[identity] = item
        expected_constraints = tuple(
            sorted(expected.values(), key=lambda item: item.constraint_digest)
        )
        if self.constraints != expected_constraints:
            raise ContextContractError("intent arbitration omitted or changed a constraint")

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "natsume.intent-arbitration.v1",
            "intent_frame_digest": self.intent_frame_digest,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "principal_id": self.principal_id,
            "intent_origin": self.intent_origin,
            "goal_ref": self.goal_ref,
            "goal_digest": self.goal_digest,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "task_node_id": self.task_node_id,
            "context_pack_digest": self.context_pack_digest,
            "scene_revision": self.scene_revision,
            "scene_decision_digest": self.scene_decision_digest,
            "requested_action_ref": self.requested_action_ref,
            "requested_action_digest": self.requested_action_digest,
            "grant_ref": self.grant_ref,
            "grant_digest": self.grant_digest,
            "arbiter_policy_ref": self.arbiter_policy_ref,
            "arbiter_policy_digest": self.arbiter_policy_digest,
            "constraints": [item.to_dict() for item in self.constraints],
            "decision": self.decision,
            "goal_preserved": self.goal_preserved,
            "permission_effect": self.permission_effect,
            "execution_authorized": self.execution_authorized,
        }
        if include_digest:
            payload["arbitration_digest"] = self.arbitration_digest
        return payload


__all__ = [
    "ContextContractError",
    "ContextConstraintV1",
    "ContextPackV2",
    "IntentArbitrationV1",
    "IntentFrameV1",
    "SceneDecisionV2",
    "SceneResolutionInputV1",
    "SceneTransitionV1",
    "WorldFactV2",
    "WorldFactExpiryV1",
    "WorldObservationBatchV1",
    "WorldObservationHeadV1",
    "WorldSnapshotV2",
]
