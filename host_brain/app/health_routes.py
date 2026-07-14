"""健康检查路由模块。

包含 /health, /ready, /health/mcp, /health/canon_sync, /startup 路由。
"""

from __future__ import annotations

import logging
import os
import time
from json import loads as json_loads
from pathlib import Path
from typing import Any, Dict, Mapping

from fastapi import APIRouter, HTTPException, Query, status
from natsume_contracts.edge.service_catalog_v1 import build_edge_transport_probe_statuses

from host_brain.agent_runtime.immune import build_immune_proposal_from_report
from host_brain.composition.process_services import collect_dependency_probes, get_ledger
from host_brain.app.main import (
    build_env_readiness_report,
    build_service_health_report,
)
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.schemas import (
    HealthResponse,
    ReadinessResponse,
)
from host_brain.core.errors import (
    STARTUP_MCP_DEGRADED,
)
from host_brain.core.ids import new_request_id
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.app.settings import get_persistence_settings
from host_brain.app.observability_runtime.remaining_query_projection import (
    CANON_SYNC_KEY,
    CANON_SYNC_KIND,
    STARTUP_SNAPSHOT_KEY,
    STARTUP_SNAPSHOT_KIND,
    read as read_query_projection,
    response_meta,
)
_logger = logging.getLogger("Natsume.health_routes")

router = APIRouter()


def _apply_host_health_status(health_report: dict[str, Any]) -> None:
    """Derive the public Host status from service and durability state."""

    summary = dict(health_report.get("summary") or {})
    required_unavailable = int(summary.get("required_unavailable") or 0)
    optional_unavailable = int(summary.get("optional_unavailable") or 0)
    public_status = "FAILED" if required_unavailable else (
        "DEGRADED" if optional_unavailable else "OK"
    )
    if required_unavailable:
        summary["overall_status"] = "DOWN"
    elif optional_unavailable:
        summary["overall_status"] = "DEGRADED"
    else:
        summary["overall_status"] = "UP"

    degradation = dict(health_report.get("degradation_state") or {})
    durability_required = bool(degradation.get("durability_required"))
    durability_status = str(degradation.get("durability_status") or "").upper()
    if durability_required and durability_status != "READY":
        if durability_status == "FAILED":
            public_status = "FAILED"
            summary["overall_status"] = "DOWN"
        elif public_status == "OK":
            public_status = "DEGRADED"
            summary["overall_status"] = "DEGRADED"
        if not health_report.get("reason_code"):
            health_report["reason_code"] = (
                str(degradation.get("last_durable_error") or "").strip()
                or "DURABILITY_NOT_READY"
            )

    health_report["summary"] = summary
    health_report["status"] = public_status


def _resolve_node_inventory_payload() -> tuple[list[dict[str, Any]], int | None, str | None]:
    def _load_json_file(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        for encoding in ("utf-8", "utf-8-sig"):
            try:
                loaded = json_loads(path.read_text(encoding=encoding))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(loaded, dict):
                return loaded
        return None

    configured = str(os.environ.get("HOST_NODE_INVENTORY_PATH") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("configs/deploy/node_inventory_9.json"),
            Path("tests/fixtures/services/good/node_inventory_9.json"),
        ]
    )

    for path in candidates:
        payload = _load_json_file(path)
        if payload is None:
            continue
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            continue
        nodes: list[dict[str, Any]] = [dict(row) for row in raw_nodes if isinstance(row, dict)]
        expected = payload.get("expected_node_count")
        expected_count: int | None = None
        if isinstance(expected, int):
            expected_count = int(expected)
        elif expected is not None:
            try:
                expected_count = int(str(expected).strip())
            except (TypeError, ValueError):
                expected_count = None
        return nodes, expected_count, str(path)
    return [], None, None


