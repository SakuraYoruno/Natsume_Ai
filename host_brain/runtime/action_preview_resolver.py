"""Canonical authority reopening for effect-bound ActionPreview receipts.

The preview journal is deliberately not an authority source.  This module
reopens the immutable HostCommand/WorkOrder/TaskGraph, the live Attempt lease,
the durable parent/child Governor decisions and the app-local World/Scene
projection before a receipt may be issued or consumed.  Acceptance Effects
use their own claimed-effect fence after Node Acceptance; an expired worker
grant is retained only as lineage and is never renewed by delivery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_BLOCKED,
    ACTION_PREVIEW_DRIFT,
    ACTION_PREVIEW_EXPIRED,
    ACTION_PREVIEW_INVALID,
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)


NODE_AUTHORITY_SCHEMA = "Natsume.action_preview.node_authority_request.v1"
ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA = (
    "Natsume.action_preview.acceptance_effect_authority_request.v1"
)
DERIVED_EFFECT_AUTHORITY_SCHEMA = (
    "Natsume.action_preview.derived_effect_authority_request.v1"
)
ACTION_PREVIEW_AUTHORITY_BINDING_SCHEMA = (
    "Natsume.action_preview.authority_binding.v1"
)
EFFECT_DESCRIPTOR_SCHEMA = "Natsume.action_preview.effect_descriptor.v1"
SCHEDULER_OPERATION_EFFECT_TYPE = "scheduler.operation.execute"
_SCENE_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTION_PREVIEW_SCENE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "authority binding is not canonical JSON",
        ) from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is required")
    return result


def _integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid") from exc
    if result < minimum:
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid")
    return result


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is required")
    try:
        return json.loads(_canonical(dict(value)))
    except json.JSONDecodeError as exc:  # pragma: no cover - canonical already parsed
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid") from exc


def action_preview_scene_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project the canonical Scene identity into the Preview digest schema.

    ``SceneDecisionV2`` intentionally stores its digest as 64 lowercase hex,
    while ActionPreview receipts use the repository-wide ``sha256:`` digest
    form.  This is a representation conversion only: the production resolver
    still reopens the WorkOrder and compares the complete projected identity
    byte-for-byte before issuing a receipt.
    """

    scene = _mapping(value, "scene_identity")
    digest = str(scene.get("decision_digest") or "").strip().lower()
    if _SCENE_DIGEST.fullmatch(digest):
        digest = "sha256:" + digest
    elif _ACTION_PREVIEW_SCENE_DIGEST.fullmatch(digest) is None:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "scene_identity.decision_digest is invalid",
        )
    return {**scene, "decision_digest": digest}


def _route_binding(work_order: Any, *, node_id: str) -> tuple[str, str]:
    metadata = getattr(work_order, "metadata", None)
    lineage = dict(metadata.get("planning_lineage") or {}) if isinstance(metadata, Mapping) else {}
    bindings = lineage.get("node_bindings")
    if not isinstance(bindings, list):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "WorkOrder has no canonical planning node bindings",
        )
    matches = [dict(item) for item in bindings if isinstance(item, Mapping) and item.get("node_id") == node_id]
    if len(matches) != 1:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "WorkOrder node binding is missing or ambiguous",
        )
    route = _text(matches[0].get("worker_route"), "node_binding.worker_route").lower()
    target = _text(matches[0].get("target_id"), "node_binding.target_id")
    return route, target


def _node_descriptor(*, purpose: str, route: str, target: str) -> tuple[str, str, str]:
    requested = _text(purpose, "authority_context.purpose").lower()
    if requested == "skill.execute" and route == "skill":
        return target, target, target.lower()
    if requested == "delegate.dispatch" and route == "delegate":
        return "delegate.dispatch", target, f"delegate_{target}".lower()
    if requested == "scheduler.execute" and route == "delegate":
        return f"scheduler.{target}", target, f"scheduler_{target}".lower()
    raise ActionPreviewAuthorityError(
        ACTION_PREVIEW_BLOCKED,
        "requested action purpose is outside the frozen TaskNode route",
    )


