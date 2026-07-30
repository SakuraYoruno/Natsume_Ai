from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import host_brain.input_runtime.ingress as input_ingress
import host_brain.input_runtime.worker_dispatch as input_worker
import host_brain.input_runtime.contracts_and_normalization as input_contracts
import host_brain.input_runtime.cortex_dispatch as cortex_dispatch
from host_brain.input_runtime.contracts_and_normalization import InputPayload, InputResponse
from host_brain.app.input_service_gateway import HostGatewayIngressOutcome
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.runtime.conversation_scope import SecureGatewayConversationScopeV1


def _run(coro):
    return asyncio.run(coro)


def _secure_outcome() -> HostGatewayIngressOutcome:
    return HostGatewayIngressOutcome(
        allowed=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        claim_ref="secure-ingress-claim://aw02/input",
        audit_ref="secure-ingress-audit://aw02/input",
        principal_id="service.edge.gateway",
        tenant_id="workspace-aw02",
        idempotency_key="aw02-input",
        body_sha256="a" * 64,
        request_id="request.gateway.aw02.input",
    )


def _gateway_body() -> InputPayload:
    return InputPayload(
        source="GATEWAY",
        platform="qq",
        conversation_id="conversation-aw02",
        from_id="gateway-user-aw02",
        content="请执行这个任务",
    )


