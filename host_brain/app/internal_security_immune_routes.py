"""Internal routes for authorized endpoint probes and manual security hunts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from host_brain.app.dependencies import get_ledger
from host_brain.security_immune.agent import get_security_immune_agent

router = APIRouter(prefix="/internal/security", tags=["internal-security"])


def _agent():
    return get_security_immune_agent(ledger_provider=get_ledger)


@router.get("/runtime")
def get_internal_security_runtime() -> dict[str, Any]:
    return _agent().export_runtime_state()


@router.post("/hunt/run-once")
async def run_security_immune_once(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = dict(body or {})
    reason = str(body.get("reason") or "manual_internal_trigger").strip() or "manual_internal_trigger"
    return await _agent().run_once(reason=reason)


@router.post("/probe/heartbeat")
def ingest_security_probe_heartbeat(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = dict(body or {})
    source = str(body.get("source") or "endpoint_probe").strip() or "endpoint_probe"
    asset = dict(body.get("asset") or body)
    return _agent().ingest_asset_snapshot(asset, source=source)
