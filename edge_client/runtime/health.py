"""Edge runtime health snapshot aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from host_brain.core.ids import now_ms

from edge_client.runtime.bootstrap import EdgeRuntime


S_EDGE_HEALTH = "S_EDGE_HEALTH"


def collect_health(runtime: EdgeRuntime, *, ts_ms: int | None = None) -> dict[str, Any]:
    snapshot_ts = int(ts_ms if ts_ms is not None else now_ms())
    queue_depth = int(getattr(getattr(runtime, "speech_queue", None), "queue_depth", 0) or 0)

    gate_enabled = bool(getattr(runtime.config, "resource_gate_enabled", False))
    gate_snapshot, gate_status, gate_detail = _collect_resource_gate(
        runtime=runtime,
        enabled=gate_enabled,
        queue_depth=queue_depth,
    )

    component_timings_ms: dict[str, float] = {}

    def _timed_component_snapshot(name: str, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        snapshot = _component_snapshot(**kwargs)
        component_timings_ms[name] = round((time.perf_counter() - started) * 1000.0, 3)
        return snapshot

    components: dict[str, Any] = {
        "asr": _timed_component_snapshot(
            "asr",
            enabled=bool(getattr(runtime.config, "asr_enabled", False)),
            detail_when_disabled="edge asr disabled by config",
            component=getattr(runtime, "asr", None),
        ),
        "tts": _timed_component_snapshot(
            "tts",
            enabled=bool(getattr(runtime.config, "tts_enabled", False)),
            detail_when_disabled="edge tts disabled by config",
            component=getattr(runtime, "tts", None),
        ),
        "tts_playback_device": _timed_component_snapshot(
            "tts_playback_device",
            enabled=bool(getattr(runtime.config, "tts_enabled", False)),
            detail_when_disabled="edge tts playback disabled by config",
            component=getattr(runtime, "tts_playback_device", None),
        ),
        "fallback": _timed_component_snapshot(
            "fallback",
            enabled=bool(getattr(runtime.config, "fallback_llm_enabled", False)),
            detail_when_disabled="edge fallback llm disabled by config",
            component=getattr(runtime, "fallback_rpc", None),
        ),
        "vts": _timed_component_snapshot(
            "vts",
            enabled=True,
            detail_when_disabled="edge vts unavailable",
            component=getattr(runtime, "vts", None),
        ),
        "voice_pipeline": _timed_component_snapshot(
            "voice_pipeline",
            enabled=True,
            detail_when_disabled="edge voice pipeline unavailable",
            component=getattr(runtime, "voice_pipeline", None),
        ),
        "event_reporter": _timed_component_snapshot(
            "event_reporter",
            enabled=True,
            detail_when_disabled="edge event reporter disabled",
            component=getattr(runtime, "event_reporter", None),
        ),
        "resource_gate": {
            "enabled": gate_enabled,
            "available": bool(getattr(runtime, "resource_gate", None) is not None),
            "status": gate_status,
            "detail": gate_detail,
            "snapshot": gate_snapshot,
        },
    }
    component_timings_ms["resource_gate"] = 0.0

    aggregate_inputs = [str(item.get("status") or "ok") for item in components.values()]
    aggregate_inputs.append(gate_status)
    status = _aggregate_status(aggregate_inputs)

    return {
        "status": status,
        "components": components,
        "resource_gate": {
            "status": gate_status,
            "detail": gate_detail,
            "snapshot": gate_snapshot,
            "queue_depth": queue_depth,
        },
        "collection_timings_ms": component_timings_ms,
        "timestamp": _format_iso8601(snapshot_ts),
    }


def build_edge_health_snapshot(
    runtime: EdgeRuntime,
    *,
    ts_ms: int | None = None,
    collected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot_ts = int(ts_ms if ts_ms is not None else now_ms())
    collected_snapshot = dict(collected or collect_health(runtime, ts_ms=snapshot_ts))
    queue_depth = int(collected_snapshot["resource_gate"].get("queue_depth", 0))
    gate_snapshot = dict(collected_snapshot["resource_gate"].get("snapshot") or {})
    gate_metrics = dict(gate_snapshot.get("metrics") or {})
    services = runtime.service_statuses() if callable(getattr(runtime, "service_statuses", None)) else {}
    config_snapshot = (
        runtime.redacted_config_snapshot()
        if callable(getattr(runtime, "redacted_config_snapshot", None))
        else {}
    )

    return {
        "stage": S_EDGE_HEALTH,
        "status": collected["status"],
        "timestamp": collected["timestamp"],
        "services": services,
        "resources": {
            "vram_total_mb": int(gate_metrics.get("vram_total_mb", runtime.config.max_vram_mb)),
            "vram_used_mb": int(gate_metrics.get("vram_used_mb", 0)),
            "queue_depth": queue_depth,
        },
        "components": collected_snapshot["components"],
        "resource_gate": collected_snapshot["resource_gate"],
        "collection_timings_ms": dict(collected_snapshot.get("collection_timings_ms") or {}),
        "config_snapshot": config_snapshot,
        "ts_ms": snapshot_ts,
    }


def _component_snapshot(
    *,
    enabled: bool,
    detail_when_disabled: str,
    component: Any,
) -> dict[str, Any]:
    if not enabled:
        return {
            "enabled": False,
            "available": False,
            "status": "ok",
            "detail": detail_when_disabled,
        }
    if component is None:
        return {
            "enabled": True,
            "available": False,
            "status": "down",
            "detail": "component unavailable",
        }
    health, error = _safe_health(component)
    if error is not None:
        return {
            "enabled": True,
            "available": True,
            "status": "error",
            "detail": f"health() failed: {error}",
        }
    component_status = _extract_component_status(health)
    return {
        "enabled": True,
        "available": True,
        "status": component_status,
        "detail": "component loaded",
        "health": health,
    }


def _collect_resource_gate(
    *,
    runtime: EdgeRuntime,
    enabled: bool,
    queue_depth: int,
) -> tuple[dict[str, Any], str, str]:
    if not enabled:
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "ok",
            "edge resource gate disabled by config",
        )

    resource_gate = getattr(runtime, "resource_gate", None)
    if resource_gate is None:
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "down",
            "resource gate unavailable",
        )
    try:
        snapshot = dict(resource_gate.snapshot())
    except Exception as exc:  # pragma: no cover - defensive guard
        return (
            {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": int(runtime.config.max_vram_mb),
                    "vram_used_mb": 0,
                    "queue_depth": queue_depth,
                    "vram_utilization": 0.0,
                },
            },
            "error",
            f"resource gate snapshot failed: {exc}",
        )
    return snapshot, "ok", "edge resource gate enabled"


def _safe_health(component: Any) -> tuple[dict[str, Any], Exception | None]:
    health = getattr(component, "health", None)
    if not callable(health):
        return {}, None
    try:
        raw = health()
    except Exception as exc:  # pragma: no cover - defensive guard
        return {}, exc
    if isinstance(raw, dict):
        return dict(raw), None
    return {"raw_health": raw}, None


def _extract_component_status(health: dict[str, Any]) -> str:
    raw = str(
        health.get("status")
        or health.get("state")
        or health.get("connection_state")
        or "ok"
    ).strip().lower()
    if raw in {"down", "error", "unavailable", "shutdown"}:
        return "down"
    if raw in {"degraded", "loading", "recovering", "warning", "unloaded", "disconnected", "connecting", "reconnecting"}:
        return "degraded"
    return "ok"


def _aggregate_status(statuses: list[str]) -> str:
    normalized = [s.strip().lower() for s in statuses if str(s).strip()]
    if any(s == "down" for s in normalized):
        return "down"
    if any(s in {"degraded", "error"} for s in normalized):
        return "degraded"
    return "ok"


def _format_iso8601(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")
