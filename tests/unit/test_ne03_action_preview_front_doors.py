from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app import acceptance_effect_handlers
from host_brain.task_runtime.acceptance_effect_dispatcher import _receipt_ref
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.life_delivery_transport import LifeDeliveryTransport
from host_brain.channels.edge_transport import submit_gateway_reply
from host_brain.channels.embodiment import (
    build_host_embodiment_command,
    queue_embodiment_command,
    submit_avatar_command,
    submit_embodiment_command,
)
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
)
from host_brain.contracts.operator_command_retirement import (
    RETIRED_OPERATOR_COMMAND_IDS,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_DRIFT,
    ActionPreviewAuthorityError,
    require_scheduler_derived_action,
)
from host_brain.runtime.action_preview_resolver import (
    DERIVED_EFFECT_AUTHORITY_SCHEMA,
    EFFECT_DESCRIPTOR_SCHEMA,
    NODE_AUTHORITY_SCHEMA,
    ProductionActionPreviewAuthorityResolver,
    ResolvedActionPreviewAuthority,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian
from host_brain.tribe.edge_command_pusher import EdgeCommandPusher
from natsume_contracts.life import (
    LifeDeliveryAuthorization,
    LifeDeliveryStatus,
    LifeOutboxMessage,
    digest_json,
)


NOW = 1_900_000_000_000


class _StaticResolver:
    def resolve(self, **kwargs):
        context = dict(kwargs["authority_context"])
        derived = context.get("schema_version") == DERIVED_EFFECT_AUTHORITY_SCHEMA
        descriptors = () if derived else (
            {
                "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
                "action_kind": "proactive.ui_push",
                "scope_mode": "EXACT_RUNTIME_VALUE",
                "max_risk_level": 3,
                "guardian_capability_id": "proactive_ui_push",
                "scope_max_length": 512,
                "args_max_bytes": 65_536,
                "forbidden_arg_key_fragments": ["authorization", "credential", "secret", "token"],
            },
            {
                "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
                "action_kind": "life-delivery.proactive.gateway",
                "scope_mode": "EXACT_RUNTIME_VALUE",
                "max_risk_level": 3,
                "guardian_capability_id": "life_delivery_proactive_gateway",
                "scope_max_length": 512,
                "args_max_bytes": 65_536,
                "forbidden_arg_key_fragments": ["authorization", "credential", "secret", "token"],
            },
        )
        return ResolvedActionPreviewAuthority(
            authority_kind="DERIVED_EFFECT" if derived else "TASK_NODE",
            action_kind=kwargs["action_kind"],
            action_scope=kwargs["action_scope"],
            risk_level=kwargs["risk_level"],
            guardian_capability_id=kwargs["guardian_capability_id"],
            context_pack_digest=kwargs["context_pack_digest"],
            scene_identity=kwargs["scene_identity"],
            task_graph_id=kwargs["task_graph_id"],
            graph_revision=kwargs["graph_revision"],
            task_node_id=kwargs["task_node_id"],
            parent_governor_grant_ref=kwargs["parent_governor_grant_ref"],
            node_grant_ref=kwargs["node_grant_ref"],
            node_grant=kwargs["node_grant"],
            expires_at_ms=NOW + 60_000,
            binding={
                "schema_version": "Natsume.action_preview.authority_binding.v1",
                "authority_kind": "DERIVED_EFFECT" if derived else "TASK_NODE",
                "context": context,
                "origin_receipt_digest": str(
                    dict(kwargs.get("origin_receipt") or {}).get("receipt_digest") or ""
                ),
            },
            effect_descriptors=descriptors,
            origin_receipt_ref=str(context.get("origin_receipt_ref") or ""),
        )


def _service(
    tmp_path,
    *,
    authority_resolver: Any | None = None,
) -> ActionPreviewService:
    db_path = tmp_path / "preview-front-doors.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne03-preview-front-doors",
        ts_ms=NOW,
    )
    assert result.success is True
    scope = "runtime-scope-preview-front-doors"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id="preview-front-door-owner",
        lease_ms=600_000,
        now_ms=NOW,
    )
    assert lease is not None

    def authority_provider():
        authority = repository.validate_runtime_lease(
            worker_id=str(lease["owner_id"]),
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=NOW,
        )
        assert authority is not None
        return {**authority, "runtime_scope_id": scope}

    store = RuntimeEventStore(db_path, clock=lambda: NOW)
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id=scope,
        runtime_authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    return ActionPreviewService(
        runtime_event_store=store,
        guardian=guardian,
        authority_resolver=authority_resolver or _StaticResolver(),
        clock=lambda: NOW,
    )