def _scheduler_effect_descriptors(target: str) -> tuple[dict[str, Any], ...]:
    if str(target).strip().lower() != "life_command":
        return ()
    ui = {
        "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
        "action_kind": "proactive.ui_push",
        "scope_rule": {"kind": "ARG_FIELD", "field": "request_id"},
        "args_rule": {
            "kind": "EXACT_TOP_LEVEL_ALLOWLIST",
            "required_keys": [
                "type",
                "content",
                "event_id",
                "ts_ms",
                "request_id",
            ],
            "allowed_keys": [
                "type",
                "content",
                "time_period",
                "event_id",
                "ts_ms",
                "emotion_tag",
                "dominant_motive",
                "proactive_episode",
                "scene_tags",
                "scene_activity",
                "source",
                "delivery_id",
                "request_id",
                "raw_extra",
            ],
        },
        "max_risk_level": 3,
        "guardian_capability_id": "proactive_ui_push",
        "args_max_bytes": 65_536,
    }
    life_values = (
        (
            "proactive.gateway",
            3,
            ("platform", "conversation_id", "content", "msg_type", "raw_extra"),
            ("platform", "conversation_id", "content"),
        ),
        (
            "edge.proactive",
            3,
            (
                "type",
                "content",
                "time_period",
                "event_id",
                "ts_ms",
                "emotion_tag",
                "dominant_motive",
                "proactive_episode",
                "scene_tags",
                "scene_activity",
                "request_id",
                "raw_extra",
            ),
            ("content",),
        ),
        (
            "edge.gateway_reply",
            3,
            ("platform", "conversation_id", "content", "msg_type", "raw_extra"),
            ("platform", "conversation_id", "content"),
        ),
        ("vts.emotion", 4, ("emotion_context", "target"), ()),
        ("mateengine.state", 4, ("state", "emotion_tag", "target"), ()),
    )
    life = tuple(
        {
            "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
            "action_kind": f"life-delivery.{destination}",
            "scope_rule": {"kind": "ARG_FIELD", "field": "delivery_key"},
            "args_rule": {
                "kind": "LIFE_DELIVERY_ENVELOPE",
                "destination": destination,
                "payload_allowed_keys": list(payload_keys),
                "payload_required_keys": list(required_payload_keys),
            },
            "max_risk_level": risk,
            "guardian_capability_id": f"life_delivery_{destination.replace('.', '_')}",
            "args_max_bytes": 65_536,
        }
        for destination, risk, payload_keys, required_payload_keys in life_values
    )
    embodiment = (
        {
            "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
            "action_kind": "embodiment.vts.set_expression",
            "scope_rule": {"kind": "ARG_FIELD", "field": "delivery_key"},
            "args_rule": {
                "kind": "EMBODIMENT_COMMAND",
                "embodiment_kind": "avatar",
                "action": "set_expression",
                "payload_required_keys": ["emotion_tag"],
                "payload_allowed_keys": ["emotion_tag"],
            },
            "max_risk_level": 4,
            "guardian_capability_id": "embodiment_vts_expression",
            "args_max_bytes": 65_536,
        },
        {
            "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
            "action_kind": "embodiment.mateengine.command",
            "scope_rule": {"kind": "ARG_FIELD", "field": "delivery_key"},
            "args_rule": {
                "kind": "EMBODIMENT_COMMAND",
                "embodiment_kind": "mateengine",
                "action": "command",
                "payload_required_keys": ["command"],
                "payload_allowed_keys": ["command"],
            },
            "max_risk_level": 4,
            "guardian_capability_id": "embodiment_mateengine_command",
            "args_max_bytes": 65_536,
        },
    )
    return (ui, *life, *embodiment)


def _effect_descriptor(
    effect_type: str,
    *,
    scheduler_risk_level: int | None = None,
) -> tuple[str, int, str]:
    if effect_type == SCHEDULER_OPERATION_EFFECT_TYPE:
        if scheduler_risk_level is None:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "Scheduler Acceptance Effect has no canonical TaskNode risk",
            )
        risk = _integer(
            scheduler_risk_level,
            "Scheduler TaskNode.risk_level",
        )
        if risk > 5:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "Scheduler TaskNode.risk_level is outside [1, 5]",
            )
        return (
            f"acceptance-effect.{effect_type}",
            risk,
            "acceptance_effect_scheduler_operation_execute",
        )
    risk = {
        "conversation.user_turn": 1,
        "host.sys_mode_transition": 4,
        "life.foreground_completed": 2,
        "surface.reply_delivery": 3,
    }.get(effect_type)
    if risk is None:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "Acceptance Effect type has no approved Preview descriptor",
        )
    return (
        f"acceptance-effect.{effect_type}",
        risk,
        f"acceptance_effect_{effect_type.replace('.', '_')}",
    )


def _node_grant_binding_digest(value: Mapping[str, Any]) -> str:
    projection = dict(value or {})
    if set(projection) == {"grant_ref", "binding_digest"}:
        return str(projection.get("binding_digest") or "").strip().lower()
    return _digest(projection)