def _prepare_receive_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        input_ingress,
        "read_life_snapshot",
        lambda person_id: {
            "person_id": person_id,
            "version": 0,
            "state": {"person_id": person_id, "version": 0},
            "state_digest": "empty",
            "last_event_id": None,
            "last_event_digest": None,
            "updated_at_ms": 0,
        },
    )
    monkeypatch.setattr(
        input_ingress,
        "_evaluate_host_gateway_ingress",
        lambda **_kwargs: _secure_outcome(),
    )
    monkeypatch.setattr(
        input_ingress,
        "_evaluate_immune_gate_for_input",
        lambda **_kwargs: {"decision": "ALLOW", "reason_codes": []},
    )
    monkeypatch.setattr(
        input_ingress,
        "_record_feedback_for_latest_persona_reply",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(input_ingress, "_get_ledger_safe", lambda **_kwargs: None)


def test_router_factory_closure_injects_governed_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Mock(name="governed_provider")
    delegated = AsyncMock(
        return_value=InputResponse(
            success=True,
            request_id="request.gateway.aw02.factory",
            source="GATEWAY",
            status="COMPLETED",
            detail="ok",
            ts_ms=1,
        )
    )
    monkeypatch.setattr(input_ingress, "receive_input", delegated)
    router = input_worker.create_input_router(
        application_ports=build_input_runtime_application_ports(),
        governed_command_application_provider=provider,
    )
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/input"))

    _run(endpoint(_gateway_body(), SimpleNamespace()))

    assert delegated.await_args.kwargs["governed_command_application_provider"] is provider


def test_dispatch_is_awaited_and_carries_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Mock(name="governed_provider")
    authority = {
        "principal_id": "service.edge.gateway",
        "tenant_id": "workspace-aw02",
        "claim_ref": "secure-ingress-claim://aw02/awaited",
        "audit_ref": "secure-ingress-audit://aw02/awaited",
        "body_sha256": "b" * 64,
    }
    expected = {
        "decision": "APPROVE",
        "commit_event": {"execution_receipt": {"command_id": "command::aw02"}},
    }
    worker = AsyncMock(return_value=expected)
    monkeypatch.setattr(input_worker, "_dispatch_to_graph_worker", worker)
    monkeypatch.setattr(input_worker, "_get_ledger_safe", lambda **_kwargs: None)
    monkeypatch.setattr(input_worker, "_log_input_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        input_worker,
        "_maybe_schedule_gateway_immediate_ack",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(input_worker, "_build_gateway_route_preview", lambda _text: {})

    result = _run(
        input_worker._dispatch_to_graph(
            request_id="request.gateway.aw02.awaited",
            session_id="conversation-aw02",
            source="GATEWAY",
            text="执行",
            payload={"platform": "qq", "conversation_id": "conversation-aw02"},
            ts_ms=1,
            governed_command_application_provider=provider,
            command_authority=authority,
            ports=build_input_runtime_application_ports(),
        )
    )

    assert result == expected
    assert worker.await_count == 1
    assert worker.await_args.kwargs["dispatch_mode"] == "governed.awaited"
    assert worker.await_args.kwargs["governed_command_application_provider"] is provider
    assert worker.await_args.kwargs["command_authority"] == authority


def test_governed_cortex_binds_secure_ingress_principal_before_governor() -> None:
    scope = SecureGatewayConversationScopeV1.derive(
        principal_id="service.edge.gateway",
        tenant_id="workspace-aw02",
        platform="qq",
        from_id="gateway-user-aw02",
        conversation_id="conversation-aw02",
    )
    authority = {
        "principal_id": "service.edge.gateway",
        "tenant_id": "workspace-aw02",
        "claim_ref": "secure-ingress-claim://aw02/principal-bind",
        "audit_ref": "secure-ingress-audit://aw02/principal-bind",
        "body_sha256": "c" * 64,
        **scope.as_command_authority(),
    }
    captured: dict[str, object] = {}

    async def evaluate_governor_with_timing(**kwargs):  # noqa: ANN003
        captured["governor_intent"] = dict(kwargs["intent_draft"])
        return (
            {
                "decision": "APPROVE",
                "request_id": kwargs["request_id"],
                "committed_route_token_ref": "GOV_OK_RULE",
                "committed_route_token": {"token": "GOV_OK_RULE"},
            },
            {},
        )

    async def cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured["generator_authority"] = dict(kwargs["command_authority"])
        yield "data: " + json.dumps(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "governed",
            }
        ) + "\n\n"

    def resolve_chat_emotion_context(**kwargs):  # noqa: ANN003
        captured["fusion_request"] = kwargs["request_body"]
        return {}

    ports = SimpleNamespace(
        evaluate_governor_with_timing=evaluate_governor_with_timing,
        get_intent_engine=lambda: object(),
        build_chat_emotion_snapshot=lambda **_kwargs: {},
        build_chat_governance_snapshot=lambda **_kwargs: {},
        resolve_chat_emotion_context=resolve_chat_emotion_context,
        chat_request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        cortex_orchestrator_sse_generator=cortex_orchestrator_sse_generator,
    )

    result = _run(
        cortex_dispatch._run_cortex_orchestrator_pipeline(
            request_id="request.gateway.aw02.principal-bind",
            session_id=scope.session_id,
            source="GATEWAY",
            text="执行",
            payload={
                "platform": "qq",
                "conversation_id": "conversation-aw02",
                "secure_gateway_conversation_scope_v1": scope.to_dict(),
            },
            ts_ms=1,
            intent_draft_override={
                "route": "plan",
                "target_id": "planning_search",
                "task": "execute",
                "person_id": "person-aw02",
                "principal_id": "person-aw02",
            },
            governed_command_application_provider=lambda: object(),
            command_authority=authority,
            ports=ports,
        )
    )

    assert result["decision"] == "APPROVE"
    assert captured["governor_intent"] == {
        "route": "plan",
        "target_id": "planning_search",
        "task": "execute",
        "person_id": "person-aw02",
        "principal_id": "service.edge.gateway",
    }
    assert captured["generator_authority"] == authority
    fusion_request = captured["fusion_request"]
    assert fusion_request.person_id == scope.relationship_subject_id
    assert fusion_request.workspace_id == scope.workspace_id
    assert fusion_request.session_id == scope.session_id


def test_governed_cortex_rejects_incomplete_authority_before_governor() -> None:
    evaluate_governor = AsyncMock()
    ports = SimpleNamespace(
        evaluate_governor_with_timing=evaluate_governor,
        get_intent_engine=lambda: object(),
        build_chat_emotion_snapshot=lambda **_kwargs: {},
        build_chat_governance_snapshot=lambda **_kwargs: {},
        resolve_chat_emotion_context=lambda **_kwargs: {},
        chat_request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        cortex_orchestrator_sse_generator=lambda **_kwargs: None,
    )

    with pytest.raises(
        RuntimeError,
        match="governed_command_authority_incomplete:tenant_id,claim_ref,audit_ref,body_sha256",
    ):
        _run(
            cortex_dispatch._run_cortex_orchestrator_pipeline(
                request_id="request.gateway.aw02.incomplete-authority",
                session_id="conversation-aw02",
                source="GATEWAY",
                text="执行",
                payload={"platform": "qq", "conversation_id": "conversation-aw02"},
                ts_ms=1,
                intent_draft_override={
                    "route": "chat",
                    "target_id": "surface_reply",
                    "task": "reject before governor",
                },
                governed_command_application_provider=lambda: object(),
                command_authority={"principal_id": "service.edge.gateway"},
                ports=ports,
            )
        )

    evaluate_governor.assert_not_awaited()


def test_receive_input_returns_durable_command_receipt_without_legacy_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_receive_input(monkeypatch)
    provider = Mock(name="governed_provider")
    dispatch = AsyncMock(
        return_value={
            "decision": "APPROVE",
            "reason_code": None,
            "route_decision": {"route": "skill", "target_id": "runtime.skill"},
            "commit_event": {
                "phase": "COMMIT",
                "execution_receipt": {"command_id": "command::aw02::durable"},
            },
        }
    )
    legacy_complete = Mock(side_effect=AssertionError("must not overwrite command id"))
    monkeypatch.setattr(input_ingress, "_dispatch_to_graph", dispatch)
    monkeypatch.setattr(input_contracts, "_complete_host_gateway_ingress", legacy_complete)

    response = _run(
        input_ingress.receive_input(
            _gateway_body(),
            SimpleNamespace(),
            governed_command_application_provider=provider,
            ports=build_input_runtime_application_ports(),
        )
    )

    assert response.request_id == "request.gateway.aw02.input"
    assert response.status == "COMPLETED"
    assert response.receipt_ref == "command::aw02::durable"
    legacy_complete.assert_not_called()
    authority = dispatch.await_args.kwargs["command_authority"]
    assert {key: authority[key] for key in (
        "principal_id",
        "tenant_id",
        "claim_ref",
        "audit_ref",
        "body_sha256",
    )} == {
        "principal_id": "service.edge.gateway",
        "tenant_id": "workspace-aw02",
        "claim_ref": "secure-ingress-claim://aw02/input",
        "audit_ref": "secure-ingress-audit://aw02/input",
        "body_sha256": "a" * 64,
    }
    scope = authority["secure_gateway_conversation_scope_v1"]
    assert scope["workspace_id"] == "tenant:workspace-aw02"
    assert scope["relationship_subject_id"].startswith("subject-")


def test_direct_skill_missing_receipt_fails_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_receive_input(monkeypatch)
    provider = Mock(name="governed_provider")
    monkeypatch.setattr(
        input_ingress,
        "_dispatch_to_graph",
        AsyncMock(
            return_value={
                "decision": "APPROVE",
                "route_decision": {"route": "skill", "target_id": "runtime.skill"},
                "commit_event": {"phase": "COMMIT"},
            }
        ),
    )
    legacy_complete = Mock()
    monkeypatch.setattr(input_contracts, "_complete_host_gateway_ingress", legacy_complete)

    with pytest.raises(HTTPException) as raised:
        _run(
            input_ingress.receive_input(
                _gateway_body(),
                SimpleNamespace(),
                governed_command_application_provider=provider,
                ports=build_input_runtime_application_ports(),
            )
        )

    assert raised.value.status_code == 503
    assert "execution receipt" in str(raised.value.detail)
    legacy_complete.assert_not_called()
