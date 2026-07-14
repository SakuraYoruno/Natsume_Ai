"""Host app entry helpers for health, readiness and observability reporting."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from natsume_contracts.edge.service_catalog_v1 import (
    EdgeHealthQuery,
    build_edge_service_catalog_snapshot,
    build_edge_transport_probe_statuses,
)
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.main_helpers import (
    _build_dependency_matrix,
    _build_orchestration_control_coverage,
    _normalize_drill_summary_rows,
    _normalize_graph_runtime_status,
    _pick_config_issue_detail,
    _pick_readiness_status_reason,
)
from host_brain.app.observability import (
    LOGGER_NAME_RUNTIME_MONITOR,
    build_agent_monitoring_profile,
    build_request_observability_report,
    build_runtime_console_profile,
    emit_runtime_bootstrap_logs,
    emit_runtime_dataflow_log,
)
from host_brain.app.webui_aggregation import (
    build_webui_link_topology,
    build_webui_request_detail,
    build_webui_status_distribution,
    build_webui_timeline_aggregation,
    search_webui_requests,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id
from host_brain.core.key_profiles import normalize_key_profiles
from host_brain.core.services import (
    KIND_DEPENDENCY,
    OWNER_HOST,
    S_ENV_READY,
    ServiceHealthReporter,
    ServiceStartupOrchestrator,
    build_default_service_registry,
    build_node_deployment_report,
    build_owner_unavailable_statuses,
    summarize_dependency_rows,
)


def build_service_health_report(
    *,
    runtime_statuses: dict[str, Any] | None = None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build deterministic health output using the default service catalog."""

    registry = build_default_service_registry()
    reporter = ServiceHealthReporter(registry=registry, ledger=ledger)
    req_id = request_id or new_request_id()
    return reporter.build_report(
        request_id=req_id,
        runtime_statuses=runtime_statuses,
        ts_ms=ts_ms,
    )