def _validate_effect_descriptor_projection(
    *,
    descriptor: Mapping[str, Any],
    scope: str,
    args: Mapping[str, Any],
) -> None:
    if len(_canonical(args).encode("utf-8")) > int(
        descriptor.get("args_max_bytes") or 0
    ):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "derived action args exceed their EffectDescriptor",
        )
    scope_rule = _mapping(descriptor.get("scope_rule"), "EffectDescriptor.scope_rule")
    scope_kind = str(scope_rule.get("kind") or "")
    if scope_kind == "ARG_FIELD":
        expected_scope = _text(args.get(scope_rule.get("field")), "derived scope field")
        if scope != expected_scope:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "derived action scope differs from its descriptor projection",
            )
    elif scope_kind == "ARG_FIELD_OR_LITERAL":
        candidates = [
            str(args.get(field) or "").strip()
            for field in scope_rule.get("fields") or ()
            if str(args.get(field) or "").strip()
        ]
        candidates.extend(
            str(item).strip()
            for item in scope_rule.get("literals") or ()
            if str(item).strip()
        )
        if not candidates or scope != candidates[0]:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "derived action scope is outside its descriptor template",
            )
    else:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "derived EffectDescriptor has no supported scope projection",
        )

    args_rule = _mapping(descriptor.get("args_rule"), "EffectDescriptor.args_rule")
    args_kind = str(args_rule.get("kind") or "")
    if args_kind == "EXACT_TOP_LEVEL_ALLOWLIST":
        _reject_derived_authority_material(args)
        allowed = {str(item) for item in args_rule.get("allowed_keys") or ()}
        required = {str(item) for item in args_rule.get("required_keys") or ()}
        if not required <= set(args) or not set(args) <= allowed:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "derived action args are outside the descriptor allowlist",
            )
    elif args_kind == "LIFE_DELIVERY_ENVELOPE":
        _reject_derived_authority_material(args)
        if set(args) != {"delivery_key", "destination", "payload"}:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "Life delivery Preview requires the exact typed envelope",
            )
        destination = _text(args.get("destination"), "derived destination").lower()
        if destination != str(args_rule.get("destination") or "").lower():
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "Life delivery destination differs from its EffectDescriptor",
            )
        payload = _mapping(args.get("payload"), "derived Life payload")
        allowed_payload = {
            str(item) for item in args_rule.get("payload_allowed_keys") or ()
        }
        required_payload = {
            str(item) for item in args_rule.get("payload_required_keys") or ()
        }
        if not required_payload <= set(payload) or not set(payload) <= allowed_payload:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "Life delivery payload is outside its EffectDescriptor",
            )
    elif args_kind == "EMBODIMENT_COMMAND":
        if set(args) != {"delivery_key", "command"}:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "embodiment Preview requires the exact typed envelope",
            )
        command = _mapping(args.get("command"), "derived embodiment command")
        exact_command_keys = {
            "schema_version",
            "command_id",
            "kind",
            "action",
            "payload",
            "authority_ref",
            "grant_ref",
            "deadline_ms",
            "idempotency_key",
            "issued_at_ms",
            "request_id",
            "session_id",
            "lease_id",
            "fencing_token",
        }
        delivery_key = _text(args.get("delivery_key"), "delivery_key")
        payload = _mapping(command.get("payload"), "embodiment command payload")
        _reject_derived_authority_material(payload)
        allowed_payload = {
            str(item) for item in args_rule.get("payload_allowed_keys") or ()
        }
        required_payload = {
            str(item) for item in args_rule.get("payload_required_keys") or ()
        }
        if (
            set(command) != exact_command_keys
            or str(command.get("kind") or "").lower()
            != str(args_rule.get("embodiment_kind") or "").lower()
            or str(command.get("action") or "") != str(args_rule.get("action") or "")
            or str(command.get("idempotency_key") or "") != delivery_key
            or str(command.get("request_id") or "") != delivery_key
            or not required_payload <= set(payload)
            or not set(payload) <= allowed_payload
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "embodiment command is outside its exact EffectDescriptor",
            )
    else:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "derived EffectDescriptor has no supported args projection",
        )


