"""Host app entry helpers for service health reporting."""

from __future__ import annotations

import json
from typing import Any

from edge_client.config import build_edge_runtime_statuses, load_edge_config
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id
from host_brain.core.key_profiles import normalize_key_profiles
from host_brain.core.services import (
    KIND_DEPENDENCY,
    OWNER_EDGE,
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

    edge_result = load_edge_config(env=edge_env, local_overrides=edge_local_overrides)
    if edge_result.success and edge_result.config is not None:
        statuses.update(build_edge_runtime_statuses(edge_result.config))
    else:
        edge_detail = _pick_config_issue_detail(edge_result.issues, fallback="edge config invalid")
        startup_issues.append(
            {
                "scope": "edge_config",
                "stage": edge_result.stage,
                "reason_code": edge_result.reason_code,
                "detail": edge_detail,
                "field": edge_result.issues[0].field if edge_result.issues else None,
            }
        )
        statuses.update(
            build_owner_unavailable_statuses(
                registry=registry,
                owner=OWNER_EDGE,
                unavailable_kind=KIND_DEPENDENCY,
                detail=f"edge config unavailable: {edge_result.reason_code}",
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
        "edge": edge_result.as_dict(),
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
        "startup_report": startup_report,
        "node_deployment": node_report,
        "readiness_status": readiness_status,
        "reason_code": readiness_reason,
        "ts_ms": ts_value,
    }


def _pick_config_issue_detail(issues: tuple[Any, ...], *, fallback: str) -> str:
    if not issues:
        return fallback
    first = issues[0]
    detail = str(getattr(first, "detail", "")).strip()
    return detail or fallback


def _normalize_graph_runtime_status(raw: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(raw or {})
    status = str(row.get("status") or "UNKNOWN").strip().upper()
    reason_code = str(row.get("reason_code") or "").strip() or None
    detail = str(row.get("detail") or "")
    return {
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
    }


def _normalize_drill_summary_rows(raw_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_rows or []:
        if not isinstance(raw, dict):
            continue
        scenario_id = str(raw.get("scenario_id") or "").strip()
        status = str(raw.get("status") or "").strip().upper()
        replay_ref = str(raw.get("replay_ref") or "").strip()
        reason_chain = [
            str(item).strip()
            for item in (raw.get("reason_code_chain") or [])
            if str(item).strip()
        ]
        if not scenario_id:
            continue
        rows.append(
            {
                "scenario_id": scenario_id,
                "status": status,
                "reason_code_chain": reason_chain,
                "replay_ref": replay_ref,
            }
        )
    rows.sort(key=lambda row: str(row["scenario_id"]))
    return rows


def _build_dependency_matrix(
    *,
    startup_report: dict[str, Any],
    graph_runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in startup_report.get("dependency_probes") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "component_id": str(row.get("service_id") or ""),
                "service_id": str(row.get("service_id") or ""),
                "component_type": "service_probe",
                "available": bool(row.get("available", True)),
                "reason_code": (str(row.get("reason_code") or "").strip() or None),
                "detail": str(row.get("detail") or ""),
            }
        )

    graph_status = str(graph_runtime.get("status") or "").upper()
    rows.append(
        {
            "component_id": "graph_runtime",
            "service_id": "graph_runtime",
            "component_type": "graph_runtime",
            "available": (graph_status == "OK"),
            "reason_code": graph_runtime.get("reason_code"),
            "detail": str(graph_runtime.get("detail") or ""),
        }
    )
    rows.sort(
        key=lambda row: (
            str(row.get("component_type") or ""),
            str(row.get("component_id") or ""),
        )
    )
    return rows


def _pick_readiness_status_reason(
    *,
    startup_report: dict[str, Any],
    node_report: dict[str, Any],
    graph_runtime: dict[str, Any],
    dependency_matrix: list[dict[str, Any]],
) -> tuple[str, str | None]:
    node_status = str(node_report.get("status") or "").upper()
    graph_status = str(graph_runtime.get("status") or "").upper()
    if node_status == "FAILED":
        return "FAILED", str(node_report.get("reason_code") or "").strip() or None
    if graph_status == "FAILED":
        return "FAILED", str(graph_runtime.get("reason_code") or "").strip() or None

    required_unavailable = int(startup_report.get("summary", {}).get("required_unavailable", 0))
    if required_unavailable > 0:
        return "DEGRADED", str(startup_report.get("reason_code") or "").strip() or None
    if graph_status == "DEGRADED":
        return "DEGRADED", str(graph_runtime.get("reason_code") or "").strip() or None
    if str(node_report.get("status") or "").upper() == "DEGRADED":
        return "DEGRADED", str(node_report.get("reason_code") or "").strip() or None
    for row in dependency_matrix:
        if not bool(row.get("available", True)):
            reason_code = str(row.get("reason_code") or "").strip()
            return "DEGRADED", (reason_code or None)
    return "READY", None


if __name__ == "__main__":
    report = build_startup_report(runtime_statuses={}, dependency_probes={})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
