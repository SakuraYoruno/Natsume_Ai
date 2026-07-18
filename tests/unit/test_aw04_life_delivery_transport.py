from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.life_delivery_transport import LifeDeliveryTransport
from natsume_contracts.life import (
    LifeDeliveryAuthorization,
    LifeDeliveryStatus,
    LifeOutboxMessage,
    digest_json,
)
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.orchestration.governor_grant import (
    GOVERNOR_GRANT_BINDING_MISSING,
    GOVERNOR_GRANT_INVALID,
    GOVERNOR_GRANT_SCOPE_MISMATCH,
    GovernorGrantError,
    canonical_proactive_intent_args,
    governor_grant_ref,
)
from host_brain.scheduler.governed_runtime import _build_governor_intent


REQUEST_ID = "request-life-delivery"
def _bound_decision(*, action: str, target: str, args: dict, expired: bool = False) -> dict:
    intent_id = f"intent-{action}-{target}"
    token_ref = f"approval::{intent_id}"
    binding = build_governed_action_binding(
        intent_envelope={
            "schema_version": "Natsume.intent.v1",
            "id": intent_id,
            "session_id": "session-life-delivery",
            "seq": 1,
            "type": "control",
            "action": action,
            "target": target,
            "args": args,
            "nonce": f"nonce-{intent_id}",
            "person_id": "person-1",
        },
        approval_token_ref=token_ref,
        risk_level=3,
        issued_at_ms=1_700_000_000_000,
        expires_at_ms=1_700_000_001_000 if expired else 4_000_000_000_000,
    )
    return {
        "decision": "APPROVE",
        "intent_id": intent_id,
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "ts_ms": 1_800_000_000_000,
    }


GOVERNOR_DECISION = _bound_decision(
    action="exec_intent",
    target="system",
    args={"text": "scheduled task"},
)
SURFACE_DECISION = _bound_decision(
    action="narrative_only",
    target="host",
    args={"text": "surface reply"},
)
GRANT_REF = governor_grant_ref(REQUEST_ID, GOVERNOR_DECISION)
AUTHORIZATION = LifeDeliveryAuthorization(
    request_id=REQUEST_ID,
    governor_grant_ref=GRANT_REF,
    authority_kind="scheduler_host_command",
    authority_principal_id="person-1",
)
SURFACE_AUTHORIZATION = LifeDeliveryAuthorization(
    request_id=REQUEST_ID,
    governor_grant_ref=governor_grant_ref(REQUEST_ID, SURFACE_DECISION),
    authority_kind="surface_governor",
    authority_principal_id="person-1",
)


class _Ledger:
    def __init__(self, decision: dict | None = None) -> None:
        self.decision = dict(decision or GOVERNOR_DECISION)
        self.requests: list[str] = []

    def trace_request(self, request_id: str) -> dict:
        self.requests.append(request_id)
        return {"governor_log": [dict(self.decision)]}


def _message(
    destination: str,
    payload: dict,
    *,
    authorization: LifeDeliveryAuthorization = AUTHORIZATION,
) -> LifeOutboxMessage:
    return LifeOutboxMessage(
        outbox_id=f"out-{destination}",
        event_id="event-1",
        command_id="command-1",
        person_id="person-1",
        destination=destination,
        delivery_key=f"delivery::{destination}",
        payload=payload,
        payload_digest="a" * 64,
        authorization=authorization,
        authorization_digest=digest_json(authorization.as_dict()),
        status=LifeDeliveryStatus.CLAIMED,
        attempt_count=1,
        fencing_token=1,
    )


def test_gateway_delivery_uses_outbox_key_for_transport_idempotency(monkeypatch) -> None:
    captured: dict = {}

    async def _post_gateway_reply(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        _post_gateway_reply,
    )
    message = _message(
        "proactive.gateway",
        {"platform": "qq", "conversation_id": "c-1", "content": "hello"},
    )

    ledger = _Ledger()
    with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
        asyncio.run(LifeDeliveryTransport(ledger=ledger)(message))

    assert captured == {}
    assert ledger.requests == [REQUEST_ID]