def _scheduler_context(service: ActionPreviewService) -> dict[str, Any]:
    boundary = {
        "action_kind": "scheduler.life_tick",
        "action_scope": "life_command",
        "risk_level": 1,
        "action_args": {"occurrence_digest": "sha256:" + "1" * 64},
        "context_pack_digest": "sha256:" + "2" * 64,
        "scene_identity": {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-preview",
            "subject_id": "person-preview",
            "scene_id": "casual-chat",
            "revision": 2,
            "decision_digest": "sha256:" + "3" * 64,
        },
        "task_graph_id": "graph-preview-scheduler",
        "graph_revision": 1,
        "task_node_id": "node-preview-scheduler",
        "parent_governor_grant_ref": "governor-grant://preview-parent",
        "node_grant_ref": "governor-grant://preview-child",
        "node_grant": {
            "grant_ref": "governor-grant://preview-child",
            "graph_id": "graph-preview-scheduler",
            "graph_revision": 1,
            "node_id": "node-preview-scheduler",
        },
        "authority_context": {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "scheduler.execute",
        },
    }
    issued = service.issue_receipt(
        **boundary,
        guardian_capability_id="scheduler_life_tick",
    )
    return {
        "schema_version": "Natsume.scheduler.action_preview_context.v1",
        "origin_event_id": issued["event_id"],
        "origin_receipt_ref": issued["receipt_ref"],
        "origin_boundary": boundary,
        "node_grant_expires_at_ms": NOW + 60_000,
    }


def test_scheduler_derived_action_reopens_origin_and_binds_exact_args(tmp_path) -> None:
    service = _service(tmp_path)
    context = _scheduler_context(service)
    issued = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="proactive.ui_push",
        action_scope="delivery-preview",
        risk_level=3,
        action_args={"content": "exact message"},
        guardian_capability_id="proactive_ui_push",
    )
    assert issued["receipt"]["action_args_digest"].startswith("sha256:")
    assert issued["receipt"]["authority_granted"] is False

    tampered = {
        **context,
        "origin_boundary": {
            **context["origin_boundary"],
            "task_node_id": "node-forged",
        },
    }
    with pytest.raises(Exception) as caught:
        require_scheduler_derived_action(
            service=service,
            preview_context=tampered,
            action_kind="proactive.ui_push",
            action_scope="delivery-preview",
            risk_level=3,
            action_args={"content": "exact message"},
            guardian_capability_id="proactive_ui_push",
        )
    assert getattr(caught.value, "reason_code", "") == "ACTION_PREVIEW_DRIFT"


