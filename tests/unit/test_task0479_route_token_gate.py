from __future__ import annotations

import json
from typing import Any

import pytest

from host_brain.governor.route_token import (
    ROUTE_TOKEN_REF_DETAIL_MISMATCH,
    ROUTE_TOKEN_REF_MISSING,
    RouteToken,
    RouteTokenDisposition,
    validate_committed_route_token,
)


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


def test_governor_and_tool_broker_share_process_token_service(monkeypatch) -> None:
    import host_brain.app.dependencies as dependencies

    monkeypatch.setattr(dependencies, "_token_service", None)
    monkeypatch.setattr(dependencies, "_tool_broker", None)
    monkeypatch.setattr(dependencies, "_intent_engine", None)
    monkeypatch.setattr(dependencies, "get_ledger", lambda: object())
    monkeypatch.setattr(dependencies, "get_tool_catalog", lambda: object())
    monkeypatch.setattr(dependencies, "get_skill_registry", lambda: object())
    monkeypatch.setattr(dependencies, "get_policy_cache", lambda: object())

    token_service = dependencies.get_token_service()
    broker = dependencies.get_tool_broker()
    engine = dependencies.get_intent_engine()

    assert dependencies.get_token_service() is token_service
    assert broker.token_service is token_service
    assert engine.token_service is token_service


class _Metrics:
    def __init__(self) -> None:
        self.rejections: list[str] = []
        self.errors = 0

    def inc_rejection(self, reason_code: str) -> None:
        self.rejections.append(str(reason_code))

    def inc_error(self) -> None:
        self.errors += 1


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

    monkeypatch.setattr(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        _fake_cortex,
    )
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
