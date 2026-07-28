"""Read-only trusted Evolution Lab routes for the Operator console."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import router
from host_brain.app.observability_runtime.evolution_lab_projection import (
    EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT,
    paginate_evolution_lab_operator_projection,
    read_evolution_lab_operator_projection,
)
from host_brain.persistence.evolution_lab_trusted_query_repository import (
    TrustedEvolutionLabQueryError,
)


@router.get("/evolution-lab/suites")
async def list_trusted_evolution_lab_suites(
    limit: Annotated[
        int,
        Query(ge=1, le=EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT),
    ] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    workspace_id: Annotated[str | None, Query(max_length=160)] = None,
    projection_store: Any = Depends(require_obs_query_projection_store),
) -> dict[str, Any]:
    """List proposal-only suites from the durable public projection."""

    try:
        payload, projection = read_evolution_lab_operator_projection(
            projection_store
        )
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "reason_code": "EVOLUTION_LAB_READ_MODEL_UNAVAILABLE",
                    "degraded_state": str(
                        projection.get("degraded_state")
                        or "SOURCE_UNAVAILABLE"
                    ),
                    "upstream_reason_code": str(
                        projection.get("reason_code")
                        or "EVOLUTION_LAB_PROJECTION_SOURCE_UNAVAILABLE"
                    ),
                    "projection_version": int(
                        projection.get("projection_version") or 0
                    ),
                    "freshness_ts_ms": int(
                        projection.get("freshness_ts_ms") or 0
                    ),
                    "retryable": True,
                },
            )
        page = paginate_evolution_lab_operator_projection(
            payload,
            limit=limit,
            cursor=cursor,
            workspace_id=workspace_id,
        )
        page["projection"] = projection
        return page
    except HTTPException:
        raise
    except TrustedEvolutionLabQueryError as exc:
        raise HTTPException(
            status_code=400
            if exc.reason_code
            in {
                "EVOLUTION_LAB_QUERY_CURSOR_INVALID",
                "EVOLUTION_LAB_QUERY_LIMIT_INVALID",
                "EVOLUTION_LAB_WORKSPACE_FILTER_INVALID",
            }
            else 503,
            detail={
                "reason_code": exc.reason_code,
                "detail": exc.detail,
                "retryable": False,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "EVOLUTION_LAB_READ_MODEL_UNAVAILABLE",
                "detail": str(exc)[:400],
                "retryable": True,
            },
        ) from exc


__all__ = ["list_trusted_evolution_lab_suites"]