def test_derived_origin_is_reopened_by_event_id_without_bounded_scan(tmp_path) -> None:
    service = _service(tmp_path)
    context = _scheduler_context(service)
    authority = service._durable_authority()

    def _forbid_scan(**_kwargs):
        raise AssertionError("derived authority must never scan a bounded event window")

    authority._store.list_events = _forbid_scan  # type: ignore[attr-defined]
    issued = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="proactive.ui_push",
        action_scope="delivery-direct-lookup",
        risk_level=3,
        action_args={"content": "direct lookup"},
        guardian_capability_id="proactive_ui_push",
    )
    assert issued["receipt"]["origin_receipt_ref"] == context["origin_receipt_ref"]

    forged_event = {
        **context,
        "origin_event_id": "runtimeevent://sha256/" + "f" * 64,
    }
    with pytest.raises(ActionPreviewAuthorityError):
        require_scheduler_derived_action(
            service=service,
            preview_context=forged_event,
            action_kind="proactive.ui_push",
            action_scope="delivery-direct-lookup",
            risk_level=3,
            action_args={"content": "direct lookup"},
            guardian_capability_id="proactive_ui_push",
        )


@pytest.mark.asyncio
async def test_gateway_terminal_reopens_exact_preview_and_rejects_tamper(
    tmp_path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    context = _scheduler_context(service)
    payload = {
        "platform": "qq",
        "conversation_id": "conversation-terminal",
        "content": "exact terminal message",
        "msg_type": "text",
    }
    issued = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="life-delivery.proactive.gateway",
        action_scope="delivery-terminal",
        risk_level=3,
        action_args={
            "delivery_key": "delivery-terminal",
            "destination": "proactive.gateway",
            "payload": payload,
        },
        guardian_capability_id="life_delivery_proactive_gateway",
    )
    sent: list[dict[str, Any]] = []

    async def _submit(**kwargs):
        sent.append(dict(kwargs))
        return {"success": True, "status": "ACCEPTED"}

    monkeypatch.setattr("host_brain.channels.edge_transport._submit_gateway_payload", _submit)
    missing = await submit_gateway_reply(
        **payload,
        request_id="delivery-terminal",
        delivery_id="delivery-terminal",
    )
    assert getattr(missing, "reason_code", "") == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert sent == []
    valid = await submit_gateway_reply(
        **payload,
        request_id="delivery-terminal",
        delivery_id="delivery-terminal",
        action_preview_service=service,
        action_preview_event_id=issued["event_id"],
        action_preview_boundary=issued["_terminal_execution_boundary"],
    )
    assert getattr(valid, "success", False) is True or valid.get("success") is True
    assert len(sent) == 1

    tampered = await submit_gateway_reply(
        **{**payload, "content": "tampered terminal message"},
        request_id="delivery-terminal",
        delivery_id="delivery-terminal",
        action_preview_service=service,
        action_preview_event_id=issued["event_id"],
        action_preview_boundary=issued["_terminal_execution_boundary"],
    )
    assert getattr(tampered, "reason_code", "") == ACTION_PREVIEW_DRIFT
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_acceptance_surface_allows_only_exact_minimal_text_payload(
    tmp_path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    reopened: list[dict[str, Any]] = []
    monkeypatch.setattr(
        service,
        "require_execution",
        lambda **kwargs: reopened.append(dict(kwargs)),
    )
    boundary = {
        "action_kind": "acceptance-effect.surface.reply_delivery",
        "action_scope": "acceptance-effect://surface/exact",
        "risk_level": 3,
        "action_args": {
            "request_id": "request-accepted-surface",
            "decision": "APPROVE",
            "reply_channel": "QQ",
            "reply_target": "conversation-accepted-surface",
            "narrative": "exact accepted text",
        },
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": _scheduler_context_scene(),
        "task_graph_id": "graph-accepted-surface",
        "graph_revision": 1,
        "task_node_id": "node-accepted-surface",
        "parent_governor_grant_ref": "governor-grant://accepted-parent",
        "node_grant_ref": "governor-grant://accepted-node",
        "node_grant": {"grant_ref": "governor-grant://accepted-node"},
        "authority_context": {"schema_version": "acceptance-test.v1"},
    }
    network_calls: list[dict[str, Any]] = []

    async def _submit(**kwargs):
        network_calls.append(dict(kwargs))
        return {"success": True, "status": "ACCEPTED"}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport._submit_gateway_payload",
        _submit,
    )
    proof = {
        "action_preview_service": service,
        "action_preview_event_id": "runtime-event://accepted-surface",
        "action_preview_boundary": boundary,
    }
    valid = await submit_gateway_reply(
        platform="qq",
        conversation_id="conversation-accepted-surface",
        content="exact accepted text",
        msg_type="text",
        request_id="request-accepted-surface",
        **proof,
    )
    assert getattr(valid, "success", False) is True or valid.get("success") is True
    assert len(network_calls) == 1
    assert len(reopened) == 1

    for drift in (
        {"raw_extra": {"performance": {"opaque": "UNBOUND_VALUE"}}},
        {"media_path": "C:/unbound/private.wav"},
        {"content": "second unaccepted narrative"},
        {"msg_type": "image"},
    ):
        result = await submit_gateway_reply(
            platform="qq",
            conversation_id="conversation-accepted-surface",
            content=str(drift.get("content") or "exact accepted text"),
            msg_type=str(drift.get("msg_type") or "text"),
            media_path=drift.get("media_path"),
            raw_extra=drift.get("raw_extra"),
            request_id="request-accepted-surface",
            **proof,
        )
        assert getattr(result, "reason_code", "") == ACTION_PREVIEW_DRIFT
    assert len(network_calls) == 1
    assert len(reopened) == 1


