"""Host app entry helpers for health, readiness and observability reporting."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Any, Mapping

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


LOGGER_NAME_RUNTIME_MONITOR = "Nastume.runtime.monitor"
OBS_LANGSMITH_DISABLED = "OBS_LANGSMITH_DISABLED"
OBS_LANGSMITH_UNAVAILABLE = "OBS_LANGSMITH_UNAVAILABLE"
OBS_LANGSMITH_API_KEY_MISSING = "OBS_LANGSMITH_API_KEY_MISSING"
OBS_CONSOLE_SPAWN_UNSUPPORTED = "OBS_CONSOLE_SPAWN_UNSUPPORTED"
OBS_CONSOLE_SPAWN_FAILED = "OBS_CONSOLE_SPAWN_FAILED"


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


def build_request_observability_report(
    *,
    request_id: str,
    ledger: SQLiteLedger,
) -> dict[str, Any]:
    """Build a read-only timeline report for a single request chain."""

    req_id = str(request_id or "").strip()
    if not req_id:
        raise ValueError("request_id is required")

    trace = ledger.trace_request(req_id)
    timeline: list[dict[str, Any]] = []

    timeline.extend(_to_timeline_rows(trace.get("intent_log") or [], source="intent_log"))
    timeline.extend(_to_timeline_rows(trace.get("governor_log") or [], source="governor_log"))
    timeline.extend(_to_timeline_rows(trace.get("skill_exec_log") or [], source="skill_exec_log"))
    timeline.extend(_to_timeline_rows(trace.get("model_control_log") or [], source="model_control_log"))

    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.error_log, req_id), source="error_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.qa_violation_log, req_id), source="qa_violation_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.channel_io_log, req_id), source="channel_io_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.avatar_action_log, req_id), source="avatar_action_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.clawd_action_log, req_id), source="clawd_action_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.config_snapshot_log, req_id), source="config_snapshot_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.voice_event_log, req_id), source="voice_event_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.retrieval_event_log, req_id), source="retrieval_event_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.vision_event_log, req_id), source="vision_event_log"))
    timeline.extend(_to_timeline_rows(_rows_by_request(ledger.service_health_log, req_id), source="service_health_log"))

    timeline.sort(
        key=lambda row: (
            int(row.get("ts_ms") or 0),
            str(row.get("source") or ""),
            str(row.get("event_id") or ""),
        )
    )

    return {
        "request_id": req_id,
        "trace_status": trace.get("status"),
        "trace_stage": trace.get("stage"),
        "trace_reason_code": trace.get("reason_code"),
        "timeline_total": len(timeline),
        "sources": _summarize_sources(timeline),
        "timeline": timeline,
    }


def build_agent_monitoring_profile(
    *,
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build agent monitoring profile using LangSmith as the preferred backend."""

    merged = dict(os.environ)
    if env:
        merged.update(dict(env))

    tracing_enabled = str(merged.get("LANGSMITH_TRACING") or "").strip().lower() in {"1", "true", "yes", "on"}
    project = str(merged.get("LANGSMITH_PROJECT") or "").strip() or None
    endpoint = str(merged.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").strip()
    api_key = str(merged.get("LANGSMITH_API_KEY") or "").strip()

    if not tracing_enabled:
        return {
            "provider": "langsmith",
            "status": "DISABLED",
            "reason_code": OBS_LANGSMITH_DISABLED,
            "project": project,
            "endpoint": endpoint,
            "tracing_enabled": False,
        }

    try:
        __import__("langsmith")
    except Exception:
        return {
            "provider": "langsmith",
            "status": "DEGRADED",
            "reason_code": OBS_LANGSMITH_UNAVAILABLE,
            "project": project,
            "endpoint": endpoint,
            "tracing_enabled": True,
        }

    if not api_key:
        return {
            "provider": "langsmith",
            "status": "DEGRADED",
            "reason_code": OBS_LANGSMITH_API_KEY_MISSING,
            "project": project,
            "endpoint": endpoint,
            "tracing_enabled": True,
        }

    return {
        "provider": "langsmith",
        "status": "ACTIVE",
        "reason_code": None,
        "project": project,
        "endpoint": endpoint,
        "tracing_enabled": True,
    }


def emit_runtime_dataflow_log(
    *,
    event: str,
    payload: Mapping[str, Any],
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
) -> None:
    """Emit structured dataflow logs for runtime monitoring."""

    target = logger or logging.getLogger(LOGGER_NAME_RUNTIME_MONITOR)
    record = {
        "event": str(event),
        "payload": dict(payload),
    }
    target.log(level, json.dumps(record, ensure_ascii=False, sort_keys=True))


def build_runtime_console_profile(
    *,
    spawn_console: bool = False,
) -> dict[str, Any]:
    """Build runtime console profile; best effort spawn for local development."""

    system_name = platform.system().lower()
    profile = {
        "console_mode": "inline",
        "spawned": False,
        "reason_code": None,
        "platform": system_name,
    }

    if not spawn_console:
        return profile

    try:
        if system_name.startswith("windows"):
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "title Nastume Runtime Monitor && echo runtime monitor attached"])
            profile.update({"console_mode": "spawn", "spawned": True})
            return profile

        if shutil.which("xterm"):
            subprocess.Popen(["xterm", "-T", "Nastume Runtime Monitor", "-e", "bash", "-lc", "echo runtime monitor attached; exec bash"])
            profile.update({"console_mode": "spawn", "spawned": True})
            return profile

        profile["reason_code"] = OBS_CONSOLE_SPAWN_UNSUPPORTED
        return profile
    except Exception:
        profile["reason_code"] = OBS_CONSOLE_SPAWN_FAILED
        return profile


