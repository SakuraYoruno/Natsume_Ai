"""Generate long-term KPI dashboard from audit ledger.

The dashboard summarizes four dimensions for a rolling time window:
  - quality
  - latency
  - cost
  - availability

It writes JSON + Markdown artifacts for operational tracking.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs" / "60_trace"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger

CLOUD_PROVIDERS = {"gemini", "openai", "anthropic", "azure_openai", "xai", "groq", "together"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).isoformat()


def _coerce_non_negative_float(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(fallback)
    if value < 0:
        return 0.0
    return float(value)


def _coerce_pct(raw: Any, fallback: float) -> float:
    value = _coerce_non_negative_float(raw, fallback)
    if value > 100:
        return 100.0
    return float(value)


def _pct(count: int, total: int) -> float:
    if int(total) <= 0:
        return 0.0
    return round((float(count) / float(total)) * 100.0, 2)


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(len(ordered) * pct), len(ordered) - 1)
    return int(ordered[index])


def _default_output_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        DOCS_ROOT / f"LONGTERM_METRIC_DASHBOARD_{stamp}.json",
        DOCS_ROOT / f"LONGTERM_METRIC_DASHBOARD_{stamp}.md",
    )


def _resolve_output_path(raw: str, *, fallback: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return fallback
    out = Path(text)
    if not out.is_absolute():
        out = ROOT / out
    return out


def _fetch_all_rows(
    ledger: SQLiteLedger,
    *,
    table: str,
    start_ms: int,
    end_ms: int,
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        chunk = ledger.fetch_by_time_range(
            table,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=int(batch_size),
            offset=int(offset),
        )
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < int(batch_size):
            break
        offset += int(batch_size)
    return rows


def _is_failure_status(status: Any) -> bool:
    normalized = str(status or "").strip().upper()
    return normalized in {"FAILED", "ERROR", "REJECTED", "TIMEOUT"}


def _is_retrieval_degraded(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().upper()
    if status == "DEGRADED":
        return True
    payload = row.get("payload") or {}
    if isinstance(payload, dict) and bool(payload.get("degraded")):
        return True
    return False


def _classify_model_side(*, provider: Any, model_id: Any) -> str:
    provider_text = str(provider or "").strip().lower()
    model_text = str(model_id or "").strip().lower()
    if provider_text in CLOUD_PROVIDERS:
        return "cloud"
    if "edge" in provider_text or "fallback" in provider_text:
        return "edge"
    if "gemini" in model_text or "gpt" in model_text or "claude" in model_text:
        return "cloud"
    if "edge" in model_text or "3b" in model_text:
        return "edge"
    return "host"


def _summarize_quality(
    *,
    governor_rows: list[dict[str, Any]],
    retrieval_rows: list[dict[str, Any]],
    qa_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    governor_total = len(governor_rows)
    governor_approved = sum(1 for row in governor_rows if str(row.get("decision") or "").upper() == "APPROVE")
    governor_rejected = sum(1 for row in governor_rows if str(row.get("decision") or "").upper() == "REJECT")
    governor_degraded = sum(1 for row in governor_rows if str(row.get("decision") or "").upper() == "DEGRADED")
    emotion_gate_blocked = sum(
        1 for row in governor_rows if "EMOTION_GATE" in str(row.get("reason_code") or "").strip().upper()
    )

    retrieval_total = len(retrieval_rows)
    retrieval_degraded = sum(1 for row in retrieval_rows if _is_retrieval_degraded(row))
    retrieval_failed = sum(1 for row in retrieval_rows if _is_failure_status(row.get("status")))
    retrieval_reason_counter: Counter[str] = Counter()
    for row in retrieval_rows:
        reason_code = str(row.get("reason_code") or "").strip()
        if reason_code:
            retrieval_reason_counter[reason_code] += 1

    qa_total = len(qa_rows)

    return {
        "governor": {
            "total": governor_total,
            "approved": governor_approved,
            "rejected": governor_rejected,
            "degraded": governor_degraded,
            "approve_rate_pct": _pct(governor_approved, governor_total),
            "reject_rate_pct": _pct(governor_rejected, governor_total),
            "degraded_rate_pct": _pct(governor_degraded, governor_total),
            "emotion_gate_blocked_count": emotion_gate_blocked,
        },
        "retrieval": {
            "total": retrieval_total,
            "degraded_count": retrieval_degraded,
            "failure_count": retrieval_failed,
            "degraded_rate_pct": _pct(retrieval_degraded, retrieval_total),
            "failure_rate_pct": _pct(retrieval_failed, retrieval_total),
            "top_reason_codes": [
                {"reason_code": code, "count": count}
                for code, count in retrieval_reason_counter.most_common(10)
            ],
        },
        "qa": {
            "violation_count": qa_total,
        },
    }


def _summarize_latency(
    *,
    retrieval_rows: list[dict[str, Any]],
    llm_node_rows: list[dict[str, Any]],
    graph_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_latencies: list[int] = []
    for row in retrieval_rows:
        payload = row.get("payload") or {}
        if isinstance(payload, dict):
            latency = payload.get("latency_ms")
            if isinstance(latency, (int, float)) and latency >= 0:
                retrieval_latencies.append(int(latency))

    llm_latencies: list[int] = []
    for row in llm_node_rows:
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            llm_latencies.append(int(latency))

    graph_latencies: list[int] = []
    for row in graph_rows:
        latency = row.get("total_latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            graph_latencies.append(int(latency))

    return {
        "retrieval_latency_ms": {
            "sample_count": len(retrieval_latencies),
            "p50": _percentile(retrieval_latencies, 0.50),
            "p95": _percentile(retrieval_latencies, 0.95),
        },
        "llm_node_latency_ms": {
            "sample_count": len(llm_latencies),
            "p50": _percentile(llm_latencies, 0.50),
            "p95": _percentile(llm_latencies, 0.95),
        },
        "graph_total_latency_ms": {
            "sample_count": len(graph_latencies),
            "p50": _percentile(graph_latencies, 0.50),
            "p95": _percentile(graph_latencies, 0.95),
        },
    }


def _summarize_cost(
    *,
    llm_node_rows: list[dict[str, Any]],
    llm_route_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    side_counter: Counter[str] = Counter()
    for row in llm_node_rows:
        side = _classify_model_side(provider=row.get("provider"), model_id=row.get("model_id"))
        side_counter[side] += 1

    total_calls = int(sum(side_counter.values()))
    cloud_calls = int(side_counter.get("cloud", 0))
    edge_calls = int(side_counter.get("edge", 0))
    host_calls = int(side_counter.get("host", 0))

    fallback_used_count = sum(1 for row in llm_route_rows if bool(row.get("fallback_used")))
    route_reason_counter: Counter[str] = Counter()
    for row in llm_route_rows:
        reason = str(row.get("reason") or "").strip()
        if reason:
            route_reason_counter[reason] += 1

    return {
        "llm_call_distribution": {
            "total_calls": total_calls,
            "cloud_calls": cloud_calls,
            "edge_calls": edge_calls,
            "host_calls": host_calls,
            "cloud_call_ratio_pct": _pct(cloud_calls, total_calls),
            "edge_call_ratio_pct": _pct(edge_calls, total_calls),
            "host_call_ratio_pct": _pct(host_calls, total_calls),
        },
        "routing": {
            "route_log_count": len(llm_route_rows),
            "fallback_used_count": fallback_used_count,
            "fallback_used_rate_pct": _pct(fallback_used_count, len(llm_route_rows)),
            "top_reasons": [
                {"reason": reason, "count": count}
                for reason, count in route_reason_counter.most_common(10)
            ],
        },
    }


def _summarize_availability(
    *,
    service_health_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    llm_node_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    required_rows = [row for row in service_health_rows if int(row.get("required") or 0) == 1]
    required_up = sum(1 for row in required_rows if str(row.get("status") or "").strip().upper() == "UP")
    llm_degraded = sum(1 for row in llm_node_rows if str(row.get("status") or "").strip().upper() != "OK")
    return {
        "service_health": {
            "total": len(service_health_rows),
            "required_total": len(required_rows),
            "required_up": required_up,
            "required_up_rate_pct": _pct(required_up, len(required_rows)),
        },
        "errors": {
            "error_count": len(error_rows),
        },
        "llm_runtime": {
            "total_node_calls": len(llm_node_rows),
            "degraded_node_calls": llm_degraded,
            "degraded_rate_pct": _pct(llm_degraded, len(llm_node_rows)),
        },
    }


def _evaluate_alerts(
    *,
    quality: dict[str, Any],
    latency: dict[str, Any],
    cost: dict[str, Any],
    availability: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    governor_total = int(dict(quality.get("governor") or {}).get("total") or 0)
    approve_rate = float(dict(quality.get("governor") or {}).get("approve_rate_pct") or 0.0)
    if governor_total > 0 and approve_rate < float(thresholds["governor_approve_rate_min_pct"]):
        alerts.append(
            {
                "code": "QUALITY_GOVERNOR_APPROVE_RATE_LOW",
                "severity": "WARN",
                "observed_value": approve_rate,
                "threshold_value": float(thresholds["governor_approve_rate_min_pct"]),
            }
        )

    retrieval_total = int(dict(quality.get("retrieval") or {}).get("total") or 0)
    retrieval_failure_rate = float(dict(quality.get("retrieval") or {}).get("failure_rate_pct") or 0.0)
    if retrieval_total > 0 and retrieval_failure_rate > float(thresholds["retrieval_failure_rate_max_pct"]):
        alerts.append(
            {
                "code": "QUALITY_RETRIEVAL_FAILURE_RATE_HIGH",
                "severity": "WARN",
                "observed_value": retrieval_failure_rate,
                "threshold_value": float(thresholds["retrieval_failure_rate_max_pct"]),
            }
        )

    graph_p95 = dict(latency.get("graph_total_latency_ms") or {}).get("p95")
    if isinstance(graph_p95, int) and graph_p95 > int(thresholds["graph_p95_latency_max_ms"]):
        alerts.append(
            {
                "code": "LATENCY_GRAPH_P95_HIGH",
                "severity": "WARN",
                "observed_value": graph_p95,
                "threshold_value": int(thresholds["graph_p95_latency_max_ms"]),
            }
        )

    total_llm_calls = int(dict(cost.get("llm_call_distribution") or {}).get("total_calls") or 0)
    cloud_ratio = float(
        dict(cost.get("llm_call_distribution") or {}).get("cloud_call_ratio_pct") or 0.0
    )
    if total_llm_calls > 0 and cloud_ratio > float(thresholds["cloud_call_ratio_max_pct"]):
        alerts.append(
            {
                "code": "COST_CLOUD_CALL_RATIO_HIGH",
                "severity": "WARN",
                "observed_value": cloud_ratio,
                "threshold_value": float(thresholds["cloud_call_ratio_max_pct"]),
            }
        )

    required_total = int(dict(availability.get("service_health") or {}).get("required_total") or 0)
    required_up_rate = float(
        dict(availability.get("service_health") or {}).get("required_up_rate_pct") or 0.0
    )
    if required_total > 0 and required_up_rate < float(thresholds["required_up_rate_min_pct"]):
        alerts.append(
            {
                "code": "AVAILABILITY_REQUIRED_UP_RATE_LOW",
                "severity": "WARN",
                "observed_value": required_up_rate,
                "threshold_value": float(thresholds["required_up_rate_min_pct"]),
            }
        )

    return alerts


def _build_markdown(report: dict[str, Any]) -> str:
    window = dict(report.get("window") or {})
    summary = dict(report.get("summary") or {})
    quality = dict(report.get("quality") or {})
    latency = dict(report.get("latency") or {})
    cost = dict(report.get("cost") or {})
    availability = dict(report.get("availability") or {})
    alerts = list((report.get("alerts") or {}).get("active") or [])

    alert_lines = ["- none"]
    if alerts:
        alert_lines = [
            (
                f"- `{str(item.get('code') or '')}` "
                f"(observed={item.get('observed_value')}, threshold={item.get('threshold_value')})"
            )
            for item in alerts
            if isinstance(item, dict)
        ] or ["- none"]

    lines = [
        "# Long-Term Metric Dashboard",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_start: {window.get('start_iso')}",
        f"- window_end: {window.get('end_iso')}",
        f"- days: {window.get('days')}",
        f"- status: {summary.get('status')}",
        f"- alert_count: {summary.get('alert_count')}",
        "",
        "## Quality",
        "",
        f"- governor_approve_rate_pct: {dict(quality.get('governor') or {}).get('approve_rate_pct')}",
        f"- retrieval_failure_rate_pct: {dict(quality.get('retrieval') or {}).get('failure_rate_pct')}",
        f"- qa_violation_count: {dict(quality.get('qa') or {}).get('violation_count')}",
        "",
        "## Latency",
        "",
        f"- retrieval_p95_ms: {dict(latency.get('retrieval_latency_ms') or {}).get('p95')}",
        f"- llm_node_p95_ms: {dict(latency.get('llm_node_latency_ms') or {}).get('p95')}",
        f"- graph_total_p95_ms: {dict(latency.get('graph_total_latency_ms') or {}).get('p95')}",
        "",
        "## Cost",
        "",
        f"- cloud_call_ratio_pct: {dict(cost.get('llm_call_distribution') or {}).get('cloud_call_ratio_pct')}",
        f"- fallback_used_rate_pct: {dict(cost.get('routing') or {}).get('fallback_used_rate_pct')}",
        "",
        "## Availability",
        "",
        f"- required_up_rate_pct: {dict(availability.get('service_health') or {}).get('required_up_rate_pct')}",
        f"- error_count: {dict(availability.get('errors') or {}).get('error_count')}",
        f"- llm_degraded_rate_pct: {dict(availability.get('llm_runtime') or {}).get('degraded_rate_pct')}",
        "",
        "## Active Alerts",
        "",
        *alert_lines,
    ]
    return "\n".join(lines) + "\n"


def _write_reports(report: dict[str, Any], *, output_json: Path, output_md: Path) -> tuple[Path, Path]:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(_build_markdown(report), encoding="utf-8")
    return output_json, output_md


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate long-term KPI dashboard")
    parser.add_argument("--ledger-db", default="host_brain/data/audit.sqlite3")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threshold-governor-approve-rate-min-pct", type=float, default=80.0)
    parser.add_argument("--threshold-retrieval-failure-rate-max-pct", type=float, default=20.0)
    parser.add_argument("--threshold-graph-p95-latency-max-ms", type=float, default=4000.0)
    parser.add_argument("--threshold-cloud-call-ratio-max-pct", type=float, default=70.0)
    parser.add_argument("--threshold-required-up-rate-min-pct", type=float, default=99.0)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    days = max(1, int(args.days))
    end_ms = _now_ms()
    start_ms = int(end_ms - (days * 24 * 60 * 60 * 1000))
    thresholds = {
        "governor_approve_rate_min_pct": _coerce_pct(args.threshold_governor_approve_rate_min_pct, 80.0),
        "retrieval_failure_rate_max_pct": _coerce_pct(args.threshold_retrieval_failure_rate_max_pct, 20.0),
        "graph_p95_latency_max_ms": _coerce_non_negative_float(args.threshold_graph_p95_latency_max_ms, 4000.0),
        "cloud_call_ratio_max_pct": _coerce_pct(args.threshold_cloud_call_ratio_max_pct, 70.0),
        "required_up_rate_min_pct": _coerce_pct(args.threshold_required_up_rate_min_pct, 99.0),
    }

    ledger = SQLiteLedger(db_path=str(args.ledger_db))
    try:
        governor_rows = _fetch_all_rows(ledger, table="governor_log", start_ms=start_ms, end_ms=end_ms)
        retrieval_rows = _fetch_all_rows(ledger, table="retrieval_event_log", start_ms=start_ms, end_ms=end_ms)
        qa_rows = _fetch_all_rows(ledger, table="qa_violation_log", start_ms=start_ms, end_ms=end_ms)
        llm_node_rows = _fetch_all_rows(ledger, table="llm_node_call_log", start_ms=start_ms, end_ms=end_ms)
        llm_route_rows = _fetch_all_rows(ledger, table="llm_route_log", start_ms=start_ms, end_ms=end_ms)
        graph_rows = _fetch_all_rows(ledger, table="graph_run_trace", start_ms=start_ms, end_ms=end_ms)
        service_health_rows = _fetch_all_rows(ledger, table="service_health_log", start_ms=start_ms, end_ms=end_ms)
        error_rows = _fetch_all_rows(ledger, table="error_log", start_ms=start_ms, end_ms=end_ms)
    finally:
        ledger.close()

    quality = _summarize_quality(
        governor_rows=governor_rows,
        retrieval_rows=retrieval_rows,
        qa_rows=qa_rows,
    )
    latency = _summarize_latency(
        retrieval_rows=retrieval_rows,
        llm_node_rows=llm_node_rows,
        graph_rows=graph_rows,
    )
    cost = _summarize_cost(
        llm_node_rows=llm_node_rows,
        llm_route_rows=llm_route_rows,
    )
    availability = _summarize_availability(
        service_health_rows=service_health_rows,
        error_rows=error_rows,
        llm_node_rows=llm_node_rows,
    )
    active_alerts = _evaluate_alerts(
        quality=quality,
        latency=latency,
        cost=cost,
        availability=availability,
        thresholds=thresholds,
    )

    status = "WARN" if active_alerts else "OK"
    report = {
        "version": "longterm_metric_dashboard.v1",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "days": days,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_iso": _iso_utc(start_ms),
            "end_iso": _iso_utc(end_ms),
        },
        "thresholds": thresholds,
        "quality": quality,
        "latency": latency,
        "cost": cost,
        "availability": availability,
        "alerts": {"active": active_alerts},
        "summary": {
            "status": status,
            "alert_count": len(active_alerts),
            "governor_total": int(dict(quality.get("governor") or {}).get("total") or 0),
            "llm_node_call_total": int(dict(cost.get("llm_call_distribution") or {}).get("total_calls") or 0),
        },
    }

    default_json, default_md = _default_output_paths()
    output_json = _resolve_output_path(args.output_json, fallback=default_json)
    output_md = _resolve_output_path(args.output_md, fallback=default_md)
    json_path, md_path = _write_reports(report, output_json=output_json, output_md=output_md)

    print(
        "longterm_dashboard: "
        f"status={report['summary']['status']} "
        f"alerts={report['summary']['alert_count']} "
        f"days={days}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    run(argv=argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