async def build_live_health_report(
    *,
    skip_dependency_probes: bool = False,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    req_id = str(request_id or new_request_id()).strip()
    ts_value = int(ts_ms if ts_ms is not None else time.time() * 1000)

    _host_result = load_host_config()
    _runtime_statuses: dict[str, Any] = {}
    _dep_probes: dict[str, Any] = {}
    if _host_result.success and _host_result.config is not None:
        _runtime_statuses = build_host_runtime_statuses(_host_result.config)

    if not skip_dependency_probes:
        try:
            _dep_probes = await collect_dependency_probes()
            _runtime_statuses.update(_dep_probes)
            _edge_gateway = dict(_dep_probes.get("edge.rpc.gateway") or {})
            _runtime_statuses.update(
                build_edge_transport_probe_statuses(
                    available=bool(_edge_gateway.get("available", False)),
                    detail=str(_edge_gateway.get("detail") or "").strip() or None,
                )
            )
        except Exception as _edge_exc:
            _logger.warning("Edge transport probe failed (degraded): %s", _edge_exc)
            _runtime_statuses.update(
                build_edge_transport_probe_statuses(
                    available=False,
                    detail=str(_edge_exc),
                )
            )
    health_report = build_service_health_report(
        runtime_statuses=_runtime_statuses,
        request_id=req_id,
        ledger=get_ledger(),
        ts_ms=ts_value,
    )
    try:
        from host_brain.observability.degradation_state import (  # noqa: PLC0415
            get_degradation_state,
            reconcile_service_observations,
        )

        if _dep_probes:
            reconcile_service_observations(
                [
                    {
                        "service_id": service_id,
                        "status": "UP" if bool(dict(row or {}).get("available")) else "DOWN",
                        "owner": "edge",
                        "ts_ms": ts_value,
                        "detail": dict(row or {}).get("detail"),
                    }
                    for service_id, row in _dep_probes.items()
                    if isinstance(row, Mapping)
                ],
                source_ref=f"health-report://{req_id}",
                now_ms=ts_value,
            )
        health_report["degradation_state"] = get_degradation_state()
    except Exception as degradation_exc:
        _logger.warning("Health degradation projection skipped: %s", degradation_exc)
    _apply_host_health_status(health_report)
    return health_report


async def build_live_readiness_report(
    *,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    req_id = str(request_id or new_request_id()).strip()
    ts_value = int(ts_ms if ts_ms is not None else time.time() * 1000)
    node_inventory, expected_node_count, inventory_path = _resolve_node_inventory_payload()
    dependency_probes = await collect_dependency_probes()
    readiness_report = build_env_readiness_report(
        env=str(os.environ.get("Natsume_ENV") or "dev"),
        node_inventory=node_inventory,
        expected_node_count=expected_node_count,
        dependency_probes=dependency_probes,
        request_id=req_id,
        ledger=get_ledger(),
        ts_ms=ts_value,
    )
    if inventory_path:
        readiness_report["node_inventory_path"] = inventory_path
    return readiness_report


@router.get("/health", response_model=HealthResponse)
async def health_check(
    skip_dependency_probes: bool = Query(
        False,
        description="Skip external dependency probes and return a lightweight host-only health snapshot.",
    ),
    include_immune_proposal: bool = Query(
        False,
        description="Attach a Codex immune proposal when the health report is not healthy.",
    ),
) -> HealthResponse:
    """Health endpoint."""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        health_report = await build_live_health_report(
            skip_dependency_probes=skip_dependency_probes,
            request_id=request_id,
            ts_ms=ts_ms,
        )
        if include_immune_proposal:
            proposal = build_immune_proposal_from_report(health_report, source="health")
            if proposal.get("triggered"):
                health_report["immune_proposal"] = proposal

        return HealthResponse(
            status=health_report.get("status", "OK"),
            version="0.1.0",
            timestamp=ts_ms,
            details=health_report,
        )
    except Exception as exc:
        _logger.exception(
            "Health check failed",
            extra={"request_id": request_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service health check failed",
        )


@router.get("/health/degradation")
async def degradation_health_state() -> Dict[str, Any]:
    """Read-only unified dependency degradation snapshot."""
    from host_brain.observability.degradation_state import get_degradation_state  # noqa: PLC0415

    return get_degradation_state()


@router.get("/ready", response_model=ReadinessResponse)
async def environment_readiness(
    include_immune_proposal: bool = Query(
        False,
        description="Attach a Codex immune proposal when the readiness report is degraded or failed.",
    ),
) -> ReadinessResponse:
    """Readiness endpoint."""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        readiness_report = await build_live_readiness_report(
            request_id=request_id,
            ts_ms=ts_ms,
        )
        if include_immune_proposal:
            proposal = build_immune_proposal_from_report(readiness_report, source="readiness")
            if proposal.get("triggered"):
                readiness_report["immune_proposal"] = proposal

        readiness_status = readiness_report.get("readiness_status", "FAILED")

        return ReadinessResponse(
            ready=(readiness_status == "READY"),
            status=readiness_status,
            reason_code=readiness_report.get("reason_code"),
            timestamp=ts_ms,
            summary=readiness_report,
        )
    except Exception as exc:
        _logger.error("Readiness check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Environment readiness check failed",
        )


@router.get("/health/mcp", response_model=HealthResponse)
async def mcp_health_check() -> HealthResponse:
    """MCP registry health endpoint."""
    ts_ms = int(time.time() * 1000)
    db_path = str(get_persistence_settings().canonical_db_path).strip()
    status_text = "OK"
    reason_code = None
    detail = "mcp registry reachable"
    tool_count = 0
    registry: MCPToolRegistry | None = None
    try:
        registry = MCPToolRegistry(db_path=db_path, check_same_thread=False)
        tool_count = len(registry.list_all())
        if tool_count == 0:
            status_text = "DEGRADED"
            reason_code = STARTUP_MCP_DEGRADED
            detail = "mcp registry reachable but no tools registered"
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = STARTUP_MCP_DEGRADED
        detail = str(exc)
    finally:
        if registry is not None:
            registry.close()
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "db_path": db_path,
            "tool_count": tool_count,
            "reason_code": reason_code,
            "detail": detail,
        },
    )


@router.get("/health/canon_sync", response_model=HealthResponse)
async def canon_sync_health_check() -> HealthResponse:
    """Read the independently refreshed canon-sync projection."""
    ts_ms = int(time.time() * 1000)
    payload, meta = read_query_projection(CANON_SYNC_KIND, CANON_SYNC_KEY)
    return HealthResponse(
        status=str((payload or {}).get("status") or "DEGRADED"),
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "drift_count": int((payload or {}).get("drift_count") or 0),
            "drift_sample": list((payload or {}).get("drift_sample") or []),
            "reason_code": (payload or {}).get("reason_code") or meta.get("reason_code"),
            "detail": (payload or {}).get("detail") or "canon sync projection unavailable",
            **response_meta(meta),
        },
    )


