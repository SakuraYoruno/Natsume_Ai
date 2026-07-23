from __future__ import annotations

import json
from typing import Any

import pytest

from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.governor.route_token import (
    ROUTE_TOKEN_REF_DETAIL_MISMATCH,
    ROUTE_TOKEN_REF_MISSING,
    RouteToken,
    RouteTokenDisposition,
    validate_committed_route_token,
)
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts


def _route_detail(token: str) -> dict[str, Any]:
    return {
        "token": token,
        "reason_code": "TEST",
        "committed_at_ms": 1,
        "proposer_node_id": "governor",
    }


@pytest.mark.parametrize(
    "token",
    [
        RouteToken.GOV_OK_RENDER.value,
        RouteToken.GOV_OK_CACHED.value,
        RouteToken.GOV_OK_RULE.value,
    ],
)
def test_route_validator_allows_only_explicit_execution_tokens(token: str) -> None:
    result = validate_committed_route_token(
        token_ref=token,
        token_detail=_route_detail(token),
    )

    assert result.disposition == RouteTokenDisposition.ALLOW
    assert result.executable is True
    assert result.consistent is True


def test_route_validator_maps_airlock_to_pause() -> None:
    token = RouteToken.GOV_AIRLOCK_REVIEW.value

    result = validate_committed_route_token(
        token_ref=token,
        token_detail=_route_detail(token),
    )

    assert result.disposition == RouteTokenDisposition.PAUSE
    assert result.paused is True


@pytest.mark.parametrize(
    "token",
    [
        RouteToken.GOV_FAIL_FALLBACK.value,
        RouteToken.GOV_SECONDARY_REASONER.value,
        "GOV_UNKNOWN",
    ],
)
def test_route_validator_blocks_every_other_token(token: str) -> None:
    result = validate_committed_route_token(
        token_ref=token,
        token_detail=_route_detail(token),
    )

    assert result.disposition == RouteTokenDisposition.BLOCK
    assert result.blocked is True


def test_route_validator_fails_closed_on_missing_or_inconsistent_pair() -> None:
    missing = validate_committed_route_token(
        token_ref=None,
        token_detail=_route_detail(RouteToken.GOV_OK_RENDER.value),
    )
    mismatch = validate_committed_route_token(
        token_ref=RouteToken.GOV_OK_RENDER.value,
        token_detail=_route_detail(RouteToken.GOV_OK_RULE.value),
    )

    assert missing.disposition == RouteTokenDisposition.BLOCK
    assert missing.reason_code == ROUTE_TOKEN_REF_MISSING
    assert mismatch.disposition == RouteTokenDisposition.BLOCK
    assert mismatch.reason_code == ROUTE_TOKEN_REF_DETAIL_MISMATCH
    assert mismatch.consistent is False


def test_governor_and_app_local_tool_boundary_share_injected_token_service() -> None:
    from host_brain.app.tool_runtime_composition import (
        GovernedHostToolRuntimeComposition,
        GovernedHostToolRuntimeDependencies,
    )
    from host_brain.composition import process_runtime_services, process_services
    from host_brain.governor.decision import IntentDecisionEngine
    from host_brain.governor.tokens import TokenService
    from host_brain.tools.catalog import ToolCatalog

    token_service = TokenService()
    catalog = ToolCatalog()
    ledger = object()
    engine = IntentDecisionEngine(ledger=ledger, token_service=token_service)

    def composition() -> GovernedHostToolRuntimeComposition:
        return GovernedHostToolRuntimeComposition(
            GovernedHostToolRuntimeDependencies(
                skill_registry=lambda: object(),
                token_service=lambda: token_service,
                capability_broker=lambda: object(),
                resource_manager=lambda **_kwargs: object(),
                ledger=lambda: ledger,
                tool_catalog=lambda: catalog,
                intent_engine=lambda: engine,
                model_attempt_service=lambda: object(),
            )
        )

    first = composition()
    second = composition()
    first_broker = first.provide_tool_broker()

    assert first.provide_tool_broker() is first_broker
    assert second.provide_tool_broker() is not first_broker
    assert first_broker.token_service is token_service
    assert engine.token_service is token_service
    assert not hasattr(process_runtime_services, "get_tool_broker")
    assert not hasattr(process_services, "get_tool_broker")


class _Metrics:
    def __init__(self) -> None:
        self.rejections: list[str] = []
        self.errors = 0

    def inc_rejection(self, reason_code: str) -> None:
        self.rejections.append(str(reason_code))

    def inc_error(self) -> None:
        self.errors += 1


