"""Edge runtime health snapshot aggregation."""

from __future__ import annotations

from typing import Any

from host_brain.core.ids import now_ms

from edge_client.runtime.bootstrap import EdgeRuntime


S_EDGE_HEALTH = "S_EDGE_HEALTH"


def build_edge_health_snapshot(runtime: EdgeRuntime, *, ts_ms: int | None = None) -> dict[str, Any]:
    snapshot_ts = int(ts_ms if ts_ms is not None else now_ms())
    queue_depth = int(runtime.speech_queue.queue_depth)
    gate_snapshot = (
        runtime.resource_gate.snapshot()
        if runtime.resource_gate is not None
        else {
            "stage": "S_VOICE_RESOURCE_GATE",
            "active_tasks": [],
            "metrics": {
                "vram_total_mb": int(runtime.config.max_vram_mb),
                "vram_used_mb": 0,
                "queue_depth": queue_depth,
                "vram_utilization": 0.0,
            },
        }
    )
    gate_metrics = dict(gate_snapshot.get("metrics") or {})

    components: dict[str, Any] = {
        "asr": _component_snapshot(
            enabled=runtime.config.asr_enabled,
            detail="edge asr disabled by config",
            health=(runtime.asr_service.health() if runtime.asr_service is not None else None),
        ),
        "tts": _component_snapshot(
            enabled=runtime.config.tts_enabled,
            detail="edge tts disabled by config",
            health=(runtime.tts_model.health() if runtime.tts_model is not None else None),
        ),
        "vts": _component_snapshot(
            enabled=True,
            detail="edge vts unavailable",
            health=(runtime.vts_driver.health() if runtime.vts_driver is not None else None),
        ),
        "fallback": {
            "enabled": runtime.config.fallback_llm_enabled,
            "available": runtime.config.fallback_llm_enabled,
            "detail": (
                "edge fallback llm enabled"
                if runtime.config.fallback_llm_enabled
                else "edge fallback llm disabled by default"
            ),
            "default_unloaded": bool(runtime.fallback_rpc.default_unloaded),
            "model_id": str(runtime.fallback_rpc.model_id),
        },
        "resource_gate": {
            "enabled": runtime.config.resource_gate_enabled,
            "available": runtime.resource_gate is not None,
            "detail": (
                "edge resource gate enabled"
                if runtime.resource_gate is not None
                else "edge resource gate disabled by config"
            ),
            "snapshot": gate_snapshot,
        },
        "event_reporter": {
            "enabled": True,
            "available": True,
            "detail": "edge event reporter active",
            "health": runtime.event_reporter.health(),
        },
    }

    return {
        "stage": S_EDGE_HEALTH,
        "services": runtime.service_statuses(),
        "resources": {
            "vram_total_mb": int(gate_metrics.get("vram_total_mb", runtime.config.max_vram_mb)),
            "vram_used_mb": int(gate_metrics.get("vram_used_mb", 0)),
            "queue_depth": queue_depth,
        },
        "components": components,
        "config_snapshot": runtime.redacted_config_snapshot(),
        "ts_ms": snapshot_ts,
    }


def _component_snapshot(*, enabled: bool, detail: str, health: dict[str, Any] | None) -> dict[str, Any]:
    if not enabled:
        return {
            "enabled": False,
            "available": False,
            "detail": detail,
        }
    if health is None:
        return {
            "enabled": True,
            "available": False,
            "detail": "component unavailable",
        }
    return {
        "enabled": True,
        "available": True,
        "detail": "component loaded",
        "health": health,
    }