def emit_runtime_bootstrap_logs(
    *,
    startup_report: Mapping[str, Any],
    readiness_report: Mapping[str, Any] | None = None,
    monitoring_profile: Mapping[str, Any] | None = None,
    console_profile: Mapping[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Print startup/runtime details and dataflow transitions to monitor logger."""

    target = logger or logging.getLogger(LOGGER_NAME_RUNTIME_MONITOR)
    if not target.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    emit_runtime_dataflow_log(event="runtime.bootstrap.startup_report", payload=dict(startup_report), logger=target)
    if readiness_report is not None:
        emit_runtime_dataflow_log(event="runtime.bootstrap.readiness_report", payload=dict(readiness_report), logger=target)
    if monitoring_profile is not None:
        emit_runtime_dataflow_log(event="runtime.bootstrap.agent_monitoring", payload=dict(monitoring_profile), logger=target)
    if console_profile is not None:
        emit_runtime_dataflow_log(event="runtime.bootstrap.console_profile", payload=dict(console_profile), logger=target)



def _pick_config_issue_detail(issues: tuple[Any, ...], *, fallback: str) -> str:
    if not issues:
        return fallback
    first = issues[0]
    detail = str(getattr(first, "detail", "")).strip()
    return detail or fallback


def _rows_by_request(rows: list[dict[str, Any]], request_id: str) -> list[dict[str, Any]]:
    req_id = str(request_id)
    return [dict(row) for row in rows if str(row.get("request_id") or "") == req_id]


def _to_timeline_rows(rows: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        normalized.append(
            {
                "event_id": f"{source}:{idx}",
                "source": source,
                "stage": str(row.get("stage") or "").strip() or None,
                "status": _pick_status(row),
                "reason_code": str(row.get("reason_code") or "").strip() or None,
                "intent_id": str(row.get("intent_id") or "").strip() or None,
                "ts_ms": int(row.get("ts_ms") or 0),
                "detail": _pick_detail(row),
            }
        )
    return normalized


def _pick_status(row: dict[str, Any]) -> str | None:
    for key in ("status", "decision", "result_status"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _pick_detail(row: dict[str, Any]) -> str | None:
    for key in ("detail", "result_code", "event_type", "op_kind"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _summarize_sources(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "")
        if not source:
            continue
        summary[source] = int(summary.get(source, 0)) + 1
    return dict(sorted(summary.items(), key=lambda item: item[0]))


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
                "stage": str(raw.get("stage") or "").strip(),
                "status": status,
                "reason_code_chain": reason_chain,
                "replay_ref": replay_ref,
                "model_profile_id": (str(raw.get("model_profile_id") or "").strip() or None),
            }
        )
    rows.sort(key=lambda row: str(row["scenario_id"]))
    return rows


def _build_orchestration_control_coverage(drill_rows: list[dict[str, Any]]) -> dict[str, Any]:
    required_scenarios = [
        "drill.orch.missing_profile",
        "drill.orch.selector_no_candidate",
        "drill.orch.param_drift",
        "drill.orch.approval_token_missing",
        "drill.dependency.provider_down",
    ]
    scenario_ids = {str(row.get("scenario_id") or "").strip() for row in drill_rows if str(row.get("scenario_id") or "").strip()}
    covered = [scenario for scenario in required_scenarios if scenario in scenario_ids]
    missing = [scenario for scenario in required_scenarios if scenario not in scenario_ids]
    status = "COVERED" if not missing else "PARTIAL"
    return {
        "status": status,
        "required_total": len(required_scenarios),
        "covered_count": len(covered),
        "missing_scenarios": missing,
        "scenario_total": len(scenario_ids),
    }


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


# ---------------------------------------------------------------------------
# Web UI 辅助函数（TASK-0066: 链路可视化面板数据底座）
# ---------------------------------------------------------------------------

def build_webui_timeline_aggregation(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    interval: str = "1h",
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按时间区间聚合请求数/成功率/平均耗时，用于时间线图表。"""

    _filters = dict(filters or {})
    interval_ms = _parse_interval_ms(interval)

    # TASK-0078: 使用 fetch_by_time_range 替代全量加载，按时间窗口查询
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    # 按状态/类型过滤
    if "status" in _filters:
        allowed = set(_filters["status"])
        all_rows = [r for r in all_rows if str(r.get("status") or r.get("decision") or "").upper() in allowed]
    if "request_type" in _filters:
        allowed = set(_filters["request_type"])
        all_rows = [r for r in all_rows if str(r.get("type") or "unknown") in allowed]

    # 构建区间桶
    intervals: list[dict[str, Any]] = []
    bucket_start = start_time
    while bucket_start < end_time:
        bucket_end = min(bucket_start + interval_ms, end_time)
        bucket_rows = [r for r in all_rows if bucket_start <= int(r.get("ts_ms") or 0) < bucket_end]
        success_count = sum(1 for r in bucket_rows if str(r.get("decision") or "").upper() in {"APPROVE", ""})
        total = len(bucket_rows)
        durations = [int(r.get("duration_ms") or 0) for r in bucket_rows if int(r.get("duration_ms") or 0) > 0]
        avg_dur = (sum(durations) / len(durations)) if durations else 0.0

        intervals.append({
            "start_time": bucket_start,
            "end_time": bucket_end,
            "total": total,
            "success": success_count,
            "failure": total - success_count,
            "success_rate": round(success_count / total, 4) if total else 0.0,
            "avg_duration": round(avg_dur, 2),
        })
        bucket_start = bucket_end

    total_all = len(all_rows)
    success_all = sum(iv["success"] for iv in intervals)
    dur_all = [int(r.get("duration_ms") or 0) for r in all_rows if int(r.get("duration_ms") or 0) > 0]

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "interval": interval,
        "intervals": intervals,
        "total_requests": total_all,
        "success_rate": round(success_all / total_all, 4) if total_all else 0.0,
        "avg_duration": round((sum(dur_all) / len(dur_all)) if dur_all else 0.0, 2),
        "timestamp": int(time.time() * 1000),
    }


def build_webui_status_distribution(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """统计时间范围内各状态（APPROVE/REJECT/DEGRADED等）的分布。"""

    _filters = dict(filters or {})
    # TASK-0078: 使用 fetch_by_time_range 替代全量加载
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    if "request_type" in _filters:
        allowed = set(_filters["request_type"])
        all_rows = [r for r in all_rows if str(r.get("type") or "unknown") in allowed]

    status_counts: dict[str, int] = {}
    for row in all_rows:
        st = str(row.get("decision") or row.get("status") or "UNKNOWN").upper()
        status_counts[st] = status_counts.get(st, 0) + 1

    total = len(all_rows)
    distribution: dict[str, float] = {}
    for st, cnt in status_counts.items():
        distribution[st] = round(cnt / total * 100, 2) if total else 0.0

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "status_counts": dict(sorted(status_counts.items())),
        "total_requests": total,
        "distribution_percentage": dict(sorted(distribution.items())),
        "timestamp": int(time.time() * 1000),
    }


def build_webui_request_detail(
    *,
    ledger: SQLiteLedger,
    request_id: str,
) -> dict[str, Any] | None:
    """获取单个请求的详情（含追踪数据），用于面板的请求详情视图。"""

    req_id = str(request_id or "").strip()
    if not req_id:
        return None

    try:
        trace = ledger.trace_request(req_id)
    except Exception:
        return None

    if not trace:
        return None

    intent_rows = trace.get("intent_log") or []
    governor_rows = trace.get("governor_log") or []

    # 若无任何 intent 或 governor 记录，视为请求不存在
    if not intent_rows and not governor_rows:
        return None

    ts_ms = int(intent_rows[0].get("ts_ms", 0)) if intent_rows else 0
    decision = str(governor_rows[0].get("decision", "UNKNOWN")).upper() if governor_rows else "UNKNOWN"
    reason_code = str(governor_rows[0].get("reason_code", "")).strip() if governor_rows else None
    duration = int(intent_rows[0].get("duration_ms", 0)) if intent_rows else 0
    req_type = str(intent_rows[0].get("type", "unknown")) if intent_rows else "unknown"

    return {
        "request_id": req_id,
        "timestamp": ts_ms,
        "status": decision,
        "duration": float(duration),
        "request_type": req_type,
        "user_id": str(intent_rows[0].get("user_id", "")).strip() or None if intent_rows else None,
        "error_message": reason_code if decision == "REJECT" else None,
        "trace_data": trace,
        "query_timestamp": int(time.time() * 1000),
    }


def build_webui_link_topology(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    min_connections: int = 1,
) -> dict[str, Any]:
    """构建链路拓扑图（节点=审计源，边=同一 request_id 串联），用于拓扑可视化。"""

    # 收集所有审计源的记录
    source_names = [
        "intent_log", "governor_log", "skill_exec_log",
        "channel_io_log", "avatar_action_log", "voice_event_log",
        "retrieval_event_log", "vision_event_log", "service_health_log",
    ]

    # 收集 request_id -> 经过的源集合
    request_sources: dict[str, set[str]] = {}
    for source in source_names:
        rows = getattr(ledger, source, [])
        for row in rows:
            ts = int(row.get("ts_ms") or 0)
            if start_time <= ts <= end_time:
                rid = str(row.get("request_id") or "")
                if rid:
                    request_sources.setdefault(rid, set()).add(source)

    # 构建节点
    all_sources_used: set[str] = set()
    for sources in request_sources.values():
        all_sources_used.update(sources)

    nodes = [
        {"id": s, "label": s.replace("_log", "").replace("_", " ").title(), "type": "audit_source"}
        for s in sorted(all_sources_used)
    ]

    # 构建边（同一请求经过的两个审计源之间的关联）
    edge_counts: dict[tuple[str, str], int] = {}
    for sources in request_sources.values():
        sorted_sources = sorted(sources)
        for i in range(len(sorted_sources)):
            for j in range(i + 1, len(sorted_sources)):
                key = (sorted_sources[i], sorted_sources[j])
                edge_counts[key] = edge_counts.get(key, 0) + 1

    edges = [
        {"source": k[0], "target": k[1], "weight": v, "label": f"{v} requests"}
        for k, v in sorted(edge_counts.items())
        if v >= min_connections
    ]

    # 构建集群（按前缀分组）
    clusters: list[dict[str, Any]] = []
    cluster_map: dict[str, list[str]] = {}
    for s in all_sources_used:
        prefix = s.split("_")[0] if "_" in s else s
        cluster_map.setdefault(prefix, []).append(s)
    for prefix, members in sorted(cluster_map.items()):
        clusters.append({"id": prefix, "label": prefix.title(), "members": sorted(members)})

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "timestamp": int(time.time() * 1000),
    }