@pytest.mark.asyncio
async def test_accepted_surface_handler_skips_unbound_reply_transformers(
    monkeypatch,
) -> None:
    submitted: list[dict[str, Any]] = []
    effect_id = "effect-accepted-surface-handler"
    accepted_value = {
        "request_id": "request-accepted-handler",
        "session_id": "session-accepted-handler",
        "person_id": "person-accepted-handler",
        "decision": "APPROVE",
        "reply_channel": "qq",
        "reply_target": "conversation-accepted-handler",
        "narrative": "immutable accepted narrative",
        "authority": {
            "request_id": "request-accepted-handler",
            "opaque": "must-not-be-forwarded",
        },
    }

    async def _submit_gateway_reply(**kwargs):
        submitted.append(dict(kwargs))
        surface_value = {
            "request_id": kwargs["request_id"],
            "reply_channel": kwargs["platform"],
            "reply_target": kwargs["conversation_id"],
            "narrative": kwargs["content"],
        }
        return {
            "success": True,
            "status": "ACCEPTED",
            "event": {
                "outbox_id": "gateway-outbox-accepted-handler",
                "delivery_id": acceptance_effect_handlers._surface_delivery_id(
                    surface_value
                ),
                "platform": kwargs["platform"],
                "conversation_id": kwargs["conversation_id"],
                "payload_digest": (
                    acceptance_effect_handlers._surface_gateway_payload_digest(
                        surface_value,
                        effect_id=kwargs["effect_id"],
                    )
                ),
            },
            "latency_ms": 2,
        }

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        _submit_gateway_reply,
    )
    result = await acceptance_effect_handlers._deliver_accepted_surface_reply(
        {
            **accepted_value,
            "_action_preview_terminal_proof": {
                "service": SimpleNamespace(
                    require_execution=lambda **_kwargs: object()
                ),
                "event_id": "runtime-event://accepted-handler",
                "boundary": {
                    "action_kind": "acceptance-effect.surface.reply_delivery",
                    "action_scope": effect_id,
                    "risk_level": 3,
                    "action_args": accepted_value,
                    "context_pack_digest": "sha256:" + "1" * 64,
                    "scene_identity": "scene://accepted-handler",
                    "task_graph_id": "graph-accepted-handler",
                    "graph_revision": 1,
                    "task_node_id": "node-accepted-handler",
                    "parent_governor_grant_ref": "grant://accepted-parent",
                    "node_grant_ref": "grant://accepted-node",
                    "node_grant": {"grant_ref": "grant://accepted-node"},
                    "authority_context": {
                        "schema_version": (
                            "Natsume.action_preview."
                            "acceptance_effect_authority_request.v1"
                        ),
                        "effect_id": effect_id,
                        "lease_owner_id": "accepted-handler-owner",
                        "fencing_token": 1,
                    },
                },
            },
        }
    )
    assert result["status"] == "ACCEPTED"
    assert (
        result["receipt_ref"]
        == "gateway-outbox://id/gateway-outbox-accepted-handler"
    )
    assert result["outbox_id"] == "gateway-outbox-accepted-handler"
    assert _receipt_ref(result) == result["receipt_ref"]
    assert len(submitted) == 1
    assert submitted[0]["platform"] == "qq"
    assert submitted[0]["conversation_id"] == "conversation-accepted-handler"
    assert submitted[0]["content"] == "immutable accepted narrative"
    assert submitted[0]["msg_type"] == "text"
    assert submitted[0]["effect_id"] == effect_id
    assert "raw_extra" not in submitted[0]
    assert "media_path" not in submitted[0]
    assert "authority" not in submitted[0]


