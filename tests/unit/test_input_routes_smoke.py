from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import host_brain.input_runtime.ingress as input_routes_module
import host_brain.input_runtime.contracts_and_normalization as input_contracts
from host_brain.input_runtime.ingress_policy import HostGatewayIngressOutcome
from host_brain.input_runtime.contracts_and_normalization import InputPayload
from host_brain.input_runtime.ingress import receive_input
from host_brain.input_runtime.settings import input_settings_from_mapping
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports


class _AllowImmuneAgent:
    def inspect_text(self, **_kwargs):  # noqa: ANN003, ANN201
        return {"decision": "ALLOW", "reason_codes": []}


def _ports(**overrides):  # noqa: ANN003
    overrides.setdefault("get_immune_agent", lambda: _AllowImmuneAgent())
    return replace(build_input_runtime_application_ports(), **overrides)


@pytest.fixture(autouse=True)
def _secure_host_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        input_routes_module,
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
        input_routes_module,
        "_evaluate_host_gateway_ingress",
        lambda **_kwargs: HostGatewayIngressOutcome(
            allowed=True,
            duplicate=False,
            reason_code="SECURE_INGRESS_ALLOWED",
            claim_ref="secure-ingress-claim://unit-smoke",
            audit_ref="secure-ingress-audit://unit-smoke",
            principal_id="service.edge.input.unit",
            tenant_id="workspace-unit-smoke",
            idempotency_key="unit-smoke",
            body_sha256="0" * 64,
            request_id="request.unit.smoke",
        ),
    )
    monkeypatch.setattr(
        input_contracts,
        "_complete_host_gateway_ingress",
        lambda **_kwargs: True,
    )


def test_receive_input_smoke_gateway_dispatches_to_graph():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.input_runtime.ingress._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="GATEWAY",
                    platform="wechat",
                    conversation_id="wxid_user_a",
                    from_id="wxid_user_a",
                    content="hello",
                    msg_type="text",
                ),
                request=MagicMock(),
                ports=_ports(),
            )
        )

    assert response.success is True
    assert response.status == "ACCEPTED"
    assert response.source == "GATEWAY"
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "GATEWAY"
    scope = kwargs["payload"]["secure_gateway_conversation_scope_v1"]
    assert kwargs["session_id"] == scope["session_id"]
    assert kwargs["session_id"] != "wxid_user_a"
    assert kwargs["text"] == "hello"


def test_receive_input_smoke_asr_dispatches_to_graph():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.input_runtime.ingress._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_1",
                    text="hello from mic",
                    confidence=0.92,
                    is_final=True,
                    seq=7,
                ),
                request=MagicMock(),
                ports=_ports(),
            )
        )

    assert response.success is True
    assert response.status == "ACCEPTED"
    assert response.source == "ASR"
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "ASR"
    assert kwargs["session_id"] == "asr_session_1"
    assert kwargs["text"] == "hello from mic"


def test_receive_input_smoke_asr_non_final_is_ignored():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.input_runtime.ingress._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_partial",
                    text="partial mic text",
                    confidence=0.92,
                    is_final=False,
                    seq=8,
                ),
                request=MagicMock(),
                ports=_ports(),
            )
        )

    assert response.success is True
    assert response.status == "IGNORED"
    assert response.source == "ASR"
    assert dispatch_mock.await_count == 0


def test_receive_input_smoke_asr_low_confidence_is_ignored():
    dispatch_mock = AsyncMock(return_value=None)
    settings = input_settings_from_mapping({"HOST_INPUT_ASR_MIN_CONFIDENCE": "0.95"})

    with patch.dict("os.environ", {"HOST_INPUT_ASR_MIN_CONFIDENCE": "0.95"}), patch(
        "host_brain.input_runtime.ingress._dispatch_to_graph",
        dispatch_mock,
    ), patch(
        "host_brain.input_runtime.contracts_and_normalization.get_input_settings",
        return_value=settings,
    ):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_low_conf",
                    text="hello from mic",
                    confidence=0.80,
                    is_final=True,
                    seq=9,
                ),
                request=MagicMock(),
                ports=_ports(),
            )
        )

    assert response.success is True
    assert response.status == "IGNORED"
    assert response.source == "ASR"
    assert "noise gate" in response.detail
    assert dispatch_mock.await_count == 0