def _reject_derived_authority_material(value: Any) -> None:
    """EffectDescriptor payloads may carry data, never grant-like authority."""

    forbidden_fragments = (
        "authorization",
        "credential",
        "secret",
        "token",
        "grant",
        "authority_context",
        "action_preview",
    )
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if any(fragment in key for fragment in forbidden_fragments):
                raise ActionPreviewAuthorityError(
                    ACTION_PREVIEW_BLOCKED,
                    "derived action args contain authority-bearing material",
                )
            _reject_derived_authority_material(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_derived_authority_material(child)


@dataclass(frozen=True, slots=True)
class ResolvedActionPreviewAuthority:
    authority_kind: str
    action_kind: str
    action_scope: str
    risk_level: int
    guardian_capability_id: str
    context_pack_digest: str
    scene_identity: Mapping[str, Any]
    task_graph_id: str
    graph_revision: int
    task_node_id: str
    parent_governor_grant_ref: str
    node_grant_ref: str
    node_grant: Mapping[str, Any]
    expires_at_ms: int
    binding: Mapping[str, Any]
    effect_descriptors: tuple[Mapping[str, Any], ...] = ()
    origin_receipt_ref: str = ""

    @property
    def binding_digest(self) -> str:
        return _digest(dict(self.binding))


class ProductionActionPreviewAuthorityResolver:
    """Reopen canonical production authority; never accept caller assertions."""

    def __init__(
        self,
        *,
        command_repository: Any,
        task_store: Any,
        node_grant_verifier: Any,
        runtime_context_application: Any,
    ) -> None:
        required_command = ("get_by_graph",)
        required_store = (
            "get_graph",
            "get_node",
            "get_attempt",
            "get_active_lease",
            "get_acceptance_effect_intent",
            "get_node_artifact",
        )
        if any(not callable(getattr(command_repository, name, None)) for name in required_command):
            raise TypeError("command_repository lacks canonical Graph lookup")
        if any(not callable(getattr(task_store, name, None)) for name in required_store):
            raise TypeError("task_store lacks canonical Preview authority reads")
        if not callable(getattr(node_grant_verifier, "verify", None)):
            raise TypeError("node_grant_verifier.verify is required")
        if not callable(getattr(runtime_context_application, "require_frozen", None)):
            raise TypeError("runtime_context_application.require_frozen is required")
        self._commands = command_repository
        self._tasks = task_store
        self._node_grants = node_grant_verifier
        self._contexts = runtime_context_application

    def resolve(
        self,
        *,
        authority_context: Mapping[str, Any],
        action_kind: str,
        action_scope: str,
        action_args: Mapping[str, Any],
        risk_level: int,
        guardian_capability_id: str,
        context_pack_digest: str,
        scene_identity: Mapping[str, Any],
        task_graph_id: str,
        graph_revision: int,
        task_node_id: str,
        parent_governor_grant_ref: str,
        node_grant_ref: str,
        node_grant: Mapping[str, Any],
        now_ms: int,
        origin_receipt: Mapping[str, Any] | None = None,
    ) -> ResolvedActionPreviewAuthority:
        context = _mapping(authority_context, "authority_context")
        schema = str(context.get("schema_version") or "")
        if schema == NODE_AUTHORITY_SCHEMA:
            return self._resolve_node(
                context=context,
                action_kind=action_kind,
                action_scope=action_scope,
                action_args=action_args,
                risk_level=risk_level,
                guardian_capability_id=guardian_capability_id,
                context_pack_digest=context_pack_digest,
                scene_identity=scene_identity,
                task_graph_id=task_graph_id,
                graph_revision=graph_revision,
                task_node_id=task_node_id,
                parent_governor_grant_ref=parent_governor_grant_ref,
                node_grant_ref=node_grant_ref,
                node_grant=node_grant,
                now_ms=now_ms,
            )
        if schema == ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA:
            return self._resolve_acceptance_effect(
                context=context,
                action_kind=action_kind,
                action_scope=action_scope,
                action_args=action_args,
                risk_level=risk_level,
                guardian_capability_id=guardian_capability_id,
                context_pack_digest=context_pack_digest,
                scene_identity=scene_identity,
                task_graph_id=task_graph_id,
                graph_revision=graph_revision,
                task_node_id=task_node_id,
                parent_governor_grant_ref=parent_governor_grant_ref,
                node_grant_ref=node_grant_ref,
                node_grant=node_grant,
                now_ms=now_ms,
            )
        if schema == DERIVED_EFFECT_AUTHORITY_SCHEMA:
            return self._resolve_derived(
                context=context,
                action_kind=action_kind,
                action_scope=action_scope,
                action_args=action_args,
                risk_level=risk_level,
                guardian_capability_id=guardian_capability_id,
                context_pack_digest=context_pack_digest,
                scene_identity=scene_identity,
                task_graph_id=task_graph_id,
                graph_revision=graph_revision,
                task_node_id=task_node_id,
                parent_governor_grant_ref=parent_governor_grant_ref,
                node_grant_ref=node_grant_ref,
                node_grant=node_grant,
                origin_receipt=origin_receipt,
                now_ms=now_ms,
            )
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "ActionPreview authority context schema is unsupported",
        )

    def _command_context(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, Any], str, str]:
        record = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
        if not isinstance(record, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "HostCommand authority is missing")
        command = record.get("command")
        work_order = record.get("work_order")
        graph = self._tasks.get_graph(graph_id, graph_revision)
        node = self._tasks.get_node(graph_id, graph_revision, node_id)
        if command is None or work_order is None or not isinstance(graph, Mapping) or not isinstance(node, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "canonical Task authority is incomplete")
        pack = getattr(work_order, "input_pack", None)
        if not isinstance(pack, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "WorkOrder ContextPack is missing")
        self._contexts.require_frozen(dict(pack))
        route, target = _route_binding(work_order, node_id=node_id)
        return dict(record), work_order, dict(graph), dict(node), route, target

    def _resolve_node(self, **values: Any) -> ResolvedActionPreviewAuthority:
        context = values.pop("context")
        now_ms = _integer(values.pop("now_ms"), "now_ms")
        graph_id = _text(values["task_graph_id"], "task_graph_id")
        revision = _integer(values["graph_revision"], "graph_revision")
        node_id = _text(values["task_node_id"], "task_node_id")
        record, work_order, graph, node, route, target = self._command_context(
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
        )
        command = record["command"]
        purpose = _text(context.get("purpose"), "authority_context.purpose")
        canonical_action, canonical_scope, canonical_capability = _node_descriptor(
            purpose=purpose,
            route=route,
            target=target,
        )
        node_payload = _mapping(node.get("node"), "TaskNode")
        canonical_risk = _integer(node_payload.get("risk_level"), "TaskNode.risk_level")
        required_context = {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": purpose,
            "command_id": str(getattr(command, "command_id", "")),
            "attempt_id": _text(context.get("attempt_id"), "authority_context.attempt_id"),
            "lease_id": _text(context.get("lease_id"), "authority_context.lease_id"),
            "lease_owner_id": _text(context.get("lease_owner_id"), "authority_context.lease_owner_id"),
            "fencing_token": _integer(context.get("fencing_token"), "authority_context.fencing_token"),
        }
        if context != required_context:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "node authority request drifted")
        attempt = self._tasks.get_attempt(required_context["attempt_id"])
        lease = self._tasks.get_active_lease(required_context["attempt_id"])
        if not isinstance(attempt, Mapping) or not isinstance(lease, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Task Attempt has no active lease")
        if (
            str(attempt.get("graph_id") or "") != graph_id
            or int(attempt.get("graph_revision") or 0) != revision
            or str(attempt.get("node_id") or "") != node_id
            or str(attempt.get("status") or "") not in {"LEASED", "RUNNING"}
            or str(lease.get("lease_id") or "") != required_context["lease_id"]
            or str(lease.get("owner_id") or "") != required_context["lease_owner_id"]
            or int(lease.get("fencing_token") or 0) != required_context["fencing_token"]
            or str(lease.get("status") or "") != "ACTIVE"
            or int(lease.get("expires_ts_ms") or 0) <= now_ms
        ):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Task Attempt/Lease authority drifted")
        verified = self._node_grants.verify(
            grant_ref=_text(values["node_grant_ref"], "node_grant_ref"),
            parent_grant_ref=_text(values["parent_governor_grant_ref"], "parent_governor_grant_ref"),
            request_id=str(getattr(command, "request_id", "")),
            session_id=str(getattr(command, "session_id", "")),
            principal_id=str(getattr(command, "principal_id", "")),
            graph_id=graph_id,
            graph_revision=revision,
            root_work_order_id=str(getattr(command, "root_work_order_id", "")),
            node_id=node_id,
            route=route,
            target_id=target,
            attempt_id=required_context["attempt_id"],
            lease_id=required_context["lease_id"],
            lease_owner_id=required_context["lease_owner_id"],
            fencing_token=required_context["fencing_token"],
        )
        verified_map = verified.to_dict() if callable(getattr(verified, "to_dict", None)) else _mapping(verified, "verified node grant")
        supplied_grant = _mapping(values["node_grant"], "node_grant")
        if supplied_grant != verified_map:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "caller node grant differs from canonical authority")
        pack = dict(getattr(work_order, "input_pack", {}) or {})
        canonical_context = context_pack_digest_from_work_order(work_order)
        canonical_scene = action_preview_scene_identity(
            _mapping(pack.get("scene_identity_v2"), "scene_identity_v2")
        )
        expected = (
            canonical_action,
            canonical_scope,
            canonical_risk,
            canonical_capability,
            canonical_context,
            canonical_scene,
            graph_id,
            revision,
            node_id,
            str(getattr(command, "governor_grant_ref", "")),
            str(verified_map.get("grant_ref") or ""),
        )
        actual = (
            str(values["action_kind"]),
            str(values["action_scope"]),
            int(values["risk_level"]),
            str(values["guardian_capability_id"]).lower(),
            str(values["context_pack_digest"]).lower(),
            _mapping(values["scene_identity"], "scene_identity"),
            graph_id,
            revision,
            node_id,
            str(values["parent_governor_grant_ref"]),
            str(values["node_grant_ref"]),
        )
        if actual != expected:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Preview boundary differs from canonical Task authority")
        expires = min(int(verified_map.get("expires_at_ms") or 0), int(lease.get("expires_ts_ms") or 0))
        if expires <= now_ms:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_EXPIRED, "canonical node authority expired")
        descriptors = _scheduler_effect_descriptors(target) if purpose == "scheduler.execute" else ()
        binding = {
            "schema_version": ACTION_PREVIEW_AUTHORITY_BINDING_SCHEMA,
            "authority_kind": "TASK_NODE",
            "command_id": str(getattr(command, "command_id", "")),
            "command_digest": str(record.get("command_digest") or ""),
            "work_order_digest": str(record.get("work_order_digest") or ""),
            "graph_digest": str(record.get("graph_digest") or ""),
            "graph_status": str(graph.get("status") or ""),
            "node_status": str(node.get("status") or ""),
            "attempt_id": required_context["attempt_id"],
            "attempt_status": str(attempt.get("status") or ""),
            "lease_id": required_context["lease_id"],
            "lease_owner_id": required_context["lease_owner_id"],
            "fencing_token": required_context["fencing_token"],
            "lease_expires_at_ms": int(lease.get("expires_ts_ms") or 0),
            "node_grant_digest": _digest(verified_map),
        }
        return ResolvedActionPreviewAuthority(
            authority_kind="TASK_NODE",
            action_kind=canonical_action,
            action_scope=canonical_scope,
            risk_level=canonical_risk,
            guardian_capability_id=canonical_capability,
            context_pack_digest=canonical_context,
            scene_identity=canonical_scene,
            task_graph_id=graph_id,
            graph_revision=revision,
            task_node_id=node_id,
            parent_governor_grant_ref=str(getattr(command, "governor_grant_ref", "")),
            node_grant_ref=str(verified_map["grant_ref"]),
            node_grant=verified_map,
            expires_at_ms=expires,
            binding=binding,
            effect_descriptors=descriptors,
        )

    def _resolve_acceptance_effect(self, **values: Any) -> ResolvedActionPreviewAuthority:
        context = values.pop("context")
        now_ms = _integer(values.pop("now_ms"), "now_ms")
        effect_id = _text(context.get("effect_id"), "authority_context.effect_id")
        effect = self._tasks.get_acceptance_effect_intent(effect_id)
        if not isinstance(effect, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Acceptance Effect authority is missing")
        graph_id = str(effect.get("graph_id") or "")
        revision = int(effect.get("graph_revision") or 0)
        node_id = str(effect.get("node_id") or "")
        record, work_order, graph, node, route, target = self._command_context(
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
        )
        command = record["command"]
        required_context = {
            "schema_version": ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA,
            "effect_id": effect_id,
            "lease_owner_id": _text(context.get("lease_owner_id"), "authority_context.lease_owner_id"),
            "fencing_token": _integer(context.get("fencing_token"), "authority_context.fencing_token"),
        }
        if context != required_context:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Acceptance Effect authority request drifted")
        effect_type = str(effect.get("effect_type") or "")
        descriptors: tuple[Mapping[str, Any], ...] = ()
        if effect_type == SCHEDULER_OPERATION_EFFECT_TYPE:
            command_surface = str(
                getattr(getattr(command, "surface", ""), "value", None)
                or getattr(command, "surface", "")
            ).strip().lower()
            command_route = str(getattr(command, "route_type", "") or "").strip().lower()
            command_target = str(getattr(command, "target_id", "") or "").strip()
            work_order_metadata = getattr(work_order, "metadata", None)
            metadata = (
                dict(work_order_metadata)
                if isinstance(work_order_metadata, Mapping)
                else {}
            )
            operator_scheduler_trigger = (
                command_surface == "operator"
                and str(metadata.get("operator_command_id") or "").strip()
                == "scheduler.task.trigger"
                and str(metadata.get("command_surface") or "").strip().lower()
                == "operator"
            )
            if (
                command_surface != "scheduler"
                and not operator_scheduler_trigger
            ) or (
                command_surface not in {"scheduler", "operator"}
                or route != "delegate"
                or command_route != "delegate"
                or not target
                or command_target != target
            ):
                raise ActionPreviewAuthorityError(
                    ACTION_PREVIEW_BLOCKED,
                    "Scheduler Acceptance Effect is outside its canonical Scheduler route",
                )
            node_payload = _mapping(node.get("node"), "TaskNode")
            action, canonical_risk, capability = _effect_descriptor(
                effect_type,
                scheduler_risk_level=_integer(
                    node_payload.get("risk_level"),
                    "TaskNode.risk_level",
                ),
            )
            # Deliberately stays narrow: only the existing life_command target
            # has downstream UI/Gateway/Edge/embodiment descriptors.  Generic
            # scheduler_production and every other Scheduler target receive no
            # derived authority.
            descriptors = _scheduler_effect_descriptors(target)
        else:
            action, canonical_risk, capability = _effect_descriptor(effect_type)
        if (
            str(effect.get("status") or "") != "CLAIMED"
            or str(effect.get("lease_owner") or "") != required_context["lease_owner_id"]
            or int(effect.get("fencing_token") or 0) != required_context["fencing_token"]
            or int(effect.get("lease_expires_ts_ms") or 0) <= now_ms
            or str(node.get("status") or "") != "ACCEPTED"
        ):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Acceptance/claim fence is not live and exact")
        attempt = self._tasks.get_attempt(str(effect.get("attempt_id") or ""))
        if (
            not isinstance(attempt, Mapping)
            or str(attempt.get("status") or "") != "SUCCEEDED"
            or str(attempt.get("graph_id") or "") != graph_id
            or int(attempt.get("graph_revision") or 0) != revision
            or str(attempt.get("node_id") or "") != node_id
        ):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Acceptance Effect Attempt is not successful and exact")
        artifact = self._tasks.get_node_artifact(graph_id, revision, node_id)
        if str(artifact.get("attempt_id") or "") != str(effect.get("attempt_id") or ""):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Acceptance artifact Attempt drifted")
        payload = _mapping(effect.get("payload"), "Acceptance Effect payload")
        preview = _mapping(payload.get("_action_preview_context"), "Acceptance Effect preview context")
        pack = dict(getattr(work_order, "input_pack", {}) or {})
        canonical_context = context_pack_digest_from_work_order(work_order)
        canonical_scene = action_preview_scene_identity(
            _mapping(pack.get("scene_identity_v2"), "scene_identity_v2")
        )
        lineage_grant_ref = _text(preview.get("node_grant_ref"), "Acceptance Effect node_grant_ref")
        lineage_grant_digest = _text(preview.get("node_grant_digest"), "Acceptance Effect node_grant_digest")
        supplied_grant = _mapping(values["node_grant"], "node_grant")
        if supplied_grant != {"grant_ref": lineage_grant_ref, "binding_digest": lineage_grant_digest}:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Acceptance Effect node lineage drifted")
        expected = (
            action,
            effect_id,
            canonical_risk,
            capability,
            canonical_context,
            canonical_scene,
            graph_id,
            revision,
            node_id,
            str(getattr(command, "governor_grant_ref", "")),
            lineage_grant_ref,
        )
        actual = (
            str(values["action_kind"]),
            str(values["action_scope"]),
            int(values["risk_level"]),
            str(values["guardian_capability_id"]).lower(),
            str(values["context_pack_digest"]).lower(),
            _mapping(values["scene_identity"], "scene_identity"),
            str(values["task_graph_id"]),
            int(values["graph_revision"]),
            str(values["task_node_id"]),
            str(values["parent_governor_grant_ref"]),
            str(values["node_grant_ref"]),
        )
        if actual != expected:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Acceptance Effect boundary differs from durable authority")
        expected_args = dict(payload)
        expected_args.pop("_action_preview_context", None)
        if _mapping(values["action_args"], "action_args") != expected_args:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "Acceptance Effect action args differ from the durable payload",
            )
        graph_status = str(graph.get("status") or "")
        if str(effect.get("commit_scope") or "") == "GRAPH" and graph_status != "COMPLETED":
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "Graph-scoped Acceptance Effect is not committed")
        binding = {
            "schema_version": ACTION_PREVIEW_AUTHORITY_BINDING_SCHEMA,
            "authority_kind": "ACCEPTANCE_EFFECT",
            "effect_id": effect_id,
            "effect_type": effect_type,
            "effect_status": "CLAIMED",
            "effect_status_version": int(effect.get("status_version") or 0),
            "effect_attempt_count": int(effect.get("attempt_count") or 0),
            "effect_payload_sha256": str(effect.get("payload_sha256") or ""),
            "attempt_id": str(effect.get("attempt_id") or ""),
            "attempt_status": "SUCCEEDED",
            "artifact_ref": str(artifact.get("artifact_ref") or ""),
            "artifact_payload_sha256": str(artifact.get("payload_sha256") or ""),
            "node_status": "ACCEPTED",
            "graph_status": graph_status,
            "lease_owner_id": required_context["lease_owner_id"],
            "fencing_token": required_context["fencing_token"],
            "lease_expires_at_ms": int(effect.get("lease_expires_ts_ms") or 0),
            "node_grant_lineage_digest": lineage_grant_digest,
        }
        if effect_type == SCHEDULER_OPERATION_EFFECT_TYPE:
            binding.update(
                {
                    "scheduler_route": route,
                    "scheduler_target": target,
                    "effect_descriptors_digest": _digest(
                        [dict(item) for item in descriptors]
                    ),
                }
            )
        return ResolvedActionPreviewAuthority(
            authority_kind="ACCEPTANCE_EFFECT",
            action_kind=action,
            action_scope=effect_id,
            risk_level=canonical_risk,
            guardian_capability_id=capability,
            context_pack_digest=canonical_context,
            scene_identity=canonical_scene,
            task_graph_id=graph_id,
            graph_revision=revision,
            task_node_id=node_id,
            parent_governor_grant_ref=str(getattr(command, "governor_grant_ref", "")),
            node_grant_ref=lineage_grant_ref,
            node_grant=supplied_grant,
            expires_at_ms=int(effect.get("lease_expires_ts_ms") or 0),
            binding=binding,
            effect_descriptors=descriptors,
        )

    def _resolve_derived(self, **values: Any) -> ResolvedActionPreviewAuthority:
        context = values.pop("context")
        now_ms = _integer(values.pop("now_ms"), "now_ms")
        origin = values.pop("origin_receipt")
        if not isinstance(origin, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "derived action has no reopened origin receipt")
        origin_ref = _text(context.get("origin_receipt_ref"), "authority_context.origin_receipt_ref")
        raw_origin_boundary = context.get("origin_boundary")
        if not isinstance(raw_origin_boundary, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "derived action has no exact origin execution boundary",
            )
        origin_boundary = dict(raw_origin_boundary)
        required_origin_fields = {
            "action_kind",
            "action_scope",
            "risk_level",
            "action_args",
            "context_pack_digest",
            "scene_identity",
            "task_graph_id",
            "graph_revision",
            "task_node_id",
            "parent_governor_grant_ref",
            "node_grant_ref",
            "node_grant",
            "authority_context",
        }
        if frozenset(origin_boundary) != required_origin_fields:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "derived action origin execution boundary drifted",
            )
        required = {
            "schema_version": DERIVED_EFFECT_AUTHORITY_SCHEMA,
            "origin_event_id": _text(
                context.get("origin_event_id"),
                "authority_context.origin_event_id",
            ),
            "origin_receipt_ref": origin_ref,
            "origin_boundary": origin_boundary,
        }
        if context != required:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "derived action authority request drifted")
        origin_authority_kind = str(
            origin.get("authority_kind") or ""
        ).strip().upper()
        if origin_authority_kind not in {"TASK_NODE", "ACCEPTANCE_EFFECT"}:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "Scheduler-derived action origin is not an allowed authority receipt",
            )
        origin_args = _mapping(
            origin_boundary.get("action_args"),
            "origin action_args",
        )
        if _digest(origin_args) != str(origin.get("action_args_digest") or ""):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "derived action origin args differ from the exact origin receipt",
            )
        try:
            reopened_origin = self.resolve(
                authority_context=_mapping(
                    origin_boundary.get("authority_context"),
                    "origin authority_context",
                ),
                action_kind=str(origin_boundary.get("action_kind") or ""),
                action_scope=str(origin_boundary.get("action_scope") or ""),
                action_args=_mapping(
                    origin_boundary.get("action_args"),
                    "origin action_args",
                ),
                risk_level=_integer(
                    origin_boundary.get("risk_level"),
                    "origin risk_level",
                ),
                guardian_capability_id=str(
                    origin.get("guardian_capability_id") or ""
                ),
                context_pack_digest=str(
                    origin_boundary.get("context_pack_digest") or ""
                ),
                scene_identity=_mapping(
                    origin_boundary.get("scene_identity"),
                    "origin scene_identity",
                ),
                task_graph_id=str(origin_boundary.get("task_graph_id") or ""),
                graph_revision=_integer(
                    origin_boundary.get("graph_revision"),
                    "origin graph_revision",
                ),
                task_node_id=str(origin_boundary.get("task_node_id") or ""),
                parent_governor_grant_ref=str(
                    origin_boundary.get("parent_governor_grant_ref") or ""
                ),
                node_grant_ref=str(origin_boundary.get("node_grant_ref") or ""),
                node_grant=_mapping(
                    origin_boundary.get("node_grant"),
                    "origin node_grant",
                ),
                now_ms=now_ms,
                origin_receipt=None,
            )
        except ActionPreviewAuthorityError:
            raise
        except Exception as exc:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "derived action origin Task authority could not be reopened",
            ) from exc
        expected_origin = (
            str(origin.get("action_kind") or ""),
            str(origin.get("action_scope") or ""),
            int(origin.get("risk_level") or 0),
            str(origin.get("context_pack_digest") or ""),
            _mapping(origin.get("scene_identity"), "origin scene_identity"),
            str(origin.get("task_graph_id") or ""),
            int(origin.get("graph_revision") or 0),
            str(origin.get("task_node_id") or ""),
            str(origin.get("parent_governor_grant_ref") or ""),
            str(origin.get("node_grant_ref") or ""),
        )
        actual_origin = (
            str(reopened_origin.action_kind),
            str(reopened_origin.action_scope),
            int(reopened_origin.risk_level),
            str(reopened_origin.context_pack_digest),
            _mapping(reopened_origin.scene_identity, "reopened origin scene_identity"),
            str(reopened_origin.task_graph_id),
            int(reopened_origin.graph_revision),
            str(reopened_origin.task_node_id),
            str(reopened_origin.parent_governor_grant_ref),
            str(reopened_origin.node_grant_ref),
        )
        if (
            str(reopened_origin.authority_kind).strip().upper()
            != origin_authority_kind
            or actual_origin != expected_origin
            or _digest(dict(reopened_origin.binding))
            != str(origin.get("authority_binding_digest") or "")
            or _node_grant_binding_digest(reopened_origin.node_grant)
            != str(origin.get("node_grant_digest") or "")
            or int(reopened_origin.expires_at_ms)
            < int(origin.get("expires_at_ms") or 0)
            or [dict(item) for item in reopened_origin.effect_descriptors]
            != [dict(item) for item in origin.get("effect_descriptors") or ()]
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "derived action origin Task authority changed after its receipt",
            )
        descriptors = origin.get("effect_descriptors")
        if not isinstance(descriptors, list):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "origin receipt has no derived EffectDescriptor set")
        action = str(values["action_kind"])
        scope = _text(values["action_scope"], "action_scope")
        args = _mapping(values["action_args"], "action_args")
        risk = int(values["risk_level"])
        capability = str(values["guardian_capability_id"]).lower()
        supplied_node_grant = _mapping(values["node_grant"], "node_grant")
        if (
            str(supplied_node_grant.get("grant_ref") or "")
            != str(origin.get("node_grant_ref") or "")
            or _node_grant_binding_digest(supplied_node_grant)
            != str(origin.get("node_grant_digest") or "")
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "derived action node lineage differs from the origin receipt",
            )
        matches = [
            dict(item)
            for item in descriptors
            if isinstance(item, Mapping)
            and item.get("schema_version") == EFFECT_DESCRIPTOR_SCHEMA
            and item.get("action_kind") == action
            and int(item.get("max_risk_level") or 0) >= risk
            and str(item.get("guardian_capability_id") or "").lower() == capability
        ]
        if len(matches) != 1:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "derived action is outside the origin EffectDescriptor set")
        descriptor = matches[0]
        _validate_effect_descriptor_projection(
            descriptor=descriptor,
            scope=scope,
            args=args,
        )
        args_rule = _mapping(descriptor.get("args_rule"), "EffectDescriptor.args_rule")
        if str(args_rule.get("kind") or "") == "EMBODIMENT_COMMAND":
            command = _mapping(args.get("command"), "derived embodiment command")
            if (
                str(command.get("grant_ref") or "")
                != str(origin.get("node_grant_ref") or "")
                or str(command.get("authority_ref") or "")
                != f"authority://task-node/{scope}"
                or int(command.get("issued_at_ms") or 0) > now_ms
                or int(command.get("deadline_ms") or 0)
                > int(origin.get("expires_at_ms") or 0)
                or str(command.get("session_id") or "")
                or str(command.get("lease_id") or "")
                or int(command.get("fencing_token") or 0) != 0
            ):
                raise ActionPreviewAuthorityError(
                    ACTION_PREVIEW_DRIFT,
                    "embodiment command extends or differs from its TaskNode authority",
                )
        expires = int(origin.get("expires_at_ms") or 0)
        if expires <= now_ms:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_EXPIRED, "origin Preview authority expired")
        binding = {
            "schema_version": ACTION_PREVIEW_AUTHORITY_BINDING_SCHEMA,
            "authority_kind": "DERIVED_EFFECT",
            "origin_authority_kind": origin_authority_kind,
            "origin_receipt_ref": origin_ref,
            "origin_receipt_digest": str(origin.get("receipt_digest") or ""),
            "origin_authority_binding_digest": str(
                origin.get("authority_binding_digest") or ""
            ),
            "effect_descriptor": descriptor,
            "derived_scope_digest": _digest(scope),
            "derived_args_digest": _digest(args),
        }
        return ResolvedActionPreviewAuthority(
            authority_kind="DERIVED_EFFECT",
            action_kind=action,
            action_scope=scope,
            risk_level=risk,
            guardian_capability_id=capability,
            context_pack_digest=str(origin["context_pack_digest"]),
            scene_identity=_mapping(origin["scene_identity"], "origin scene_identity"),
            task_graph_id=str(origin["task_graph_id"]),
            graph_revision=int(origin["graph_revision"]),
            task_node_id=str(origin["task_node_id"]),
            parent_governor_grant_ref=str(origin["parent_governor_grant_ref"]),
            node_grant_ref=str(origin["node_grant_ref"]),
            node_grant=supplied_node_grant,
            expires_at_ms=expires,
            binding=binding,
            origin_receipt_ref=origin_ref,
        )