@router.get("/startup")
async def startup_report() -> Dict[str, Any]:
    """Return the immutable startup snapshot; never rerun startup probes."""

    payload, meta = read_query_projection(STARTUP_SNAPSHOT_KIND, STARTUP_SNAPSHOT_KEY)
    ts_ms = int((payload or {}).get("ts_ms") or 0)
    request_id = str((payload or {}).get("request_id") or "startup-projection")
    return {
        "request_id": request_id,
        "timestamp": ts_ms,
        "report": dict(payload or {}),
        **response_meta(meta),
    }


@router.get("/health/env-readiness")
async def env_readiness_report() -> Dict[str, Any]:
    """Settings 分层 readiness 报告（OPT-4）。

    汇总 `host_brain/app/settings/` 下分层 Settings 的字段来源：
    - env：显式通过环境变量设置
    - default：使用代码默认值
    - missing：必填但未设置

    供运维/新同事定位"哪些环境变量已生效、哪些用默认、哪些缺失"。
    """
    from host_brain.app.settings import (
        CapabilityForgeSettings,
        CoreSettings,
        LLMSettings,
        ObservabilitySettings,
        TribeSettings,
        verify_readiness,
    )

    ts_ms = int(time.time() * 1000)
    report = verify_readiness(
        producers=[
            CoreSettings.readiness_fields,
            LLMSettings.readiness_fields,
            TribeSettings.readiness_fields,
            ObservabilitySettings.readiness_fields,
            CapabilityForgeSettings.readiness_fields,
        ]
    )
    return {
        "timestamp": ts_ms,
        **report.to_dict(),
    }
