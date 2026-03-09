"""运行时可观测性报告与监控函数。"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.main_helpers import (
    _rows_by_request,
    _to_timeline_rows,
    _summarize_sources,
)


LOGGER_NAME_RUNTIME_MONITOR = "Natsume.runtime.monitor"
OBS_LANGSMITH_DISABLED = "OBS_LANGSMITH_DISABLED"
OBS_LANGSMITH_UNAVAILABLE = "OBS_LANGSMITH_UNAVAILABLE"
OBS_LANGSMITH_API_KEY_MISSING = "OBS_LANGSMITH_API_KEY_MISSING"
OBS_CONSOLE_SPAWN_UNSUPPORTED = "OBS_CONSOLE_SPAWN_UNSUPPORTED"
OBS_CONSOLE_SPAWN_FAILED = "OBS_CONSOLE_SPAWN_FAILED"


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
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "title Natsume Runtime Monitor && echo runtime monitor attached"])
            profile.update({"console_mode": "spawn", "spawned": True})
            return profile

        if shutil.which("xterm"):
            subprocess.Popen(["xterm", "-T", "Natsume Runtime Monitor", "-e", "bash", "-lc", "echo runtime monitor attached; exec bash"])
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
