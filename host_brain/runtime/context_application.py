"""App-local request boundary for one canonical World and Scene identity.

The Host may collect many contextual signals before command admission, but it
must not let each consumer independently select a Scene.  This service derives
the logical workspace from server-owned authority, resolves World and Scene
once, and returns a secret-free mapping that can be frozen in ``WorkOrderV3``.
It never writes Memory/Life and never accepts a caller-selected final scene.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import re
from typing import Any

from host_brain.core.emotion_constants import normalize_emotion_tag

from .context_contracts import (
    ContextConstraintV1,
    ContextContractError,
    SceneResolutionInputV1,
    WorldFactV2,
    WorldSnapshotV2,
)
from .conversation_scope import (
    GatewayConversationScopeError,
    SecureGatewayConversationScopeV1,
)
from .production_composition import RuntimeContextComposition
from .world_observation_application import RuntimeWorldObservationError
from .world_resolver import WorldResolverError
from .world_source_verifier import WorldObservationAuthorityProjectionV1


class RuntimeContextApplicationError(RuntimeError):
    """A request cannot be bound to the canonical runtime context."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_UPSTREAM_AUTHORITY_FIELDS = (
    "source_ref",
    "source_digest",
    "event_ref",
    "payload_digest",
)
_RUNTIME_CONTEXT_FIELDS = frozenset(
    {
        "runtime_context_schema",
        "runtime_context_observed_at_ms",
        "runtime_context_principal_id",
        "workspace_id",
        "subject_id",
        "world_snapshot_v2",
        "world_identity_v2",
        "scene_resolution_input_v1",
        "scene_decision_v2",
        "scene_identity_v2",
        "intent_constraints_v1",
    }
)


def _digest_identifier(prefix: str, value: object) -> str:
    text = str(value or "").strip()
    if text and _IDENTIFIER_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _upstream_projection(
    value: Mapping[str, Any] | None,
    *,
    authority_kind: str,
) -> WorldObservationAuthorityProjectionV1 | None:
    item = _mapping(value)
    raw = item.get("world_authority_projection_v1")
    claims_durable_source = any(
        str(item.get(field) or "").strip()
        for field in _UPSTREAM_AUTHORITY_FIELDS
    )
    if raw is None:
        if claims_durable_source:
            raise RuntimeContextApplicationError(
                f"{authority_kind} source authority lacks a typed projection"
            )
        return None
    if not isinstance(raw, Mapping):
        raise RuntimeContextApplicationError(
            f"{authority_kind} source authority projection is invalid"
        )
    try:
        projection = WorldObservationAuthorityProjectionV1.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeContextApplicationError(
            f"{authority_kind} source authority projection is invalid"
        ) from exc
    if projection.authority_kind != authority_kind:
        raise RuntimeContextApplicationError(
            f"{authority_kind} source authority kind drifted"
        )
    return projection


def _request_source_projections(
    *,
    bond_summary: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    emotion_context: Mapping[str, Any] | None,
) -> tuple[WorldObservationAuthorityProjectionV1, ...]:
    candidates: list[WorldObservationAuthorityProjectionV1] = []
    bond = _upstream_projection(bond_summary, authority_kind="BOND")
    if bond is not None:
        candidates.append(bond)
    emotion_sources: Sequence[Mapping[str, Any] | None] = (
        emotion_snapshot,
        _mapping(emotion_snapshot).get("emotion_context"),
        emotion_context,
    )
    emotions = tuple(
        item
        for item in (
            _upstream_projection(source, authority_kind="EMOTION")
            for source in emotion_sources
        )
        if item is not None
    )
    if len({item.event_ref for item in emotions}) > 1:
        raise RuntimeContextApplicationError(
            "emotion source authority projections are ambiguous"
        )
    if emotions:
        candidates.append(emotions[0])
    return tuple(candidates)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _upper(value: object, default: str) -> str:
    text = str(value or "").strip().upper()
    return text[:96] or default


