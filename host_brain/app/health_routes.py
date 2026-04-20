"""健康检查路由模块。

从 fastapi_app.py 拆分，包含 /health, /ready, /health/mcp, /health/canon_sync, /startup, /observability/trace 路由。
"""

from __future__ import annotations

import logging
import os
import time
from json import loads as json_loads
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, status

from host_brain.app.codex_self_heal import (
    build_codex_self_heal_proposal_from_health_report,
    build_codex_self_heal_proposal_from_readiness_report,
)
from host_brain.app.dependencies import collect_dependency_probes, get_ledger, get_obs_metrics_collector
from host_brain.app.main import (
    build_env_readiness_report,
    build_request_observability_report,
    build_service_health_report,
    build_startup_report,
    emit_runtime_bootstrap_logs,
)
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.schemas import (
    HealthResponse,
    MetricsResponse,
    ObservabilityRequest,
    ObservabilityResponse,
    ReadinessResponse,
)
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    STARTUP_MCP_DEGRADED,
)
from host_brain.core.ids import new_request_id
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.canon_sync import detect_world_version_drift
from host_brain.observability.routes import build_obs_metrics_response
from edge_client.rpc.health import build_edge_transport_probe_statuses
_logger = logging.getLogger("Natsume.health_routes")

router = APIRouter()


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
    return build_service_health_report(
        runtime_statuses=_runtime_statuses,
        request_id=req_id,
        ledger=get_ledger(),
        ts_ms=ts_value,
    )


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
    include_self_heal_proposal: bool = Query(
        False,
        description="Attach a Codex self-heal proposal when the health report is not healthy.",
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
        if include_self_heal_proposal:
            proposal = build_codex_self_heal_proposal_from_health_report(
                health_report,
                include_dependency_probes=not skip_dependency_probes,
            )
            if proposal.get("triggered"):
                health_report["self_heal_proposal"] = proposal

        return HealthResponse(
            status=health_report.get("status", "OK"),
            version="0.1.0",
            timestamp=ts_ms,
            details=health_report,
        )
    except Exception as exc:
        _logger.error("Health check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service health check failed",
        )


@router.get("/ready", response_model=ReadinessResponse)
async def environment_readiness(
    include_self_heal_proposal: bool = Query(
        False,
        description="Attach a Codex self-heal proposal when the readiness report is degraded or failed.",
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
        if include_self_heal_proposal:
            proposal = build_codex_self_heal_proposal_from_readiness_report(
                readiness_report,
                include_dependency_probes=True,
            )
            if proposal.get("triggered"):
                readiness_report["self_heal_proposal"] = proposal

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


@router.get("/obs/metrics")
async def obs_metrics(request_id: Optional[str] = None) -> Dict[str, Any]:
    """Metrics endpoint."""
    ts_ms = int(time.time() * 1000)
    payload = build_obs_metrics_response(
        collector=get_obs_metrics_collector(),
        request_id=request_id,
        ts_ms=ts_ms,
    )
    payload["timestamp"] = ts_ms
    return payload


@router.get("/health/mcp", response_model=HealthResponse)
async def mcp_health_check() -> HealthResponse:
    """MCP registry health endpoint."""
    ts_ms = int(time.time() * 1000)
    db_path = str(os.environ.get("HOST_MCP_REGISTRY_DB") or "host_brain/data/mcp_registry.sqlite3").strip()
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
    """World canon sync health endpoint."""
    ts_ms = int(time.time() * 1000)
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    status_text = "OK"
    reason_code = None
    detail = "canon sync healthy"
    drift_count = 0
    drift_sample: list[dict[str, Any]] = []
    try:
        drifts = detect_world_version_drift(world_root=world_root, ledger=get_ledger())
        drift_count = len(drifts)
        if drift_count > 0:
            status_text = "DEGRADED"
            reason_code = CANON_SYNC_FAILED
            detail = "world canon version drift detected"
            drift_sample = [dict(row) for row in drifts[:5]]
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = CANON_SYNC_FAILED
        detail = str(exc)
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "world_root": world_root,
            "drift_count": drift_count,
            "drift_sample": drift_sample,
            "reason_code": reason_code,
            "detail": detail,
        },
    )


@router.post("/observability/trace", response_model=ObservabilityResponse)
async def request_trace(request_body: ObservabilityRequest) -> ObservabilityResponse:
    """Request trace endpoint."""
    ts_ms = int(time.time() * 1000)

    try:
        trace_report = build_request_observability_report(
            request_id=request_body.request_id,
            ledger=get_ledger(),
        )

        return ObservabilityResponse(
            request_id=request_body.request_id,
            trace_status=trace_report.get("trace_status", "UNKNOWN"),
            timeline_total=trace_report.get("timeline_total", 0),
            sources=trace_report.get("sources", {}),
            timeline=trace_report.get("timeline", []),
            timestamp=ts_ms,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        _logger.error("Request trace failed", extra={"request_id": request_body.request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve request trace",
        )


@router.get("/startup")
async def startup_report() -> Dict[str, Any]:
    """Startup report endpoint."""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        dependency_probes = await collect_dependency_probes()
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes=dependency_probes,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        return {
            "request_id": request_id,
            "timestamp": ts_ms,
            "report": report,
        }
    except Exception as exc:
        _logger.error("Startup report failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate startup report",
        )


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