def search_webui_requests(
    *,
    ledger: SQLiteLedger,
    query: str,
    start_time: int,
    end_time: int,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """按关键词搜索请求记录（request_id/reason_code/type 模糊匹配）。"""

    keyword = str(query or "").strip().lower()
    # TASK-0078: 使用 fetch_by_time_range 替代全量加载
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    if keyword:
        matched = []
        for row in all_rows:
            searchable = " ".join(str(v) for v in row.values()).lower()
            if keyword in searchable:
                matched.append(row)
    else:
        matched = all_rows

    total = len(matched)
    offset = (page - 1) * page_size
    page_rows = matched[offset: offset + page_size]

    results = []
    for row in page_rows:
        results.append({
            "request_id": str(row.get("request_id") or ""),
            "timestamp": int(row.get("ts_ms") or 0),
            "status": str(row.get("decision") or row.get("status") or "UNKNOWN").upper(),
            "type": str(row.get("type") or "unknown"),
            "reason_code": str(row.get("reason_code") or "").strip() or None,
        })

    return {
        "results": results,
        "total": total,
    }


def _parse_interval_ms(interval: str) -> int:
    """将间隔字符串（1m/5m/15m/1h/1d）解析为毫秒。"""
    interval = str(interval).strip().lower()
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for suffix, mult in multipliers.items():
        if interval.endswith(suffix):
            try:
                return int(interval[:-1]) * mult
            except ValueError:
                pass
    return 3_600_000  # 默认 1h


if __name__ == "__main__":
    report = build_startup_report(runtime_statuses={}, dependency_probes={})
    monitor_profile = build_agent_monitoring_profile()
    console_profile = build_runtime_console_profile(
        spawn_console=(str(os.environ.get("Nastume_RUNTIME_MONITOR_CONSOLE") or "").strip().lower() in {"1", "true", "yes", "on"})
    )
    emit_runtime_bootstrap_logs(
        startup_report=report,
        monitoring_profile=monitor_profile,
        console_profile=console_profile,
    )
    _main_logger = logging.getLogger(LOGGER_NAME_RUNTIME_MONITOR)
    _main_logger.info(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