@pytest.mark.asyncio
async def test_public_embodiment_submit_requires_exact_preview_before_port(
    tmp_path,
) -> None:
    class _Port:
        def __init__(self) -> None:
            self.calls = 0

        async def submit(self, command):
            self.calls += 1
            return SimpleNamespace(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=True,
                success=True,
            )

    port = _Port()
    with pytest.raises(RuntimeError, match="ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"):
        await submit_avatar_command(
            action="set_expression",
            payload={"emotion_tag": "HAPPY"},
            command_port=port,
            request_id="delivery-avatar-public",
            idempotency_key="delivery-avatar-public",
        )
    assert port.calls == 0

    service = _service(tmp_path)
    context = _scheduler_context(service)
    command = build_host_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload={"emotion_tag": "HAPPY"},
        request_id="delivery-avatar-public",
        authority_ref="authority://task-node/delivery-avatar-public",
        grant_ref="governor-grant://preview-child",
        deadline_ms=NOW + 30_000,
        idempotency_key="delivery-avatar-public",
    )
    issued = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="embodiment.vts.set_expression",
        action_scope="delivery-avatar-public",
        risk_level=4,
        action_args={
            "delivery_key": "delivery-avatar-public",
            "command": command.as_dict(),
        },
        guardian_capability_id="embodiment_vts_expression",
    )
    receipt = await submit_embodiment_command(
        command,
        command_port=port,
        action_preview_service=service,
        action_preview_event_id=issued["event_id"],
        action_preview_boundary=issued["_terminal_execution_boundary"],
    )
    assert receipt.success is True
    assert port.calls == 1


