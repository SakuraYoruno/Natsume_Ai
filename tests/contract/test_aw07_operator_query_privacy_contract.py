from __future__ import annotations

import hashlib
import importlib
import json

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from host_brain.observability.operator_query_gateway import (
    DigestOperatorQueryAuthenticator,
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
    OperatorQueryAuthStatus,
    OperatorQueryGateway,
    OperatorQueryMiddleware,
    get_operator_query_context,
)


def _test_app(monkeypatch) -> FastAPI:
    token = "contract-operator-token"
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.contract",
                    "scopes": ["obs:trace:read"],
                }
            ]
        ),
    )
    app = FastAPI()
    gateway = OperatorQueryGateway.from_environment()
    app.add_middleware(OperatorQueryMiddleware, gateway_provider=lambda: gateway)

    @app.get("/obs/trace/{request_id}")
    async def trace(request_id: str, request: Request, response: Response):
        context = get_operator_query_context(request)
        # The operator gateway must replace unsafe downstream cache policy and
        # retain unrelated Vary dimensions.
        response.headers["Cache-Control"] = "public, max-age=600"
        response.headers["Vary"] = "Origin"
        return {
            "request_id": request_id,
            "auth_status_seen_by_route": context.authority.auth_status.value,
            "summary": {
                "input_text": "private user sentence",
                "output_text": "private assistant sentence",
                "status": "DONE",
            },
            "timeline": [
                {
                    "raw_text": "private timeline sentence",
                    "normalized_intent": {"kind": "private"},
                    "private_reasoning": "hidden reasoning",
                    "evidence_refs": ["ledger://trace/req-1"],
                }
            ],
        }

    @app.get("/obs/public-summary")
    async def public_summary(response: Response):
        # Anonymous public routes retain their application-owned cache policy.
        response.headers["Cache-Control"] = "public, max-age=60"
        return {
            "status": "OK",
            "summary": {
                "input_text": "private body hidden from public summary",
                "input_preview": "private session preview",
                "decision_summary": "request completed",
            },
        }

    @app.get("/obs/trace-feed/live")
    async def private_trace_feed():
        return StreamingResponse(
            iter(["data: private trace event\n\n"]),
            media_type="text/event-stream",
        )

    return app


def test_anonymous_trace_query_is_rejected_before_route_execution(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get("/obs/trace/req-1")

    assert response.status_code == 401
    assert response.headers["x-natsume-query-auth"] == OperatorQueryAuthStatus.ANONYMOUS.value
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "private, no-store"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} == {
        "cookie",
        "authorization",
    }
    assert response.json()["detail"] == "OPERATOR_QUERY_AUTH_REQUIRED"
    assert "private user sentence" not in response.text


def test_anonymous_public_summary_remains_available_but_private_fields_are_references(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get("/obs/public-summary")

    assert response.status_code == 200
    assert response.headers["x-natsume-query-auth"] == OperatorQueryAuthStatus.ANONYMOUS.value
    assert response.headers["x-natsume-query-redactions"] == "2"
    assert response.headers["cache-control"] == "public, max-age=60"
    assert "vary" not in response.headers
    assert response.json()["summary"]["decision_summary"] == "request completed"
    assert "private body hidden from public summary" not in response.text
    assert "private session preview" not in response.text
    withheld = response.json()["summary"]["_operator_privacy"]["withheld"]
    assert withheld[0]["citation_ref"].startswith("operator-private-input-text://sha256/")


def test_authenticated_trace_still_cannot_bypass_default_private_serializer(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get(
        "/obs/trace/req-1",
        headers={
            "Authorization": "Bearer contract-operator-token",
            # Caller-supplied authority headers are deliberately ignored.
            "X-Operator-Principal": "attacker",
            "X-Operator-Scope": "obs:*",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-natsume-query-auth"] == OperatorQueryAuthStatus.AUTHENTICATED.value
    assert response.headers["x-natsume-query-privacy"] == "natsume.operator-query.v1"
    assert response.headers["x-natsume-query-redactions"] == "5"
    assert response.headers["cache-control"] == "private, no-store"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} == {
        "origin",
        "cookie",
        "authorization",
    }
    assert response.json()["auth_status_seen_by_route"] == "AUTHENTICATED"
    assert "private user sentence" not in response.text
    assert "private assistant sentence" not in response.text
    assert "hidden reasoning" not in response.text


def test_invalid_bearer_is_rejected_before_sensitive_trace_route(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get(
        "/obs/trace/req-1",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 403
    assert response.headers["x-natsume-query-auth"] == OperatorQueryAuthStatus.INVALID.value
    assert response.headers["cache-control"] == "private, no-store"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} == {
        "cookie",
        "authorization",
    }
    assert response.json()["detail"] == "OPERATOR_QUERY_SCOPE_DENIED"
    assert "private user sentence" not in response.text
    assert "hidden reasoning" not in response.text


def test_anonymous_non_json_trace_feed_is_gated_before_streaming(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get("/obs/trace-feed/live")

    assert response.status_code == 401
    assert response.json()["detail"] == "OPERATOR_QUERY_AUTH_REQUIRED"
    assert "private trace event" not in response.text


def test_authenticated_non_json_operator_stream_is_private_no_store(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get(
        "/obs/trace-feed/live",
        headers={"Authorization": "Bearer contract-operator-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "private, no-store"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} == {
        "cookie",
        "authorization",
    }
    assert response.headers["x-natsume-query-auth"] == "AUTHENTICATED"
    assert response.headers["x-natsume-query-privacy"] == "natsume.operator-query.v1"
    assert "x-natsume-query-redactions" not in response.headers


def test_authenticated_public_route_is_private_no_store(monkeypatch) -> None:
    response = TestClient(_test_app(monkeypatch)).get(
        "/obs/public-summary",
        headers={"Authorization": "Bearer contract-operator-token"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} == {
        "cookie",
        "authorization",
    }
    assert response.headers["x-natsume-query-auth"] == "AUTHENTICATED"
    assert response.headers["x-natsume-query-redactions"] == "2"


def test_real_obs_fastapi_trace_route_crosses_operator_query_gateway(monkeypatch) -> None:
    obs_module = importlib.import_module("host_brain.app.obs_fastapi_app")
    _test_app(monkeypatch)
    gateway = OperatorQueryGateway.from_environment()
    monkeypatch.setattr(obs_module.app.state, "operator_query_gateway", gateway)
    projection = type(
        "Projection",
        (),
        {
            "payload": {
            "trace_status": "OK",
            "sources": {"intent": 1},
            "timeline": [
                {
                    "raw_text": "real route private body",
                    "private_reasoning": "real route reasoning",
                    "status": "DONE",
                }
            ],
            }
        },
    )()
    monkeypatch.setattr(
        obs_module,
        "get_query_projection_store",
        lambda: type("Store", (), {"get": lambda _self, _kind, _key: projection})(),
    )

    response = TestClient(obs_module.app).post(
        "/observability/trace",
        json={"request_id": "req-real-boundary"},
        headers={"Authorization": "Bearer contract-operator-token"},
    )

    assert response.status_code == 200
    assert response.headers["x-natsume-query-privacy"] == "natsume.operator-query.v1"
    assert response.headers["x-natsume-query-redactions"] == "2"
    assert response.json()["timeline"][0]["status"] == "DONE"
    assert "real route private body" not in response.text
    assert "real route reasoning" not in response.text
