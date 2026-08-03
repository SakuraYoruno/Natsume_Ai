from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
    OperatorQueryGateway,
    OperatorQueryMiddleware,
    get_operator_query_context,
)
from host_brain.observability.operator_session_bootstrap import (
    OPERATOR_SESSION_BOOTSTRAP_ENABLED_ENV,
    OPERATOR_SESSION_FILE_ENV,
    create_operator_session_bootstrap_router,
)


TOKEN = "local-monitor-owner-token-for-bootstrap-tests"
ORIGIN = "http://127.0.0.1:18180"


def _app(monkeypatch, tmp_path: Path) -> FastAPI:
    token_file = tmp_path / "operator-session.token"
    token_file.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv(OPERATOR_SESSION_BOOTSTRAP_ENABLED_ENV, "true")
    monkeypatch.setenv(OPERATOR_SESSION_FILE_ENV, str(token_file))
    monkeypatch.setenv("HOST_MONITOR_PROXY_PORT", "18180")
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
                    "principal_id": "local-monitor-owner",
                    "tenant_id": "local",
                    "principal_kind": "user",
                    "scopes": ["obs:operator:read", "obs:health:stream"],
                }
            ]
        ),
    )
    app = FastAPI()
    app.state.operator_query_gateway = OperatorQueryGateway.from_environment()
    app.include_router(
        create_operator_session_bootstrap_router(
            lambda: app.state.operator_query_gateway
        )
    )

    @app.get("/obs/runtime-snapshot")
    async def runtime_snapshot(request: Request) -> dict[str, str]:
        context = get_operator_query_context(request)
        return {
            "status": "READY",
            "principal_id": context.authority.principal_id,
        }

    app.add_middleware(
        OperatorQueryMiddleware,
        gateway_provider=lambda: app.state.operator_query_gateway,
    )
    return app


def _bootstrap_headers(*, origin: str = ORIGIN) -> dict[str, str]:
    return {
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }


def test_loopback_bootstrap_sets_http_only_cookie_and_unlocks_queries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = TestClient(
        _app(monkeypatch, tmp_path),
        base_url=ORIGIN,
        client=("127.0.0.1", 51000),
    )

    response = client.post(
        "/obs/operator-session/bootstrap",
        headers=_bootstrap_headers(),
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "Natsume.operator-session.bootstrap.v1",
        "status": "AUTHENTICATED",
        "principal_id": "local-monitor-owner",
        "query_schema_version": "natsume.operator-query.v1",
    }
    assert TOKEN not in response.text
    cookie = response.headers["set-cookie"]
    assert "natsume_operator_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie

    protected = client.get("/obs/runtime-snapshot")
    assert protected.status_code == 200
    assert protected.headers["x-natsume-query-auth"] == "AUTHENTICATED"
    assert protected.json()["principal_id"] == "local-monitor-owner"


def test_bootstrap_rejects_cross_site_and_non_loopback_callers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app(monkeypatch, tmp_path)
    loopback = TestClient(
        app,
        base_url=ORIGIN,
        client=("127.0.0.1", 51000),
    )
    cross_site = loopback.post(
        "/obs/operator-session/bootstrap",
        headers=_bootstrap_headers(origin="https://example.invalid"),
        json={},
    )
    assert cross_site.status_code == 403
    assert TOKEN not in cross_site.text

    remote = TestClient(
        app,
        base_url=ORIGIN,
        client=("192.0.2.10", 51000),
    )
    non_loopback = remote.post(
        "/obs/operator-session/bootstrap",
        headers=_bootstrap_headers(),
        json={},
    )
    assert non_loopback.status_code == 403
    assert TOKEN not in non_loopback.text


def test_bootstrap_fails_closed_when_token_file_and_digest_do_not_match(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app(monkeypatch, tmp_path)
    Path(str(tmp_path / "operator-session.token")).write_text(
        "different-owner-token-with-valid-minimum-length",
        encoding="utf-8",
    )
    client = TestClient(
        app,
        base_url=ORIGIN,
        client=("127.0.0.1", 51000),
    )

    response = client.post(
        "/obs/operator-session/bootstrap",
        headers=_bootstrap_headers(),
        json={},
    )

    assert response.status_code == 503
    assert TOKEN not in response.text