def test_avatar_and_mateengine_queues_reopen_transformed_commands_and_reject_tamper(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    context = _scheduler_context(service)
    command = build_host_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload={"emotion_tag": "HAPPY"},
        request_id="delivery-avatar",
        authority_ref="authority://task-node/delivery-avatar",
        grant_ref="governor-grant://preview-child",
        deadline_ms=NOW + 30_000,
        idempotency_key="delivery-avatar",
    )
    issued = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="embodiment.vts.set_expression",
        action_scope="delivery-avatar",
        risk_level=4,
        action_args={"delivery_key": "delivery-avatar", "command": command.as_dict()},
        guardian_capability_id="embodiment_vts_expression",
    )

    class _Outbox:
        calls = 0

        def enqueue(self, value):
            self.calls += 1
            return SimpleNamespace(success=True, command_id=value.command_id)

    outbox = _Outbox()
    queue_embodiment_command(
        command,
        outbox=outbox,
        action_preview_service=service,
        action_preview_event_id=issued["event_id"],
        action_preview_boundary=issued["_terminal_execution_boundary"],
    )
    assert outbox.calls == 1

    tampered = replace(command, payload={"emotion_tag": "ANGRY"})
    with pytest.raises(RuntimeError, match="ACTION_PREVIEW_DRIFT"):
        queue_embodiment_command(
            tampered,
            outbox=outbox,
            action_preview_service=service,
            action_preview_event_id=issued["event_id"],
            action_preview_boundary=issued["_terminal_execution_boundary"],
        )
    assert outbox.calls == 1

    mate = build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "expression", "preset": "HAPPY", "emotion_tag": "HAPPY"}},
        request_id="delivery-mateengine",
        authority_ref="authority://task-node/delivery-mateengine",
        grant_ref="governor-grant://preview-child",
        deadline_ms=NOW + 30_000,
        idempotency_key="delivery-mateengine",
    )
    mate_preview = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="embodiment.mateengine.command",
        action_scope="delivery-mateengine",
        risk_level=4,
        action_args={
            "delivery_key": "delivery-mateengine",
            "command": mate.as_dict(),
        },
        guardian_capability_id="embodiment_mateengine_command",
    )
    queue_embodiment_command(
        mate,
        outbox=outbox,
        action_preview_service=service,
        action_preview_event_id=mate_preview["event_id"],
        action_preview_boundary=mate_preview["_terminal_execution_boundary"],
    )
    assert outbox.calls == 2

    tampered_mate = replace(
        mate,
        payload={"command": {"type": "reset"}},
    )
    with pytest.raises(RuntimeError, match="ACTION_PREVIEW_DRIFT"):
        queue_embodiment_command(
            tampered_mate,
            outbox=outbox,
            action_preview_service=service,
            action_preview_event_id=mate_preview["event_id"],
            action_preview_boundary=mate_preview["_terminal_execution_boundary"],
        )
    assert outbox.calls == 2


