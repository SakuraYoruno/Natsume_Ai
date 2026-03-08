"""System route handlers extracted from fastapi_app."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import HTTPException, status

from edge_client.rpc.health import build_edge_transport_probe_statuses
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.main import (
    build_env_readiness_report,
    build_request_observability_report,
    build_service_health_report,
    build_startup_report,
    emit_runtime_bootstrap_logs,
)
from host_brain.app.schemas import (
    EdgeEventsRequest,
    EdgeEventsResponse,
    HealthResponse,
    ObservabilityRequest,
    ObservabilityResponse,
    ReadinessResponse,
)
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_SCHEMA_INVALID,
    STARTUP_MCP_DEGRADED,
)
from host_brain.core.ids import new_request_id
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.canon_sync import detect_world_version_drift
from host_brain.obs.routes import build_obs_metrics_response


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            loaded = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _resolve_node_inventory_payload() -> tuple[list[dict[str, Any]], int | None, str | None]:
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


def health_check_response(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> HealthResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        host_result = load_host_config()
        runtime_statuses: dict[str, Any] = {}
        if host_result.success and host_result.config is not None:
            runtime_statuses = build_host_runtime_statuses(host_result.config)
        try:
            edge_available = runtime_statuses.get("edge.rpc.gateway", {}).get("available", True)
            edge_probes = build_edge_transport_probe_statuses(available=bool(edge_available))
            for svc_id, probe in edge_probes.items():
                if svc_id not in runtime_statuses:
                    runtime_statuses[svc_id] = probe
        except Exception as edge_exc:
            logger.warning("Edge transport probe failed (degraded): %s", edge_exc)
        health_report = build_service_health_report(
            runtime_statuses=runtime_statuses,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        return HealthResponse(
            status=health_report.get("status", "OK"),
            version="0.1.0",
            timestamp=ts_ms,
            details=health_report,
        )
    except Exception as exc:
        logger.error("Health check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service health check failed",
        )


def environment_readiness_response(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> ReadinessResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        node_inventory, expected_node_count, inventory_path = _resolve_node_inventory_payload()
        readiness_report = build_env_readiness_report(
            env=str(os.environ.get("Natsume_ENV") or "dev"),
            node_inventory=node_inventory,
            expected_node_count=expected_node_count,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        if inventory_path:
            readiness_report["node_inventory_path"] = inventory_path
        readiness_status = readiness_report.get("readiness_status", "FAILED")
        return ReadinessResponse(
            ready=(readiness_status == "READY"),
            status=readiness_status,
            reason_code=readiness_report.get("reason_code"),
            timestamp=ts_ms,
            summary=readiness_report,
        )
    except Exception as exc:
        logger.error("Readiness check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Environment readiness check failed",
        )


def obs_metrics_payload(
    *,
    collector: Any,
    request_id: str | None,
) -> dict[str, Any]:
    ts_ms = int(time.time() * 1000)
    payload = build_obs_metrics_response(
        collector=collector,
        request_id=request_id,
        ts_ms=ts_ms,
    )
    payload["timestamp"] = ts_ms
    return payload


def mcp_health_check_response() -> HealthResponse:
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


def canon_sync_health_check_response(*, get_ledger: Callable[[], Any]) -> HealthResponse:
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


def request_trace_response(
    *,
    request_body: ObservabilityRequest,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> ObservabilityResponse:
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
        logger.error("Request trace failed", extra={"request_id": request_body.request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve request trace",
        )


def startup_report_payload(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> dict[str, Any]:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes={},
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
        logger.error("Startup report failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate startup report",
        )


def receive_edge_events_response(
    *,
    request_body: EdgeEventsRequest,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> EdgeEventsResponse:
    ts_ms = int(time.time() * 1000)
    raw_events = request_body.events
    if not raw_events:
        return EdgeEventsResponse(
            success=True, total=0, accepted=0, rejected=0,
            detail="empty batch", timestamp=ts_ms,
        )

    required_fields = {"event_type", "ts_ms", "request_id", "session_id"}
    accepted_count = 0
    rejected_indices: list[int] = []
    ledger = get_ledger()
    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            rejected_indices.append(idx)
            continue
        missing = required_fields - set(raw.keys())
        event_type = str(raw.get("event_type") or "").strip()
        request_id = str(raw.get("request_id") or "").strip()
        session_id = str(raw.get("session_id") or "").strip()
        ts_val = raw.get("ts_ms")
        if missing or not event_type or not request_id or not session_id or not isinstance(ts_val, (int, float)):
            rejected_indices.append(idx)
            continue
        try:
            ledger.log_voice_event({
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": str(raw.get("direction") or "EDGE_TO_HOST"),
                "seq": raw.get("seq") if isinstance(raw.get("seq"), int) else None,
                "status": str(raw.get("status") or "RECEIVED"),
                "reason_code": str(raw.get("reason_code") or "").strip() or None,
                "stage": str(raw.get("stage") or "").strip() or None,
                "detail": str(raw.get("detail") or "").strip() or None,
                "payload": raw.get("payload") or {},
                "ts_ms": int(ts_val),
            })
            accepted_count += 1
        except Exception as exc:
            logger.warning("Failed to write edge event %d: %s", idx, exc)
            rejected_indices.append(idx)

    rejected_count = len(rejected_indices)
    total = len(raw_events)
    if rejected_count == 0:
        reason_code = None
        detail = f"all {total} events accepted"
    elif accepted_count == 0:
        reason_code = RPC_EVENT_SCHEMA_INVALID
        detail = f"all {total} events rejected"
    else:
        reason_code = RPC_EVENT_BATCH_PARTIAL_FAIL
        detail = f"accepted {accepted_count}, rejected {rejected_count} of {total}"
    return EdgeEventsResponse(
        success=(rejected_count == 0),
        total=total,
        accepted=accepted_count,
        rejected=rejected_count,
        reason_code=reason_code,
        detail=detail,
        rejected_indices=rejected_indices,
        timestamp=ts_ms,
    )
