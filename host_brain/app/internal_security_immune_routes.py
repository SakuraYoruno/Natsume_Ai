"""App-local routes for authorized endpoint probes and manual security hunts.

The Host composition root owns the Security Immune Agent provider. These
routes resolve that app-local provider and never construct an agent from a
process-wide ledger at request time.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from host_brain.app.host_route_ports import HostRoutePorts


SECURITY_IMMUNE_AGENT_UNAVAILABLE = "SECURITY_IMMUNE_AGENT_UNAVAILABLE"
SECURITY_IMMUNE_AGENT_INVALID = "SECURITY_IMMUNE_AGENT_INVALID"


def _require_agent(*, route_ports: HostRoutePorts) -> Any:
    """Resolve the app-owned agent or fail before the endpoint does work."""

    provider = route_ports.security_immune_agent_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=SECURITY_IMMUNE_AGENT_UNAVAILABLE,
        )
    try:
        agent = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=SECURITY_IMMUNE_AGENT_UNAVAILABLE,
        ) from exc
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail=SECURITY_IMMUNE_AGENT_UNAVAILABLE,
        )
    return agent


def _require_agent_method(*, agent: Any, method_name: str) -> Any:
    method = getattr(agent, method_name, None)
    if not callable(method):
        raise HTTPException(
            status_code=503,
            detail=SECURITY_IMMUNE_AGENT_INVALID,
        )
    return method


def create_internal_security_immune_router(
    route_ports: HostRoutePorts,
) -> APIRouter:
    """Build a router bound to one Host application's immutable route ports."""

    if not isinstance(route_ports, HostRoutePorts):
        raise TypeError("route_ports must be HostRoutePorts")
    router = APIRouter(prefix="/internal/security", tags=["internal-security"])

    @router.get("/runtime")
    def get_internal_security_runtime() -> dict[str, Any]:
        export_runtime_state = _require_agent_method(
            agent=_require_agent(route_ports=route_ports),
            method_name="export_runtime_state",
        )
        result = export_runtime_state()
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=503,
                detail=SECURITY_IMMUNE_AGENT_INVALID,
            )
        return dict(result)

    @router.post("/hunt/run-once")
    async def run_security_immune_once(
        body: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        values = dict(body or {})
        reason = (
            str(values.get("reason") or "manual_internal_trigger").strip()
            or "manual_internal_trigger"
        )
        run_once = _require_agent_method(
            agent=_require_agent(route_ports=route_ports),
            method_name="run_once",
        )
        result = run_once(reason=reason)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=503,
                detail=SECURITY_IMMUNE_AGENT_INVALID,
            )
        return dict(result)

    @router.post("/probe/heartbeat")
    def ingest_security_probe_heartbeat(
        body: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        values = dict(body or {})
        source = (
            str(values.get("source") or "endpoint_probe").strip()
            or "endpoint_probe"
        )
        asset = dict(values.get("asset") or values)
        ingest_asset_snapshot = _require_agent_method(
            agent=_require_agent(route_ports=route_ports),
            method_name="ingest_asset_snapshot",
        )
        result = ingest_asset_snapshot(asset, source=source)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=503,
                detail=SECURITY_IMMUNE_AGENT_INVALID,
            )
        return dict(result)

    return router


__all__ = [
    "SECURITY_IMMUNE_AGENT_INVALID",
    "SECURITY_IMMUNE_AGENT_UNAVAILABLE",
    "create_internal_security_immune_router",
]