def test_production_resolver_rejects_forged_grant_context_scene_and_old_attempt(
    monkeypatch,
    tmp_path,
) -> None:
    scene = {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": "workspace-production",
        "subject_id": "subject-production",
        "scene_id": "release-maintenance",
        "revision": 9,
        "decision_digest": "sha256:" + "9" * 64,
    }
    command = SimpleNamespace(
        command_id="command-production",
        request_id="request-production",
        session_id="session-production",
        principal_id="subject-production",
        root_work_order_id="work-order-production",
        governor_grant_ref="governor-grant://parent-production",
    )
    work_order = SimpleNamespace(
        input_pack={"scene_identity_v2": scene},
        metadata={
            "planning_lineage": {
                "node_bindings": [
                    {
                        "node_id": "node-production",
                        "worker_route": "skill",
                        "target_id": "desktop.execute",
                    }
                ]
            }
        },
    )
    grant = {
        "grant_ref": "governor-grant://node-production",
        "binding_digest": "sha256:" + "8" * 64,
        "expires_at_ms": NOW + 60_000,
    }

    class _Commands:
        def get_by_graph(self, **_kwargs):
            return {
                "command": command,
                "work_order": work_order,
                "command_digest": "sha256:" + "1" * 64,
                "work_order_digest": "sha256:" + "2" * 64,
                "graph_digest": "sha256:" + "3" * 64,
            }

    class _Tasks:
        attempt_status = "RUNNING"
        lease_status = "ACTIVE"
        risk_level = 4

        def get_graph(self, *_args):
            return {"status": "RUNNING"}

        def get_node(self, *_args):
            return {
                "status": "RUNNING",
                "node": {"risk_level": self.risk_level},
            }

        def get_attempt(self, _attempt_id):
            return {
                "graph_id": "graph-production",
                "graph_revision": 2,
                "node_id": "node-production",
                "status": self.attempt_status,
            }

        def get_active_lease(self, _attempt_id):
            return {
                "lease_id": "lease-production",
                "owner_id": "worker-production",
                "fencing_token": 7,
                "status": self.lease_status,
                "expires_ts_ms": NOW + 60_000,
            }

        def get_acceptance_effect_intent(self, _effect_id):
            return None

        def get_node_artifact(self, *_args):
            return {}

    class _Contexts:
        def require_frozen(self, pack):
            assert pack == work_order.input_pack

    class _Grants:
        def verify(self, **_kwargs):
            return dict(grant)

    monkeypatch.setattr(
        "host_brain.runtime.action_preview_resolver.context_pack_digest_from_work_order",
        lambda _work_order: "sha256:" + "7" * 64,
    )
    tasks = _Tasks()
    resolver = ProductionActionPreviewAuthorityResolver(
        command_repository=_Commands(),
        task_store=tasks,
        node_grant_verifier=_Grants(),
        runtime_context_application=_Contexts(),
    )
    values = {
        "authority_context": {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "skill.execute",
            "command_id": "command-production",
            "attempt_id": "attempt-production",
            "lease_id": "lease-production",
            "lease_owner_id": "worker-production",
            "fencing_token": 7,
        },
        "action_kind": "desktop.execute",
        "action_scope": "desktop.execute",
        "risk_level": 4,
        "guardian_capability_id": "desktop.execute",
        "context_pack_digest": "sha256:" + "7" * 64,
        "scene_identity": scene,
        "task_graph_id": "graph-production",
        "graph_revision": 2,
        "task_node_id": "node-production",
        "parent_governor_grant_ref": "governor-grant://parent-production",
        "node_grant_ref": grant["grant_ref"],
        "node_grant": grant,
        "action_args": {"command_digest": "sha256:" + "6" * 64},
        "now_ms": NOW,
    }
    assert resolver.resolve(**values).authority_kind == "TASK_NODE"
    for forged in (
        {"node_grant": {**grant, "expires_at_ms": NOW + 120_000}},
        {"context_pack_digest": "sha256:" + "5" * 64},
        {"scene_identity": {**scene, "scene_id": "forged"}},
    ):
        with pytest.raises(ActionPreviewAuthorityError):
            resolver.resolve(**{**values, **forged})
    tasks.attempt_status = "SUCCEEDED"
    with pytest.raises(ActionPreviewAuthorityError):
        resolver.resolve(**values)

    # A derived receipt is not continuing Task authority.  Even after its
    # exact issue/reopen succeeds, the terminal boundary must recursively
    # reopen the Scheduler Attempt/Lease/Grant and reject a subsequently lost
    # lease before a network or device side effect.
    tasks.attempt_status = "RUNNING"
    tasks.lease_status = "ACTIVE"
    tasks.risk_level = 3
    work_order.metadata["planning_lineage"]["node_bindings"][0].update(
        worker_route="delegate",
        target_id="life_command",
    )
    scheduler_issue = {
        **values,
        "authority_context": {
            **values["authority_context"],
            "purpose": "scheduler.execute",
        },
        "action_kind": "scheduler.life_command",
        "action_scope": "life_command",
        "risk_level": 3,
        "guardian_capability_id": "scheduler_life_command",
        "action_args": {"occurrence_digest": "sha256:" + "6" * 64},
    }
    scheduler_issue.pop("now_ms")
    scheduler_boundary = {
        key: value
        for key, value in scheduler_issue.items()
        if key != "guardian_capability_id"
    }
    service = _service(tmp_path, authority_resolver=resolver)
    origin = service.issue_receipt(**scheduler_issue)
    preview_context = {
        "schema_version": "Natsume.scheduler.action_preview_context.v1",
        "origin_event_id": origin["event_id"],
        "origin_receipt_ref": origin["receipt_ref"],
        "origin_boundary": scheduler_boundary,
        "node_grant_expires_at_ms": NOW + 60_000,
    }
    derived = require_scheduler_derived_action(
        service=service,
        preview_context=preview_context,
        action_kind="life-delivery.edge.proactive",
        action_scope="delivery-production",
        risk_level=3,
        action_args={
            "delivery_key": "delivery-production",
            "destination": "edge.proactive",
            "payload": {"content": "exact"},
        },
        guardian_capability_id="life_delivery_edge_proactive",
    )
    tasks.lease_status = "EXPIRED"
    with pytest.raises(ActionPreviewAuthorityError) as lost:
        service.require_execution(
            event_id=derived["event_id"],
            **derived["_terminal_execution_boundary"],
        )
    assert lost.value.reason_code == "ACTION_PREVIEW_BLOCKED"