def _bounded_risk(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = 1
    return max(1, min(5, result))


def _long_term_goal_context(
    snapshot: WorldSnapshotV2,
) -> dict[str, Any]:
    refs = tuple(
        sorted(
            {
                str(item.value).strip()
                for item in snapshot.facts
                if item.key == "goal.long_term.ref"
                and isinstance(item.value, str)
                and str(item.value).strip()
            }
        )
    )
    constraints: dict[str, ContextConstraintV1] = {}
    try:
        for fact in snapshot.facts:
            if fact.key != "goal.long_term.constraint":
                continue
            raw = fact.value
            if not isinstance(raw, Mapping):
                raise RuntimeContextApplicationError(
                    "World long-term-goal constraint is untyped"
                )
            item = ContextConstraintV1(
                code=raw["code"],
                disposition=raw["disposition"],
                origin=raw["origin"],
                source_ref=raw["source_ref"],
                reason=raw.get("reason", ""),
            )
            if item.origin != "LONG_TERM_GOAL" or item.source_ref not in refs:
                raise RuntimeContextApplicationError(
                    "World long-term-goal constraint lacks a bound goal"
                )
            existing = constraints.get(item.constraint_digest)
            if existing is not None and existing != item:
                raise RuntimeContextApplicationError(
                    "World long-term-goal constraints conflict"
                )
            constraints[item.constraint_digest] = item
    except (ContextContractError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RuntimeContextApplicationError):
            raise
        raise RuntimeContextApplicationError(
            "World long-term-goal constraint is invalid"
        ) from exc
    bound_refs = {item.source_ref for item in constraints.values()}
    if set(refs) != bound_refs:
        raise RuntimeContextApplicationError(
            "active World long-term goal has no restrictive constraint"
        )
    return {
        "schema": "natsume.intent-constraints.v1",
        "long_term_goal_refs": list(refs),
        "long_term_goal_constraints": [
            item.to_dict()
            for item in sorted(
                constraints.values(),
                key=lambda value: value.constraint_digest,
            )
        ],
    }


def _relationship_state(bond_summary: Mapping[str, Any] | None) -> str:
    bond = _mapping(bond_summary)
    explicit = _upper(
        bond.get("relationship_state")
        or bond.get("trust_state")
        or bond.get("access_state"),
        "NORMAL",
    )
    if explicit in {"RESTRICTED", "UNTRUSTED"}:
        return explicit
    if explicit in {"TRUSTED", "ALLY", "INTIMATE"}:
        return "TRUSTED"
    # ``stage`` is the canonical production Bond summary field.  It never
    # expands authority: closer stages only project a trusted relationship
    # signal, while unknown/Friend stages remain the conservative NORMAL.
    stage = _upper(bond.get("stage"), "UNKNOWN")
    return (
        "TRUSTED"
        if stage
        in {
            "CLOSE_FRIEND",
            "CONFIDANT",
            "SOULMATE",
            "INTIMATE",
            "亲近兄妹",
            "亲密恋人",
            "灵魂伴侣",
        }
        else "NORMAL"
    )


def _user_state(
    emotion_snapshot: Mapping[str, Any] | None,
    emotion_context: Mapping[str, Any] | None,
) -> str:
    snapshot = _mapping(emotion_snapshot)
    context = _mapping(emotion_context)
    snapshot_context = _mapping(snapshot.get("emotion_context"))
    candidate = (
        snapshot_context.get("emotion_tag")
        or snapshot.get("emotion_tag")
        or snapshot.get("primary_emotion")
        or snapshot.get("emotion")
        or context.get("emotion_tag")
        or context.get("primary_emotion")
        or context.get("emotion")
        or context.get("label")
    )
    # Reuse the emotion subsystem's canonical alias authority.  In
    # particular IRRITATED becomes FRUSTRATED and HURT becomes SAD; unknown
    # labels remain fail-soft and are reduced to NEUTRAL by the World writer.
    return normalize_emotion_tag(str(candidate or "NEUTRAL"))


def _task_kind(intent_draft: Mapping[str, Any] | None) -> str:
    intent = _mapping(intent_draft)
    explicit = str(intent.get("task_kind") or intent.get("domain") or "").strip()
    if explicit:
        return _upper(explicit, "CHAT")
    route = _upper(intent.get("route"), "CHAT")
    target = str(intent.get("target_id") or intent.get("skill_id") or "").lower()
    task = str(intent.get("task") or "").lower()
    searchable = f"{target}\n{task}"
    if route == "PLAN" or any(word in searchable for word in ("research", "检索", "研究")):
        return "RESEARCH"
    if any(word in searchable for word in ("debug", "调试", "故障")):
        return "DEBUGGING"
    if any(word in searchable for word in ("refactor", "架构", "重构")):
        return "ARCHITECTURE_REFACTOR"
    if any(word in searchable for word in ("release", "deploy", "发布", "部署")):
        return "RELEASE_MAINTENANCE"
    return route


@dataclass(frozen=True, slots=True)
class FrozenRuntimeContextV1:
    """One JSON-safe projection frozen into a canonical WorkOrder."""

    payload: dict[str, Any]

    @property
    def scene_identity(self) -> dict[str, Any]:
        return dict(self.payload["scene_identity_v2"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class RuntimeContextApplicationService:
    """Resolve a request-scoped context without becoming a global locator."""

    def __init__(self, composition: RuntimeContextComposition) -> None:
        if not isinstance(composition, RuntimeContextComposition) or not composition.durable:
            raise RuntimeContextApplicationError(
                "a durable app-local RuntimeContextComposition is required"
            )
        self._composition = composition

    @property
    def composition(self) -> RuntimeContextComposition:
        return self._composition

    def freeze_request(
        self,
        *,
        request_body: Any,
        person_id: str,
        ts_ms: int,
        intent_draft: Mapping[str, Any],
        governor_result: Mapping[str, Any],
        command_authority: Mapping[str, Any] | None,
        emotion_context: Mapping[str, Any] | None,
        emotion_snapshot: Mapping[str, Any] | None,
        bond_summary: Mapping[str, Any] | None,
    ) -> FrozenRuntimeContextV1:
        if isinstance(ts_ms, bool) or not isinstance(ts_ms, int) or ts_ms < 0:
            raise RuntimeContextApplicationError("ts_ms must be non-negative")
        authority = _mapping(command_authority)
        try:
            secure_gateway_scope = (
                SecureGatewayConversationScopeV1.from_command_authority(authority)
            )
            if secure_gateway_scope is not None:
                secure_gateway_scope.assert_bound_to_authority(authority)
        except GatewayConversationScopeError as exc:
            raise RuntimeContextApplicationError(
                "Secure Gateway conversation scope is invalid"
            ) from exc
        if secure_gateway_scope is not None:
            # AffectFusion/relationship input and Runtime Context must share
            # this exact ingress-bound scope.  Re-deriving it here from raw
            # request fields would reopen the cross-user default-identity gap.
            if str(person_id or "").strip() != secure_gateway_scope.relationship_subject_id:
                raise RuntimeContextApplicationError(
                    "Secure Gateway conversation subject drifted"
                )
            principal = secure_gateway_scope.principal_id
            workspace = secure_gateway_scope.workspace_id
            subject = secure_gateway_scope.relationship_subject_id
        else:
            principal = _digest_identifier(
                "principal",
                authority.get("principal_id") or person_id or "anonymous",
            )
            # ``command_authority`` is assembled by Secure Ingress.  Raw request
            # bodies never nominate a workspace.  Conversational requests without
            # a tenant use a stable, server-derived personal workspace.
            tenant = authority.get("tenant_id") or authority.get("workspace_id")
            workspace = _digest_identifier(
                "workspace",
                f"tenant:{tenant}" if tenant else f"personal:{principal}",
            )
            subject = _digest_identifier("subject", person_id or principal)
        now_ms = int(ts_ms)
        # The raw request body (including desired Scene, constraints, and
        # multimodal activity hints) has no path into World.  Canonical server
        # signals first become a verified RuntimeEvent + observation batch;
        # Scene consumes only the reopened World projection.
        del request_body
        source_projections = _request_source_projections(
            bond_summary=bond_summary,
            emotion_snapshot=emotion_snapshot,
            emotion_context=emotion_context,
        )
        try:
            world_observation = (
                self._composition.world_observation_application.observe_and_resolve(
                    workspace_id=workspace,
                    subject_id=subject,
                    principal_id=principal,
                    relationship_state=_relationship_state(bond_summary),
                    user_state=_user_state(emotion_snapshot, emotion_context),
                    observed_at_ms=now_ms,
                    authority_projections=source_projections,
                )
            )
        except (RuntimeWorldObservationError, WorldResolverError) as exc:
            raise RuntimeContextApplicationError(
                "canonical World observation could not be persisted and reopened"
            ) from exc
        snapshot = world_observation.snapshot
        governor = _mapping(governor_result)
        scene_input = SceneResolutionInputV1(
            workspace_id=workspace,
            subject_id=subject,
            task_kind=_task_kind(intent_draft),
            relationship_state=world_observation.relationship_state,
            user_state=world_observation.user_state,
            activity_state=world_observation.activity_state,
            local_hour=world_observation.local_hour,
            risk_level=_bounded_risk(
                governor.get("risk_level") or _mapping(intent_draft).get("risk_level")
            ),
        )
        scene = self._composition.scene_resolver.resolve(
            snapshot=snapshot,
            context=scene_input,
            observed_at_ms=now_ms,
        )
        payload = {
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "runtime_context_observed_at_ms": now_ms,
            "runtime_context_principal_id": principal,
            "workspace_id": workspace,
            "subject_id": subject,
            "world_snapshot_v2": snapshot.to_dict(),
            "world_identity_v2": {
                "workspace_id": workspace,
                "subject_id": subject,
                "revision": snapshot.revision,
                "snapshot_digest": snapshot.snapshot_digest,
            },
            "scene_resolution_input_v1": scene_input.to_dict(),
            "scene_decision_v2": scene.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": workspace,
                "subject_id": subject,
                "scene_id": scene.scene_id,
                "revision": scene.revision,
                "decision_digest": scene.decision_digest,
            },
            "intent_constraints_v1": _long_term_goal_context(snapshot),
        }
        return self.require_frozen(payload)

    def require_frozen(self, value: Mapping[str, Any] | None) -> FrozenRuntimeContextV1:
        payload = _mapping(value)
        if payload.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
            raise RuntimeContextApplicationError("frozen runtime context schema is missing")
        identity = _mapping(payload.get("scene_identity_v2"))
        decision = _mapping(payload.get("scene_decision_v2"))
        required = (
            "workspace_id",
            "subject_id",
            "scene_id",
            "revision",
            "decision_digest",
        )
        if any(identity.get(key) in (None, "") for key in required):
            raise RuntimeContextApplicationError("frozen Scene identity is incomplete")
        expected = {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": decision.get("workspace_id"),
            "subject_id": decision.get("subject_id"),
            "scene_id": decision.get("scene_id"),
            "revision": decision.get("revision"),
            "decision_digest": decision.get("decision_digest"),
        }
        if dict(identity) != expected:
            raise RuntimeContextApplicationError("frozen Scene identity drifted")
        exact = self._composition.scene_resolver.require_decision(
            workspace_id=str(identity["workspace_id"]),
            subject_id=str(identity["subject_id"]),
            scene_id=str(identity["scene_id"]),
            revision=int(identity["revision"]),
            decision_digest=str(identity["decision_digest"]),
        )
        if exact.to_dict() != decision:
            raise RuntimeContextApplicationError("frozen Scene payload is not canonical")
        world = _mapping(payload.get("world_snapshot_v2"))
        try:
            canonical_world = WorldSnapshotV2(
                workspace_id=world["workspace_id"],
                subject_id=world["subject_id"],
                revision=int(world["revision"]),
                facts=tuple(
                    WorldFactV2(
                        workspace_id=item["workspace_id"],
                        subject_id=item["subject_id"],
                        key=item["key"],
                        value=item["value"],
                        source_ref=item["source_ref"],
                        confidence=float(item["confidence"]),
                        observed_at_ms=int(item["observed_at_ms"]),
                        expires_at_ms=int(item["expires_at_ms"]),
                        conflict_group=item.get("conflict_group", ""),
                        revision=int(item.get("revision", 1)),
                    )
                    for item in list(world.get("facts") or ())
                    if isinstance(item, Mapping)
                ),
                snapshot_digest=world["snapshot_digest"],
            )
        except (ContextContractError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeContextApplicationError(
                "frozen World snapshot is invalid"
            ) from exc
        if canonical_world.to_dict() != world:
            raise RuntimeContextApplicationError("frozen World snapshot drifted")
        observed_at_raw = payload.get("runtime_context_observed_at_ms")
        if observed_at_raw is None:
            # Compatibility for already frozen v1 fixtures: the observation
            # time is recoverable from its typed facts and is never read from
            # the caller's current request body.
            observed_at_raw = max(
                (item.observed_at_ms for item in canonical_world.facts),
                default=0,
            )
        if (
            isinstance(observed_at_raw, bool)
            or not isinstance(observed_at_raw, int)
            or observed_at_raw < 0
        ):
            raise RuntimeContextApplicationError(
                "frozen runtime context observation time is invalid"
            )
        try:
            reopened_world = self._composition.world_resolver.require_snapshot(
                workspace_id=canonical_world.workspace_id,
                subject_id=canonical_world.subject_id,
                revision=canonical_world.revision,
                snapshot_digest=canonical_world.snapshot_digest,
                observed_at_ms=observed_at_raw,
            )
        except WorldResolverError as exc:
            raise RuntimeContextApplicationError(
                "frozen World source evidence is unavailable or drifted"
            ) from exc
        if reopened_world.to_dict() != world:
            raise RuntimeContextApplicationError(
                "frozen World snapshot is not canonical"
            )
        canonical_goal_context = _long_term_goal_context(canonical_world)
        frozen_goal_context = payload.get("intent_constraints_v1")
        if frozen_goal_context is None:
            # Compatibility is limited to historical contexts with no goal
            # authority at all.  A non-empty goal set must always carry its
            # exact constraint projection.
            if canonical_goal_context["long_term_goal_refs"]:
                raise RuntimeContextApplicationError(
                    "frozen long-term-goal constraints are missing"
                )
        elif (
            not isinstance(frozen_goal_context, Mapping)
            or dict(frozen_goal_context) != canonical_goal_context
        ):
            raise RuntimeContextApplicationError(
                "frozen long-term-goal constraints drifted"
            )
        world_identity = _mapping(payload.get("world_identity_v2"))
        if world_identity != {
            "workspace_id": world.get("workspace_id"),
            "subject_id": world.get("subject_id"),
            "revision": world.get("revision"),
            "snapshot_digest": world.get("snapshot_digest"),
        } or scene_input_scope_drifted(payload, world=world, decision=decision):
            raise RuntimeContextApplicationError("frozen World/Scene scope drifted")
        # ``WorkOrderV3.input_pack`` also contains the separately frozen Host
        # request fields.  Reopening a replay must return only the canonical
        # Runtime Context projection; otherwise those Host fields overlap with
        # the freshly reconstructed request envelope and reject an exact
        # replay as authority drift.
        runtime_projection = {
            key: value
            for key, value in payload.items()
            if key in _RUNTIME_CONTEXT_FIELDS or key.startswith("runtime_context_")
        }
        return FrozenRuntimeContextV1(payload=runtime_projection)


def scene_input_scope_drifted(
    payload: Mapping[str, Any],
    *,
    world: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> bool:
    scene_input = _mapping(payload.get("scene_resolution_input_v1"))
    workspace = payload.get("workspace_id")
    subject = payload.get("subject_id")
    return any(
        item.get("workspace_id") != workspace or item.get("subject_id") != subject
        for item in (world, decision, scene_input)
    ) or decision.get("world_snapshot_digest") != world.get("snapshot_digest")


__all__ = [
    "FrozenRuntimeContextV1",
    "RuntimeContextApplicationError",
    "RuntimeContextApplicationService",
]
