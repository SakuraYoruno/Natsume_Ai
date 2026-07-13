"""Observability — 服务健康检查端点。

拆分自 obs_routes_core.py（P2-7）：
  GET /obs/services/health  — 全服务健康聚合
  GET /obs/llm/nodes        — LLM 云节点独立健康状态
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from host_brain.observability.api_models import (
    LlmNodeItem,
    LlmNodesResponse,
    ServiceHealthItem,
    ServicesHealthResponse,
)
from host_brain.observability.health_device_projection import (
    HEALTH_MAX_AGE_MS,
    LLM_NODE_HEALTH_KEY,
    LLM_NODE_HEALTH_KIND,
    ORGAN_TRIBE_KEY,
    ORGAN_TRIBE_KIND,
    ORGAN_TRIBE_MAX_AGE_MS,
    SERVICE_HEALTH_KEY,
    SERVICE_HEALTH_KIND,
    read_projection_payload,
)

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _read_query_projection(
    *,
    kind: str,
    key: str,
    max_age_ms: int,
) -> tuple[dict | None, dict]:
    try:
        from host_brain.app.dependencies import get_query_projection_store

        return read_projection_payload(
            get_query_projection_store(),
            kind=kind,
            key=key,
            max_age_ms=max_age_ms,
        )
    except Exception as exc:
        logger.warning("operator projection unavailable: kind=%s key=%s error=%s", kind, key, exc)
        return None, {
            "schema_version": "Natsume.operator.query_projection.v1",
            "projection_version": 0,
            "source_cursor": 0,
            "freshness_ts_ms": 0,
            "degraded_state": "SOURCE_UNAVAILABLE",
            "reason_code": "OPERATOR_PROJECTION_UNAVAILABLE",
        }

# ---------------------------------------------------------------------------
# GET /obs/services/health
# ---------------------------------------------------------------------------
@router.get("/services/health", response_model=ServicesHealthResponse)
async def get_services_health(
    window_minutes: int = Query(60, ge=1, le=1440, description="统计窗口（分钟）"),
    side: Optional[str] = Query(None, description="过滤端：host|edge"),
):
    """Read one durable snapshot; ``window_minutes`` remains validation-only."""
    payload, meta = _read_query_projection(
        kind=SERVICE_HEALTH_KIND,
        key=SERVICE_HEALTH_KEY,
        max_age_ms=HEALTH_MAX_AGE_MS,
    )
    rows = list((payload or {}).get("services") or [])
    side_filter = side if isinstance(side, str) and side in {"host", "edge"} else None
    if side_filter is not None:
        rows = [row for row in rows if str(row.get("service_type") or "") == side_filter]
    counts = {key: 0 for key in ("UP", "DEGRADED", "DOWN", "UNKNOWN")}
    services: list[ServiceHealthItem] = []
    for row in rows:
        status = str(row.get("status") or "UNKNOWN").upper()
        counts[status if status in counts else "UNKNOWN"] += 1
        services.append(ServiceHealthItem(**dict(row)))
    return ServicesHealthResponse(
        total=len(services),
        up=counts["UP"],
        degraded=counts["DEGRADED"],
        down=counts["DOWN"],
        unknown=counts["UNKNOWN"],
        services=services,
        timestamp=int((payload or {}).get("timestamp") or _now_ms()),
        projection_version=int(meta.get("projection_version") or 0),
        cursor=int(meta.get("source_cursor") or 0),
        freshness_ts_ms=int(meta.get("freshness_ts_ms") or 0),
        degraded_state=str(meta.get("degraded_state") or "REBUILDING"),
    )


# ---------------------------------------------------------------------------
# GET /obs/llm/nodes
# ---------------------------------------------------------------------------
@router.get("/llm/nodes", response_model=LlmNodesResponse)
def get_llm_nodes(
    window_minutes: int = Query(60, ge=1, le=1440),
):
    """Read the durable LLM-node projection without touching the ledger."""
    payload, meta = _read_query_projection(
        kind=LLM_NODE_HEALTH_KIND,
        key=LLM_NODE_HEALTH_KEY,
        max_age_ms=HEALTH_MAX_AGE_MS,
    )
    return LlmNodesResponse(
        nodes=[LlmNodeItem(**dict(row)) for row in list((payload or {}).get("nodes") or [])],
        timestamp=int((payload or {}).get("timestamp") or _now_ms()),
        projection_version=int(meta.get("projection_version") or 0),
        cursor=int(meta.get("source_cursor") or 0),
        freshness_ts_ms=int(meta.get("freshness_ts_ms") or 0),
        degraded_state=str(meta.get("degraded_state") or "REBUILDING"),
    )


# ---------------------------------------------------------------------------
# D10: GET /obs/organ-tribe/status — 器官族实时状态快照
# ---------------------------------------------------------------------------
class OrganTribeAgentItem(BaseModel):
    agent_id: str
    family: str
    status: str
    key_metrics: dict
    last_updated_ts_ms: Optional[int] = None


class OrganTribeStatusResponse(BaseModel):
    total_agents: int
    active_agents: int
    agents: List[OrganTribeAgentItem]
    continuous_system_active: bool
    emotion_decay_tick_enabled: bool
    timestamp: int
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"


@router.get("/organ-tribe/status", response_model=OrganTribeStatusResponse)
def get_organ_tribe_status():
    """Read the background process-state projection; never construct Agents."""
    payload, meta = _read_query_projection(
        kind=ORGAN_TRIBE_KIND,
        key=ORGAN_TRIBE_KEY,
        max_age_ms=ORGAN_TRIBE_MAX_AGE_MS,
    )
    return OrganTribeStatusResponse(
        total_agents=int((payload or {}).get("total_agents") or 0),
        active_agents=int((payload or {}).get("active_agents") or 0),
        agents=[OrganTribeAgentItem(**dict(row)) for row in list((payload or {}).get("agents") or [])],
        continuous_system_active=bool((payload or {}).get("continuous_system_active", False)),
        emotion_decay_tick_enabled=bool((payload or {}).get("emotion_decay_tick_enabled", False)),
        timestamp=int((payload or {}).get("timestamp") or _now_ms()),
        projection_version=int(meta.get("projection_version") or 0),
        cursor=int(meta.get("source_cursor") or 0),
        freshness_ts_ms=int(meta.get("freshness_ts_ms") or 0),
        degraded_state=str(meta.get("degraded_state") or "REBUILDING"),
    )