def build_node_authority_context(
    *,
    command: Any,
    execution_context: Mapping[str, Any],
    purpose: str,
) -> dict[str, Any]:
    """Build a request to the resolver; it is not itself authority."""

    context = dict(execution_context or {})
    return {
        "schema_version": NODE_AUTHORITY_SCHEMA,
        "purpose": _text(purpose, "purpose"),
        "command_id": _text(getattr(command, "command_id", ""), "command.command_id"),
        "attempt_id": _text(context.get("attempt_id"), "execution_context.attempt_id"),
        "lease_id": _text(context.get("lease_id"), "execution_context.lease_id"),
        "lease_owner_id": _text(context.get("lease_owner_id"), "execution_context.lease_owner_id"),
        "fencing_token": _integer(context.get("fencing_token"), "execution_context.fencing_token"),
    }


__all__ = [
    "ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA",
    "ACTION_PREVIEW_AUTHORITY_BINDING_SCHEMA",
    "DERIVED_EFFECT_AUTHORITY_SCHEMA",
    "EFFECT_DESCRIPTOR_SCHEMA",
    "NODE_AUTHORITY_SCHEMA",
    "SCHEDULER_OPERATION_EFFECT_TYPE",
    "ProductionActionPreviewAuthorityResolver",
    "ResolvedActionPreviewAuthority",
    "action_preview_scene_identity",
    "build_node_authority_context",
]
