"""Compile trusted graph-scoped effects after governed execution Acceptance.

Workers may return a rich, authority-free foreground payload, but they never
choose durable identity, graph scope, attempt binding, delivery destination or
authorization.  This compiler rebuilds those fields from the admitted command
and immutable WorkOrder and returns canonical TaskStore intent specs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
)
from natsume_contracts.task import HostCommand
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)


ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID = "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID"
_EFFECT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_FORBIDDEN_WORKER_NON_TOKEN_KEY = re.compile(
    r"(?:^|_)(?:authorization|governor_grant|approval|credentials?|secret)(?:$|_)",
    re.IGNORECASE,
)
_TOKEN_FIELD = re.compile(r"(?:^|_)tokens?(?:$|_)", re.IGNORECASE)
_SAFE_TOKEN_METADATA = re.compile(
    r"^(?:"
    r"token_(?:count|budget|limit|usage)|"
    r"(?:prompt|completion|input|output|total|cached|reasoning|actual|estimated|budget|context)_tokens?|"
    r"(?:max|min)(?:_context)?_tokens|"
    r"(?:enforce_)?context_token_budget|context_token_estimator|token_budget_tokens|"
    r"(?:first_token|time_to_first_token|stream_after_first_token)(?:_latency)?_ms"
    r")$",
    re.IGNORECASE,
)


class AcceptanceEffectCompileError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID
        self.detail = str(detail or "acceptance effect descriptor is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def compile_graph_acceptance_effects(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    worker_commit: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    if not isinstance(command, HostCommand):
        raise AcceptanceEffectCompileError("HostCommand is required")
    node_ref = _required(node_id, "node_id")
    attempt_ref = _required(attempt_id, "attempt_id")
    commit = _json_mapping(worker_commit, field_name="worker_commit")
    if str(commit.get("decision") or "").strip().upper() != "APPROVE":
        raise AcceptanceEffectCompileError("only an approved worker projection may emit effects")
    narrative = _required(commit.get("narrative"), "worker_commit.narrative")
    if str(commit.get("request_id") or command.request_id) != command.request_id:
        raise AcceptanceEffectCompileError("worker projection request_id drifted")

    pack = _json_mapping(getattr(work_order, "input_pack", {}), field_name="work_order.input_pack")
    surface_message = str(
        pack.get("surface_user_message")
        or pack.get("user_message")
        or getattr(work_order, "objective", "")
        or ""
    ).strip()
    if not surface_message:
        raise AcceptanceEffectCompileError("accepted graph has no frozen surface user message")
    render_lang = str(
        pack.get("render_target_lang")
        or pack.get("prepared_render_target_lang")
        or "zh"
    ).strip() or "zh"
    request_body = _json_mapping(pack.get("request_body"), field_name="request_body")
    delivery_context = _json_mapping(pack.get("delivery_context"), field_name="delivery_context")
    emotion_context = _json_mapping(
        pack.get("emotion_context") or pack.get("prepared_emotion_context"),
        field_name="emotion_context",
    )
    bond_summary = _json_mapping(pack.get("bond_summary"), field_name="bond_summary")

    descriptors = _worker_descriptors(commit)
    foreground = _trusted_foreground_command(
        descriptors=descriptors,
        command=command,
        surface_message=surface_message,
        narrative=narrative,
        render_lang=render_lang,
        request_body=request_body,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        pack=pack,
    )
    user_turn = {
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": command.principal_id,
        "content": surface_message,
        "ts_ms": command.created_at_ms,
        "lang": render_lang,
        "metadata": {
            "input_source": str(request_body.get("input_source") or ""),
            "reply_channel": str(delivery_context.get("reply_channel") or ""),
            "reply_target": str(delivery_context.get("reply_target") or ""),
            "attachments": list(request_body.get("attachments") or []),
            "multimodal_observations": dict(
                request_body.get("multimodal_observations") or {}
            ),
            "attachment_summary": str(request_body.get("attachment_summary") or ""),
            "source": "governed_task_acceptance",
        },
    }
    payloads: list[tuple[str, Mapping[str, Any]]] = [
        ("conversation.user_turn", user_turn),
        ("life.foreground_completed", foreground),
    ]
    reply_channel = str(delivery_context.get("reply_channel") or "").strip()
    reply_target = str(delivery_context.get("reply_target") or "").strip()
    if reply_channel and reply_target:
        payloads.append(
            (
                "surface.reply_delivery",
                {
                    "request_id": command.request_id,
                    "session_id": command.session_id,
                    "person_id": command.principal_id,
                    "reply_channel": reply_channel,
                    "reply_target": reply_target,
                    "decision": "APPROVE",
                    "narrative": narrative,
                    "authority": {
                        "request_id": command.request_id,
                        "governor_grant_ref": command.governor_grant_ref,
                        "authority_kind": "governed_task_acceptance",
                        "authority_principal_id": command.principal_id,
                    },
                },
            )
        )

    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_ref,
        worker_commit=commit,
    )
    if preview_context is not None:
        payloads = [
            (
                effect_type,
                {**dict(payload), "_action_preview_context": preview_context},
            )
            for effect_type, payload in payloads
        ]

    return tuple(
        _spec(
            effect_type=effect_type,
            payload=payload,
            command=command,
            node_id=node_ref,
            attempt_id=attempt_ref,
        )
        for effect_type, payload in payloads
    )


def _action_preview_context(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any] | None:
    pack = getattr(work_order, "input_pack", None)
    if not isinstance(pack, Mapping) or pack.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
        return None
    audit = worker_commit.get("audit_refs")
    audit_map = dict(audit) if isinstance(audit, Mapping) else {}
    node_grant_ref = str(audit_map.get("node_grant_ref") or "").strip()
    node_grant_digest = str(audit_map.get("node_grant_digest") or "").strip().lower()
    if not node_grant_ref or not node_grant_digest.startswith("sha256:"):
        raise AcceptanceEffectCompileError(
            "runtime-context effect has no exact node grant preview binding"
        )
    try:
        context_digest = context_pack_digest_from_work_order(work_order)
    except ActionPreviewAuthorityError as exc:
        raise AcceptanceEffectCompileError(exc.detail) from exc
    scene = pack.get("scene_identity_v2")
    if not isinstance(scene, Mapping):
        raise AcceptanceEffectCompileError("runtime-context effect has no exact Scene identity")
    return {
        "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
        "context_pack_digest": context_digest,
        "scene_identity": _json_mapping(scene, field_name="scene_identity_v2"),
        "task_graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "task_node_id": node_id,
        "parent_governor_grant_ref": command.governor_grant_ref,
        "node_grant_ref": node_grant_ref,
        "node_grant_digest": node_grant_digest,
    }


def _worker_descriptors(commit: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = commit.get("acceptance_effect_intents")
    if raw in (None, (), []):
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("worker effect descriptors must be a sequence")
    descriptors: list[dict[str, Any]] = []
    for item in raw:
        descriptor = _json_mapping(item, field_name="worker effect descriptor")
        if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
            raise AcceptanceEffectCompileError(
                "worker descriptor may contain only effect_type, commit_scope and payload"
            )
        if (
            descriptor.get("effect_type") != "life.foreground_completed"
            or str(descriptor.get("commit_scope") or "").upper() != "NODE"
        ):
            raise AcceptanceEffectCompileError("worker proposed an unsupported effect")
        payload = _json_mapping(descriptor.get("payload"), field_name="worker effect payload")
        _reject_worker_authority(payload)
        descriptors.append({**descriptor, "payload": payload})
    if len(descriptors) > 1:
        raise AcceptanceEffectCompileError("worker proposed duplicate foreground effects")
    return tuple(descriptors)


def _trusted_foreground_command(
    *,
    descriptors: Sequence[Mapping[str, Any]],
    command: HostCommand,
    surface_message: str,
    narrative: str,
    render_lang: str,
    request_body: Mapping[str, Any],
    emotion_context: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> dict[str, Any]:
    if descriptors:
        foreground = deepcopy(dict(descriptors[0]["payload"]))
        if str(foreground.get("command_type") or "") != "foreground_completed":
            raise AcceptanceEffectCompileError("worker foreground command_type drifted")
        payload = _json_mapping(foreground.get("payload"), field_name="foreground.payload")
    else:
        payload = {}
        foreground = {"payload": payload}

    completed_at_ms = int(foreground.get("created_at_ms") or command.created_at_ms)
    projection_payload = {
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": command.principal_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "narrative": narrative,
        "request_ts_ms": command.created_at_ms,
        "ts_ms": command.created_at_ms,
        "render_target_lang": render_lang,
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "reply_channel": None,
        "reply_target": None,
        "retrieval_summary": dict(pack.get("retrieval_summary") or {}),
        "retrieval_evidence_items": list(pack.get("retrieval_evidence_items") or []),
        "pii_ingress_stats": dict(pack.get("pii_ingress_stats") or {}),
        "motive_state": dict(pack.get("motive_state") or {}),
        "attachments": list(request_body.get("attachments") or []),
        "multimodal_observations": dict(
            request_body.get("multimodal_observations") or {}
        ),
        "attachment_summary": str(request_body.get("attachment_summary") or ""),
        "trace_stage_meta": {
            "governed_task_acceptance": True,
            "surface_main_chain_used": bool(descriptors),
            "reply_source": "governed_task_runtime",
            "life_projection_mode": True,
        },
    }
    existing_projection = next(
        (
            dict(item)
            for item in list(payload.get("projection_intents") or [])
            if isinstance(item, Mapping) and item.get("target") == "surface.post_process"
        ),
        None,
    )
    if existing_projection is not None:
        rich = _json_mapping(existing_projection.get("payload"), field_name="surface.post_process")
        rich.update(projection_payload)
        projection_payload = rich
    canonical_payload = {
        **payload,
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": command.principal_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "completed_at_ms": completed_at_ms,
        "state_patch": {
            "clock": {"last_user_seen_ms": completed_at_ms, "idle_minutes": 0},
            "continuity": {"last_interaction_digest": surface_message[:160]},
        },
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "projection_intents": [
            {"target": "surface.post_process", "payload": projection_payload}
        ],
        # Transport delivery is a separate acceptance effect with independently
        # rebuilt authority and idempotency.
        "deliveries": [],
    }
    return {
        "command_type": "foreground_completed",
        "person_id": command.principal_id,
        "command_id": f"life::foreground_completed::{command.request_id}",
        "correlation_id": command.request_id,
        "payload": canonical_payload,
        "created_at_ms": completed_at_ms,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


def _spec(
    *,
    effect_type: str,
    payload: Mapping[str, Any],
    command: HostCommand,
    node_id: str,
    attempt_id: str,
) -> AcceptanceEffectIntentSpec:
    if not _EFFECT_TYPE.fullmatch(effect_type):
        raise AcceptanceEffectCompileError(f"invalid effect_type: {effect_type}")
    canonical = _canonical_json(payload)
    payload_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    binding_hex = hashlib.sha256(
        _canonical_json(
            {
                "effect_type": effect_type,
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "payload_sha256": f"sha256:{payload_hex}",
            }
        ).encode("utf-8")
    ).hexdigest()
    return AcceptanceEffectIntentSpec(
        effect_id=f"acceptance-effect://sha256/{binding_hex}",
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        effect_type=effect_type,
        payload_ref=f"acceptance-payload://sha256/{payload_hex}",
        payload=json.loads(canonical),
        payload_sha256=f"sha256:{payload_hex}",
        idempotency_key=f"acceptance-idempotency://sha256/{binding_hex}",
    )


def _reject_worker_authority(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_forbidden_worker_key(key):
                raise AcceptanceEffectCompileError(
                    f"worker effect contains forbidden authority field: {path}.{key}"
                )
            _reject_worker_authority(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_worker_authority(child, path=f"{path}[{index}]")


def _is_forbidden_worker_key(key: str) -> bool:
    """Separate credential tokens from harmless model-usage metadata."""

    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if _FORBIDDEN_WORKER_NON_TOKEN_KEY.search(normalized):
        return True
    if _TOKEN_FIELD.search(normalized):
        return _SAFE_TOKEN_METADATA.fullmatch(normalized) is None
    return False


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AcceptanceEffectCompileError(f"{field_name} must be a mapping")
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise AcceptanceEffectCompileError(f"{field_name} must be canonical JSON") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AcceptanceEffectCompileError(f"{field_name} is required")
    return text


__all__ = [
    "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
    "AcceptanceEffectCompileError",
    "compile_graph_acceptance_effects",
]
