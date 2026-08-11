"""Feature-gated, read-only governance snapshot route."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings


def _require_governance_views_enabled() -> None:
    if not get_core_settings().nle_governance_views_enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "NLE_GOVERNANCE_VIEWS_DISABLED",
                "detail": "set HOST_NLE_GOVERNANCE_VIEWS_ENABLED=true to expose governance projections",
            },
        )


async def _default_snapshot(projection_store: Any) -> dict[str, Any]:
    from host_brain.app.observability_runtime.domain_state_projection import (
        GOVERNANCE_SNAPSHOT_KEY,
        GOVERNANCE_SNAPSHOT_KIND,
        GOVERNANCE_SNAPSHOT_MAX_AGE_MS,
        read_domain_projection_from_store,
    )

    payload, projection = read_domain_projection_from_store(
        projection_store,
        kind=GOVERNANCE_SNAPSHOT_KIND,
        key=GOVERNANCE_SNAPSHOT_KEY,
        max_age_ms=GOVERNANCE_SNAPSHOT_MAX_AGE_MS,
    )
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "GOVERNANCE_READ_MODEL_UNAVAILABLE",
                "degraded_state": str(
                    projection.get("degraded_state") or "SOURCE_UNAVAILABLE"
                ),
                "upstream_reason_code": str(
                    projection.get("reason_code")
                    or "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE"
                ),
                "projection_version": int(
                    projection.get("projection_version") or 0
                ),
                "freshness_ts_ms": int(projection.get("freshness_ts_ms") or 0),
                "retryable": True,
            },
        )
    snapshot = dict(payload)
    snapshot["projection"] = projection
    return snapshot


async def _get_governance_snapshot(
    projection_store: Any = Depends(require_obs_query_projection_store),
) -> dict[str, Any]:
    # Gate before selecting or constructing any production provider.
    _require_governance_views_enabled()
    try:
        return await _default_snapshot(projection_store)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "GOVERNANCE_READ_MODEL_UNAVAILABLE", "detail": str(exc)[:400]},
        ) from exc


@router.get("/governance/snapshot")
async def get_governance_snapshot(
    snapshot: dict[str, Any] = Depends(_get_governance_snapshot),
) -> dict[str, Any]:
    return snapshot


__all__ = [
    "get_governance_snapshot",
]