def _scheduler_context_scene() -> dict[str, Any]:
    return {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": "workspace-preview",
        "subject_id": "person-preview",
        "scene_id": "casual-chat",
        "revision": 2,
        "decision_digest": "sha256:" + "3" * 64,
    }


@pytest.mark.asyncio
async def test_life_gateway_actuator_requires_preview_and_strips_private_context(
    tmp_path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    context = _scheduler_context(service)
    authorization = LifeDeliveryAuthorization(
        request_id="request-preview-life",
        governor_grant_ref="governor-grant://preview-parent",
        authority_kind="scheduler_host_command",
        authority_principal_id="person-preview",
    )
    payload = {
        "platform": "qq",
        "conversation_id": "conversation-preview",
        "content": "exact message",
        "_action_preview_context": context,
    }
    message = LifeOutboxMessage(
        outbox_id="outbox-preview-life",
        event_id="event-preview-life",
        command_id="command-preview-life",
        person_id="person-preview",
        destination="proactive.gateway",
        delivery_key="delivery-preview-life",
        payload=payload,
        payload_digest=digest_json(payload),
        authorization=authorization,
        authorization_digest=digest_json(authorization.as_dict()),
        status=LifeDeliveryStatus.CLAIMED,
        attempt_count=1,
        fencing_token=1,
    )
    seen: dict[str, Any] = {}

    async def submit_gateway_reply(**kwargs):
        seen.update(kwargs)
        return {"success": True, "status": "ACCEPTED"}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        submit_gateway_reply,
    )
    transport = LifeDeliveryTransport(
        ledger=type("Ledger", (), {"trace_request": lambda *_: {}})(),
        action_preview_service=service,
    )
    transport._grant_resolver = type(
        "GrantResolver",
        (),
        {"require_delivery_bound": lambda *_args, **_kwargs: {}},
    )()
    result = await transport(message)

    assert result["action_preview_receipt_ref"].startswith(
        "action-preview://sha256/"
    )
    assert seen["content"] == "exact message"
    assert "_action_preview_context" not in seen

    missing_payload = {
        "platform": "qq",
        "conversation_id": "x",
        "content": "x",
    }
    missing = replace(
        message,
        payload=missing_payload,
        payload_digest=digest_json(missing_payload),
    )
    with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
        await transport(missing)


def test_retired_operator_and_legacy_edge_actuators_have_no_writer(
    monkeypatch,
) -> None:
    registry = build_default_obs_domain_command_registry()
    assert registry.resolve_spec("mate.action.submit") is None
    assert registry.resolve_port("mate.action.submit") is None

    monkeypatch.setenv("EDGE_COMMAND_BASE_URL", "http://127.0.0.1:17800")
    pusher = EdgeCommandPusher.from_env()
    result = pusher.push_command(
        command_type="gate_control_command",
        target_agent="edge.channel",
        payload={"gate_open": True},
    )
    assert result["reason"] == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"


def test_legacy_obs_commands_are_retired_from_expanded_active_manifest() -> None:
    active = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    registry = build_default_obs_domain_command_registry()
    assert len(active) == 16
    assert len(RETIRED_OPERATOR_COMMAND_IDS) == 23
    assert active.isdisjoint(RETIRED_OPERATOR_COMMAND_IDS)
    for command_id in RETIRED_OPERATOR_COMMAND_IDS:
        assert registry.resolve_spec(command_id) is None
        assert registry.resolve_port(command_id) is None
