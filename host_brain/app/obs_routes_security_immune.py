"""Observability endpoints for Security Immune Agent."""

from __future__ import annotations

from typing import Any

from fastapi import Query

from host_brain.app.obs_routes_core import router
from host_brain.observability.domain_state_projection import (
    SECURITY_IMMUNE_KEY,
    SECURITY_IMMUNE_KIND,
    SECURITY_IMMUNE_MAX_AGE_MS,
    read_domain_projection,
)


def _runtime_state() -> dict[str, Any]:
    payload, projection = read_domain_projection(
        kind=SECURITY_IMMUNE_KIND,
        key=SECURITY_IMMUNE_KEY,
        max_age_ms=SECURITY_IMMUNE_MAX_AGE_MS,
    )
    state = dict(payload or {})
    state.setdefault("schema_version", "Natsume.security_immune.runtime.v1")
    state.setdefault("enabled", False)
    state.setdefault("mode", "observe")
    state.setdefault("assets", [])
    state.setdefault("incidents", [])
    state.setdefault("asset_count", 0)
    state.setdefault("incident_count", 0)
    state.setdefault("risk_counts", {})
    state.setdefault("generated_ts_ms", 0)
    state["projection"] = projection
    return state


@router.get("/security/runtime")
def get_security_immune_runtime() -> dict[str, Any]:
    return _runtime_state()


@router.get("/security/assets")
def get_security_immune_assets() -> dict[str, Any]:
    state = _runtime_state()
    return {
        "schema_version": "Natsume.security_immune.assets.v1",
        "assets": list(state.get("assets") or []),
        "asset_count": int(state.get("asset_count") or 0),
        "generated_ts_ms": state.get("generated_ts_ms"),
        "projection": dict(state.get("projection") or {}),
    }


@router.get("/security/incidents")
def get_security_immune_incidents(
    min_risk: str = Query("low", description="Minimum risk: low, medium, high, critical"),
) -> dict[str, Any]:
    order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    threshold = order.get(str(min_risk or "low").strip().lower(), 1)
    state = _runtime_state()
    incidents = [
        incident for incident in list(state.get("incidents") or [])
        if order.get(str(incident.get("risk") or "none").lower(), 0) >= threshold
    ]
    return {
        "schema_version": "Natsume.security_immune.incidents.v1",
        "incidents": incidents,
        "incident_count": len(incidents),
        "risk_counts": dict(state.get("risk_counts") or {}),
        "generated_ts_ms": state.get("generated_ts_ms"),
        "projection": dict(state.get("projection") or {}),
    }