def _input_runtime_ports() -> InputRuntimeApplicationPorts:
    def _unused(*_args: Any, **_kwargs: Any) -> Any:
        return None

    return InputRuntimeApplicationPorts(
        get_intent_engine=lambda: object(),
        get_ledger=lambda: object(),
        get_life_snapshot_reader=lambda: object(),
        get_multimodal_observer=lambda: object(),
        get_multimodal_processor=lambda: object(),
        get_task_status_projection=lambda: object(),
        notify_user_interaction=lambda: None,
        evaluate_governor_with_timing=_unused,
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_chat_governance_snapshot=build_chat_governance_snapshot,
        resolve_chat_emotion_context=resolve_chat_emotion_context,
        chat_request_factory=object,
        cortex_orchestrator_sse_generator=_unused,
        create_multimodal_job=_unused,
        update_multimodal_job=_unused,
        reply_form_decision_type=object,
        get_reply_form_decider=lambda: object(),
        message_type=object,
        trigger_reply_performance=_unused,
        render_quick_gateway_progress_text=lambda **_kwargs: "",
        render_quick_gateway_realign_text=lambda **_kwargs: "",
    )


def _parse_sse(chunk: str) -> dict[str, Any]:
    for line in str(chunk).splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[6:]))
    return {}


async def _run_preapproved_chat(
    monkeypatch,
    *,
    governor_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], _Metrics]:
    from host_brain.app import chat_handler
    from host_brain.app.schemas import ChatRequest

    cortex_calls: list[dict[str, Any]] = []

    async def _fake_cortex(**kwargs):  # noqa: ANN003
        cortex_calls.append(dict(kwargs))
        yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE"}\n\n'

    monkeypatch.setattr(chat_handler, "_mateengine_state_cues_enabled", lambda: False)
    metrics = _Metrics()
    chunks = []
    async for chunk in chat_handler._unified_chat_sse_generator(
        request_body=ChatRequest(message="route gate test"),
        request_id="req-task0479-route-gate",
        session_id="session-task0479-route-gate",
        ts_ms=1_780_000_000_000,
        request_started_ms=1_780_000_000_000,
        intent_draft={
            "schema_version": "Natsume.intent.v1",
            "id": "intent-task0479-route-gate",
            "session_id": "session-task0479-route-gate",
            "route": "chat",
            "target": "host",
            "action": "narrative_only",
        },
        metrics=metrics,
        input_runtime_ports=_input_runtime_ports(),
        cortex_orchestrator_sse_generator=_fake_cortex,
        _pre_approved_governor_result=governor_result,
    ):
        chunks.append(_parse_sse(chunk))
    return chunks, cortex_calls, metrics


@pytest.mark.asyncio
async def test_chat_airlock_pauses_before_gov_approved_and_cortex(monkeypatch) -> None:
    monkeypatch.delenv("HOST_ACTION_ARBITER_ENABLED", raising=False)
    token = RouteToken.GOV_AIRLOCK_REVIEW.value

    events, cortex_calls, metrics = await _run_preapproved_chat(
        monkeypatch,
        governor_result={
            "decision": "APPROVE",
            "intent_id": "intent-task0479-route-gate",
            "committed_route_token_ref": token,
            "committed_route_token": _route_detail(token),
        },
    )

    assert not any(event.get("event") == "GOV_APPROVED" for event in events)
    assert events[-1]["decision"] == "PAUSE"
    assert events[-1]["execution_state"] == "PAUSED_REVIEW"
    assert cortex_calls == []
    assert metrics.errors == 0


@pytest.mark.asyncio
async def test_chat_enabled_gate_blocks_inconsistent_route_before_cortex(monkeypatch) -> None:
    monkeypatch.setenv("HOST_ACTION_ARBITER_ENABLED", "true")

    events, cortex_calls, metrics = await _run_preapproved_chat(
        monkeypatch,
        governor_result={
            "decision": "APPROVE",
            "intent_id": "intent-task0479-route-gate",
            "committed_route_token_ref": RouteToken.GOV_OK_RENDER.value,
            "committed_route_token": _route_detail(RouteToken.GOV_OK_RULE.value),
        },
    )

    assert not any(event.get("event") == "GOV_APPROVED" for event in events)
    assert events[-1]["decision"] == "REJECT"
    assert events[-1]["reason_code"] == ROUTE_TOKEN_REF_DETAIL_MISMATCH
    assert cortex_calls == []
    assert metrics.rejections == [ROUTE_TOKEN_REF_DETAIL_MISMATCH]


@pytest.mark.asyncio
async def test_chat_disabled_gate_keeps_absent_legacy_pair_compatible(monkeypatch) -> None:
    monkeypatch.delenv("HOST_ACTION_ARBITER_ENABLED", raising=False)

    events, cortex_calls, metrics = await _run_preapproved_chat(
        monkeypatch,
        governor_result={
            "decision": "APPROVE",
            "intent_id": "intent-task0479-route-gate",
        },
    )

    assert any(event.get("event") == "GOV_APPROVED" for event in events)
    assert len(cortex_calls) == 1
    assert metrics.rejections == []
    assert metrics.errors == 0
