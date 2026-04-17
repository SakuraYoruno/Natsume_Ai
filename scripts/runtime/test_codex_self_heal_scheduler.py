#!/usr/bin/env python
"""Live scheduler-managed Codex self-heal probe for a running host service."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]
DEFAULT_REPORT_DIR = Path("artifacts/release/reports")
_AUTO_DISPATCH_ACCEPTED_DECISIONS = {"DISPATCHED", "EXTERNAL_DEPENDENCY_BLOCKED"}


def _resolve_base_url(*, explicit_url: str | None = None) -> str:
    if explicit_url and explicit_url.strip():
        return explicit_url.strip().rstrip("/")
    env_url = str(os.environ.get("HOST_APP_BASE_URL") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    host = str(os.environ.get("HOST_APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = str(os.environ.get("HOST_APP_PORT") or "18080").strip() or "18080"
    return f"http://{host}:{port}"


def _http_json(
    method: str,
    url: str,
    *,
    payload: JsonDict | None = None,
    timeout_s: float = 10.0,
) -> JsonDict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            body: Any = raw
            if raw.strip():
                try:
                    body = json.loads(raw)
                except Exception:
                    body = raw
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "latency_ms": int((time.time() - started) * 1000),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = str(exc)
        body: Any = raw
        if raw.strip():
            try:
                body = json.loads(raw)
            except Exception:
                body = raw
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def _json_body(response: JsonDict) -> JsonDict:
    body = response.get("body")
    return body if isinstance(body, dict) else {}


def _find_scheduler_task(tasks_response: JsonDict, *, task_id: str) -> JsonDict | None:
    body = _json_body(tasks_response)
    tasks = body.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if isinstance(task, dict) and str(task.get("task_id") or "") == task_id:
            return task
    return None


def _normalize_source(value: str | None, *, default: str = "health") -> str:
    candidate = str(value or "").strip().lower()
    if candidate == "ready":
        candidate = "readiness"
    if candidate not in {"health", "readiness"}:
        return default
    return candidate


def _resolve_probe_source(*, explicit_source: str | None, monitor_response: JsonDict | None = None) -> str:
    if explicit_source and str(explicit_source).strip():
        return _normalize_source(explicit_source)
    monitor_body = _json_body(monitor_response or {})
    return _normalize_source(str(monitor_body.get("source") or ""), default="health")


def _poll_scheduler_self_heal_state(
    *,
    base_url: str,
    source: str,
    timeout_s: float,
    poll_interval_s: float,
    request_timeout_s: float,
) -> JsonDict:
    active_source = _normalize_source(source)
    effective_timeout_s = float(timeout_s)
    started = time.time()
    last_monitor = {}
    last_auto = {}
    last_job = {}

    while True:
        monitor_response = _http_json(
            "GET",
            f"{base_url}/internal/codex/self_heal/monitor_status",
            timeout_s=request_timeout_s,
        )
        if bool(monitor_response.get("ok")) or not last_monitor:
            last_monitor = monitor_response
        monitor_body = _json_body(last_monitor)
        monitor_source = _normalize_source(str(monitor_body.get("source") or ""), default=active_source)
        active_source = monitor_source or active_source
        configured_timeout_s = float(monitor_body.get("timeout_seconds") or 0.0)
        if configured_timeout_s > 0:
            effective_timeout_s = max(effective_timeout_s, configured_timeout_s + 30.0)
        encoded_source = urllib.parse.quote(active_source, safe="")
        auto_response = _http_json(
            "GET",
            f"{base_url}/internal/codex/self_heal/auto_state?source={encoded_source}",
            timeout_s=request_timeout_s,
        )
        if bool(auto_response.get("ok")) or not last_auto:
            last_auto = auto_response
        auto_body = _json_body(last_auto)
        job_id = str(auto_body.get("last_job_id") or "").strip()
        if job_id:
            job_response = _http_json(
                "GET",
                f"{base_url}/internal/codex/jobs/{job_id}",
                timeout_s=request_timeout_s,
            )
            if bool(job_response.get("ok")) or not last_job:
                last_job = job_response
            job_body = _json_body(last_job)
            job_status = str(job_body.get("status") or "").strip().upper()
            if job_status in {"COMPLETED", "FAILED", "SKIPPED"}:
                return {
                    "timed_out": False,
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "effective_timeout_s": effective_timeout_s,
                    "monitor": last_monitor,
                    "auto_state": last_auto,
                    "job": last_job,
                }
        if time.time() - started >= effective_timeout_s:
            return {
                "timed_out": True,
                "elapsed_ms": int((time.time() - started) * 1000),
                "effective_timeout_s": effective_timeout_s,
                "monitor": last_monitor,
                "auto_state": last_auto,
                "job": last_job,
            }
        time.sleep(poll_interval_s)


def build_probe_report(
    *,
    base_url: str,
    source: str | None = None,
    task_id: str = "codex_self_heal_auto_dispatch_tick",
    request_timeout_s: float = 10.0,
    poll_timeout_s: float = 45.0,
    poll_interval_s: float = 1.0,
) -> JsonDict:
    tasks_before = _http_json("GET", f"{base_url}/host-scheduler/tasks", timeout_s=request_timeout_s)
    monitor_before = _http_json(
        "GET",
        f"{base_url}/internal/codex/self_heal/monitor_status",
        timeout_s=request_timeout_s,
    )
    resolved_source = _resolve_probe_source(explicit_source=source, monitor_response=monitor_before)
    encoded_source = urllib.parse.quote(resolved_source, safe="")
    auto_before = _http_json(
        "GET",
        f"{base_url}/internal/codex/self_heal/auto_state?source={encoded_source}",
        timeout_s=request_timeout_s,
    )
    trigger = _http_json(
        "POST",
        f"{base_url}/host-scheduler/tasks/{task_id}/trigger",
        timeout_s=request_timeout_s,
    )
    monitor_after_trigger = _http_json(
        "GET",
        f"{base_url}/internal/codex/self_heal/monitor_status",
        timeout_s=request_timeout_s,
    )
    monitor_after_trigger_body = _json_body(monitor_after_trigger)
    configured_timeout_s = float(monitor_after_trigger_body.get("timeout_seconds") or 0.0)
    effective_poll_timeout_s = poll_timeout_s
    if configured_timeout_s > 0:
        effective_poll_timeout_s = max(poll_timeout_s, configured_timeout_s + 30.0)
    poll = _poll_scheduler_self_heal_state(
        base_url=base_url,
        source=resolved_source,
        timeout_s=effective_poll_timeout_s,
        poll_interval_s=poll_interval_s,
        request_timeout_s=request_timeout_s,
    )
    best_monitor = poll.get("monitor") if isinstance(poll.get("monitor"), dict) and bool((poll.get("monitor") or {}).get("ok")) else monitor_after_trigger
    if not isinstance(best_monitor, dict) or not bool(best_monitor.get("ok")):
        best_monitor = monitor_before
    final_source = _resolve_probe_source(explicit_source=source, monitor_response=best_monitor)
    if poll.get("effective_timeout_s"):
        effective_poll_timeout_s = max(effective_poll_timeout_s, float(poll.get("effective_timeout_s") or 0.0))

    codex_task = _find_scheduler_task(tasks_before, task_id=task_id)
    monitor_before_body = _json_body(best_monitor)
    monitor_after_body = _json_body(poll.get("monitor") or {})
    auto_after_body = _json_body(poll.get("auto_state") or {})
    job_after_body = _json_body(poll.get("job") or {})
    last_result = monitor_after_body.get("last_result") if isinstance(monitor_after_body.get("last_result"), dict) else {}
    auto_dispatch_decision = str(auto_after_body.get("decision") or "").strip().upper()
    last_job_id = str(auto_after_body.get("last_job_id") or "").strip()
    blocked_external_dependency = auto_dispatch_decision == "EXTERNAL_DEPENDENCY_BLOCKED"

    checks = [
        {
            "check": "scheduler_task_present",
            "passed": bool(tasks_before.get("ok"))
            and int(tasks_before.get("status_code") or 0) == 200
            and bool(codex_task),
            "detail": codex_task or {},
        },
        {
            "check": "scheduler_managed",
            "passed": bool(best_monitor.get("ok"))
            and int(best_monitor.get("status_code") or 0) == 200
            and bool(monitor_before_body.get("scheduler_managed")),
            "detail": {
                "scheduler_managed": monitor_before_body.get("scheduler_managed"),
                "enabled": monitor_before_body.get("enabled"),
                "running": monitor_before_body.get("running"),
            },
        },
        {
            "check": "scheduler_trigger",
            "passed": (
                bool(trigger.get("ok"))
                and int(trigger.get("status_code") or 0) == 200
                and bool(_json_body(trigger).get("triggered"))
            ) or bool(last_result.get("accepted")) or bool(last_result.get("dispatched")),
            "detail": _json_body(trigger) or last_result,
        },
        {
            "check": "auto_dispatch_decision",
            "passed": auto_dispatch_decision in _AUTO_DISPATCH_ACCEPTED_DECISIONS,
            "detail": {
                "decision": auto_after_body.get("decision"),
                "last_dispatch_run_mode": auto_after_body.get("last_dispatch_run_mode"),
                "last_job_status": auto_after_body.get("last_job_status"),
                "last_dispatch_success": auto_after_body.get("last_dispatch_success"),
                "last_job_id": auto_after_body.get("last_job_id"),
                "last_dispatch_reason_code": auto_after_body.get("last_dispatch_reason_code"),
            },
        },
        {
            "check": "job_terminal",
            "passed": (
                blocked_external_dependency
                and not last_job_id
            )
            or str(job_after_body.get("status") or "").strip().upper() in {"COMPLETED", "FAILED", "SKIPPED"},
            "detail": {
                "status": "SKIPPED_EXTERNAL_DEPENDENCY_BLOCK" if blocked_external_dependency and not last_job_id else job_after_body.get("status"),
                "error": None if blocked_external_dependency and not last_job_id else job_after_body.get("error"),
                "dispatch_status": (
                    None
                    if blocked_external_dependency and not last_job_id
                    else (
                        (job_after_body.get("dispatch") or {}).get("status")
                        if isinstance(job_after_body.get("dispatch"), dict)
                        else None
                    )
                ),
                "dispatch_success": (
                    None
                    if blocked_external_dependency and not last_job_id
                    else (
                        (job_after_body.get("dispatch") or {}).get("success")
                        if isinstance(job_after_body.get("dispatch"), dict)
                        else None
                    )
                ),
            },
        },
    ]

    failed_checks = [row["check"] for row in checks if not row["passed"]]
    return {
        "meta": {
            "script": "test_codex_self_heal_scheduler.py",
            "base_url": base_url,
            "source": final_source,
            "task_id": task_id,
            "request_timeout_s": request_timeout_s,
            "poll_timeout_s": poll_timeout_s,
            "effective_poll_timeout_s": effective_poll_timeout_s,
            "poll_interval_s": poll_interval_s,
            "generated_at_ms": int(time.time() * 1000),
        },
        "checks": checks,
        "overall": {
            "passed": not failed_checks,
            "failed_checks": failed_checks,
        },
        "snapshots": {
            "tasks_before": tasks_before,
            "monitor_before": monitor_before,
            "auto_before": auto_before,
            "trigger": trigger,
            "monitor_after_trigger": monitor_after_trigger,
            "poll": poll,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live scheduler-managed Codex self-heal probe.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Host base URL. Defaults to HOST_APP_BASE_URL or http://127.0.0.1:18080.",
    )
    parser.add_argument(
        "--source",
        default="",
        choices=["", "health", "readiness", "ready"],
        help="Self-heal source used for auto_state polling. Default: infer from live monitor status.",
    )
    parser.add_argument(
        "--task-id",
        default="codex_self_heal_auto_dispatch_tick",
        help="Scheduler task id to trigger (default: codex_self_heal_auto_dispatch_tick).",
    )
    parser.add_argument(
        "--request-timeout-s",
        type=float,
        default=10.0,
        help="Per-request HTTP timeout in seconds (default: 10).",
    )
    parser.add_argument(
        "--poll-timeout-s",
        type=float,
        default=45.0,
        help="Polling timeout for auto_state/job completion in seconds (default: 45).",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1).",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help=f"Report output directory (default: {DEFAULT_REPORT_DIR}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = _normalize_source(args.source, default="") if str(args.source).strip() else None
    base_url = _resolve_base_url(explicit_url=args.base_url)
    report = build_probe_report(
        base_url=base_url,
        source=source,
        task_id=str(args.task_id).strip() or "codex_self_heal_auto_dispatch_tick",
        request_timeout_s=float(args.request_timeout_s),
        poll_timeout_s=float(args.poll_timeout_s),
        poll_interval_s=float(args.poll_interval_s),
    )

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"codex_self_heal_scheduler_probe_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in report["checks"]:
        marker = "PASS" if row["passed"] else "FAIL"
        print(f"[{marker}] {row['check']}")
    overall = "PASS" if report["overall"]["passed"] else "FAIL"
    print(f"[codex-self-heal-scheduler] overall={overall} report={report_path}")
    return 0 if report["overall"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
