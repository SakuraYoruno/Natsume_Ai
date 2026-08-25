"""Generate weekly stability report from audit ledger.

The report focuses on timeout/degraded/failure rates and writes JSON + Markdown
artifacts for operational review.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from host_brain.core.ids import now_ms as _now_ms
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = ROOT / "artifacts" / "diagnostics"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger


def _iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).isoformat()


def _coerce_pct(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(fallback)
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return float(value)


def _pct(count: int, total: int) -> float:
    if int(total) <= 0:
        return 0.0
    return round((float(count) / float(total)) * 100.0, 2)


def _contains_timeout_marker(*, status: Any, reason_code: Any, detail: Any) -> bool:
    status_text = str(status or "").strip().upper()
    reason_text = str(reason_code or "").strip().upper()
    detail_text = str(detail or "").strip().upper()
    return (
        ("TIMEOUT" in status_text)
        or ("TIMEOUT" in reason_text)
        or ("TIMEOUT" in detail_text)
    )


def _is_failure_status(status: Any) -> bool:
    normalized = str(status or "").strip().upper()
    return normalized in {"FAILED", "ERROR", "REJECTED", "TIMEOUT"}


def _default_output_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        ARTIFACTS_ROOT / f"WEEKLY_STABILITY_REPORT_{stamp}.json",
        ARTIFACTS_ROOT / f"WEEKLY_STABILITY_REPORT_{stamp}.md",
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


def _summarize_governor(
    rows: list[dict[str, Any]],
    *,
    threshold_degraded_rate_pct: float,
    threshold_failure_rate_pct: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total = len(rows)
    approved = sum(1 for row in rows if str(row.get("decision") or "").upper() == "APPROVE")
    rejected = sum(1 for row in rows if str(row.get("decision") or "").upper() == "REJECT")
    degraded = sum(1 for row in rows if str(row.get("decision") or "").upper() == "DEGRADED")
    failure = rejected + degraded
    degraded_rate = _pct(degraded, total)
    failure_rate = _pct(failure, total)

    reason_counter: Counter[str] = Counter()
    for row in rows:
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            reason_counter[reason] += 1

    alerts: list[dict[str, Any]] = []
    if total > 0 and degraded_rate >= threshold_degraded_rate_pct:
        alerts.append(
            {
                "code": "GOVERNOR_DEGRADED_RATE_HIGH",
                "severity": "WARN",
                "observed_rate_pct": degraded_rate,
                "threshold_rate_pct": threshold_degraded_rate_pct,
            }
        )
    if total > 0 and failure_rate >= threshold_failure_rate_pct:
        alerts.append(
            {
                "code": "GOVERNOR_FAILURE_RATE_HIGH",
                "severity": "WARN",
                "observed_rate_pct": failure_rate,
                "threshold_rate_pct": threshold_failure_rate_pct,
            }
        )

    summary = {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "degraded": degraded,
        "failure": failure,
        "degraded_rate_pct": degraded_rate,
        "failure_rate_pct": failure_rate,
        "top_reason_codes": [
            {"reason_code": code, "count": count}
            for code, count in reason_counter.most_common(10)
        ],
    }
    return summary, alerts


def _summarize_retrieval(
    rows: list[dict[str, Any]],
    *,
    threshold_timeout_rate_pct: float,
    threshold_degraded_rate_pct: float,
    threshold_failure_rate_pct: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total = len(rows)
    success_count = 0
    timeout_count = 0
    degraded_count = 0
    failure_count = 0
    latencies: list[int] = []
    reason_counter: Counter[str] = Counter()

    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        reason_code = row.get("reason_code")
        detail = row.get("detail")
        payload = row.get("payload") or {}

        if status == "SUCCESS":
            success_count += 1
        if _is_failure_status(status):
            failure_count += 1
        if _contains_timeout_marker(status=status, reason_code=reason_code, detail=detail):
            timeout_count += 1

        row_degraded = (status == "DEGRADED")
        if isinstance(payload, dict):
            lat = payload.get("latency_ms")
            if isinstance(lat, (int, float)) and lat >= 0:
                latencies.append(int(lat))
            if bool(payload.get("degraded")):
                row_degraded = True
        if row_degraded:
            degraded_count += 1

        reason_text = str(reason_code or "").strip()
        if reason_text:
            reason_counter[reason_text] += 1

    timeout_rate = _pct(timeout_count, total)
    degraded_rate = _pct(degraded_count, total)
    failure_rate = _pct(failure_count, total)
    p50: int | None = None
    p95: int | None = None
    if latencies:
        ordered = sorted(latencies)
        n = len(ordered)
        p50 = ordered[int(n * 0.50)]
        p95 = ordered[min(int(n * 0.95), n - 1)]

    alerts: list[dict[str, Any]] = []
    if total > 0 and timeout_rate >= threshold_timeout_rate_pct:
        alerts.append(
            {
                "code": "RETRIEVAL_TIMEOUT_RATE_HIGH",
                "severity": "WARN",
                "observed_rate_pct": timeout_rate,
                "threshold_rate_pct": threshold_timeout_rate_pct,
            }
        )
    if total > 0 and degraded_rate >= threshold_degraded_rate_pct:
        alerts.append(
            {
                "code": "RETRIEVAL_DEGRADED_RATE_HIGH",
                "severity": "WARN",
                "observed_rate_pct": degraded_rate,
                "threshold_rate_pct": threshold_degraded_rate_pct,
            }
        )
    if total > 0 and failure_rate >= threshold_failure_rate_pct:
        alerts.append(
            {
                "code": "RETRIEVAL_FAILURE_RATE_HIGH",
                "severity": "WARN",
                "observed_rate_pct": failure_rate,
                "threshold_rate_pct": threshold_failure_rate_pct,
            }
        )

    summary = {
        "event_count": total,
        "success_count": success_count,
        "timeout_count": timeout_count,
        "degraded_count": degraded_count,
        "failure_count": failure_count,
        "timeout_rate_pct": timeout_rate,
        "degraded_rate_pct": degraded_rate,
        "failure_rate_pct": failure_rate,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "latency_sample_count": len(latencies),
        "top_reason_codes": [
            {"reason_code": code, "count": count}
            for code, count in reason_counter.most_common(10)
        ],
    }
    return summary, alerts


def _build_markdown(report: dict[str, Any]) -> str:
    window = dict(report.get("window") or {})
    thresholds = dict(report.get("thresholds") or {})
    summary = dict(report.get("summary") or {})
    governor = dict(report.get("governor") or {})
    retrieval = dict(report.get("retrieval") or {})
    alerts = list((report.get("alerts") or {}).get("active") or [])

    alert_lines = ["- none"]
    if alerts:
        alert_lines = [
            (
                f"- `{str(item.get('code') or '')}` "
                f"(observed={item.get('observed_rate_pct')}%, threshold={item.get('threshold_rate_pct')}%)"
            )
            for item in alerts
            if isinstance(item, dict)
        ] or ["- none"]

    lines = [
        "# Weekly Stability Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_start: {window.get('start_iso')}",
        f"- window_end: {window.get('end_iso')}",
        f"- days: {window.get('days')}",
        f"- status: {summary.get('status')}",
        f"- alert_count: {summary.get('alert_count')}",
        "",
        "## Thresholds",
        "",
        f"- timeout_rate_pct: {thresholds.get('timeout_rate_pct')}",
        f"- degraded_rate_pct: {thresholds.get('degraded_rate_pct')}",
        f"- failure_rate_pct: {thresholds.get('failure_rate_pct')}",
        "",
        "## Governor",
        "",
        f"- total: {governor.get('total')}",
        f"- approved: {governor.get('approved')}",
        f"- rejected: {governor.get('rejected')}",
        f"- degraded: {governor.get('degraded')}",
        f"- failure_rate_pct: {governor.get('failure_rate_pct')}",
        f"- degraded_rate_pct: {governor.get('degraded_rate_pct')}",
        "",
        "## Retrieval",
        "",
        f"- event_count: {retrieval.get('event_count')}",
        f"- timeout_rate_pct: {retrieval.get('timeout_rate_pct')}",
        f"- degraded_rate_pct: {retrieval.get('degraded_rate_pct')}",
        f"- failure_rate_pct: {retrieval.get('failure_rate_pct')}",
        f"- latency_p50_ms: {retrieval.get('latency_p50_ms')}",
        f"- latency_p95_ms: {retrieval.get('latency_p95_ms')}",
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
    parser = argparse.ArgumentParser(description="Generate weekly stability report")
    parser.add_argument("--ledger-db", default="host_brain/data/audit.sqlite3")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--threshold-timeout-rate-pct", type=float, default=_coerce_pct(os.environ.get("OBS_ALERT_TIMEOUT_RATE_PCT"), 10.0))
    parser.add_argument("--threshold-degraded-rate-pct", type=float, default=_coerce_pct(os.environ.get("OBS_ALERT_DEGRADED_RATE_PCT"), 20.0))
    parser.add_argument("--threshold-failure-rate-pct", type=float, default=_coerce_pct(os.environ.get("OBS_ALERT_FAILURE_RATE_PCT"), 15.0))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    days = max(1, int(args.days))
    threshold_timeout_rate_pct = _coerce_pct(args.threshold_timeout_rate_pct, 10.0)
    threshold_degraded_rate_pct = _coerce_pct(args.threshold_degraded_rate_pct, 20.0)
    threshold_failure_rate_pct = _coerce_pct(args.threshold_failure_rate_pct, 15.0)

    end_ms = _now_ms()
    start_ms = int(end_ms - (days * 24 * 60 * 60 * 1000))

    ledger = SQLiteLedger(db_path=str(args.ledger_db))
    try:
        governor_rows = _fetch_all_rows(
            ledger,
            table="governor_log",
            start_ms=start_ms,
            end_ms=end_ms,
        )
        retrieval_rows = _fetch_all_rows(
            ledger,
            table="retrieval_event_log",
            start_ms=start_ms,
            end_ms=end_ms,
        )
    finally:
        ledger.close()

    governor_summary, governor_alerts = _summarize_governor(
        governor_rows,
        threshold_degraded_rate_pct=threshold_degraded_rate_pct,
        threshold_failure_rate_pct=threshold_failure_rate_pct,
    )
    retrieval_summary, retrieval_alerts = _summarize_retrieval(
        retrieval_rows,
        threshold_timeout_rate_pct=threshold_timeout_rate_pct,
        threshold_degraded_rate_pct=threshold_degraded_rate_pct,
        threshold_failure_rate_pct=threshold_failure_rate_pct,
    )
    active_alerts = list(governor_alerts) + list(retrieval_alerts)
    status = "WARN" if active_alerts else "OK"

    report = {
        "version": "stability_weekly.v1",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "days": days,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_iso": _iso_utc(start_ms),
            "end_iso": _iso_utc(end_ms),
        },
        "thresholds": {
            "timeout_rate_pct": threshold_timeout_rate_pct,
            "degraded_rate_pct": threshold_degraded_rate_pct,
            "failure_rate_pct": threshold_failure_rate_pct,
        },
        "governor": governor_summary,
        "retrieval": retrieval_summary,
        "alerts": {
            "active": active_alerts,
        },
        "summary": {
            "status": status,
            "alert_count": len(active_alerts),
            "governor_total": int(governor_summary.get("total") or 0),
            "retrieval_event_count": int(retrieval_summary.get("event_count") or 0),
        },
    }

    default_json, default_md = _default_output_paths()
    output_json = _resolve_output_path(args.output_json, fallback=default_json)
    output_md = _resolve_output_path(args.output_md, fallback=default_md)
    json_path, md_path = _write_reports(report, output_json=output_json, output_md=output_md)

    print(
        "weekly_stability: "
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
