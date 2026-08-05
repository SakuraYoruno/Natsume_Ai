"""Loopback-only browser session bootstrap for the personal Monitor UI.

The Monitor proxy is bound to loopback by the production launcher.  A normal
browser therefore needs a small owner-login bridge, otherwise only Playwright
contexts that inject a raw token can read operator projections.  The bridge
never returns the token to JavaScript: it validates the launcher-owned token
file against the configured digest authority and emits an HttpOnly cookie.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status

from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_SCHEMA_VERSION,
    OPERATOR_QUERY_SESSION_COOKIE,
    OperatorQueryAuthStatus,
    OperatorQueryGateway,
)


OPERATOR_SESSION_BOOTSTRAP_PATH = "/obs/operator-session/bootstrap"
OPERATOR_SESSION_BOOTSTRAP_ENABLED_ENV = (
    "NATSUME_OPERATOR_SESSION_BOOTSTRAP_ENABLED"
)
OPERATOR_SESSION_FILE_ENV = "NATSUME_OPERATOR_SESSION_FILE"
OPERATOR_SESSION_BOOTSTRAP_SCHEMA_VERSION = "Natsume.operator-session.bootstrap.v1"
_MAX_TOKEN_BYTES = 4096


def create_operator_session_bootstrap_router(
    gateway_provider: Callable[[], OperatorQueryGateway],
) -> APIRouter:
    """Create the app-local, loopback-only Monitor session endpoint."""

    if not callable(gateway_provider):
        raise TypeError("gateway_provider must be callable")
    router = APIRouter(tags=["operator-session"])

    @router.post(OPERATOR_SESSION_BOOTSTRAP_PATH)
    async def bootstrap_operator_session(
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        if not _env_enabled(OPERATOR_SESSION_BOOTSTRAP_ENABLED_ENV):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="OPERATOR_SESSION_BOOTSTRAP_DISABLED",
            )
        if not _is_loopback_client(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OPERATOR_SESSION_LOOPBACK_REQUIRED",
            )
        origin = _require_same_origin_monitor_request(request)
        token = _read_operator_session_token()
        gateway = gateway_provider()
        if not isinstance(gateway, OperatorQueryGateway):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OPERATOR_QUERY_GATEWAY_UNAVAILABLE",
            )
        context = gateway.open_context(
            authorization_values=(f"Bearer {token}",),
        )
        authority = context.authority
        if (
            authority.auth_status is not OperatorQueryAuthStatus.AUTHENTICATED
            or authority.principal_kind != "user"
            or not authority.allows("obs:operator:read")
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OPERATOR_SESSION_AUTHORITY_UNAVAILABLE",
            )

        response.set_cookie(
            key=OPERATOR_QUERY_SESSION_COOKIE,
            value=token,
            httponly=True,
            secure=urlsplit(origin).scheme == "https",
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            "schema_version": OPERATOR_SESSION_BOOTSTRAP_SCHEMA_VERSION,
            "status": "AUTHENTICATED",
            "principal_id": authority.principal_id,
            "query_schema_version": OPERATOR_QUERY_SCHEMA_VERSION,
        }

    return router


def _env_enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_loopback_client(request: Request) -> bool:
    client_host = str(getattr(request.client, "host", "") or "").strip()
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host.lower() == "localhost"


def _require_same_origin_monitor_request(request: Request) -> str:
    content_type = str(request.headers.get("content-type") or "").lower()
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    if (
        not content_type.startswith("application/json")
        or fetch_site != "same-origin"
        or origin not in _allowed_monitor_origins()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OPERATOR_SESSION_SAME_ORIGIN_REQUIRED",
        )
    return origin


def _allowed_monitor_origins() -> frozenset[str]:
    raw_port = str(os.environ.get("HOST_MONITOR_PROXY_PORT") or "18180").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_MONITOR_ORIGIN_INVALID",
        ) from exc
    if not 1 <= port <= 65535:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_MONITOR_ORIGIN_INVALID",
        )
    return frozenset(
        {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            f"http://[::1]:{port}",
            f"https://127.0.0.1:{port}",
            f"https://localhost:{port}",
            f"https://[::1]:{port}",
        }
    )


def _read_operator_session_token() -> str:
    raw_path = str(os.environ.get(OPERATOR_SESSION_FILE_ENV) or "").strip()
    if not raw_path:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_FILE_UNAVAILABLE",
        )
    token_path = Path(raw_path).expanduser().resolve(strict=False)
    try:
        raw = token_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_FILE_UNAVAILABLE",
        ) from exc
    if not raw or len(raw) > _MAX_TOKEN_BYTES or b"\x00" in raw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_FILE_INVALID",
        )
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_FILE_INVALID",
        ) from exc
    if not token or any(character.isspace() for character in token):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_SESSION_FILE_INVALID",
        )
    return token


__all__ = [
    "OPERATOR_SESSION_BOOTSTRAP_ENABLED_ENV",
    "OPERATOR_SESSION_BOOTSTRAP_PATH",
    "OPERATOR_SESSION_BOOTSTRAP_SCHEMA_VERSION",
    "OPERATOR_SESSION_FILE_ENV",
    "create_operator_session_bootstrap_router",
]
