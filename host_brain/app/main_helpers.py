"""main.py 的私有辅助函数集合。"""

from __future__ import annotations

from typing import Any


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
    if not row:
        return {
            "status": "SKIPPED",
            "reason_code": None,
            "detail": "graph runtime probe not provided",
        }
    status = str(row.get("status") or "SKIPPED").strip().upper()
    reason_code = str(row.get("reason_code") or "").strip() or None
    detail = str(row.get("detail") or ("graph runtime probe not provided" if status == "SKIPPED" else ""))
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
    graph_available = graph_status in {"OK", "SKIPPED"}
    rows.append(
        {
            "component_id": "graph_runtime",
            "service_id": "graph_runtime",
            "component_type": "graph_runtime",
            "available": graph_available,
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