def test_embodiment_destinations_are_independent_outbox_operations(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []

    def _queue_embodiment_command(command, **_kwargs):
        captured.append(("vts" if command.kind == "avatar" else "mate", command.request_id))
        return {
            "status": "PENDING",
            "success": True,
            "delivery_state": "DURABLY_QUEUED",
        }

    monkeypatch.setattr(
        "host_brain.channels.embodiment.queue_embodiment_command",
        _queue_embodiment_command,
    )
    # The production transport is intentionally app-runtime scoped.  This
    # narrow transport test supplies a sentinel while replacing the actual
    # actuator helpers above.
    transport = LifeDeliveryTransport(
        ledger=_Ledger(SURFACE_DECISION),
        embodiment_runtime=object(),
    )

    for destination, payload in (
        ("vts.emotion", {"emotion_context": {"emotion_tag": "HAPPY"}, "ts_ms": 1}),
        ("mateengine.state", {"state": "idle", "emotion_tag": "HAPPY"}),
    ):
        with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
            asyncio.run(
                transport(
                    _message(
                        destination,
                        payload,
                        authorization=SURFACE_AUTHORIZATION,
                    )
                )
            )
    assert captured == []


def test_unknown_delivery_destination_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported Life delivery destination"):
        asyncio.run(
            LifeDeliveryTransport(ledger=_Ledger())(
                _message("unknown.transport", {})
            )
        )


def test_transport_level_failure_raises_for_outbox_retry(monkeypatch) -> None:
    calls: list[dict] = []

    async def _post_proactive_push(**_kwargs):
        calls.append(dict(_kwargs))
        return {"success": False, "reason_code": "EDGE_UNAVAILABLE"}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_proactive_push",
        _post_proactive_push,
    )
    with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
        asyncio.run(
            LifeDeliveryTransport(ledger=_Ledger())(
                _message("edge.proactive", {"content": "hello"})
            )
        )
    assert calls == []


