"""Trusted authority boundary for Intent arbitration.

``IntentFrameV1`` and ``SceneDecisionV2`` are immutable, digest-bound
declarations.  Immutability alone does not prove that the declarations were
loaded from the canonical TaskGraph, Scene journal or Grant authority.  This
module keeps that trust decision behind an injected port and requires an exact
typed attestation before arbitration may proceed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import time
from typing import Protocol, runtime_checkable

from host_brain.contracts.runtime_context import ContextConstraintV1, IntentFrameV1, SceneDecisionV2
from host_brain.core.runtime_helpers import canonical_json_sha256 as _json_digest


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DURABLE_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[^\s\x00-\x1f\x7f]{1,511}$"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_MAX_ATTESTATION_AGE_MS = 5_000


class IntentAuthorityError(PermissionError):
    """Intent declarations could not be proven by canonical authorities."""


def _text(value: object, field: str, *, limit: int = 544) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise IntentAuthorityError(f"{field} is required and bounded")
    return result


def _identifier(value: object, field: str) -> str:
    result = _text(value, field, limit=256)
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise IntentAuthorityError(f"{field} is not a canonical identifier")
    return result


def _durable_ref(value: object, field: str) -> str:
    result = _text(value, field)
    if _DURABLE_REF_RE.fullmatch(result) is None:
        raise IntentAuthorityError(f"{field} is not a durable reference")
    return result


def _digest(value: object, field: str) -> str:
    result = str(value or "").strip().lower()
    if _DIGEST_RE.fullmatch(result) is None:
        raise IntentAuthorityError(f"{field} is not a SHA-256 digest")
    return result


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IntentAuthorityError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result < 1:
        raise IntentAuthorityError(f"{field} must be positive")
    return result


def _constraints(
    values: tuple[ContextConstraintV1, ...],
    field: str,
    *,
    origins: frozenset[str],
) -> tuple[ContextConstraintV1, ...]:
    result = tuple(values)
    if len(result) > 128:
        raise IntentAuthorityError(f"{field} exceeds its bounded limit")
    if not all(type(item) is ContextConstraintV1 for item in result):
        raise IntentAuthorityError(f"{field} must contain typed constraints")
    if any(item.origin not in origins for item in result):
        raise IntentAuthorityError(f"{field} origin is not canonical")
    canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
    for item in result:
        identity = (item.origin, item.source_ref, item.code)
        existing = canonical.get(identity)
        if existing is not None and existing.constraint_digest != item.constraint_digest:
            raise IntentAuthorityError(
                f"{field} contains a conflicting constraint identity"
            )
        canonical[identity] = item
    return tuple(sorted(canonical.values(), key=lambda item: item.constraint_digest))


@dataclass(frozen=True, slots=True)
class IntentPolicyIdentityV1:
    """Composition-owned identity of the policy allowed to arbitrate."""

    policy_ref: str
    policy_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_ref",
            _durable_ref(self.policy_ref, "policy_ref"),
        )
        object.__setattr__(
            self,
            "policy_digest",
            _digest(self.policy_digest, "policy_digest"),
        )


@dataclass(frozen=True, slots=True)
class IntentAuthorityAttestationV1:
    """Canonical, secret-free proof covering every arbitration input."""

    authority_ref: str
    attested_at_ms: int
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
    scene_id: str
    scene_revision: int
    scene_decision_digest: str
    scene_context_digest: str
    scene_world_snapshot_digest: str
    scene_guardian_snapshot_digest: str
    requested_action_ref: str
    requested_action_digest: str
    grant_ref: str
    grant_digest: str
    grant_principal_id: str
    grant_status: str
    grant_issued_at_ms: int
    grant_expires_at_ms: int
    grant_revoked_at_ms: int | None
    policy_ref: str
    policy_digest: str
    policy_constraints: tuple[ContextConstraintV1, ...]
    scene_constraints: tuple[ContextConstraintV1, ...]
    task_constraints: tuple[ContextConstraintV1, ...]
    relationship_constraints: tuple[ContextConstraintV1, ...]
    long_term_goal_refs: tuple[str, ...]
    long_term_goal_constraints: tuple[ContextConstraintV1, ...]
    risk_constraints: tuple[ContextConstraintV1, ...]
    grant_constraints: tuple[ContextConstraintV1, ...]
    attestation_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authority_ref",
            _durable_ref(self.authority_ref, "authority_ref"),
        )
        object.__setattr__(
            self,
            "attested_at_ms",
            _non_negative_int(self.attested_at_ms, "attested_at_ms"),
        )
        for name in (
            "intent_frame_digest",
            "goal_digest",
            "context_pack_digest",
            "scene_decision_digest",
            "scene_context_digest",
            "scene_world_snapshot_digest",
            "scene_guardian_snapshot_digest",
            "requested_action_digest",
            "grant_digest",
            "policy_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in (
            "workspace_id",
            "subject_id",
            "principal_id",
            "task_graph_id",
            "task_node_id",
            "grant_principal_id",
            "scene_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        intent_origin = _text(self.intent_origin, "intent_origin", limit=32).upper()
        if intent_origin not in {
            "EXPLICIT_USER",
            "PROACTIVE",
            "SCHEDULED",
            "DELEGATED",
        }:
            raise IntentAuthorityError("intent_origin is invalid")
        object.__setattr__(self, "intent_origin", intent_origin)
        for name in (
            "goal_ref",
            "requested_action_ref",
            "grant_ref",
            "policy_ref",
        ):
            object.__setattr__(self, name, _durable_ref(getattr(self, name), name))
        for name in ("graph_revision", "scene_revision"):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        issued = _non_negative_int(self.grant_issued_at_ms, "grant_issued_at_ms")
        expires = _non_negative_int(self.grant_expires_at_ms, "grant_expires_at_ms")
        if expires <= issued:
            raise IntentAuthorityError("grant expiry must follow issuance")
        revoked = self.grant_revoked_at_ms
        if revoked is not None:
            revoked = _non_negative_int(revoked, "grant_revoked_at_ms")
            if revoked < issued:
                raise IntentAuthorityError("grant revocation predates issuance")
        status = _text(self.grant_status, "grant_status", limit=32).upper()
        if status not in {"ACTIVE", "EXPIRED", "REVOKED", "DENIED"}:
            raise IntentAuthorityError("grant_status is invalid")
        object.__setattr__(self, "grant_issued_at_ms", issued)
        object.__setattr__(self, "grant_expires_at_ms", expires)
        object.__setattr__(self, "grant_revoked_at_ms", revoked)
        object.__setattr__(self, "grant_status", status)

        constraint_fields = {
            "policy_constraints": frozenset({"POLICY"}),
            "scene_constraints": frozenset({"SCENE", "POLICY", "GUARDIAN"}),
            "task_constraints": frozenset({"TASK"}),
            "relationship_constraints": frozenset({"RELATIONSHIP"}),
            "long_term_goal_constraints": frozenset({"LONG_TERM_GOAL"}),
            "risk_constraints": frozenset({"RISK"}),
            "grant_constraints": frozenset({"GRANT"}),
        }
        for name, origins in constraint_fields.items():
            object.__setattr__(
                self,
                name,
                _constraints(getattr(self, name), name, origins=origins),
            )
        refs = tuple(
            sorted(
                {
                    _durable_ref(item, "long_term_goal_refs[]")
                    for item in tuple(self.long_term_goal_refs)
                }
            )
        )
        if len(refs) != len(tuple(self.long_term_goal_refs)) or len(refs) > 64:
            raise IntentAuthorityError("long_term_goal_refs are not canonical")
        if any(item.source_ref not in refs for item in self.long_term_goal_constraints):
            raise IntentAuthorityError(
                "long-term constraint is not bound to canonical goal authority"
            )
        if any(item.source_ref != self.grant_ref for item in self.grant_constraints):
            raise IntentAuthorityError(
                "grant constraint is not bound to the attested grant"
            )
        if any(item.source_ref != self.policy_ref for item in self.policy_constraints):
            raise IntentAuthorityError(
                "policy constraint is not bound to the attested policy"
            )
        object.__setattr__(self, "long_term_goal_refs", refs)

        computed = _json_digest(self.to_dict(include_digest=False))
        supplied = str(self.attestation_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise IntentAuthorityError("intent authority attestation digest drift")
        object.__setattr__(self, "attestation_digest", computed)

    @property
    def canonical_constraints_digest(self) -> str:
        return _json_digest(
            {
                "policy_authority": [
                    item.to_dict() for item in self.policy_constraints
                ],
                "scene": [item.to_dict() for item in self.scene_constraints],
                "task": [item.to_dict() for item in self.task_constraints],
                "relationship": [
                    item.to_dict() for item in self.relationship_constraints
                ],
                "long_term_goal_refs": list(self.long_term_goal_refs),
                "long_term_goal": [
                    item.to_dict() for item in self.long_term_goal_constraints
                ],
                "risk": [item.to_dict() for item in self.risk_constraints],
                "grant": [item.to_dict() for item in self.grant_constraints],
            }
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "natsume.intent-authority-attestation.v1",
            "authority_ref": self.authority_ref,
            "attested_at_ms": self.attested_at_ms,
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
            "scene_id": self.scene_id,
            "scene_revision": self.scene_revision,
            "scene_decision_digest": self.scene_decision_digest,
            "scene_context_digest": self.scene_context_digest,
            "scene_world_snapshot_digest": self.scene_world_snapshot_digest,
            "scene_guardian_snapshot_digest": self.scene_guardian_snapshot_digest,
            "requested_action_ref": self.requested_action_ref,
            "requested_action_digest": self.requested_action_digest,
            "grant_ref": self.grant_ref,
            "grant_digest": self.grant_digest,
            "grant_principal_id": self.grant_principal_id,
            "grant_status": self.grant_status,
            "grant_issued_at_ms": self.grant_issued_at_ms,
            "grant_expires_at_ms": self.grant_expires_at_ms,
            "grant_revoked_at_ms": self.grant_revoked_at_ms,
            "policy_ref": self.policy_ref,
            "policy_digest": self.policy_digest,
            "policy_constraints": [
                item.to_dict() for item in self.policy_constraints
            ],
            "scene_constraints": [item.to_dict() for item in self.scene_constraints],
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
            "canonical_constraints_digest": self.canonical_constraints_digest,
        }
        if include_digest:
            payload["attestation_digest"] = self.attestation_digest
        return payload

    def verify_integrity(self) -> None:
        if self.attestation_digest != _json_digest(
            self.to_dict(include_digest=False)
        ):
            raise IntentAuthorityError("intent authority attestation was mutated")


@runtime_checkable
class IntentAuthorityPort(Protocol):
    """Read canonical bindings; never derive them from the request body."""

    def resolve_intent_authority(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        task_graph_id: str,
        graph_revision: int,
        task_node_id: str,
        scene_id: str,
        scene_revision: int,
        scene_decision_digest: str,
        grant_ref: str,
    ) -> IntentAuthorityAttestationV1:
        """Return one complete attestation or fail closed."""


class IntentAuthorityAssembler:
    """Build the typed proof after an adapter loads all canonical records.

    The arguments named ``canonical_*`` must be objects reconstructed from the
    durable owners, not objects deserialised from the inbound request.  Keeping
    this assembly separate makes that trust boundary explicit and testable.
    """

    @staticmethod
    def assemble(
        *,
        canonical_frame: IntentFrameV1,
        canonical_scene: SceneDecisionV2,
        policy_identity: IntentPolicyIdentityV1,
        authority_ref: str,
        attested_at_ms: int,
        grant_status: str,
        grant_issued_at_ms: int,
        grant_expires_at_ms: int,
        canonical_policy_constraints: tuple[ContextConstraintV1, ...],
        grant_revoked_at_ms: int | None = None,
        grant_principal_id: str | None = None,
    ) -> IntentAuthorityAttestationV1:
        if type(canonical_frame) is not IntentFrameV1:
            raise IntentAuthorityError("canonical IntentFrameV1 is required")
        if type(canonical_scene) is not SceneDecisionV2:
            raise IntentAuthorityError("canonical SceneDecisionV2 is required")
        if type(policy_identity) is not IntentPolicyIdentityV1:
            raise IntentAuthorityError("typed policy identity is required")
        if (
            canonical_frame.workspace_id != canonical_scene.workspace_id
            or canonical_frame.subject_id != canonical_scene.subject_id
            or canonical_frame.scene_revision != canonical_scene.revision
            or canonical_frame.scene_decision_digest
            != canonical_scene.decision_digest
        ):
            raise IntentAuthorityError("canonical Frame and Scene do not bind")
        if (
            canonical_frame.arbiter_policy_ref != policy_identity.policy_ref
            or canonical_frame.arbiter_policy_digest != policy_identity.policy_digest
        ):
            raise IntentAuthorityError("canonical Frame policy identity drifted")
        return IntentAuthorityAttestationV1(
            authority_ref=authority_ref,
            attested_at_ms=attested_at_ms,
            intent_frame_digest=canonical_frame.frame_digest,
            workspace_id=canonical_frame.workspace_id,
            subject_id=canonical_frame.subject_id,
            principal_id=canonical_frame.principal_id,
            intent_origin=canonical_frame.intent_origin,
            goal_ref=canonical_frame.goal_ref,
            goal_digest=canonical_frame.goal_digest,
            task_graph_id=canonical_frame.task_graph_id,
            graph_revision=canonical_frame.graph_revision,
            task_node_id=canonical_frame.task_node_id,
            context_pack_digest=canonical_frame.context_pack_digest,
            scene_id=canonical_scene.scene_id,
            scene_revision=canonical_scene.revision,
            scene_decision_digest=canonical_scene.decision_digest,
            scene_context_digest=canonical_scene.context_digest,
            scene_world_snapshot_digest=canonical_scene.world_snapshot_digest,
            scene_guardian_snapshot_digest=canonical_scene.guardian_snapshot_digest,
            requested_action_ref=canonical_frame.requested_action_ref,
            requested_action_digest=canonical_frame.requested_action_digest,
            grant_ref=canonical_frame.grant_ref,
            grant_digest=canonical_frame.grant_digest,
            grant_principal_id=(
                canonical_frame.principal_id
                if grant_principal_id is None
                else grant_principal_id
            ),
            grant_status=grant_status,
            grant_issued_at_ms=grant_issued_at_ms,
            grant_expires_at_ms=grant_expires_at_ms,
            grant_revoked_at_ms=grant_revoked_at_ms,
            policy_ref=policy_identity.policy_ref,
            policy_digest=policy_identity.policy_digest,
            policy_constraints=canonical_policy_constraints,
            scene_constraints=canonical_scene.constraints,
            task_constraints=canonical_frame.task_constraints,
            relationship_constraints=canonical_frame.relationship_constraints,
            long_term_goal_refs=canonical_frame.long_term_goal_refs,
            long_term_goal_constraints=canonical_frame.long_term_goal_constraints,
            risk_constraints=canonical_frame.risk_constraints,
            grant_constraints=canonical_frame.grant_constraints,
        )


class IntentAuthorityVerifier:
    """Exact-match untrusted declarations against canonical attestation."""

    def __init__(
        self,
        *,
        authority_port: IntentAuthorityPort,
        policy_identity: IntentPolicyIdentityV1,
        clock: Callable[[], int] | None = None,
    ) -> None:
        resolver = getattr(authority_port, "resolve_intent_authority", None)
        if not callable(resolver):
            raise TypeError(
                "authority_port must expose resolve_intent_authority"
            )
        if type(policy_identity) is not IntentPolicyIdentityV1:
            raise TypeError("policy_identity must be IntentPolicyIdentityV1")
        self._authority_port = authority_port
        self._policy_identity = policy_identity
        self._clock = clock or (lambda: int(time.time() * 1000))

    def verify(
        self,
        *,
        frame: IntentFrameV1,
        scene: SceneDecisionV2,
    ) -> IntentAuthorityAttestationV1:
        if type(frame) is not IntentFrameV1:
            raise IntentAuthorityError("typed IntentFrameV1 is required")
        if type(scene) is not SceneDecisionV2:
            raise IntentAuthorityError("typed SceneDecisionV2 is required")
        if (
            frame.arbiter_policy_ref != self._policy_identity.policy_ref
            or frame.arbiter_policy_digest != self._policy_identity.policy_digest
        ):
            raise IntentAuthorityError(
                "caller policy identity differs from composition authority"
            )
        if (
            frame.workspace_id != scene.workspace_id
            or frame.subject_id != scene.subject_id
            or frame.scene_revision != scene.revision
            or frame.scene_decision_digest != scene.decision_digest
        ):
            raise IntentAuthorityError(
                "Intent Frame is not bound to the supplied canonical Scene"
            )
        try:
            attestation = self._authority_port.resolve_intent_authority(
                workspace_id=frame.workspace_id,
                subject_id=frame.subject_id,
                task_graph_id=frame.task_graph_id,
                graph_revision=frame.graph_revision,
                task_node_id=frame.task_node_id,
                scene_id=scene.scene_id,
                scene_revision=scene.revision,
                scene_decision_digest=scene.decision_digest,
                grant_ref=frame.grant_ref,
            )
        except IntentAuthorityError:
            raise
        except Exception as exc:
            raise IntentAuthorityError("canonical Intent authority lookup failed") from exc
        if type(attestation) is not IntentAuthorityAttestationV1:
            raise IntentAuthorityError(
                "authority port returned no typed Intent attestation"
            )
        attestation.verify_integrity()

        try:
            now_ms = self._clock()
        except Exception as exc:
            raise IntentAuthorityError("authority clock is unavailable") from exc
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise IntentAuthorityError("authority clock returned an invalid timestamp")
        if (
            attestation.attested_at_ms > now_ms
            or now_ms - attestation.attested_at_ms > _MAX_ATTESTATION_AGE_MS
            or attestation.grant_status != "ACTIVE"
            or attestation.grant_issued_at_ms > attestation.attested_at_ms
            or attestation.grant_issued_at_ms > now_ms
            or attestation.grant_expires_at_ms <= now_ms
            or attestation.grant_revoked_at_ms is not None
            or attestation.grant_principal_id != frame.principal_id
        ):
            raise IntentAuthorityError(
                "Grant is inactive, expired, revoked or principal-bound elsewhere"
            )
        expected_scalars = {
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
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "scene_context_digest": scene.context_digest,
            "scene_world_snapshot_digest": scene.world_snapshot_digest,
            "scene_guardian_snapshot_digest": scene.guardian_snapshot_digest,
            "requested_action_ref": frame.requested_action_ref,
            "requested_action_digest": frame.requested_action_digest,
            "grant_ref": frame.grant_ref,
            "grant_digest": frame.grant_digest,
            "policy_ref": self._policy_identity.policy_ref,
            "policy_digest": self._policy_identity.policy_digest,
        }
        drifted = [
            name
            for name, expected in expected_scalars.items()
            if getattr(attestation, name) != expected
        ]
        expected_constraints = {
            "scene_constraints": scene.constraints,
            "task_constraints": frame.task_constraints,
            "relationship_constraints": frame.relationship_constraints,
            "long_term_goal_refs": frame.long_term_goal_refs,
            "long_term_goal_constraints": frame.long_term_goal_constraints,
            "risk_constraints": frame.risk_constraints,
            "grant_constraints": frame.grant_constraints,
        }
        drifted.extend(
            name
            for name, expected in expected_constraints.items()
            if getattr(attestation, name) != expected
        )
        if drifted:
            raise IntentAuthorityError(
                "Intent authority binding drift: " + ", ".join(sorted(drifted))
            )
        return attestation


__all__ = [
    "IntentAuthorityAssembler",
    "IntentAuthorityAttestationV1",
    "IntentAuthorityError",
    "IntentAuthorityPort",
    "IntentAuthorityVerifier",
    "IntentPolicyIdentityV1",
]
