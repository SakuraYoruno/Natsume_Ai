from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from host_brain.app import obs_routes_platform
from host_brain.observability.command_http import create_obs_command_http_router
from host_brain.observability.command_ports import ObsCommandPortRegistry
from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
    OperatorQueryGateway,
)
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressOutcome,
    build_obs_command_secure_request,
)
from host_brain.secure_ingress import build_hmac_sha256_signature


TOKEN = "aw07-http-only-session-token"
SECRET = "0123456789abcdef" * 2
COMMAND_ID = "sys_mode.switch"


def _configure_binding(monkeypatch, scopes: list[str]) -> OperatorQueryGateway:
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
                    "principal_id": "operator.ui.production",
                    "tenant_id": "local",
                    "key_id": "operator-ui.v1",
                    "principal_kind": "service",
                    "scopes": scopes,
                }
            ]
        ),
    )
    monkeypatch.setenv(
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON",
        json.dumps({"operator-ui.v1": SECRET}),
    )
    return OperatorQueryGateway.from_environment()


class _VerifyingIngress:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request):  # noqa: ANN001, ANN202
        self.requests.append(request)
        spec = ObsCommandPortRegistry().resolve_spec(request.command_id)
        assert spec is not None
        secure = build_obs_command_secure_request(request, spec)
        assert request.credential.raw_proof == build_hmac_sha256_signature(secure, SECRET)
        return ObsCommandIngressOutcome(
            allowed=True,
            executed=True,
            duplicate=False,
            reason_code="OBS_COMMAND_COMPLETED",
            command_id=request.command_id,
            principal_id=request.identity.principal.principal_id,
            claim_ref="claim://operator-bff/1",
            audit_ref="audit://operator-bff/1",
            result_ref="result://operator-bff/1",
            terminal_state="SUCCEEDED",
        )


def _bff_client(gateway: OperatorQueryGateway, ingress: _VerifyingIngress) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_obs_command_http_router(
            ingress_provider=lambda: ingress,
            operator_query_gateway_provider=lambda: gateway,
            clock_ms=lambda: 1_800_000_000_000,
        )
    )
    return TestClient(app)


def _command_headers() -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-Natsume-Correlation-Id": "corr.aw07.bff.1",
        "X-Natsume-Idempotency-Key": "idem.aw07.bff.1",
        "X-Natsume-Nonce": "nonce.aw07.bff.1",
    }


def test_anonymous_and_low_scope_bff_fail_before_secure_ingress(monkeypatch) -> None:
    ingress = _VerifyingIngress()
    low_gateway = _configure_binding(monkeypatch, ["obs:public:read"])
    client = _bff_client(low_gateway, ingress)
    path = f"/v1/host/operator-command-bff/{COMMAND_ID}"

    anonymous = client.post(path, json={"payload": {"target_mode": "focus"}}, headers=_command_headers())
    denied_headers = _command_headers()
    denied_headers["Cookie"] = f"natsume_operator_session={TOKEN}"
    denied = client.post(
        path,
        json={"payload": {"target_mode": "focus"}},
        headers=denied_headers,
    )

    assert anonymous.status_code == 401
    assert denied.status_code == 403
    assert ingress.requests == []


def test_http_only_session_bff_server_signs_and_calls_secure_ingress(monkeypatch) -> None:
    gateway = _configure_binding(
        monkeypatch,
        ["obs:write", "obs:sys-mode:write"],
    )
    ingress = _VerifyingIngress()
    client = _bff_client(gateway, ingress)
    headers = _command_headers()
    headers["Cookie"] = f"natsume_operator_session={TOKEN}"

    response = client.post(
        f"/v1/host/operator-command-bff/{COMMAND_ID}",
        json={"payload": {"target_mode": "focus", "reason": "operator"}},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert len(ingress.requests) == 1
    submitted = ingress.requests[0]
    assert submitted.identity.principal.principal_id == "operator.ui.production"
    assert submitted.transport_key.startswith("operator-session:")
    assert TOKEN not in repr(submitted)


def test_cross_site_request_with_valid_operator_cookie_is_csrf_denied(monkeypatch) -> None:
    gateway = _configure_binding(
        monkeypatch,
        ["obs:write", "obs:sys-mode:write"],
    )
    ingress = _VerifyingIngress()
    client = _bff_client(gateway, ingress)
    headers = _command_headers()
    headers["Origin"] = "https://attacker.example"
    headers["Sec-Fetch-Site"] = "cross-site"
    headers["Cookie"] = f"natsume_operator_session={TOKEN}"

    response = client.post(
        f"/v1/host/operator-command-bff/{COMMAND_ID}",
        json={"payload": {"target_mode": "focus"}},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["reason_code"] == "OPERATOR_BFF_CSRF_DENIED"
    assert ingress.requests == []


class _FakeWebSocket:
    def __init__(
        self,
        gateway: OperatorQueryGateway,
        *,
        cookie: str | None = None,
        projection_store=None,
    ) -> None:
        from host_brain.app.obs_route_ports import ObsRoutePorts

        headers = [] if cookie is None else [(b"cookie", cookie.encode("ascii"))]
        self.scope = {"headers": headers}
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                operator_query_gateway=gateway,
                obs_route_ports=ObsRoutePorts(
                    query_projection_store_provider=lambda: projection_store,
                ),
            )
        )
        self.accepted = False
        self.closed = None
        self.messages = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, *, code: int, reason: str = "") -> None:
        self.closed = (code, reason)

    async def send_json(self, payload) -> None:  # noqa: ANN001
        self.messages.append(payload)


def test_ws_rejects_before_accept_and_before_projection_getter(monkeypatch) -> None:
    gateway = _configure_binding(monkeypatch, ["obs:health:stream"])
    source_calls = []
    monkeypatch.setattr(
        obs_routes_platform,
        "_read_ws_health_projection",
        lambda _store: source_calls.append(True),
    )
    websocket = _FakeWebSocket(gateway)

    asyncio.run(obs_routes_platform.ws_health(websocket))

    assert websocket.accepted is False
    assert websocket.closed == (4401, "OPERATOR_QUERY_AUTH_REQUIRED")
    assert source_calls == []


def test_ws_cookie_auth_reads_only_durable_health_projection(monkeypatch) -> None:
    gateway = _configure_binding(monkeypatch, ["obs:health:stream"])
    projection_store = SimpleNamespace(get=lambda *_args: None)
    observed_stores = []
    monkeypatch.setattr(
        obs_routes_platform,
        "_read_ws_health_projection",
        lambda store: observed_stores.append(store) or {
            "services": [{"service_id": "host", "status": "UP"}],
            "total": 1,
            "up": 1,
            "down": 0,
            "degraded": 0,
            "unknown": 0,
            "timestamp": 100,
        },
    )

    async def stop_after_first(_delay):  # noqa: ANN001
        raise WebSocketDisconnect(code=1000)

    monkeypatch.setattr(obs_routes_platform.asyncio, "sleep", stop_after_first)
    websocket = _FakeWebSocket(
        gateway,
        cookie=f"natsume_operator_session={TOKEN}",
        projection_store=projection_store,
    )

    asyncio.run(obs_routes_platform.ws_health(websocket))

    assert websocket.accepted is True
    assert websocket.messages[0]["type"] == "health_snapshot"
    assert websocket.messages[0]["data"]["services"][0]["status"] == "UP"
    assert observed_stores == [projection_store]
