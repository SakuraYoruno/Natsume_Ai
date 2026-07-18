"""Production Intent authority reconstructed from durable owners.

The inbound ``IntentFrameV1`` is a declaration, not an authority.  This
adapter re-opens the exact HostCommand admission, Scene journal entry and
Governor decision, then deterministically rebuilds the frame before issuing an
attestation.  No caller-provided frame or request payload participates in the
authority lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskNode
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    GovernorGrantError,
    canonical_payload_digest,
    durable_governor_binding,
)
from natsume_contracts.task import HostCommand, HostCommandSurface

from .context_contracts import (
    ContextConstraintV1,
    ContextPackV2,
    IntentFrameV1,
    SceneDecisionV2,
    WorldFactV2,
    WorldSnapshotV2,
)
from .intent_authority import (
    IntentAuthorityAssembler,
    IntentAuthorityAttestationV1,
    IntentAuthorityError,
    IntentPolicyIdentityV1,
)


_RUNTIME_CONTEXT_SCHEMA = "natsume.runtime-context.request.v1"
_GOVERNED_ACTION_SCHEMA = "Natsume.governed_action.binding.v1"
_INTENT_CONSTRAINT_FIELDS = {
    "task_constraints": "TASK",
    "relationship_constraints": "RELATIONSHIP",
    "long_term_goal_constraints": "LONG_TERM_GOAL",
    "risk_constraints": "RISK",
    "grant_constraints": "GRANT",
}
_LONG_TERM_GOAL_FACT_KEYS = frozenset(
    {"goal.long_term.ref", "long_term_goal.ref"}
)
_DURABLE_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentAuthorityError(f"canonical {field} is missing")
    return dict(value)


def _optional_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise IntentAuthorityError(f"canonical {field} is missing")
    return result


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise IntentAuthorityError(f"canonical {field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise IntentAuthorityError(f"canonical {field} is invalid") from exc
    if result < minimum:
        raise IntentAuthorityError(f"canonical {field} is invalid")
    return result


def _raw_sha256(value: object, *, field: str) -> str:
    result = str(value or "").strip().lower()
    if result.startswith("sha256:"):
        result = result.split(":", 1)[1]
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise IntentAuthorityError(f"canonical {field} is not SHA-256")
    return result


def _json_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IntentAuthorityError("canonical Intent input is not finite JSON") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest_ref(scheme: str, value: object) -> str:
    return f"{scheme}://sha256/{_json_digest(value)}"


def _constraint_from_mapping(
    value: object,
    *,
    field: str,
    expected_origin: str | None = None,
) -> ContextConstraintV1:
    payload = _mapping(value, field=field)
    if payload.get("schema") not in (None, "natsume.context-constraint.v1"):
        raise IntentAuthorityError(f"canonical {field} schema drifted")
    constraint = ContextConstraintV1(
        code=payload.get("code", ""),
        disposition=payload.get("disposition", ""),
        origin=payload.get("origin", ""),
        source_ref=payload.get("source_ref", ""),
        reason=payload.get("reason", ""),
    )
    if expected_origin is not None and constraint.origin != expected_origin:
        raise IntentAuthorityError(f"canonical {field} origin drifted")
    expected = constraint.to_dict()
    supplied = dict(payload)
    supplied.pop("schema", None)
    expected.pop("schema", None)
    if supplied != expected:
        raise IntentAuthorityError(f"canonical {field} projection drifted")
    return constraint


def _constraint_tuple(
    value: object,
    *,
    field: str,
    expected_origin: str,
) -> tuple[ContextConstraintV1, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise IntentAuthorityError(f"canonical {field} must be a list")
    return tuple(
        _constraint_from_mapping(
            item,
            field=f"{field}[{index}]",
            expected_origin=expected_origin,
        )
        for index, item in enumerate(value)
    )


def _world_fact_from_mapping(value: object, *, index: int) -> WorldFactV2:
    payload = _mapping(value, field=f"world_snapshot_v2.facts[{index}]")
    fact = WorldFactV2(
        workspace_id=payload.get("workspace_id", ""),
        subject_id=payload.get("subject_id", ""),
        key=payload.get("key", ""),
        value=payload.get("value"),
        source_ref=payload.get("source_ref", ""),
        confidence=payload.get("confidence", -1),
        observed_at_ms=payload.get("observed_at_ms", -1),
        expires_at_ms=payload.get("expires_at_ms", -1),
        conflict_group=payload.get("conflict_group", ""),
        revision=payload.get("revision", -1),
    )
    if fact.to_dict() != payload:
        raise IntentAuthorityError("canonical World fact projection drifted")
    return fact


def _world_snapshot_from_pack(input_pack: Mapping[str, Any]) -> WorldSnapshotV2:
    payload = _mapping(input_pack.get("world_snapshot_v2"), field="world_snapshot_v2")
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        raise IntentAuthorityError("canonical World facts are missing")
    snapshot = WorldSnapshotV2(
        workspace_id=payload.get("workspace_id", ""),
        subject_id=payload.get("subject_id", ""),
        revision=payload.get("revision", -1),
        facts=tuple(
            _world_fact_from_mapping(item, index=index)
            for index, item in enumerate(raw_facts)
        ),
        snapshot_digest=payload.get("snapshot_digest", ""),
    )
    if snapshot.to_dict() != payload:
        raise IntentAuthorityError("canonical World snapshot projection drifted")
    return snapshot


def scene_decision_from_mapping(value: object) -> SceneDecisionV2:
    """Reconstruct a Scene decision while verifying its complete projection."""

    payload = _mapping(value, field="scene_decision_v2")
    raw_constraints = payload.get("constraints")
    if not isinstance(raw_constraints, list):
        raise IntentAuthorityError("canonical Scene constraints are missing")
    scene = SceneDecisionV2(
        workspace_id=payload.get("workspace_id", ""),
        subject_id=payload.get("subject_id", ""),
        scene_id=payload.get("scene_id", ""),
        revision=payload.get("revision", -1),
        world_snapshot_digest=payload.get("world_snapshot_digest", ""),
        context_digest=payload.get("context_digest", ""),
        guardian_snapshot_digest=payload.get("guardian_snapshot_digest", ""),
        constraints=tuple(
            _constraint_from_mapping(
                item,
                field=f"scene_decision_v2.constraints[{index}]",
            )
            for index, item in enumerate(raw_constraints)
        ),
        decision_digest=payload.get("decision_digest", ""),
    )
    if scene.to_dict() != payload:
        raise IntentAuthorityError("canonical Scene decision projection drifted")
    return scene


def build_canonical_context_pack(
    *,
    work_order: WorkOrderV3,
    graph: TaskGraph,
) -> ContextPackV2:
    """Rebuild the graph-bound ContextPack from the durable WorkOrder."""

    if not isinstance(work_order, WorkOrderV3) or not isinstance(graph, TaskGraph):
        raise IntentAuthorityError("typed WorkOrderV3 and TaskGraph are required")
    input_pack = _mapping(work_order.input_pack, field="WorkOrder input_pack")
    if input_pack.get("runtime_context_schema") != _RUNTIME_CONTEXT_SCHEMA:
        raise IntentAuthorityError("canonical Runtime Context schema is missing")
    snapshot = _world_snapshot_from_pack(input_pack)
    workspace_id = _text(input_pack.get("workspace_id"), field="workspace_id")
    subject_id = _text(input_pack.get("subject_id"), field="subject_id")
    world_identity = _mapping(
        input_pack.get("world_identity_v2"),
        field="world_identity_v2",
    )
    if world_identity != {
        "workspace_id": snapshot.workspace_id,
        "subject_id": snapshot.subject_id,
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.snapshot_digest,
    }:
        raise IntentAuthorityError("frozen World identity differs from snapshot")
    if (
        snapshot.workspace_id != workspace_id
        or snapshot.subject_id != subject_id
        or work_order.task_graph_id != graph.graph_id
        or work_order.graph_revision != graph.revision
    ):
        raise IntentAuthorityError("canonical ContextPack scope or graph drifted")
    workspace = _optional_mapping(work_order.workspace)
    declared_workspace = str(workspace.get("workspace_id") or "").strip()
    if declared_workspace and declared_workspace != workspace_id:
        raise IntentAuthorityError("WorkOrder workspace differs from Runtime Context")
    pack = ContextPackV2(
        workspace_id=workspace_id,
        subject_id=subject_id,
        task_graph_id=graph.graph_id,
        graph_revision=graph.revision,
        world_snapshot_digest=snapshot.snapshot_digest,
        facts=snapshot.facts,
    )
    pack.verify_against_snapshot(snapshot)
    stored = input_pack.get("context_pack_v2")
    if stored is not None and _mapping(stored, field="context_pack_v2") != pack.to_dict():
        raise IntentAuthorityError("stored ContextPack differs from canonical graph")
    return pack


def _require_node(graph: TaskGraph, task_node_id: str) -> TaskNode:
    matches = tuple(node for node in graph.nodes if node.node_id == task_node_id)
    if len(matches) != 1:
        raise IntentAuthorityError("exact canonical TaskNode is missing")
    return matches[0]


def _validate_node_route_binding(*, work_order: WorkOrderV3, node: TaskNode) -> None:
    lineage = _mapping(
        work_order.metadata.get("planning_lineage"),
        field="planning_lineage",
    )
    raw_bindings = lineage.get("node_bindings")
    if not isinstance(raw_bindings, list):
        raise IntentAuthorityError("canonical TaskNode route binding is missing")
    matches = [
        _mapping(raw, field="planning_lineage node binding")
        for raw in raw_bindings
        if isinstance(raw, Mapping)
        and str(raw.get("node_id") or "").strip() == node.node_id
    ]
    if len(matches) != 1:
        raise IntentAuthorityError("exact canonical TaskNode route binding is missing")
    binding = matches[0]
    route = _text(binding.get("worker_route"), field="node worker_route").lower()
    target_id = _text(binding.get("target_id"), field="node target_id")
    expected = f"{route}:{quote(target_id, safe='')}"
    if _text(node.route, field="TaskNode route") != expected:
        raise IntentAuthorityError("canonical TaskNode route binding drifted")


def _node_goal(work_order: WorkOrderV3, node: TaskNode) -> str:
    lineage = _optional_mapping(work_order.metadata.get("planning_lineage"))
    raw_bindings = lineage.get("node_bindings")
    matches: list[str] = []
    if isinstance(raw_bindings, list):
        for raw in raw_bindings:
            binding = _optional_mapping(raw)
            if str(binding.get("node_id") or "").strip() == node.node_id:
                objective = str(binding.get("objective") or "").strip()
                if objective:
                    matches.append(objective)
    if len(set(matches)) > 1:
        raise IntentAuthorityError("canonical TaskNode goal bindings conflict")
    return matches[0] if matches else _text(work_order.objective, field="objective")


def _origin(command: HostCommand, input_pack: Mapping[str, Any]) -> str:
    declared = str(input_pack.get("intent_origin") or "").strip().upper()
    derived = (
        "SCHEDULED"
        if command.surface is HostCommandSurface.SCHEDULER
        else "DELEGATED"
        if command.surface is HostCommandSurface.INTERNAL
        else "EXPLICIT_USER"
    )
    if declared and declared != derived:
        raise IntentAuthorityError("canonical intent origin differs from Host surface")
    return derived


def _long_term_goal_refs(
    input_pack: Mapping[str, Any],
    *,
    context_pack: ContextPackV2,
) -> tuple[str, ...]:
    frozen: set[str] = set()
    for fact in context_pack.facts:
        if fact.key not in _LONG_TERM_GOAL_FACT_KEYS:
            continue
        value = str(fact.value or "").strip() if isinstance(fact.value, str) else ""
        if not value or _DURABLE_REF_RE.fullmatch(value) is None:
            raise IntentAuthorityError(
                "frozen World long-term goal fact must contain a durable ref"
            )
        frozen.add(value)

    constraints = _optional_mapping(input_pack.get("intent_constraints_v1"))
    raw = constraints.get("long_term_goal_refs")
    if raw is None:
        motive = _optional_mapping(input_pack.get("motive_state"))
        raw = motive.get("long_term_goal_refs")
        if raw is None:
            raw = motive.get("goal_refs")
    if raw is None:
        return tuple(sorted(frozen))
    if not isinstance(raw, (list, tuple)):
        raise IntentAuthorityError("canonical long-term goal refs must be a list")
    declared: set[str] = set()
    for item in raw:
        value = str(item or "").strip() if isinstance(item, str) else ""
        if not value or _DURABLE_REF_RE.fullmatch(value) is None:
            raise IntentAuthorityError(
                "declared long-term goal must be a durable ref"
            )
        declared.add(value)
    unproven = sorted(declared - frozen)
    if unproven:
        raise IntentAuthorityError(
            "declared long-term goal is absent from frozen World evidence"
        )
    # Caller declarations are evidence requests, never the owner.  Returning
    # every frozen ref prevents omission from weakening long-term constraints.
    return tuple(sorted(frozen))


def _explicit_constraints(
    input_pack: Mapping[str, Any],
) -> dict[str, tuple[ContextConstraintV1, ...]]:
    payload = _optional_mapping(input_pack.get("intent_constraints_v1"))
    return {
        field: _constraint_tuple(
            payload.get(field),
            field=f"intent_constraints_v1.{field}",
            expected_origin=origin,
        )
        for field, origin in _INTENT_CONSTRAINT_FIELDS.items()
    }


def _derived_constraints(
    *,
    work_order: WorkOrderV3,
    node: TaskNode,
    context_pack: ContextPackV2,
    approved_action_binding: Mapping[str, Any],
) -> dict[str, tuple[ContextConstraintV1, ...]]:
    result = {field: () for field in _INTENT_CONSTRAINT_FIELDS}
    review = _optional_mapping(work_order.review_policy)
    if bool(review.get("review_required") or review.get("durable_review_required")):
        result["task_constraints"] = (
            ContextConstraintV1(
                code="TASK_REVIEW_REQUIRED",
                disposition="REVIEW",
                origin="TASK",
                source_ref=_digest_ref(
                    "task-graph",
                    {
                        "graph_id": work_order.task_graph_id,
                        "graph_revision": work_order.graph_revision,
                        "node_id": node.node_id,
                    },
                ),
                reason="canonical WorkOrder requires review",
            ),
        )
    relationship_facts = tuple(
        item for item in context_pack.facts if item.key == "relationship.state"
    )
    if relationship_facts:
        latest_ms = max(item.observed_at_ms for item in relationship_facts)
        latest = tuple(
            item for item in relationship_facts if item.observed_at_ms == latest_ms
        )
        values = {str(item.value or "").strip().upper() for item in latest}
        allowed = {"TRUSTED", "NORMAL", "RESTRICTED", "UNTRUSTED"}
        if not values or not values.issubset(allowed):
            raise IntentAuthorityError(
                "canonical World relationship state is unsupported"
            )
        relationship = next(
            state
            for state in ("UNTRUSTED", "RESTRICTED", "NORMAL", "TRUSTED")
            if state in values
        )
    else:
        latest = ()
        relationship = "NORMAL"
    if relationship in {"RESTRICTED", "UNTRUSTED"}:
        selected_sources = tuple(
            sorted(
                {
                    item.source_ref
                    for item in latest
                    if str(item.value or "").strip().upper() == relationship
                }
            )
        )
        if not selected_sources:
            raise IntentAuthorityError(
                "canonical World relationship evidence is missing"
            )
        result["relationship_constraints"] = tuple(
            ContextConstraintV1(
                code=(
                    "RELATIONSHIP_UNTRUSTED"
                    if relationship == "UNTRUSTED"
                    else "RELATIONSHIP_REVIEW"
                ),
                disposition="BLOCK" if relationship == "UNTRUSTED" else "REVIEW",
                origin="RELATIONSHIP",
                source_ref=source_ref,
                reason=f"canonical relationship state is {relationship}",
            )
            for source_ref in selected_sources
        )
    binding_risk = _integer(
        approved_action_binding.get("risk_level"),
        field="grant risk_level",
        minimum=1,
    )
    if binding_risk > 5:
        raise IntentAuthorityError("canonical grant risk_level is invalid")
    risk = max(int(work_order.risk_level), int(node.risk_level), binding_risk)
    if risk >= 3:
        result["risk_constraints"] = (
            ContextConstraintV1(
                code="CRITICAL_RISK_BLOCKED" if risk >= 5 else "HIGH_RISK_REVIEW",
                disposition="BLOCK" if risk >= 5 else "REVIEW",
                origin="RISK",
                source_ref=(
                    f"risk-policy://{context_pack.workspace_id}/host-runtime-v1"
                ),
                reason=f"canonical task risk level is {risk}",
            ),
        )
    return result


def _merge_constraints(
    first: tuple[ContextConstraintV1, ...],
    second: tuple[ContextConstraintV1, ...],
) -> tuple[ContextConstraintV1, ...]:
    canonical: dict[tuple[str, str, str], ContextConstraintV1] = {}
    for item in (*first, *second):
        identity = (item.origin, item.source_ref, item.code)
        existing = canonical.get(identity)
        if existing is not None and existing.constraint_digest != item.constraint_digest:
            raise IntentAuthorityError("canonical Intent constraints conflict")
        canonical[identity] = item
    return tuple(sorted(canonical.values(), key=lambda item: item.constraint_digest))


def _validate_lineage(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    grant_ref: str,
) -> None:
    if (
        command.graph_id != graph.graph_id
        or command.graph_revision != graph.revision
        or command.root_work_order_id != graph.root_work_order_id
        or work_order.work_order_id != graph.root_work_order_id
        or work_order.root_work_order_id != graph.root_work_order_id
        or work_order.task_graph_id != graph.graph_id
        or work_order.graph_revision != graph.revision
        or command.governor_grant_ref != grant_ref
        or work_order.approval_ref != grant_ref
    ):
        raise IntentAuthorityError("canonical HostCommand lineage drifted")


def _validate_grant_binding(
    *,
    binding: Mapping[str, Any],
    command: HostCommand,
    work_order: WorkOrderV3,
    node: TaskNode,
    input_pack: Mapping[str, Any],
    subject_id: str,
    now_ms: int,
) -> None:
    if binding.get("schema_version") != _GOVERNED_ACTION_SCHEMA:
        raise IntentAuthorityError("durable approved_action_binding schema is invalid")
    if binding.get("read_only") is not True:
        raise IntentAuthorityError("durable approved_action_binding is not immutable")
    issued = _integer(binding.get("issued_at_ms"), field="grant issued_at_ms", minimum=1)
    expires = _integer(binding.get("expires_at_ms"), field="grant expires_at_ms", minimum=1)
    if issued > now_ms:
        raise IntentAuthorityError("durable approved_action_binding is not active yet")
    if expires <= issued or expires <= now_ms:
        raise IntentAuthorityError("durable approved_action_binding is expired")
    if binding.get("revoked_at_ms") is not None:
        _integer(binding.get("revoked_at_ms"), field="grant revoked_at_ms", minimum=1)
        raise IntentAuthorityError("durable approved_action_binding is revoked")
    principal = _text(binding.get("principal_id"), field="grant principal_id")
    person = _text(binding.get("person_id"), field="grant person_id")
    if principal != command.principal_id or person != subject_id:
        raise IntentAuthorityError("durable grant principal or subject drifted")
    frozen_principal = _text(
        input_pack.get("runtime_context_principal_id"),
        field="runtime_context_principal_id",
    )
    if frozen_principal != command.principal_id:
        raise IntentAuthorityError("Runtime Context principal differs from HostCommand")
    intent_id = _text(input_pack.get("intent_id"), field="intent_id")
    if _text(binding.get("intent_id"), field="grant intent_id") != intent_id:
        raise IntentAuthorityError("durable grant intent identity drifted")
    intent = _mapping(input_pack.get("intent_draft"), field="intent_draft")
    args = _optional_mapping(intent.get("args"))
    expected_args_digest = f"sha256:{_json_digest(args)}"
    if binding.get("intent_args_digest") != expected_args_digest:
        raise IntentAuthorityError("durable grant intent arguments drifted")
    declared_action = _text(intent.get("action"), field="intent_draft action")
    declared_scope = _text(
        intent.get("target"),
        field="intent_draft target",
    ).lower()
    declared_route = _text(
        intent.get("route"),
        field="intent_draft route",
    ).lower()
    declared_target_id = _text(
        intent.get("target_id"),
        field="intent_draft target_id",
    )
    command_route = _text(command.route_type, field="HostCommand route_type").lower()
    command_target_id = _text(command.target_id, field="HostCommand target_id")
    if binding.get("action_kind") != declared_action:
        raise IntentAuthorityError("durable grant action drifted")
    if str(binding.get("scope") or "").strip().lower() != declared_scope:
        raise IntentAuthorityError("durable grant scope drifted")
    if declared_route != command_route or declared_target_id != command_target_id:
        raise IntentAuthorityError("canonical command route binding drifted")
    _validate_node_route_binding(work_order=work_order, node=node)
    _text(binding.get("binding_ref"), field="grant binding_ref")


def build_canonical_intent_frame(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    task_node_id: str,
    canonical_scene: SceneDecisionV2,
    approved_action_binding: Mapping[str, Any],
    policy_identity: IntentPolicyIdentityV1,
    now_ms: int,
) -> IntentFrameV1:
    """Deterministically rebuild one frame from canonical durable records."""

    if not isinstance(command, HostCommand):
        raise IntentAuthorityError("typed canonical HostCommand is required")
    if not isinstance(work_order, WorkOrderV3) or not isinstance(graph, TaskGraph):
        raise IntentAuthorityError("typed canonical WorkOrderV3 and TaskGraph are required")
    if type(canonical_scene) is not SceneDecisionV2:
        raise IntentAuthorityError("typed canonical SceneDecisionV2 is required")
    if type(policy_identity) is not IntentPolicyIdentityV1:
        raise IntentAuthorityError("typed composition Intent policy is required")
    current_ms = _integer(now_ms, field="authority time")
    binding = dict(approved_action_binding or {})
    grant_ref = _text(command.governor_grant_ref, field="governor_grant_ref")
    _validate_lineage(
        command=command,
        work_order=work_order,
        graph=graph,
        grant_ref=grant_ref,
    )
    node = _require_node(graph, _text(task_node_id, field="task_node_id"))
    input_pack = _mapping(work_order.input_pack, field="WorkOrder input_pack")
    context_pack = build_canonical_context_pack(work_order=work_order, graph=graph)
    workspace_id = context_pack.workspace_id
    subject_id = context_pack.subject_id
    _validate_grant_binding(
        binding=binding,
        command=command,
        work_order=work_order,
        node=node,
        input_pack=input_pack,
        subject_id=subject_id,
        now_ms=current_ms,
    )
    if (
        canonical_scene.workspace_id != workspace_id
        or canonical_scene.subject_id != subject_id
        or canonical_scene.world_snapshot_digest != context_pack.world_snapshot_digest
    ):
        raise IntentAuthorityError("canonical Scene differs from ContextPack")
    scene_identity = _mapping(input_pack.get("scene_identity_v2"), field="scene_identity_v2")
    expected_scene_identity = {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "scene_id": canonical_scene.scene_id,
        "revision": canonical_scene.revision,
        "decision_digest": canonical_scene.decision_digest,
    }
    if scene_identity != expected_scene_identity:
        raise IntentAuthorityError("frozen Scene identity differs from canonical Scene")
    stored_scene = scene_decision_from_mapping(input_pack.get("scene_decision_v2"))
    if stored_scene != canonical_scene:
        raise IntentAuthorityError("frozen Scene payload differs from Scene journal")

    explicit = _explicit_constraints(input_pack)
    derived = _derived_constraints(
        work_order=work_order,
        node=node,
        context_pack=context_pack,
        approved_action_binding=binding,
    )
    constraints = {
        field: _merge_constraints(explicit[field], derived[field])
        for field in _INTENT_CONSTRAINT_FIELDS
    }
    objective = _node_goal(work_order, node)
    goal_digest = _json_digest(
        {
            "command_id": command.command_id,
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": node.node_id,
            "objective": objective,
            "input_sha256": command.input_sha256,
        }
    )
    binding_digest = canonical_payload_digest(binding)
    requested_action_digest = _json_digest(
        {
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node": node.to_dict(),
            "command_route_type": command.route_type,
            "command_target_id": command.target_id,
            "work_order_allowed_actions": list(work_order.allowed_actions),
            "work_order_forbidden_actions": list(work_order.forbidden_actions),
            "grant_action_kind": binding.get("action_kind"),
            "grant_scope": binding.get("scope"),
            "grant_intent_args_digest": binding.get("intent_args_digest"),
        }
    )
    frame = IntentFrameV1(
        workspace_id=workspace_id,
        subject_id=subject_id,
        principal_id=command.principal_id,
        intent_origin=_origin(command, input_pack),
        goal_ref=_digest_ref(
            "task-goal",
            {
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": node.node_id,
                "goal_digest": goal_digest,
            },
        ),
        goal_digest=goal_digest,
        task_graph_id=graph.graph_id,
        graph_revision=graph.revision,
        task_node_id=node.node_id,
        context_pack_digest=context_pack.pack_digest,
        scene_decision_digest=canonical_scene.decision_digest,
        scene_revision=canonical_scene.revision,
        requested_action_ref=_digest_ref(
            "task-action",
            {
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": node.node_id,
                "requested_action_digest": requested_action_digest,
            },
        ),
        requested_action_digest=requested_action_digest,
        grant_ref=grant_ref,
        grant_digest=binding_digest,
        arbiter_policy_ref=policy_identity.policy_ref,
        arbiter_policy_digest=policy_identity.policy_digest,
        task_constraints=constraints["task_constraints"],
        relationship_constraints=constraints["relationship_constraints"],
        long_term_goal_refs=_long_term_goal_refs(
            input_pack,
            context_pack=context_pack,
        ),
        long_term_goal_constraints=constraints["long_term_goal_constraints"],
        risk_constraints=constraints["risk_constraints"],
        grant_constraints=constraints["grant_constraints"],
    )
    stored_frame = input_pack.get("intent_frame_v1")
    if (
        stored_frame is not None
        and _mapping(stored_frame, field="intent_frame_v1") != frame.to_dict()
    ):
        raise IntentAuthorityError("stored IntentFrame differs from canonical authorities")
    return frame


@dataclass(slots=True)
class ProductionIntentAuthorityPort:
    """IntentAuthorityPort backed by HostCommand, Scene and Governor owners."""

    command_repository: Any
    scene_resolver: Any | None
    governor_ledger: Any
    policy_identity: IntentPolicyIdentityV1
    policy_constraints: tuple[ContextConstraintV1, ...] = ()
    clock: Callable[[], int] | None = None

    def __post_init__(self) -> None:
        if not callable(getattr(self.command_repository, "get_by_graph", None)):
            raise TypeError("command_repository must expose get_by_graph")
        if not callable(getattr(self.governor_ledger, "trace_request", None)):
            raise TypeError("governor_ledger must expose trace_request")
        if type(self.policy_identity) is not IntentPolicyIdentityV1:
            raise TypeError("policy_identity must be IntentPolicyIdentityV1")
        constraints = tuple(self.policy_constraints)
        if any(
            type(item) is not ContextConstraintV1
            or item.origin != "POLICY"
            or item.source_ref != self.policy_identity.policy_ref
            for item in constraints
        ):
            raise TypeError("policy_constraints must bind the composition policy")
        self.policy_constraints = constraints
        if self.clock is None:
            self.clock = lambda: int(time.time() * 1000)
        elif not callable(self.clock):
            raise TypeError("clock must be callable")
        if self.scene_resolver is not None:
            self.bind_scene_resolver(self.scene_resolver)

    def bind_scene_resolver(self, scene_resolver: Any) -> None:
        """Bind the composition-owned resolver exactly once.

        ``build_runtime_context_composition`` needs this port while it creates
        the resolver, so production composition performs a one-time bind
        immediately after construction and before admission opens.
        """

        if not callable(getattr(scene_resolver, "require_decision", None)):
            raise TypeError("scene_resolver must expose require_decision")
        existing = self.scene_resolver
        if existing is not None and existing is not scene_resolver:
            raise IntentAuthorityError("SceneResolver authority is already bound")
        self.scene_resolver = scene_resolver

    @property
    def scene_resolver_bound(self) -> bool:
        return self.scene_resolver is not None

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
        frame, scene, binding, current_ms = self._resolve_canonical_bundle(
            workspace_id=workspace_id,
            subject_id=subject_id,
            task_graph_id=task_graph_id,
            graph_revision=graph_revision,
            task_node_id=task_node_id,
            scene_id=scene_id,
            scene_revision=scene_revision,
            scene_decision_digest=scene_decision_digest,
            grant_ref=grant_ref,
        )
        issued_at_ms = _integer(
            binding.get("issued_at_ms"),
            field="grant issued_at_ms",
            minimum=1,
        )
        expires_at_ms = _integer(
            binding.get("expires_at_ms"),
            field="grant expires_at_ms",
            minimum=1,
        )
        revoked_raw = binding.get("revoked_at_ms")
        revoked_at_ms = (
            None
            if revoked_raw is None
            else _integer(revoked_raw, field="grant revoked_at_ms", minimum=1)
        )
        return IntentAuthorityAssembler.assemble(
            canonical_frame=frame,
            canonical_scene=scene,
            policy_identity=self.policy_identity,
            authority_ref=f"intent-authority://sha256/{frame.frame_digest}",
            attested_at_ms=current_ms,
            grant_status="ACTIVE",
            grant_issued_at_ms=issued_at_ms,
            grant_expires_at_ms=expires_at_ms,
            grant_revoked_at_ms=revoked_at_ms,
            grant_principal_id=_text(
                binding.get("principal_id"),
                field="grant principal_id",
            ),
            canonical_policy_constraints=self.policy_constraints,
        )

    def resolve_canonical_intent(
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
    ) -> tuple[IntentFrameV1, SceneDecisionV2]:
        """Rebuild the typed Frame and Scene for the admission boundary.

        This is deliberately the same canonical lookup used by
        ``resolve_intent_authority``.  Admission callers receive no mutable
        Governor record and cannot substitute a request-provided Frame.
        """

        frame, scene, _, _ = self._resolve_canonical_bundle(
            workspace_id=workspace_id,
            subject_id=subject_id,
            task_graph_id=task_graph_id,
            graph_revision=graph_revision,
            task_node_id=task_node_id,
            scene_id=scene_id,
            scene_revision=scene_revision,
            scene_decision_digest=scene_decision_digest,
            grant_ref=grant_ref,
        )
        return frame, scene

    def _resolve_canonical_bundle(
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
    ) -> tuple[IntentFrameV1, SceneDecisionV2, dict[str, Any], int]:
        resolver = self.scene_resolver
        if resolver is None:
            raise IntentAuthorityError("composition SceneResolver is not bound")
        try:
            now_ms = self.clock() if self.clock is not None else -1
        except Exception as exc:
            raise IntentAuthorityError("Intent authority clock is unavailable") from exc
        current_ms = _integer(now_ms, field="authority time")
        try:
            record = self.command_repository.get_by_graph(
                graph_id=task_graph_id,
                graph_revision=graph_revision,
            )
        except Exception as exc:
            raise IntentAuthorityError("canonical HostCommand lookup failed") from exc
        durable = _mapping(record, field="HostCommand admission")
        command = durable.get("command")
        work_order = durable.get("work_order")
        graph = durable.get("graph")
        if not isinstance(command, HostCommand):
            raise IntentAuthorityError("canonical HostCommand admission is untyped")
        if not isinstance(work_order, WorkOrderV3) or not isinstance(graph, TaskGraph):
            raise IntentAuthorityError("canonical WorkOrder or TaskGraph is untyped")
        if (
            command.graph_id != task_graph_id
            or command.graph_revision != graph_revision
            or command.governor_grant_ref != grant_ref
        ):
            raise IntentAuthorityError("Intent authority query differs from HostCommand")
        try:
            scene = resolver.require_decision(
                workspace_id=workspace_id,
                subject_id=subject_id,
                scene_id=scene_id,
                revision=scene_revision,
                decision_digest=_raw_sha256(
                    scene_decision_digest,
                    field="scene_decision_digest",
                ),
            )
        except IntentAuthorityError:
            raise
        except Exception as exc:
            raise IntentAuthorityError("canonical Scene lookup failed") from exc
        if type(scene) is not SceneDecisionV2:
            raise IntentAuthorityError("SceneResolver returned an untyped decision")
        try:
            decision = DurableGovernorGrantResolver(self.governor_ledger).require(
                request_id=command.request_id,
                expected_ref=grant_ref,
            )
        except GovernorGrantError as exc:
            raise IntentAuthorityError("canonical Governor grant lookup failed") from exc
        binding = durable_governor_binding(decision)
        if not binding:
            raise IntentAuthorityError("durable approved_action_binding is missing")
        frame = build_canonical_intent_frame(
            command=command,
            work_order=work_order,
            graph=graph,
            task_node_id=task_node_id,
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=self.policy_identity,
            now_ms=current_ms,
        )
        if frame.workspace_id != workspace_id or frame.subject_id != subject_id:
            raise IntentAuthorityError("Intent authority query crossed workspace scope")
        return frame, scene, dict(binding), current_ms


__all__ = [
    "ProductionIntentAuthorityPort",
    "build_canonical_context_pack",
    "build_canonical_intent_frame",
    "scene_decision_from_mapping",
]
