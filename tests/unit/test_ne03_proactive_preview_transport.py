from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.vision.proactive_push_client import ProactivePushClient
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.lifecycle_runtime import proactive_dispatch
from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_BLOCKED,
    ActionPreviewAuthorityError,
)
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof
from host_brain.runtime import action_preview_resolver as resolver_module
from host_brain.runtime.action_preview_resolver import (
    DERIVED_EFFECT_AUTHORITY_SCHEMA,
    NODE_AUTHORITY_SCHEMA,
    ProductionActionPreviewAuthorityResolver,
    ResolvedActionPreviewAuthority,
)
from natsume_contracts.edge.transport_v1 import EdgeTransportResult


def test_raw_edge_transports_are_private_and_not_exported() -> None:
    from host_brain.channels import edge_transport, embodiment

    assert "submit_edge_request" not in edge_transport.__all__
    assert "HttpEdgeTransportPort" not in edge_transport.__all__
    assert not hasattr(edge_transport, "submit_edge_request")
    assert not hasattr(edge_transport, "HttpEdgeTransportPort")
    assert "EdgeRpcEmbodimentCommandPort" not in embodiment.__all__
    assert not hasattr(embodiment, "EdgeRpcEmbodimentCommandPort")


def _service(require_execution=None) -> ActionPreviewService:  # noqa: ANN001
    service = ActionPreviewService(
        runtime_event_store=object(),
        guardian=SimpleNamespace(evaluate_capability=lambda **_kwargs: {}),
        authority_resolver=SimpleNamespace(resolve=lambda **_kwargs: None),
    )
    service.require_execution = (  # type: ignore[method-assign]
        require_execution or (lambda **_kwargs: SimpleNamespace())
    )
    return service


