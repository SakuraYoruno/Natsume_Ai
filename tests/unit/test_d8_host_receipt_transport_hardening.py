from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from host_brain.channels.edge_transport import (
    _HttpEdgeTransportPort,
    _submit_gateway_payload,
    submit_gateway_reply,
)
from host_brain.channels.embodiment import (
    EdgeEmbodimentSessionConfig,
    _EdgeRpcEmbodimentCommandPort,
    build_host_embodiment_command,
)
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt
from natsume_contracts.edge.transport_v1 import EdgeSessionLease, stable_lease_id


class _HttpResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {"success": True, "status": "ACCEPTED", "event": {}}


def test_remote_cleartext_edge_rpc_is_rejected_before_http_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[object] = []

    class _NeverHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            constructed.append("client")

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=_NeverHttpClient),
    )

    result = asyncio.run(
        _HttpEdgeTransportPort(
            base_url="http://192.0.2.44:17800",
            shared_key="d8-test-key",
        ).submit(
            path="/v1/edge/embodiment",
            payload={"command_id": "emb-test"},
            request_id="d8-remote-cleartext",
            timeout_ms=1_000,
        )
    )

    assert result.success is False
    assert result.status == "REJECTED"
    assert result.reason_code == "EDGE_RPC_TLS_REQUIRED"
    assert result.event == {"external_side_effects": False}
    assert constructed == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:17800",
        "http://localhost:17800",
        "http://[::1]:17800",
    ],
)
def test_loopback_cleartext_edge_rpc_remains_allowed(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    calls: list[str] = []

    class _LoopbackHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:  # noqa: ANN002
            return None

        async def post(self, url: str, **_kwargs):  # noqa: ANN201
            calls.append(url)
            return _HttpResponse()

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=_LoopbackHttpClient),
    )
    result = asyncio.run(
        _HttpEdgeTransportPort(base_url=base_url, shared_key="d8-test-key").submit(
            path="/v1/edge/health",
            payload={"operation": "health"},
            request_id="d8-loopback-cleartext",
            timeout_ms=1_000,
        )
    )

    assert result.success is True
    assert calls == [f"{base_url}/v1/edge/health"]


def test_remote_cleartext_gateway_reply_is_rejected_before_http_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[object] = []

    class _NeverHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            constructed.append("client")

    monkeypatch.setenv("HOST_GATEWAY_REPLY_HMAC_SECRET", "d8-gateway-test-key")
    monkeypatch.setenv("HOST_EDGE_GATEWAY_BASE_URL", "http://192.0.2.44:19000")
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=_NeverHttpClient),
    )

    result = asyncio.run(
        submit_gateway_reply(
            platform="qq",
            conversation_id="d8-gateway-cleartext",
            content="must not leave over plaintext",
        )
    )

    assert result.success is False
    assert result.status == "REJECTED"
    assert result.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert constructed == []

    transport_result = asyncio.run(
        _submit_gateway_payload(
            path="/v1/gateway/reply",
            payload={"request_id": "d8-gateway-cleartext"},
            timeout_ms=1_000,
        )
    )
    assert transport_result.reason_code == "EDGE_GATEWAY_TLS_REQUIRED"
    assert transport_result.event == {"external_side_effects": False}
    assert constructed == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:19000",
        "http://localhost:19000",
        "http://[::1]:19000",
    ],
)
def test_loopback_cleartext_gateway_reply_remains_allowed(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    calls: list[str] = []

    class _LoopbackHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:  # noqa: ANN002
            return None

        async def post(self, url: str, **_kwargs):  # noqa: ANN201
            calls.append(url)
            return _HttpResponse()

    monkeypatch.setenv("HOST_GATEWAY_REPLY_HMAC_SECRET", "d8-gateway-test-key")
    monkeypatch.setenv("HOST_EDGE_GATEWAY_BASE_URL", base_url)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=_LoopbackHttpClient),
    )

    result = asyncio.run(
        submit_gateway_reply(
            platform="qq",
            conversation_id="d8-gateway-loopback",
            content="local gateway delivery",
        )
    )

    assert result.success is False
    assert result.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert calls == []

    transport_result = asyncio.run(
        _submit_gateway_payload(
            path="/v1/gateway/reply",
            payload={"request_id": "d8-gateway-loopback"},
            timeout_ms=1_000,
        )
    )
    assert transport_result.success is True
    assert calls == [f"{base_url}/v1/gateway/reply"]


def _lease(*, now: int = 1_000) -> EdgeSessionLease:
    return EdgeSessionLease(
        session_id="d8-receipt-host",
        lease_id=stable_lease_id(
            session_id="d8-receipt-host",
            owner_id="d8-receipt-owner",
            fencing_token=1,
        ),
        owner_id="d8-receipt-owner",
        fencing_token=1,
        issued_at_ms=now,
        expires_at_ms=now + 30_000,
    )


def _command():  # noqa: ANN201
    return build_host_embodiment_command(
        kind="mateengine",
        action="command",
        payload={"command": {"type": "status"}},
        request_id="d8-receipt-hardening",
    )


