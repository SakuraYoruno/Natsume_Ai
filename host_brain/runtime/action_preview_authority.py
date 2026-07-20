"""Durable, exact and restrict-only action preview receipts.

An action preview is not an execution grant.  It binds an already-approved
Governor/Task authority to the exact action, frozen ContextPack/Scene and the
current app-local RuntimeGuardian decision immediately before a worker crosses
an effectful boundary.  Reopening the receipt re-evaluates Guardian authority;
stale, forged or context-drifted receipts fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Callable

from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    build_runtime_event_dedupe_key,
    validate_runtime_event_record_envelope,
)
from host_brain.runtime.context_contracts import (
    ContextPackV2,
    WorldFactV2,
    WorldSnapshotV2,
)


ACTION_PREVIEW_RECEIPT_SCHEMA_VERSION = "Natsume.action_preview.receipt.v1"
ACTION_PREVIEW_EVENT_TYPE = "ActionPreviewReceiptIssued"
ACTION_PREVIEW_AGGREGATE_TYPE = "TaskNodeActionPreview"

ACTION_PREVIEW_INVALID = "ACTION_PREVIEW_INVALID"
ACTION_PREVIEW_BLOCKED = "ACTION_PREVIEW_BLOCKED"
ACTION_PREVIEW_EXPIRED = "ACTION_PREVIEW_EXPIRED"
ACTION_PREVIEW_DRIFT = "ACTION_PREVIEW_DRIFT"
ACTION_PREVIEW_MAX_TTL_MS = 30_000
SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1 = (
    "Natsume.scheduler.action_preview_context.v1"
)
SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2 = (
    "Natsume.scheduler.action_preview_context.v2"
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "preview_id",
        "action_kind",
        "action_scope",
        "risk_level",
        "action_args_digest",
        "context_pack_digest",
        "scene_identity",
        "task_graph_id",
        "graph_revision",
        "task_node_id",
        "parent_governor_grant_ref",
        "node_grant_ref",
        "node_grant_digest",
        "guardian_capability_id",
        "guardian_authority",
        "guardian_authority_digest",
        "guardian_policy_digest",
        "policy_decision",
        "policy_decision_digest",
        "authority_kind",
        "authority_binding",
        "authority_binding_digest",
        "effect_descriptors",
        "origin_receipt_ref",
        "operation_digest",
        "decision",
        "issued_at_ms",
        "expires_at_ms",
        "authority_granted",
        "receipt_digest",
    }
)
_SCENE_FIELDS = frozenset(
    {
        "schema",
        "workspace_id",
        "subject_id",
        "scene_id",
        "revision",
        "decision_digest",
    }
)
_GUARDIAN_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "authority",
        "authority_status",
        "aggregate_type",
        "aggregate_id",
        "aggregate_ref",
        "runtime_scope_id",
        "runtime_owner_id",
        "runtime_lease_id",
        "fencing_token",
        "lease_issued_at_ms",
        "lease_expires_at_ms",
        "lease_released_at_ms",
        "guardian_revision",
        "guardian_status",
        "last_event_digest",
        "durable",
    }
)


class ActionPreviewAuthorityError(RuntimeError):
    """A preview receipt cannot authorize crossing the requested boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or ACTION_PREVIEW_INVALID)
        self.detail = str(detail or "action preview authority rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


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
            "preview binding is not finite canonical JSON",
        ) from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is required")
    return result


def _ref(value: Any, field: str) -> str:
    result = _text(value, field)
    if _REF.fullmatch(result) is None:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            f"{field} must be a durable reference",
        )
    return result


def _sha(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if _DIGEST.fullmatch(result) is None:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            f"{field} must be sha256:<64 hex>",
        )
    return result


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            f"{field} is invalid",
        ) from exc
    if result < 1:
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, f"{field} is invalid")
    return result


def _risk(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "risk_level is invalid",
        ) from exc
    if not 1 <= result <= 5:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "risk_level must be within [1, 5]",
        )
    return result


def _receipt_digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("receipt_digest", None)
    return _digest(body)


def _node_grant_digest(value: Mapping[str, Any]) -> str:
    projection = dict(value or {})
    if frozenset(projection) == {"grant_ref", "binding_digest"}:
        _ref(projection.get("grant_ref"), "node_grant.grant_ref")
        return _sha(projection.get("binding_digest"), "node_grant.binding_digest")
    return _digest(projection)