def _boundary(*, message: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {
        "action_kind": "proactive.ui_push",
        "action_scope": request_id,
        "risk_level": 3,
        "action_args": dict(message),
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": {"decision_digest": "sha256:" + "2" * 64},
        "task_graph_id": "graph-proactive",
        "graph_revision": 1,
        "task_node_id": "node-proactive",
        "parent_governor_grant_ref": "grant://proactive/parent",
        "node_grant_ref": "grant://proactive/node",
        "node_grant": {"grant_ref": "grant://proactive/node"},
        "authority_context": {
            "schema_version": (
                "Natsume.action_preview.derived_effect_authority_request.v1"
            ),
            "origin_event_id": "runtime-event://scheduler/origin",
            "origin_receipt_ref": "action-preview://scheduler/origin",
            "origin_boundary": {},
        },
    }


def _proof(
    *,
    message: dict[str, Any],
    request_id: str,
    require_execution=None,  # noqa: ANN001
) -> ActionPreviewExecutionProof:
    return ActionPreviewExecutionProof(
        service=_service(require_execution),
        event_id="runtime-event://proactive/derived",
        boundary=_boundary(message=message, request_id=request_id),
    )


def test_proactive_client_requires_exact_proof_before_network(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def submit_edge_request(**kwargs):
        calls.append(dict(kwargs))
        return EdgeTransportResult(True, "ACCEPTED", None, "", {}, 1)

    monkeypatch.setattr(
        "host_brain.channels.edge_transport._submit_edge_request",
        submit_edge_request,
    )
    client = ProactivePushClient(edge_base_url="http://127.0.0.1:17800")
    message = {
        "type": "proactive_talk",
        "content": "exact proactive message",
        "event_id": "event-proactive-exact",
        "ts_ms": 1_900_000_000_000,
        "request_id": "request-proactive-exact",
        "source": "scheduler",
    }

    missing = client.push(message)
    assert missing["reason_code"] == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert calls == []

    exact = client.push(
        message,
        preview_proof=_proof(message=message, request_id="request-proactive-exact"),
    )
    assert exact["success"] is True
    assert calls[0]["request_id"] == "request-proactive-exact"
    assert calls[0]["payload"] == message


def test_proactive_client_rejects_tamper_and_lost_lease_before_network(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def submit_edge_request(**kwargs):
        calls.append(dict(kwargs))
        return EdgeTransportResult(True, "ACCEPTED", None, "", {}, 1)

    monkeypatch.setattr(
        "host_brain.channels.edge_transport._submit_edge_request",
        submit_edge_request,
    )
    client = ProactivePushClient(edge_base_url="http://127.0.0.1:17800")
    message = {
        "type": "proactive_talk",
        "content": "exact proactive message",
        "event_id": "event-proactive-tamper",
        "ts_ms": 1_900_000_000_000,
        "request_id": "request-proactive-tamper",
    }
    proof = _proof(message=message, request_id="request-proactive-tamper")

    tampered = client.push({**message, "content": "tampered"}, preview_proof=proof)
    assert tampered["reason_code"] == "ACTION_PREVIEW_DRIFT"

    def _lost_lease(**_kwargs):
        raise ActionPreviewAuthorityError(
            ACTION_PREVIEW_BLOCKED,
            "Scheduler node Lease is no longer active",
        )

    lost_message = {
        **message,
        "request_id": "request-proactive-lost-lease",
    }
    lost = client.push(
        lost_message,
        preview_proof=_proof(
            message=lost_message,
            request_id="request-proactive-lost-lease",
            require_execution=_lost_lease,
        ),
    )
    assert lost["reason_code"] == ACTION_PREVIEW_BLOCKED
    assert calls == []


def test_scheduler_dispatch_passes_issued_typed_proof_to_ui_pusher(
    monkeypatch,
) -> None:
    origin_ref = "action-preview://scheduler/origin"
    issued_event = "runtime-event://proactive/issued"
    issued_ref = "action-preview://proactive/issued"
    service = _service()

    def require_execution(*, event_id, **_kwargs):  # noqa: ANN001
        return SimpleNamespace(
            receipt_ref=origin_ref if event_id.endswith("/origin") else issued_ref
        )

    service.require_execution = require_execution  # type: ignore[method-assign]
    service.issue_receipt = lambda **_kwargs: {  # type: ignore[method-assign]
        "event_id": issued_event,
        "receipt_ref": issued_ref,
    }
    service.now_ms = lambda: 1_900_000_000_000  # type: ignore[method-assign]
    origin_boundary = {
        "action_kind": "scheduler.life_command",
        "action_scope": "life_command",
        "risk_level": 3,
        "action_args": {"occurrence_digest": "sha256:" + "3" * 64},
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": {"decision_digest": "sha256:" + "2" * 64},
        "task_graph_id": "graph-proactive",
        "graph_revision": 1,
        "task_node_id": "node-proactive",
        "parent_governor_grant_ref": "grant://proactive/parent",
        "node_grant_ref": "grant://proactive/node",
        "node_grant": {"grant_ref": "grant://proactive/node"},
        "authority_context": {"schema_version": "test.scheduler-origin.v1"},
    }
    preview_context = {
        "schema_version": "Natsume.scheduler.action_preview_context.v1",
        "origin_event_id": "runtime-event://scheduler/origin",
        "origin_receipt_ref": origin_ref,
        "origin_boundary": origin_boundary,
        "node_grant_expires_at_ms": 1_900_000_060_000,
    }
    captured: dict[str, Any] = {}
    message = {
        "type": "gaming_companion",
        "content": "scheduler exact message",
        "event_id": "event-proactive-scheduler",
        "ts_ms": 1_900_000_000_000,
        "request_id": "request-proactive-scheduler",
        "source": "gaming_companion_tick",
    }

    def personality_handler(**kwargs):
        return kwargs["ui_pusher"](message)

    def raw_ui_pusher(concrete, *, preview_proof):  # noqa: ANN001
        captured["message"] = dict(concrete)
        captured["proof"] = preview_proof
        return {"success": True, "status": "ACCEPTED"}

    monkeypatch.setattr(
        proactive_dispatch,
        "_run_personality_scheduler_skill",
        personality_handler,
    )
    result = proactive_dispatch.run_proactive_intent_dispatch(
        {
            "skill_name": "relationship_milestone",
            "request_id": "request-proactive-scheduler",
            "args": {},
        },
        ports=SimpleNamespace(),
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        get_ledger=lambda: object(),
        retention_policy=None,
        ui_pusher=raw_ui_pusher,
        execution_context={"_action_preview_context": preview_context},
        action_preview_service=service,
    )

    assert result["success"] is True
    assert captured["message"] == message
    proof = captured["proof"]
    assert isinstance(proof, ActionPreviewExecutionProof)
    assert proof.event_id == issued_event
    assert proof.action_kind == "proactive.ui_push"
    assert proof.action_scope == "request-proactive-scheduler"
    assert proof.action_args == message


def test_production_resolver_enforces_typed_proactive_message_projection() -> None:
    now = 1_900_000_000_000
    expires = now + 60_000
    scene = {"decision_digest": "sha256:" + "2" * 64}
    grant = {
        "grant_ref": "grant://proactive/node",
        "binding_digest": "sha256:" + "3" * 64,
    }
    binding = {
        "schema_version": "Natsume.action_preview.authority_binding.v1",
        "authority_kind": "TASK_NODE",
        "command_id": "command-proactive",
    }
    descriptors = resolver_module._scheduler_effect_descriptors("life_command")
    reopened = ResolvedActionPreviewAuthority(
        authority_kind="TASK_NODE",
        action_kind="scheduler.life_command",
        action_scope="life_command",
        risk_level=3,
        guardian_capability_id="scheduler_life_command",
        context_pack_digest="sha256:" + "1" * 64,
        scene_identity=scene,
        task_graph_id="graph-proactive",
        graph_revision=1,
        task_node_id="node-proactive",
        parent_governor_grant_ref="grant://proactive/parent",
        node_grant_ref=grant["grant_ref"],
        node_grant=grant,
        expires_at_ms=expires,
        binding=binding,
        effect_descriptors=descriptors,
    )

    class Resolver(ProductionActionPreviewAuthorityResolver):
        def resolve(self, **kwargs):  # noqa: ANN003
            context = dict(kwargs.get("authority_context") or {})
            if context.get("schema_version") == DERIVED_EFFECT_AUTHORITY_SCHEMA:
                return super().resolve(**kwargs)
            assert context.get("schema_version") == NODE_AUTHORITY_SCHEMA
            return reopened

    empty = lambda *_args, **_kwargs: None
    resolver = Resolver(
        command_repository=SimpleNamespace(get_by_graph=empty),
        task_store=SimpleNamespace(
            get_graph=empty,
            get_node=empty,
            get_attempt=empty,
            get_active_lease=empty,
            get_acceptance_effect_intent=empty,
            get_node_artifact=empty,
        ),
        node_grant_verifier=SimpleNamespace(verify=empty),
        runtime_context_application=SimpleNamespace(require_frozen=empty),
    )
    origin_boundary = {
        "action_kind": reopened.action_kind,
        "action_scope": reopened.action_scope,
        "risk_level": reopened.risk_level,
        "action_args": {"occurrence_digest": "sha256:" + "4" * 64},
        "context_pack_digest": reopened.context_pack_digest,
        "scene_identity": scene,
        "task_graph_id": reopened.task_graph_id,
        "graph_revision": reopened.graph_revision,
        "task_node_id": reopened.task_node_id,
        "parent_governor_grant_ref": reopened.parent_governor_grant_ref,
        "node_grant_ref": reopened.node_grant_ref,
        "node_grant": grant,
        "authority_context": {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "scheduler.execute",
        },
    }
    origin_ref = "action-preview://proactive/origin"
    origin = {
        "authority_kind": "TASK_NODE",
        "action_args_digest": resolver_module._digest(
            origin_boundary["action_args"]
        ),
        "guardian_capability_id": reopened.guardian_capability_id,
        "action_kind": reopened.action_kind,
        "action_scope": reopened.action_scope,
        "risk_level": reopened.risk_level,
        "context_pack_digest": reopened.context_pack_digest,
        "scene_identity": scene,
        "task_graph_id": reopened.task_graph_id,
        "graph_revision": reopened.graph_revision,
        "task_node_id": reopened.task_node_id,
        "parent_governor_grant_ref": reopened.parent_governor_grant_ref,
        "node_grant_ref": reopened.node_grant_ref,
        "node_grant_digest": grant["binding_digest"],
        "authority_binding_digest": resolver_module._digest(binding),
        "expires_at_ms": expires,
        "effect_descriptors": [dict(item) for item in descriptors],
        "receipt_digest": "sha256:" + "5" * 64,
    }
    request_id = "request-proactive-resolver"
    message = {
        "type": "proactive_talk",
        "content": "exact typed message",
        "event_id": "event-proactive-resolver",
        "ts_ms": now,
        "request_id": request_id,
        "emotion_tag": "CALM",
        "scene_tags": ["casual_chat"],
    }
    context = {
        "schema_version": DERIVED_EFFECT_AUTHORITY_SCHEMA,
        "origin_event_id": "runtime-event://proactive/origin",
        "origin_receipt_ref": origin_ref,
        "origin_boundary": origin_boundary,
    }
    values = {
        "authority_context": context,
        "action_kind": "proactive.ui_push",
        "action_scope": request_id,
        "action_args": message,
        "risk_level": 3,
        "guardian_capability_id": "proactive_ui_push",
        "context_pack_digest": reopened.context_pack_digest,
        "scene_identity": scene,
        "task_graph_id": reopened.task_graph_id,
        "graph_revision": reopened.graph_revision,
        "task_node_id": reopened.task_node_id,
        "parent_governor_grant_ref": reopened.parent_governor_grant_ref,
        "node_grant_ref": reopened.node_grant_ref,
        "node_grant": grant,
        "origin_receipt": origin,
        "now_ms": now,
    }

    resolved = resolver.resolve(**values)
    assert resolved.authority_kind == "DERIVED_EFFECT"
    assert resolved.action_scope == request_id

    invalid_args = (
        {key: value for key, value in message.items() if key != "event_id"},
        {**message, "unexpected": True},
    )
    for args in invalid_args:
        with pytest.raises(ActionPreviewAuthorityError) as blocked:
            resolver.resolve(**{**values, "action_args": args})
        assert blocked.value.reason_code == ACTION_PREVIEW_BLOCKED

    with pytest.raises(ActionPreviewAuthorityError) as scope_drift:
        resolver.resolve(**{**values, "action_scope": "request-proactive-drift"})
    assert scope_drift.value.reason_code == ACTION_PREVIEW_BLOCKED