def _receipt_payload(  # noqa: ANN201
    command,  # noqa: ANN001
    *,
    command_id: str | None = None,
    idempotency_key: str | None = None,
):
    return EmbodimentReceipt(
        command_id=command_id or command.command_id,
        idempotency_key=idempotency_key or command.idempotency_key,
        accepted=True,
        status="APPLIED",
        reason_code=None,
        detail="edge applied command",
        received_at_ms=command.issued_at_ms,
        completed_at_ms=command.issued_at_ms + 1,
        event={"external_side_effects": True},
    ).as_dict()


@pytest.mark.parametrize(
    ("receipt_event", "expected_reason"),
    [
        ({"receipt": {"command_id": "forged-only"}}, "EDGE_EMBODIMENT_RECEIPT_INVALID"),
        (
            {
                "receipt": {
                    "command_id": "foreign",
                    "idempotency_key": "foreign-key",
                    "accepted": True,
                    "status": "APPLIED",
                }
            },
            "EDGE_EMBODIMENT_RECEIPT_IDENTITY_MISMATCH",
        ),
    ],
)
def test_forged_or_mismatched_dispatch_receipt_stays_in_doubt_with_original_identity(
    monkeypatch: pytest.MonkeyPatch,
    receipt_event: dict[str, object],
    expected_reason: str,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    command = _command()
    lease = _lease(now=clock["value"])

    async def post_to_edge(*, path, **_kwargs):  # noqa: ANN001, ANN201
        if path.endswith("/session"):
            return SimpleNamespace(
                success=True,
                status="ACCEPTED",
                reason_code=None,
                detail="lease granted",
                event={"lease": lease.as_dict()},
            )
        return SimpleNamespace(
            success=True,
            status="APPLIED",
            reason_code=None,
            detail="forged response",
            event=receipt_event,
        )

    receipt = asyncio.run(
        _EdgeRpcEmbodimentCommandPort(
            post_to_edge=post_to_edge,
            session_config=EdgeEmbodimentSessionConfig(
                session_id=lease.session_id,
                owner_id=lease.owner_id,
            ),
        ).submit(command)
    )

    assert receipt.success is False
    assert receipt.accepted is False
    assert receipt.status == "IN_DOUBT"
    assert receipt.reason_code == expected_reason
    assert receipt.command_id == command.command_id
    assert receipt.idempotency_key == command.idempotency_key
    assert receipt.event == {
        "external_side_effects": "UNKNOWN",
        "receipt_validation": "FAILED",
    }


def test_matching_dispatch_receipt_preserves_edge_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    command = _command()
    lease = _lease(now=clock["value"])

    async def post_to_edge(*, path, payload, **_kwargs):  # noqa: ANN001, ANN201
        if path.endswith("/session"):
            return SimpleNamespace(
                success=True,
                status="ACCEPTED",
                reason_code=None,
                detail="lease granted",
                event={"lease": lease.as_dict()},
            )
        return SimpleNamespace(
            success=True,
            status="APPLIED",
            reason_code=None,
            detail="edge applied command",
            event={
                "receipt": _receipt_payload(
                    command,
                    command_id=str(payload["command_id"]),
                    idempotency_key=str(payload["idempotency_key"]),
                )
            },
        )

    receipt = asyncio.run(
        _EdgeRpcEmbodimentCommandPort(
            post_to_edge=post_to_edge,
            session_config=EdgeEmbodimentSessionConfig(
                session_id=lease.session_id,
                owner_id=lease.owner_id,
            ),
        ).submit(command)
    )

    assert receipt.success is True
    assert receipt.status == "APPLIED"
    assert receipt.command_id == command.command_id
    assert receipt.idempotency_key == command.idempotency_key


def test_expired_command_is_rejected_before_edge_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"value": 1_000}
    monkeypatch.setattr("host_brain.channels.embodiment.now_ms", lambda: clock["value"])
    command = build_host_embodiment_command(
        kind="voice",
        action="speak",
        payload={"text": "expired"},
        request_id="d8-expired-before-transport",
        deadline_ms=1_500,
    )
    lease = _lease(now=clock["value"])
    calls: list[str] = []

    async def post_to_edge(*, path, **_kwargs):  # noqa: ANN001, ANN201
        calls.append(path)
        raise AssertionError("expired command must not cross the network boundary")

    clock["value"] = 2_000
    receipt = asyncio.run(
        _EdgeRpcEmbodimentCommandPort(
            post_to_edge=post_to_edge,
            session_config=EdgeEmbodimentSessionConfig(
                session_id=lease.session_id,
                owner_id=lease.owner_id,
            ),
            initial_lease=lease,
        ).submit(command)
    )

    assert receipt.accepted is False
    assert receipt.status == "REJECTED"
    assert receipt.reason_code == "EMBODIMENT_COMMAND_EXPIRED"
    assert receipt.event == {"external_side_effects": False}
    assert calls == []