def _sanitize_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Persist only authority-free policy identity and restrictions."""

    source = dict(value or {})
    reasons = source.get("reason_codes")
    if not isinstance(reasons, (list, tuple)):
        reasons = ()
    restrictions = source.get("restrictions")
    if not isinstance(restrictions, (list, tuple)):
        restrictions = ()
    snapshot_digest = str(source.get("authority_snapshot_digest") or "").strip().lower()
    if snapshot_digest and _DIGEST.fullmatch(snapshot_digest) is None:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "policy authority_snapshot_digest is invalid",
        )
    return {
        "decision": str(source.get("decision") or "BLOCK").strip().upper(),
        "execution_state": str(
            source.get("execution_state") or "BLOCKED"
        ).strip().upper(),
        "reason_codes": sorted(
            {str(item).strip().upper() for item in reasons if str(item).strip()}
        ),
        "restrictions": sorted(
            {str(item).strip() for item in restrictions if str(item).strip()}
        ),
        "authority_binding_ref": str(
            source.get("authority_binding_ref") or ""
        ).strip(),
        "authority_snapshot_digest": snapshot_digest,
        # This bit is written only from the independently reopened resolver
        # result.  Public callers never provide the policy projection.
        "verified_node_grant": source.get("verified_node_grant") is True,
        "may_expand_scope": False,
        "may_reduce_risk": False,
        "may_bypass_review": False,
        "may_bypass_token": False,
    }


def _sanitize_guardian(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    source = dict(value or {})
    authority = source.get("guardian_authority")
    authority_map = dict(authority) if isinstance(authority, Mapping) else {}
    if frozenset(authority_map) != _GUARDIAN_AUTHORITY_FIELDS:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "RuntimeGuardian returned no exact durable authority identity",
        )
    if (
        authority_map.get("authority") != "RUNTIME_GUARDIAN"
        or authority_map.get("authority_status") != "ACTIVE"
        or authority_map.get("durable") is not True
    ):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "RuntimeGuardian authority is not active and durable",
        )
    policy = {
        "schema_version": str(source.get("schema_version") or ""),
        "capability_id": str(source.get("capability_id") or "").strip().lower(),
        "decision": str(source.get("decision") or "BLOCK").strip().upper(),
        "allowed": source.get("allowed") is True,
        "allowed_by_degradation": source.get("allowed_by_degradation") is True,
        "authority_granted": source.get("authority_granted") is True,
        "static_risk_level": source.get("static_risk_level"),
        "effective_risk_level": source.get("effective_risk_level"),
        "reason_code": str(source.get("reason_code") or "").strip().upper(),
        "permission_effect": str(source.get("permission_effect") or "").strip().upper(),
        "can_expand_scope": source.get("can_expand_scope") is True,
        "can_reduce_risk": source.get("can_reduce_risk") is True,
        "can_bypass_review": source.get("can_bypass_review") is True,
        "can_bypass_token": source.get("can_bypass_token") is True,
        "durability_status": str(source.get("durability_status") or "").strip().upper(),
    }
    if (
        policy["allowed"] is not True
        or policy["decision"] == "BLOCK"
        or policy["can_expand_scope"]
        or policy["can_reduce_risk"]
        or policy["can_bypass_review"]
        or policy["can_bypass_token"]
        or policy["durability_status"] != "READY"
    ):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "RuntimeGuardian blocked or could broaden the action",
        )
    return authority_map, _digest(policy)


def _same_guardian_authority_after_renewal(
    issued: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """Accept only a monotonic extension of the exact Host runtime lease.

    The production authority provider renews its durable lease on every read.
    Renewal changes only ``lease_expires_at_ms``; treating that extension as an
    authority rotation would make issue-then-reopen fail in the same process.
    Owner, lease, fence, Guardian revision/status and journal head remain exact,
    while a regressed expiry still fails closed.
    """

    previous = dict(issued or {})
    reopened = dict(current or {})
    try:
        previous_expiry = int(previous.pop("lease_expires_at_ms"))
        reopened_expiry = int(reopened.pop("lease_expires_at_ms"))
    except (KeyError, TypeError, ValueError):
        return False
    return previous == reopened and reopened_expiry >= previous_expiry


def _scene(value: Mapping[str, Any]) -> dict[str, Any]:
    scene = dict(value or {})
    if frozenset(scene) != _SCENE_FIELDS or scene.get("schema") != "natsume.scene-identity.v2":
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "exact Scene identity is required",
        )
    for field in ("workspace_id", "subject_id", "scene_id"):
        _text(scene.get(field), f"scene_identity.{field}")
    _positive(scene.get("revision"), "scene_identity.revision")
    _sha(scene.get("decision_digest"), "scene_identity.decision_digest")
    return scene


@dataclass(frozen=True, slots=True)
class ActionPreviewReceiptV1:
    payload: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActionPreviewReceiptV1":
        payload = dict(value or {})
        if frozenset(payload) != _RECEIPT_FIELDS:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview receipt field set drifted",
            )
        if payload.get("schema_version") != ACTION_PREVIEW_RECEIPT_SCHEMA_VERSION:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview receipt schema is invalid",
            )
        _text(payload.get("preview_id"), "preview_id")
        _text(payload.get("action_kind"), "action_kind")
        _text(payload.get("action_scope"), "action_scope")
        _risk(payload.get("risk_level"))
        for field in (
            "action_args_digest",
            "context_pack_digest",
            "node_grant_digest",
            "guardian_authority_digest",
            "guardian_policy_digest",
            "policy_decision_digest",
            "authority_binding_digest",
            "operation_digest",
            "receipt_digest",
        ):
            _sha(payload.get(field), field)
        _scene(payload.get("scene_identity") if isinstance(payload.get("scene_identity"), Mapping) else {})
        _text(payload.get("task_graph_id"), "task_graph_id")
        _positive(payload.get("graph_revision"), "graph_revision")
        _text(payload.get("task_node_id"), "task_node_id")
        _ref(payload.get("parent_governor_grant_ref"), "parent_governor_grant_ref")
        _ref(payload.get("node_grant_ref"), "node_grant_ref")
        _text(payload.get("guardian_capability_id"), "guardian_capability_id")
        authority = payload.get("guardian_authority")
        if not isinstance(authority, Mapping) or frozenset(authority) != _GUARDIAN_AUTHORITY_FIELDS:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "guardian authority field set drifted",
            )
        policy = payload.get("policy_decision")
        if not isinstance(policy, Mapping) or _sanitize_policy(policy) != dict(policy):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "policy decision projection drifted",
            )
        authority_kind = str(payload.get("authority_kind") or "").strip().upper()
        if authority_kind not in {"TASK_NODE", "ACCEPTANCE_EFFECT", "DERIVED_EFFECT"}:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "preview authority kind is invalid",
            )
        binding = payload.get("authority_binding")
        if not isinstance(binding, Mapping) or not binding:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "preview authority binding is missing",
            )
        if payload["authority_binding_digest"] != _digest(dict(binding)):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "preview authority binding digest drifted",
            )
        descriptors = payload.get("effect_descriptors")
        if not isinstance(descriptors, list) or any(
            not isinstance(item, Mapping) for item in descriptors
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "preview EffectDescriptor set is invalid",
            )
        origin_ref = str(payload.get("origin_receipt_ref") or "").strip()
        if authority_kind == "DERIVED_EFFECT":
            _ref(origin_ref, "origin_receipt_ref")
        elif origin_ref:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "only a derived effect may bind an origin receipt",
            )
        issued = _positive(payload.get("issued_at_ms"), "issued_at_ms")
        expires = _positive(payload.get("expires_at_ms"), "expires_at_ms")
        if expires <= issued:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview receipt expiry is invalid",
            )
        if expires - issued > ACTION_PREVIEW_MAX_TTL_MS:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview receipt exceeds the maximum TTL",
            )
        if payload.get("decision") not in {"ALLOW", "REVIEW", "BLOCK"}:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview decision is invalid",
            )
        if payload.get("authority_granted") is not False:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "action preview must never grant execution authority",
            )
        if payload["guardian_authority_digest"] != _digest(dict(authority)):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "Guardian authority digest drifted")
        if payload["policy_decision_digest"] != _digest(dict(policy)):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "policy decision digest drifted")
        if payload["receipt_digest"] != _receipt_digest(payload):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "receipt digest drifted")
        expected_id = "action-preview-" + str(payload["operation_digest"]).split(":", 1)[1][:32]
        if payload["preview_id"] != expected_id:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "preview identity drifted")
        return cls(payload=payload)

    @property
    def preview_id(self) -> str:
        return str(self.payload["preview_id"])

    @property
    def receipt_ref(self) -> str:
        return "action-preview://sha256/" + str(self.payload["receipt_digest"]).split(":", 1)[1]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(_canonical(dict(self.payload)))


class DurableActionPreviewAuthority:
    """Issue and reopen receipts using the canonical RuntimeEvent journal."""

    def __init__(
        self,
        *,
        runtime_event_store: Any,
        guardian: Any,
        authority_resolver: Any,
        clock: Callable[[], int],
        max_ttl_ms: int = ACTION_PREVIEW_MAX_TTL_MS,
    ) -> None:
        if not callable(getattr(runtime_event_store, "append_event", None)) or not callable(
            getattr(runtime_event_store, "get_event_by_id", None)
        ):
            raise TypeError("runtime_event_store must expose append_event/get_event_by_id")
        if not callable(getattr(guardian, "evaluate_capability", None)):
            raise TypeError("app-local RuntimeGuardian.evaluate_capability is required")
        if not callable(clock):
            raise TypeError("clock is required")
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise TypeError("canonical ActionPreview authority_resolver.resolve is required")
        if isinstance(max_ttl_ms, bool) or not 1 <= int(max_ttl_ms) <= ACTION_PREVIEW_MAX_TTL_MS:
            raise TypeError("max_ttl_ms is invalid")
        self._store = runtime_event_store
        self._guardian = guardian
        self._authority_resolver = authority_resolver
        self._clock = clock
        self._max_ttl_ms = int(max_ttl_ms)

    def issue(
        self,
        *,
        action_kind: str,
        action_scope: str,
        risk_level: int,
        action_args: Mapping[str, Any],
        context_pack_digest: str,
        scene_identity: Mapping[str, Any],
        task_graph_id: str,
        graph_revision: int,
        task_node_id: str,
        parent_governor_grant_ref: str,
        node_grant_ref: str,
        node_grant: Mapping[str, Any],
        guardian_capability_id: str,
        authority_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = int(self._clock())
        if now < 1:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "preview clock is invalid")
        requested = {
            "action_kind": _text(action_kind, "action_kind"),
            "action_scope": _text(action_scope, "action_scope"),
            "risk_level": _risk(risk_level),
            "guardian_capability_id": _text(
                guardian_capability_id,
                "guardian_capability_id",
            ).lower(),
            "context_pack_digest": _sha(context_pack_digest, "context_pack_digest"),
            "scene_identity": _scene(scene_identity),
            "task_graph_id": _text(task_graph_id, "task_graph_id"),
            "graph_revision": _positive(graph_revision, "graph_revision"),
            "task_node_id": _text(task_node_id, "task_node_id"),
            "parent_governor_grant_ref": _ref(
                parent_governor_grant_ref,
                "parent_governor_grant_ref",
            ),
            "node_grant_ref": _ref(node_grant_ref, "node_grant_ref"),
            "node_grant": dict(node_grant or {}),
            "action_args": dict(action_args or {}),
        }
        resolved = self._authority_resolver.resolve(
            authority_context=dict(authority_context or {}),
            **requested,
            now_ms=now,
            origin_receipt=self._origin_receipt(authority_context),
        )
        canonical = {
            "action_kind": str(resolved.action_kind),
            "action_scope": str(resolved.action_scope),
            "risk_level": int(resolved.risk_level),
            "guardian_capability_id": str(resolved.guardian_capability_id).lower(),
            "context_pack_digest": str(resolved.context_pack_digest).lower(),
            "scene_identity": dict(resolved.scene_identity),
            "task_graph_id": str(resolved.task_graph_id),
            "graph_revision": int(resolved.graph_revision),
            "task_node_id": str(resolved.task_node_id),
            "parent_governor_grant_ref": str(resolved.parent_governor_grant_ref),
            "node_grant_ref": str(resolved.node_grant_ref),
            "node_grant": dict(resolved.node_grant),
            "action_args": dict(action_args or {}),
        }
        if any(
            canonical[key] != requested[key]
            for key in canonical
            if key not in {"node_grant", "action_args"}
        ) or _node_grant_digest(canonical["node_grant"]) != _node_grant_digest(
            requested["node_grant"]
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "caller boundary differs from reopened canonical authority",
            )
        risk = canonical["risk_level"]
        capability = canonical["guardian_capability_id"]
        guardian_raw = self._guardian.evaluate_capability(
            capability_id=capability,
            static_risk_level=risk,
        )
        if not isinstance(guardian_raw, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_BLOCKED,
                "RuntimeGuardian returned an invalid decision",
        )
        guardian_authority, guardian_policy_digest = _sanitize_guardian(guardian_raw)
        binding = dict(resolved.binding)
        binding_digest = _digest(binding)
        authority_kind = str(resolved.authority_kind).strip().upper()
        policy = _sanitize_policy(
            {
                "decision": "ALLOW",
                "execution_state": "EXECUTABLE",
                "reason_codes": [f"CANONICAL_{authority_kind}_REOPENED"],
                "restrictions": ["exact-action", "exact-context", "no-authority-expansion"],
                "authority_binding_ref": (
                    str(resolved.origin_receipt_ref)
                    or canonical["node_grant_ref"]
                ),
                "authority_snapshot_digest": binding_digest,
                "verified_node_grant": authority_kind == "TASK_NODE",
            }
        )
        decision = "ALLOW"
        issued = now
        expires = min(int(resolved.expires_at_ms), issued + self._max_ttl_ms)
        if expires <= issued:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_EXPIRED, "canonical authority expired")
        descriptors = [dict(item) for item in tuple(resolved.effect_descriptors)]
        operation_body = {
            "action_kind": canonical["action_kind"],
            "action_scope": canonical["action_scope"],
            "risk_level": risk,
            "action_args_digest": _digest(dict(action_args or {})),
            "context_pack_digest": canonical["context_pack_digest"],
            "scene_identity": canonical["scene_identity"],
            "task_graph_id": canonical["task_graph_id"],
            "graph_revision": canonical["graph_revision"],
            "task_node_id": canonical["task_node_id"],
            "authority_kind": authority_kind,
            "authority_binding_digest": binding_digest,
            "origin_receipt_ref": str(resolved.origin_receipt_ref or ""),
        }
        operation_digest = _digest(operation_body)
        preview_id = "action-preview-" + operation_digest.split(":", 1)[1][:32]
        replay = self._load_operation_replay(
            preview_id=preview_id,
            node_grant_ref=canonical["node_grant_ref"],
            graph_revision=canonical["graph_revision"],
            expected_operation_digest=operation_digest,
        )
        if replay is not None:
            return replay
        payload: dict[str, Any] = {
            "schema_version": ACTION_PREVIEW_RECEIPT_SCHEMA_VERSION,
            "preview_id": preview_id,
            "action_kind": canonical["action_kind"],
            "action_scope": canonical["action_scope"],
            "risk_level": risk,
            "action_args_digest": _digest(dict(action_args or {})),
            "context_pack_digest": canonical["context_pack_digest"],
            "scene_identity": canonical["scene_identity"],
            "task_graph_id": canonical["task_graph_id"],
            "graph_revision": canonical["graph_revision"],
            "task_node_id": canonical["task_node_id"],
            "parent_governor_grant_ref": canonical["parent_governor_grant_ref"],
            "node_grant_ref": canonical["node_grant_ref"],
            "node_grant_digest": _node_grant_digest(canonical["node_grant"]),
            "guardian_capability_id": capability,
            "guardian_authority": guardian_authority,
            "guardian_authority_digest": _digest(guardian_authority),
            "guardian_policy_digest": guardian_policy_digest,
            "policy_decision": policy,
            "policy_decision_digest": _digest(policy),
            "authority_kind": authority_kind,
            "authority_binding": binding,
            "authority_binding_digest": binding_digest,
            "effect_descriptors": descriptors,
            "origin_receipt_ref": str(resolved.origin_receipt_ref or ""),
            "operation_digest": operation_digest,
            "decision": decision,
            "issued_at_ms": issued,
            "expires_at_ms": expires,
            "authority_granted": False,
            "receipt_digest": "sha256:" + "0" * 64,
        }
        payload["receipt_digest"] = _receipt_digest(payload)
        receipt = ActionPreviewReceiptV1.from_mapping(payload)
        event = self._event(receipt)
        record = self._store.append_event(event)
        return self._validate_record(
            record,
            receipt=receipt,
            expected_event=event,
            replayed=False,
        )

    def require_execution(
        self,
        *,
        event_id: str,
        action_kind: str,
        action_scope: str,
        risk_level: int,
        action_args: Mapping[str, Any],
        context_pack_digest: str,
        scene_identity: Mapping[str, Any],
        task_graph_id: str,
        graph_revision: int,
        task_node_id: str,
        parent_governor_grant_ref: str,
        node_grant_ref: str,
        node_grant: Mapping[str, Any],
        authority_context: Mapping[str, Any],
    ) -> ActionPreviewReceiptV1:
        event_ref = _ref(event_id, "event_id")
        record = self._store.get_event_by_id(event_id=event_ref)
        if not isinstance(record, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "preview receipt is missing")
        raw_payload = record.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "preview receipt payload is missing")
        receipt = ActionPreviewReceiptV1.from_mapping(raw_payload)
        expected_event = self._event(receipt)
        self._validate_record(
            record,
            receipt=receipt,
            expected_event=expected_event,
            replayed=True,
        )
        expected = {
            "action_kind": _text(action_kind, "action_kind"),
            "action_scope": _text(action_scope, "action_scope"),
            "risk_level": _risk(risk_level),
            "action_args_digest": _digest(dict(action_args or {})),
            "context_pack_digest": _sha(context_pack_digest, "context_pack_digest"),
            "scene_identity": _scene(scene_identity),
            "task_graph_id": _text(task_graph_id, "task_graph_id"),
            "graph_revision": _positive(graph_revision, "graph_revision"),
            "task_node_id": _text(task_node_id, "task_node_id"),
            "parent_governor_grant_ref": _ref(
                parent_governor_grant_ref,
                "parent_governor_grant_ref",
            ),
            "node_grant_ref": _ref(node_grant_ref, "node_grant_ref"),
            "node_grant_digest": _node_grant_digest(node_grant),
        }
        if any(receipt.payload.get(key) != value for key, value in expected.items()):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "preview receipt differs from the exact execution boundary",
            )
        now = int(self._clock())
        if now < int(receipt.payload["issued_at_ms"]):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "preview receipt is not active yet",
            )
        if now >= int(receipt.payload["expires_at_ms"]):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_EXPIRED, "preview receipt expired")
        current = self._guardian.evaluate_capability(
            capability_id=str(receipt.payload["guardian_capability_id"]),
            static_risk_level=int(receipt.payload["risk_level"]),
        )
        if not isinstance(current, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "RuntimeGuardian is unavailable")
        authority, policy_digest = _sanitize_guardian(current)
        if (
            not _same_guardian_authority_after_renewal(
                receipt.payload["guardian_authority"],
                authority,
            )
            or receipt.payload["guardian_policy_digest"] != policy_digest
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "RuntimeGuardian authority or policy changed after preview",
            )
        if receipt.payload["decision"] != "ALLOW":
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_BLOCKED, "preview did not allow execution")
        resolved = self._authority_resolver.resolve(
            authority_context=dict(authority_context or {}),
            action_kind=expected["action_kind"],
            action_scope=expected["action_scope"],
            risk_level=expected["risk_level"],
            guardian_capability_id=str(receipt.payload["guardian_capability_id"]),
            context_pack_digest=expected["context_pack_digest"],
            scene_identity=expected["scene_identity"],
            task_graph_id=expected["task_graph_id"],
            graph_revision=expected["graph_revision"],
            task_node_id=expected["task_node_id"],
            parent_governor_grant_ref=expected["parent_governor_grant_ref"],
            node_grant_ref=expected["node_grant_ref"],
            node_grant=dict(node_grant or {}),
            action_args=dict(action_args or {}),
            now_ms=now,
            origin_receipt=self._origin_receipt(authority_context),
        )
        if (
            str(resolved.authority_kind).upper() != receipt.payload["authority_kind"]
            or _digest(dict(resolved.binding)) != receipt.payload["authority_binding_digest"]
            or int(resolved.expires_at_ms) < int(receipt.payload["expires_at_ms"])
        ):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "canonical execution authority changed after preview",
            )
        return receipt

    def reopen_receipt(self, *, event_id: str) -> dict[str, Any]:
        """Reopen one immutable issuance receipt without granting execution.

        Terminal checkpoint recovery uses this read-only proof to establish
        that a seal-bound Preview really exists in the append-only RuntimeEvent
        journal.  It deliberately does not renew expiry, re-evaluate policy, or
        return an execution proof.
        """

        event_ref = _ref(event_id, "event_id")
        record = self._store.get_event_by_id(event_id=event_ref)
        if not isinstance(record, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "preview receipt is missing",
            )
        raw_payload = record.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "preview receipt payload is missing",
            )
        receipt = ActionPreviewReceiptV1.from_mapping(raw_payload)
        expected_event = self._event(receipt)
        return self._validate_record(
            record,
            receipt=receipt,
            expected_event=expected_event,
            replayed=True,
        )

    def _origin_receipt(
        self,
        authority_context: Mapping[str, Any] | None,
    ) -> Mapping[str, Any] | None:
        context = dict(authority_context or {})
        if context.get("schema_version") != (
            "Natsume.action_preview.derived_effect_authority_request.v1"
        ):
            return None
        origin_event_id = _ref(
            context.get("origin_event_id"),
            "origin_event_id",
        )
        origin_ref = _ref(context.get("origin_receipt_ref"), "origin_receipt_ref")
        digest = origin_ref.rsplit("/", 1)[-1].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "origin receipt reference is invalid",
            )
        record = self._store.get_event_by_id(event_id=origin_event_id)
        if not isinstance(record, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "origin preview event is missing",
            )
        raw_payload = record.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "origin preview event payload is missing",
            )
        receipt = ActionPreviewReceiptV1.from_mapping(dict(raw_payload))
        expected_event = self._event(receipt)
        validated = self._validate_record(
            record,
            receipt=receipt,
            expected_event=expected_event,
            replayed=True,
        )
        if str(validated["event_id"]) != origin_event_id:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "origin preview event identity drifted",
            )
        if receipt.receipt_ref != origin_ref:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "origin preview receipt reference drifted",
            )
        return receipt.to_dict()

    def _load_operation_replay(
        self,
        *,
        preview_id: str,
        node_grant_ref: str,
        graph_revision: int,
        expected_operation_digest: str,
    ) -> dict[str, Any] | None:
        dedupe = build_runtime_event_dedupe_key(
            source_kind="TASK",
            source_ref=node_grant_ref,
            event_type=ACTION_PREVIEW_EVENT_TYPE,
            aggregate_type=ACTION_PREVIEW_AGGREGATE_TYPE,
            aggregate_id=preview_id,
            aggregate_version=graph_revision,
        )
        event_id = "runtimeevent://sha256/" + dedupe.split(":", 1)[1]
        record = self._store.get_event_by_id(event_id=event_id)
        if record is None:
            return None
        raw = record.get("payload")
        if not isinstance(raw, Mapping):
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "replayed preview payload is missing")
        receipt = ActionPreviewReceiptV1.from_mapping(raw)
        if receipt.payload["operation_digest"] != expected_operation_digest:
            raise ActionPreviewAuthorityError(ACTION_PREVIEW_DRIFT, "preview operation replay drifted")
        event = self._event(receipt)
        return self._validate_record(
            record,
            receipt=receipt,
            expected_event=event,
            replayed=True,
        )

    @staticmethod
    def _event(receipt: ActionPreviewReceiptV1) -> RuntimeEvent:
        payload = receipt.to_dict()
        digest = str(payload["receipt_digest"]).split(":", 1)[1]
        scene = dict(payload["scene_identity"])
        return RuntimeEvent.create(
            event_type=ACTION_PREVIEW_EVENT_TYPE,
            source_kind="TASK",
            source_ref=str(payload["node_grant_ref"]),
            aggregate_type=ACTION_PREVIEW_AGGREGATE_TYPE,
            aggregate_id=receipt.preview_id,
            aggregate_version=int(payload["graph_revision"]),
            payload_ref=f"action-preview://sha256/{digest}",
            payload=payload,
            evidence_refs=(
                str(payload["parent_governor_grant_ref"]),
                str(payload["node_grant_ref"]),
                "context-pack://sha256/" + str(payload["context_pack_digest"]).split(":", 1)[1],
                "scene-decision://sha256/" + str(scene["decision_digest"]).split(":", 1)[1],
                str(dict(payload["guardian_authority"])["aggregate_ref"]),
            ),
            redaction_level="INTERNAL_REDACTED",
            ts_ms=int(payload["issued_at_ms"]),
        )

    @staticmethod
    def _validate_record(
        record: Mapping[str, Any],
        *,
        receipt: ActionPreviewReceiptV1,
        expected_event: RuntimeEvent,
        replayed: bool,
    ) -> dict[str, Any]:
        try:
            validated = validate_runtime_event_record_envelope(
                record,
                expected=expected_event,
            )
        except Exception as exc:
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_DRIFT,
                "action preview RuntimeEvent envelope drifted",
            ) from exc
        return {
            "receipt": receipt.to_dict(),
            "receipt_ref": receipt.receipt_ref,
            "event_id": str(validated["event_id"]),
            "cursor": int(validated["cursor"]),
            "replayed": bool(replayed),
        }


def context_pack_digest_from_work_order(work_order: Any) -> str:
    """Rebuild ContextPackV2 without trusting a caller-supplied digest."""

    pack = getattr(work_order, "input_pack", None)
    if not isinstance(pack, Mapping):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "WorkOrder input_pack is missing")
    source = dict(pack)
    world = source.get("world_snapshot_v2")
    if not isinstance(world, Mapping):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "WorldSnapshotV2 is missing")
    facts_raw = world.get("facts")
    if not isinstance(facts_raw, list):
        raise ActionPreviewAuthorityError(ACTION_PREVIEW_INVALID, "WorldSnapshotV2 facts are missing")
    try:
        facts = tuple(
            WorldFactV2(
                workspace_id=item["workspace_id"],
                subject_id=item["subject_id"],
                key=item["key"],
                value=item["value"],
                source_ref=item["source_ref"],
                confidence=item["confidence"],
                observed_at_ms=item["observed_at_ms"],
                expires_at_ms=item["expires_at_ms"],
                conflict_group=item.get("conflict_group", ""),
                revision=item.get("revision", 1),
            )
            for item in facts_raw
            if isinstance(item, Mapping)
        )
        if len(facts) != len(facts_raw):
            raise ValueError("World facts contain a non-mapping item")
        snapshot = WorldSnapshotV2(
            workspace_id=world["workspace_id"],
            subject_id=world["subject_id"],
            revision=world["revision"],
            facts=facts,
            snapshot_digest=world["snapshot_digest"],
        )
        if snapshot.to_dict() != dict(world):
            raise ValueError("World snapshot projection drifted")
        context = ContextPackV2(
            workspace_id=str(source["workspace_id"]),
            subject_id=str(source["subject_id"]),
            task_graph_id=str(getattr(work_order, "task_graph_id")),
            graph_revision=int(getattr(work_order, "graph_revision")),
            world_snapshot_digest=snapshot.snapshot_digest,
            facts=snapshot.facts,
        )
        context.verify_against_snapshot(snapshot)
    except Exception as exc:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "canonical ContextPackV2 could not be rebuilt",
        ) from exc
    digest = str(context.pack_digest).strip().lower()
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def require_scheduler_derived_action(
    *,
    service: Any,
    preview_context: Mapping[str, Any],
    action_kind: str,
    action_scope: str,
    risk_level: int,
    action_args: Mapping[str, Any],
    guardian_capability_id: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Reopen a Scheduler authority and bind one exact downstream action.

    V1 reopens the live TaskNode used by the legacy in-worker Scheduler
    boundary.  V2 reopens the live, claimed ``scheduler.operation.execute``
    Acceptance Effect after node Acceptance.  Both receipts remain
    restrict-only: this helper first reopens the exact origin boundary, then
    issues a separate receipt for the concrete message/actuator call.  It
    never invents a parent or child grant.
    """

    context = dict(preview_context or {})
    context_schema = str(context.get("schema_version") or "")
    if context_schema not in {
        SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1,
        SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2,
    }:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "Scheduler-derived action lacks a typed Preview context",
        )
    required_context_fields = {
        "schema_version",
        "origin_event_id",
        "origin_receipt_ref",
        "origin_boundary",
    }
    if context_schema == SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1:
        required_context_fields.add("node_grant_expires_at_ms")
    else:
        required_context_fields.add("origin_authority_kind")
    if frozenset(context) != required_context_fields:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "Scheduler-derived Preview context field set drifted",
        )
    origin_event_id = _ref(context.get("origin_event_id"), "origin_event_id")
    origin_receipt_ref = _ref(
        context.get("origin_receipt_ref"),
        "origin_receipt_ref",
    )
    raw_boundary = context.get("origin_boundary")
    if not isinstance(raw_boundary, Mapping):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "Scheduler-derived action lacks its exact origin boundary",
        )
    boundary = dict(raw_boundary)
    required_boundary = {
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
    if frozenset(boundary) != required_boundary:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "Scheduler origin boundary field set drifted",
        )
    origin_receipt = service.require_execution(event_id=origin_event_id, **boundary)
    if str(getattr(origin_receipt, "receipt_ref", "")) != origin_receipt_ref:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_DRIFT,
            "Scheduler origin receipt reference differs from its event",
        )

    receipt_payload = getattr(origin_receipt, "payload", None)
    if (
        not isinstance(receipt_payload, Mapping)
        and context_schema == SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2
    ):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_INVALID,
            "Scheduler origin receipt has no canonical payload",
        )
    origin_authority_kind = str(
        receipt_payload.get("authority_kind") or ""
        if isinstance(receipt_payload, Mapping)
        else "TASK_NODE"
    ).strip().upper()
    if context_schema == SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1:
        expected_origin_kind = "TASK_NODE"
    else:
        expected_origin_kind = str(
            context.get("origin_authority_kind") or ""
        ).strip().upper()
        if expected_origin_kind != "ACCEPTANCE_EFFECT":
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_INVALID,
                "Scheduler v2 origin must declare Acceptance Effect authority",
            )
    if origin_authority_kind != expected_origin_kind:
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_DRIFT,
            "Scheduler origin authority kind differs from its typed context",
        )

    service_clock = getattr(service, "now_ms", None)
    current_ms = (
        now_ms
        if now_ms is not None
        else service_clock()
        if callable(service_clock)
        else time.time() * 1_000
    )
    if context_schema == SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1:
        node_expiry = _positive(
            context.get("node_grant_expires_at_ms"),
            "node_grant_expires_at_ms",
        )
        if node_expiry <= int(current_ms):
            raise ActionPreviewAuthorityError(
                ACTION_PREVIEW_EXPIRED,
                "Scheduler node authority expired before the downstream action",
            )
    kwargs = {
        "action_kind": _text(action_kind, "action_kind"),
        "action_scope": _text(action_scope, "action_scope"),
        "risk_level": _risk(risk_level),
        "action_args": dict(action_args or {}),
        "context_pack_digest": boundary["context_pack_digest"],
        "scene_identity": boundary["scene_identity"],
        "task_graph_id": boundary["task_graph_id"],
        "graph_revision": boundary["graph_revision"],
        "task_node_id": boundary["task_node_id"],
        "parent_governor_grant_ref": boundary["parent_governor_grant_ref"],
        "node_grant_ref": boundary["node_grant_ref"],
        "node_grant": boundary["node_grant"],
        "guardian_capability_id": _text(
            guardian_capability_id,
            "guardian_capability_id",
        ).lower(),
        "authority_context": {
            "schema_version": (
                "Natsume.action_preview.derived_effect_authority_request.v1"
            ),
            "origin_event_id": origin_event_id,
            "origin_receipt_ref": origin_receipt_ref,
            # A derived receipt is evidence, never continuing authority.  Keep
            # the exact Scheduler boundary private and reopen it again at the
            # final terminal check so an Attempt/Lease loss between issue and
            # network/device execution fails closed.
            "origin_boundary": boundary,
        },
    }
    issued = service.issue_receipt(**kwargs)
    execution_boundary = {
        key: value
        for key, value in kwargs.items()
        if key != "guardian_capability_id"
    }
    service.require_execution(
        event_id=issued["event_id"],
        **execution_boundary,
    )
    result = dict(issued)
    # Private in-process proof material.  Terminal transports reopen this
    # boundary immediately before the network call and never serialize it.
    result["_terminal_execution_boundary"] = execution_boundary
    return result


__all__ = [
    "ACTION_PREVIEW_AGGREGATE_TYPE",
    "ACTION_PREVIEW_BLOCKED",
    "ACTION_PREVIEW_DRIFT",
    "ACTION_PREVIEW_EVENT_TYPE",
    "ACTION_PREVIEW_EXPIRED",
    "ACTION_PREVIEW_INVALID",
    "ACTION_PREVIEW_RECEIPT_SCHEMA_VERSION",
    "ActionPreviewAuthorityError",
    "ActionPreviewReceiptV1",
    "DurableActionPreviewAuthority",
    "SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V1",
    "SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2",
    "context_pack_digest_from_work_order",
    "require_scheduler_derived_action",
]