def test_invalid_durable_grant_fails_before_any_transport_side_effect(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    async def _post_gateway_reply(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        _post_gateway_reply,
    )
    invalid = LifeDeliveryAuthorization(
        request_id=REQUEST_ID,
        governor_grant_ref="governor-decision://sha256/" + "f" * 64,
        authority_kind="scheduler_host_command",
        authority_principal_id="principal-1",
    )

    with pytest.raises(GovernorGrantError) as exc_info:
        asyncio.run(
            LifeDeliveryTransport(ledger=_Ledger())(
                _message(
                    "proactive.gateway",
                    {"platform": "qq", "conversation_id": "c-1", "content": "no"},
                    authorization=invalid,
                )
            )
        )

    assert exc_info.value.reason_code == GOVERNOR_GRANT_INVALID
    assert calls == []


def test_surface_grant_cannot_be_retyped_for_proactive_delivery(monkeypatch) -> None:
    calls: list[dict] = []

    async def _post_gateway_reply(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        _post_gateway_reply,
    )

    with pytest.raises(ValueError, match="authority kind is not allowed"):
        asyncio.run(
            LifeDeliveryTransport(ledger=_Ledger())(
                _message(
                    "proactive.gateway",
                    {"platform": "qq", "conversation_id": "c-1", "content": "no"},
                    authorization=SURFACE_AUTHORIZATION,
                )
            )
        )

    assert calls == []


def test_http_delivery_requires_exact_bound_payload_before_side_effect(monkeypatch) -> None:
    calls: list[dict] = []

    async def _post_gateway_reply(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr("host_brain.channels.edge_transport.submit_gateway_reply", _post_gateway_reply)
    payload = {
        "platform": "qq",
        "conversation_id": "c-1",
        "content": "hello",
        "source_kind": "manual_proactive_attempt",
    }
    decision = _bound_decision(
        action="proactive_attempt",
        target="proactive.gateway",
        args=canonical_proactive_intent_args(person_id="person-1", **payload),
    )
    authorization = LifeDeliveryAuthorization(
        request_id=REQUEST_ID,
        governor_grant_ref=governor_grant_ref(REQUEST_ID, decision),
        authority_kind="governed_life_http",
        authority_principal_id="person-1",
    )
    transport = LifeDeliveryTransport(ledger=_Ledger(decision))

    with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
        asyncio.run(
            transport(
                _message("proactive.gateway", payload, authorization=authorization)
            )
        )
    assert calls == []

    calls.clear()
    with pytest.raises(GovernorGrantError) as exc_info:
        asyncio.run(
            transport(
                _message(
                    "proactive.gateway",
                    {**payload, "content": "tampered"},
                    authorization=authorization,
                )
            )
        )
    assert exc_info.value.reason_code == GOVERNOR_GRANT_SCOPE_MISMATCH
    assert calls == []


@pytest.mark.parametrize("failure", ["ordinary", "expired", "person", "principal"])
def test_transport_binding_failures_happen_before_side_effect(monkeypatch, failure: str) -> None:
    calls: list[dict] = []

    async def _post_gateway_reply(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr("host_brain.channels.edge_transport.submit_gateway_reply", _post_gateway_reply)
    decision = (
        {"decision": "APPROVE", "intent_id": "ordinary", "ts_ms": 123}
        if failure == "ordinary"
        else _bound_decision(
            action="exec_intent",
            target="system",
            args={"text": "scheduled task"},
            expired=failure == "expired",
        )
    )
    authorization = LifeDeliveryAuthorization(
        request_id=REQUEST_ID,
        governor_grant_ref=governor_grant_ref(REQUEST_ID, decision),
        authority_kind="scheduler_host_command",
        authority_principal_id="person-2" if failure == "principal" else "person-1",
    )
    message = _message(
        "proactive.gateway",
        {"platform": "qq", "conversation_id": "c-1", "content": "blocked"},
        authorization=authorization,
    )
    if failure == "person":
        message = replace(message, person_id="person-2")

    with pytest.raises(GovernorGrantError) as exc_info:
        asyncio.run(LifeDeliveryTransport(ledger=_Ledger(decision))(message))
    assert exc_info.value.reason_code in {
        GOVERNOR_GRANT_BINDING_MISSING,
        GOVERNOR_GRANT_SCOPE_MISMATCH,
    }
    assert calls == []


def test_real_scheduler_intent_binds_service_principal_and_life_person(monkeypatch) -> None:
    calls: list[dict] = []

    async def _post_gateway_reply(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr("host_brain.channels.edge_transport.submit_gateway_reply", _post_gateway_reply)
    occurrence = type(
        "Occurrence",
        (),
        {
            "task_id": "proactive_tick",
            "occurrence_id": "scheduler-occurrence://real/one",
            "principal_id": "scheduler-service-principal",
            "payload": {"person_id": "person-1"},
        },
    )()
    definition = type(
        "Definition",
        (),
        {
            "worker_key": "intent_dispatch",
            "intent_template": {"args": {"person_id": "template-person"}},
        },
    )()
    intent = _build_governor_intent(
        occurrence=occurrence,
        definition=definition,
        request_id=REQUEST_ID,
        session_id="scheduler-session-real",
        ts_ms=1_800_000_000_000,
        mode="normal",
    )
    assert intent["person_id"] == "person-1"
    assert intent["principal_id"] == "scheduler-service-principal"
    ledger = SQLiteLedger(":memory:")
    decision = IntentDecisionEngine(ledger=ledger).evaluate_intent(
        intent,
        request_id=REQUEST_ID,
        current_ms=1_800_000_000_100,
        governance_snapshot={
            "trust_level": 80,
            "energy_level": 80,
            "sys_mode": "normal",
            "rate_limit_hit": False,
            "scope": "system",
            "scope_allowlist": ["system"],
            "safety_flag": False,
            "airlock_blocked": False,
        },
    )
    assert decision["decision"] == "APPROVE"
    authorization = LifeDeliveryAuthorization(
        request_id=REQUEST_ID,
        governor_grant_ref=governor_grant_ref(REQUEST_ID, decision),
        authority_kind="scheduler_host_command",
        authority_principal_id="scheduler-service-principal",
    )
    message = _message(
        "proactive.gateway",
        {"platform": "qq", "conversation_id": "c-1", "content": "scheduled"},
        authorization=authorization,
    )

    with pytest.raises(ValueError, match="ACTION_PREVIEW_INVALID"):
        asyncio.run(LifeDeliveryTransport(ledger=ledger)(message))
    assert calls == []