def build_startup_report(
    *,
    host_env: dict[str, Any] | None = None,
    edge_env: dict[str, Any] | None = None,
    host_local_overrides: dict[str, Any] | None = None,
    edge_local_overrides: dict[str, Any] | None = None,
    edge_health_query: EdgeHealthQuery | None = None,
    runtime_statuses: dict[str, Any] | None = None,
    dependency_probes: dict[str, Any] | None = None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build startup orchestration report with config checks and dependency probes."""

    registry = build_default_service_registry()
    req_id = request_id or new_request_id()
    statuses: dict[str, Any] = {}
    startup_issues: list[dict[str, Any]] = []

    host_result = load_host_config(env=host_env, local_overrides=host_local_overrides)
    if host_result.success and host_result.config is not None:
        statuses.update(build_host_runtime_statuses(host_result.config))
    else:
        host_detail = _pick_config_issue_detail(host_result.issues, fallback="host config invalid")
        startup_issues.append(
            {
                "scope": "host_config",
                "stage": host_result.stage,
                "reason_code": host_result.reason_code,
                "detail": host_detail,
                "field": host_result.issues[0].field if host_result.issues else None,
            }
        )
        statuses.update(
            build_owner_unavailable_statuses(
                registry=registry,
                owner=OWNER_HOST,
                unavailable_kind=KIND_DEPENDENCY,
                detail=f"host config unavailable: {host_result.reason_code}",
            )
        )

    # Edge configuration is owned and evaluated by the Edge process.  Host only
    # consumes a versioned service catalog plus an optional composition-injected
    # health query.  The legacy env parameters remain accepted to avoid an API
    # flag day, but Host deliberately does not inspect or echo their contents.
    _ = (edge_env, edge_local_overrides)
    edge_catalog_snapshot = build_edge_service_catalog_snapshot()
    if edge_health_query is not None:
        try:
            statuses.update(edge_health_query.query().runtime_statuses())
        except Exception as edge_health_exc:
            logging.getLogger("Natsume.app.main").warning(
                "Edge health query failed (degraded): %s",
                edge_health_exc,
            )
            statuses.update(
                build_edge_transport_probe_statuses(
                    available=False,
                    detail=f"edge health query failed: {type(edge_health_exc).__name__}",
                )
            )

    if runtime_statuses:
        statuses.update(runtime_statuses)

    orchestrator = ServiceStartupOrchestrator(registry=registry, ledger=ledger)
    startup_report = orchestrator.build_startup_report(
        request_id=req_id,
        runtime_statuses=statuses,
        dependency_probes=dependency_probes,
        startup_issues=startup_issues,
        ts_ms=ts_ms,
    )
    startup_report["config"] = {
        "host": host_result.as_dict(),
        "edge": edge_catalog_snapshot,
    }
    return startup_report


def build_env_readiness_report(
    *,
    env: str,
    node_inventory: list[dict[str, Any]] | None = None,
    node_runtime_statuses: dict[str, Any] | None = None,
    key_profiles: list[dict[str, Any]] | None = None,
    graph_runtime: dict[str, Any] | None = None,
    drill_summaries: list[dict[str, Any]] | None = None,
    host_env: dict[str, Any] | None = None,
    edge_env: dict[str, Any] | None = None,
    host_local_overrides: dict[str, Any] | None = None,
    edge_local_overrides: dict[str, Any] | None = None,
    edge_health_query: EdgeHealthQuery | None = None,
    runtime_statuses: dict[str, Any] | None = None,
    dependency_probes: dict[str, Any] | None = None,
    expected_node_count: int | None = None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build environment readiness report for external dependency integration."""

    req_id = request_id or new_request_id()
    startup_report = build_startup_report(
        host_env=host_env,
        edge_env=edge_env,
        host_local_overrides=host_local_overrides,
        edge_local_overrides=edge_local_overrides,
        edge_health_query=edge_health_query,
        runtime_statuses=runtime_statuses,
        dependency_probes=dependency_probes,
        request_id=req_id,
        ledger=ledger,
        ts_ms=ts_ms,
    )

    registry = build_default_service_registry()
    required_services = [
        spec.service_id
        for spec in registry.all()
        if bool(spec.required)
    ]
    node_rows = list(node_inventory or [])
    node_report = build_node_deployment_report(
        nodes=node_rows,
        required_services=required_services,
        expected_node_count=(int(expected_node_count) if expected_node_count is not None else (len(node_rows) if node_rows else None)),
        runtime_statuses=node_runtime_statuses,
        request_id=req_id,
        ts_ms=ts_ms,
    )

    normalized_key_profiles = normalize_key_profiles(key_profiles)
    key_profile_summary = {
        "total": len(normalized_key_profiles),
        "active": sum(1 for row in normalized_key_profiles if str(row.get("status") or "").lower() == "active"),
        "rows": normalized_key_profiles,
    }

    graph = _normalize_graph_runtime_status(graph_runtime)
    drill_rows = _normalize_drill_summary_rows(drill_summaries)
    orchestration_control_coverage = _build_orchestration_control_coverage(drill_rows)
    dependency_matrix = _build_dependency_matrix(
        startup_report=startup_report,
        graph_runtime=graph,
    )
    dependency_summary = summarize_dependency_rows(dependency_matrix)
    readiness_status, readiness_reason = _pick_readiness_status_reason(
        startup_report=startup_report,
        node_report=node_report,
        graph_runtime=graph,
        dependency_matrix=dependency_matrix,
    )

    ts_value = int(ts_ms if ts_ms is not None else startup_report.get("ts_ms") or 0)
    return {
        "request_id": req_id,
        "stage": S_ENV_READY,
        "env": str(env or "").strip(),
        "node_count": len(node_rows),
        "key_profiles": key_profile_summary,
        "services": {
            "summary": dict(startup_report.get("summary") or {}),
            "rows": list(startup_report.get("services") or []),
        },
        "graph_runtime": graph,
        "dependency_matrix": dependency_matrix,
        "dependency_summary": dependency_summary,
        "drill_summary": drill_rows,
        "orchestration_control_coverage": orchestration_control_coverage,
        "startup_report": startup_report,
        "node_deployment": node_report,
        "readiness_status": readiness_status,
        "reason_code": readiness_reason,
        "ts_ms": ts_value,
    }


if __name__ == "__main__":
    report = build_startup_report(runtime_statuses={}, dependency_probes={})
    monitor_profile = build_agent_monitoring_profile()
    console_profile = build_runtime_console_profile(
        spawn_console=(str(os.environ.get("Natsume_RUNTIME_MONITOR_CONSOLE") or "").strip().lower() in {"1", "true", "yes", "on"})
    )
    emit_runtime_bootstrap_logs(
        startup_report=report,
        monitoring_profile=monitor_profile,
        console_profile=console_profile,
    )
    _main_logger = logging.getLogger(LOGGER_NAME_RUNTIME_MONITOR)
    _main_logger.info(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
