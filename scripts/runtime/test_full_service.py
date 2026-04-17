#!/usr/bin/env python
"""Live host service probe for runtime smoke checks.

Checks:
1. GET /health
2. GET /ready
3. GET /metrics
4. POST /v1/chat and wait for SSE COMMIT
5. GET /health/canon_sync
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]
DEFAULT_REPORT_DIR = Path("artifacts/release/reports")


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


def _http_json(method: str, url: str, *, payload: JsonDict | None = None, timeout_s: float = 10.0) -> JsonDict:
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


def _stream_chat_sse(*, url: str, payload: JsonDict, timeout_s: float = 90.0) -> JsonDict:
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    events: list[JsonDict] = []
    commit_event: JsonDict | None = None
    data_lines: list[str] = []

    def _flush() -> None:
        nonlocal commit_event
        if not data_lines:
            return
        raw_payload = "\n".join(data_lines)
        data_lines.clear()
        row: JsonDict = {"raw": raw_payload}
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                row["json"] = parsed
                if parsed.get("phase") == "COMMIT" and commit_event is None:
                    commit_event = parsed
        except Exception as exc:
            row["parse_error"] = str(exc)
        events.append(row)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    _flush()
                    if commit_event is not None:
                        break
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].lstrip())
            _flush()
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "latency_ms": int((time.time() - started) * 1000),
                "events": events,
                "commit_event": commit_event,
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": body,
            "events": events,
            "commit_event": commit_event,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "events": events,
            "commit_event": commit_event,
        }


def _health_summary(health_body: Any) -> JsonDict:
    if not isinstance(health_body, dict):
        return {}
    details = health_body.get("details")
    if isinstance(details, dict):
        summary = details.get("summary")
        if isinstance(summary, dict):
            return summary
    return {}


def build_probe_report(*, base_url: str, timeout_s: float = 10.0, chat_timeout_s: float = 90.0) -> JsonDict:
    health = _http_json("GET", f"{base_url}/health", timeout_s=timeout_s)
    ready = _http_json("GET", f"{base_url}/ready", timeout_s=timeout_s)
    metrics = _http_json("GET", f"{base_url}/metrics", timeout_s=timeout_s)
    canon_sync = _http_json("GET", f"{base_url}/health/canon_sync", timeout_s=timeout_s)
    chat = _stream_chat_sse(
        url=f"{base_url}/v1/chat",
        payload={
            "message": "你好，夏目蓝，给我一句简短回复。",
            "session_id": "runtime-probe-session-001",
        },
        timeout_s=chat_timeout_s,
    )

    health_body = health.get("body") if isinstance(health.get("body"), dict) else {}
    ready_body = ready.get("body") if isinstance(ready.get("body"), dict) else {}
    metrics_body = metrics.get("body") if isinstance(metrics.get("body"), dict) else {}
    canon_sync_body = canon_sync.get("body") if isinstance(canon_sync.get("body"), dict) else {}
    commit_event = chat.get("commit_event") if isinstance(chat.get("commit_event"), dict) else {}
    summary = _health_summary(health_body)

    checks = [
        {
            "check": "health",
            "passed": bool(health.get("ok")) and int(health.get("status_code") or 0) == 200,
            "detail": {
                "status": health_body.get("status"),
                "overall_status": summary.get("overall_status"),
                "required_total": summary.get("required_total"),
                "required_unavailable": summary.get("required_unavailable"),
            },
        },
        {
            "check": "ready",
            "passed": bool(ready.get("ok")) and int(ready.get("status_code") or 0) == 200,
            "detail": {
                "ready": ready_body.get("ready"),
                "status": ready_body.get("status"),
                "reason_code": ready_body.get("reason_code"),
            },
        },
        {
            "check": "metrics",
            "passed": bool(metrics.get("ok"))
            and int(metrics.get("status_code") or 0) == 200
            and isinstance(metrics_body.get("reason_code_distribution"), dict),
            "detail": {
                "uptime_seconds": metrics_body.get("uptime_seconds"),
                "total_requests": metrics_body.get("total_requests"),
                "total_chat_requests": metrics_body.get("total_chat_requests"),
            },
        },
        {
            "check": "chat_commit",
            "passed": bool(chat.get("ok"))
            and int(chat.get("status_code") or 0) == 200
            and bool(commit_event)
            and bool(commit_event.get("done")),
            "detail": {
                "decision": commit_event.get("decision"),
                "done": commit_event.get("done"),
                "request_id": commit_event.get("request_id"),
            },
        },
        {
            "check": "canon_sync",
            "passed": bool(canon_sync.get("ok")) and int(canon_sync.get("status_code") or 0) == 200,
            "detail": {
                "status": canon_sync_body.get("status"),
                "detail": (
                    canon_sync_body.get("details", {}).get("detail")
                    if isinstance(canon_sync_body.get("details"), dict)
                    else None
                ),
                "drift_count": (
                    canon_sync_body.get("details", {}).get("drift_count")
                    if isinstance(canon_sync_body.get("details"), dict)
                    else None
                ),
            },
        },
    ]

    failed_checks = [row["check"] for row in checks if not bool(row.get("passed"))]
    return {
        "meta": {
            "script": "test_full_service.py",
            "base_url": base_url,
            "generated_at_ms": int(time.time() * 1000),
        },
        "checks": checks,
        "responses": {
            "health": health,
            "ready": ready,
            "metrics": metrics,
            "chat": {
                "ok": chat.get("ok"),
                "status_code": chat.get("status_code"),
                "latency_ms": chat.get("latency_ms"),
                "event_count": len(chat.get("events") or []),
                "commit_event": commit_event,
                "error": chat.get("error"),
            },
            "canon_sync": canon_sync,
        },
        "overall": {
            "passed": len(failed_checks) == 0,
            "failed_checks": failed_checks,
        },
    }


def _print_report(report: JsonDict) -> None:
    base_url = str(report.get("meta", {}).get("base_url") or "")
    print(f"[full-service] base_url={base_url}")
    for row in report.get("checks") or []:
        state = "PASS" if row.get("passed") else "FAIL"
        detail = json.dumps(row.get("detail") or {}, ensure_ascii=False, sort_keys=True)
        print(f"[{state}] {row.get('check')} | {detail}")
    overall = report.get("overall") or {}
    print(f"[full-service] overall={'PASS' if overall.get('passed') else 'FAIL'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Host live service runtime probe.")
    parser.add_argument("--base-url", default="", help="Host base URL, e.g. http://127.0.0.1:18080")
    parser.add_argument("--timeout-s", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--chat-timeout-s", type=float, default=90.0, help="SSE timeout seconds")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Directory for JSON report output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_url = _resolve_base_url(explicit_url=args.base_url)
    report = build_probe_report(base_url=base_url, timeout_s=args.timeout_s, chat_timeout_s=args.chat_timeout_s)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"full_service_probe_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_report(report)
    print(f"[full-service] report={report_path}")
    return 0 if bool(report.get("overall", {}).get("passed")) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
