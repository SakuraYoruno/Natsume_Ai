"""Background-only health, device and Organ-Tribe query projections.

Source loaders live in this module so HTTP query handlers cannot accidentally
probe a service, instantiate an Agent, submit a command, or scan a ledger.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from natsume_contracts.edge.embodiment_v1 import EmbodimentCommand, EmbodimentReceipt
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.channels.embodiment import register_embodiment_receipt_observer
from host_brain.app.observability_runtime.projection_daemon import register_projection_job
from host_brain.app.observability_runtime.projection_runtime import projection_meta
from host_brain.observability.projection_store import QueryProjectionStore, digest_json
from host_brain.observability.service_registry import (
    ALL_SERVICES,
    EDGE_SERVICE_EVENT_PREFIXES,
    LLM_NODE_REGISTRY,
)


SERVICE_HEALTH_KIND = "service_health"
SERVICE_HEALTH_KEY = "all"
LLM_NODE_HEALTH_KIND = "llm_node_health"
LLM_NODE_HEALTH_KEY = "all"
DEVICE_STATUS_KIND = "device_status"
ORGAN_TRIBE_KIND = "organ_tribe_status"
ORGAN_TRIBE_KEY = "current"
HEALTH_MAX_AGE_MS = 120_000
DEVICE_MAX_AGE_MS = 300_000
ORGAN_TRIBE_MAX_AGE_MS = 120_000


ORGAN_AGENT_REGISTRY: tuple[tuple[str, str, str, str], ...] = (
    ("limbic_agent", "limbic", "host_brain.tribe.limbic_agent", "_LIMBIC_AGENT"),
    ("social_brain_agent", "social_brain", "host_brain.tribe.social_brain_agent", "_SOCIAL_BRAIN_AGENT"),
    ("prefrontal_agent", "prefrontal", "host_brain.tribe.prefrontal_agent", "_PREFRONTAL_AGENT"),
    ("ans_agent", "ans", "host_brain.tribe.ans_agent", "_ANS_AGENT"),
    ("immune_agent", "immune", "host_brain.tribe.immune_agent", "_IMMUNE_AGENT"),
    ("pain_reward_agent", "pain_reward", "host_brain.tribe.pain_reward_agent", "_PAIN_REWARD_AGENT"),
    ("hippocampus_agent", "hippocampus", "host_brain.tribe.hippocampus_agent", "_HIPPOCAMPUS_AGENT"),
    ("cortex_agent", "cortex", "host_brain.tribe.cortex_agent", "_CORTEX_AGENT"),
    ("peripheral_agent", "peripheral", "host_brain.tribe.peripheral_agent", "_PERIPHERAL_AGENT"),
    ("motor_cortex_agent", "motor_cortex", "host_brain.tribe.motor_cortex_agent", "_MOTOR_CORTEX_AGENT"),
    ("thalamus_agent", "thalamus", "host_brain.tribe.thalamus_agent", "_THALAMUS_AGENT"),
    ("language_system_agent", "language", "host_brain.tribe.language_system_agent", "_LANGUAGE_SYSTEM_AGENT"),
    ("endocrine_agent", "endocrine", "host_brain.tribe.endocrine_agent", "_ENDOCRINE_AGENT"),
    ("initiative_governor_agent", "initiative_governor", "host_brain.tribe.initiative_governor_agent", "_INITIATIVE_GOVERNOR_AGENT"),
    ("circadian_agent", "circadian", "host_brain.tribe.circadian_agent", "_CIRCADIAN_AGENT"),
    ("mirror_neuron_agent", "mirror_neuron", "host_brain.tribe.mirror_neuron_agent", "_MIRROR_NEURON_AGENT"),
    ("somatic_agent", "somatic", "host_brain.tribe.somatic_agent", "_SOMATIC_AGENT"),
    ("proprioception_agent", "proprioception", "host_brain.tribe.proprioception_agent", "_PROPRIOCEPTION_AGENT"),
    ("interoception_agent", "interoception", "host_brain.tribe.interoception_agent", "_INTEROCEPTION_AGENT"),
    ("vestibular_agent", "vestibular", "host_brain.tribe.vestibular_agent", "_VESTIBULAR_AGENT"),
    ("glymphatic_agent", "glymphatic", "host_brain.tribe.glymphatic_agent", "_GLYMPHATIC_AGENT"),
    ("cerebellum_agent", "cerebellum", "host_brain.tribe.cerebellum_agent", "_CEREBELLUM_AGENT"),
    ("default_mode_agent", "default_mode", "host_brain.tribe.default_mode_agent", "_DEFAULT_MODE_AGENT"),
)


_receipt_lock = threading.Lock()
_latest_receipts: dict[str, tuple[dict[str, Any], int]] = {}
_job_lock = threading.Lock()
_last_job_run: dict[str, float] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compute_health(rows: list[Mapping[str, Any]], *, window: int = 20) -> dict[str, Any]:
    if not rows:
        return {
            "status": "UNKNOWN",
            "success_rate": None,
            "p95_ms": None,
            "last_call_ts": None,
            "call_count": 0,
        }
    recent = rows[-max(1, int(window)):]
    success = sum(
        1
        for row in recent
        if str(row.get("status") or "").strip().upper()
        in {"OK", "UP", "SUCCEEDED", "SUCCESS", "COMPLETED", "ACCEPTED"}
    )
    success_rate = round(success / len(recent) * 100, 1)
    latencies = sorted(
        _safe_int(row.get("latency_ms"))
        for row in recent
        if row.get("latency_ms") is not None
    )
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None
    last_ts = max(
        (_safe_int(row.get("ts_ms") or row.get("ended_at_ms")) for row in recent),
        default=0,
    ) or None
    return {
        "status": "UP" if success_rate >= 80 else "DEGRADED" if success_rate >= 50 else "DOWN",
        "success_rate": success_rate,
        "p95_ms": p95,
        "last_call_ts": last_ts,
        "call_count": len(recent),
    }


def _ledger_rows(
    ledger: Any,
    table: str,
    *,
    start_ms: int,
    end_ms: int,
) -> list[Mapping[str, Any]]:
    try:
        rows = ledger.fetch_by_time_range(table, start_ms=start_ms, end_ms=end_ms, limit=5000)
    except Exception:
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def load_health_projection_sources(
    ledger: Any,
    *,
    now_ms: int | None = None,
    window_minutes: int = 60,
    include_process_cache: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Read only heartbeat cache and audit rows; never launch a probe."""

    heartbeat_cache_reader: Callable[[str], Mapping[str, Any] | None] | None = None
    if include_process_cache:
        from host_brain.app.observability_runtime import (
            service_health_heartbeat as heartbeat,
        )

        heartbeat_cache_reader = heartbeat.get_cached

    now = max(0, int(now_ms if now_ms is not None else _now_ms()))
    since = now - max(1, int(window_minutes)) * 60_000
    voice_rows = _ledger_rows(ledger, "voice_event_log", start_ms=since, end_ms=now)
    llm_rows = _ledger_rows(ledger, "llm_node_call_log", start_ms=since, end_ms=now)
    if not llm_rows:
        llm_rows = _ledger_rows(ledger, "model_attempt_log", start_ms=since, end_ms=now)

    services: list[dict[str, Any]] = []
    for service in ALL_SERVICES:
        service_id = service["service_id"]
        cached = (
            heartbeat_cache_reader(service_id)
            if heartbeat_cache_reader is not None
            else None
        )
        if cached is not None:
            health = {
                "status": str(cached.get("status") or "UNKNOWN").upper(),
                "success_rate": None,
                "p95_ms": cached.get("latency_ms"),
                "last_call_ts": cached.get("last_check_ts"),
                "call_count": 0,
            }
        elif service_id in EDGE_SERVICE_EVENT_PREFIXES:
            prefixes = EDGE_SERVICE_EVENT_PREFIXES[service_id]
            health = _compute_health(
                [
                    row
                    for row in voice_rows
                    if any(
                        str(row.get("event_type") or "").upper().startswith(prefix)
                        for prefix in prefixes
                    )
                ]
            )
        elif service_id == "llm_gateway":
            health = _compute_health(llm_rows)
        elif service_id == "sqlite_ledger":
            health = {
                "status": "UP",
                "success_rate": None,
                "p95_ms": None,
                "last_call_ts": now,
                "call_count": 0,
            }
        else:
            health = _compute_health([])
        services.append(
            {
                "service_id": service_id,
                "service_type": service["side"],
                "display_name": service["name"],
                **health,
            }
        )

    nodes: list[dict[str, Any]] = []
    for node_id, meta in LLM_NODE_REGISTRY.items():
        cached = (
            heartbeat_cache_reader(node_id)
            if heartbeat_cache_reader is not None
            else None
        )
        if cached is not None:
            health = {
                "status": str(cached.get("status") or "UNKNOWN").upper(),
                "success_rate": None,
                "p95_ms": cached.get("latency_ms"),
                "last_call_ts": cached.get("last_check_ts"),
                "call_count": 0,
            }
        else:
            matching = [
                row
                for row in llm_rows
                if str(row.get("node_id") or row.get("model_node_id") or "") == node_id
                or (
                    str(row.get("provider") or "").lower() == meta["provider"].lower()
                    and str(row.get("model_id") or "") == meta["model_id"]
                )
            ]
            health = _compute_health(matching)
        nodes.append({"node_id": node_id, **meta, **health})

    counts = {key: 0 for key in ("UP", "DEGRADED", "DOWN", "UNKNOWN")}
    for service in services:
        status = str(service.get("status") or "UNKNOWN").upper()
        counts[status if status in counts else "UNKNOWN"] += 1
    cursor = max(
        [now]
        + [_safe_int(row.get("last_call_ts")) for row in services]
        + [_safe_int(row.get("last_call_ts")) for row in nodes]
    )
    service_payload = {
        "total": len(services),
        "up": counts["UP"],
        "degraded": counts["DEGRADED"],
        "down": counts["DOWN"],
        "unknown": counts["UNKNOWN"],
        "services": services,
        "timestamp": now,
    }
    node_payload = {"nodes": nodes, "timestamp": now}
    return service_payload, node_payload, cursor


def refresh_health_query_projections() -> dict[str, int]:
    from host_brain.composition.process_services import get_ledger, get_query_projection_store

    service_payload, node_payload, cursor = load_health_projection_sources(get_ledger())
    store = get_query_projection_store()
    for kind, key, payload in (
        (SERVICE_HEALTH_KIND, SERVICE_HEALTH_KEY, service_payload),
        (LLM_NODE_HEALTH_KIND, LLM_NODE_HEALTH_KEY, node_payload),
    ):
        store.upsert(
            kind,
            key,
            payload,
            source_cursor=cursor,
            source_digest=digest_json(payload),
            source_ref="health-passive-background-worker",
        )
    return {"service_count": len(service_payload["services"]), "node_count": len(node_payload["nodes"])}


_PUBLIC_EVENT_FIELDS = frozenset(
    {
        "action", "available", "behavior_id", "component", "connected",
        "device", "emotion_tag", "enabled", "external_side_effects", "kind",
        "model", "state", "status",
    }
)
_SENSITIVE_DETAIL_MARKERS = (
    "api_key", "approval::", "authorization", "bearer ", "confirm::",
    "credential", "password", "secret", "token",
)


def _public_detail(value: Any) -> str:
    detail = str(value or "")[:300]
    lowered = detail.lower()
    return (
        "[redacted receipt detail]"
        if any(marker in lowered for marker in _SENSITIVE_DETAIL_MARKERS)
        else detail
    )


def _public_receipt_event(value: Mapping[str, Any] | None) -> dict[str, Any]:
    event: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if str(key) not in _PUBLIC_EVENT_FIELDS or not isinstance(item, (str, int, float, bool, type(None))):
            continue
        event[str(key)] = item
    return event


def _observe_embodiment_receipt(
    command: EmbodimentCommand,
    receipt: EmbodimentReceipt,
) -> None:
    payload = {
        "status": str(receipt.status or "UNKNOWN").upper(),
        "success": bool(receipt.success),
        "reason_code": receipt.reason_code,
        "detail": _public_detail(receipt.detail),
        "latency_ms": receipt.latency_ms,
        "event": {
            "kind": command.kind,
            "action": command.action,
            **_public_receipt_event(receipt.event),
        },
        "command_id": receipt.command_id,
        "receipt_ref": receipt.receipt_ref,
        "observed_at_ms": int(receipt.completed_at_ms or receipt.received_at_ms),
    }
    keys = {command.kind}
    if command.kind == "avatar":
        keys.add("vts")
    if command.kind == "mateengine":
        keys.add("mate")
    cursor = int(receipt.completed_at_ms or receipt.received_at_ms)
    with _receipt_lock:
        for key in keys:
            current = _latest_receipts.get(key)
            if current is None or cursor >= current[1]:
                _latest_receipts[key] = (dict(payload), cursor)


def load_device_status_projection_sources(
    ledger: Any | None = None,
    *,
    include_process_cache: bool = True,
) -> dict[str, tuple[dict[str, Any], int]]:
    sources: dict[str, tuple[dict[str, Any], int]] = {}
    if include_process_cache:
        with _receipt_lock:
            sources = {
                key: (dict(payload), int(cursor))
                for key, (payload, cursor) in _latest_receipts.items()
            }
    if ledger is None:
        return sources
    rows = _ledger_rows(
        ledger,
        "voice_event_log",
        start_ms=_now_ms() - 30 * 24 * 60 * 60 * 1000,
        end_ms=_now_ms(),
    )
    for row in rows:
        event_type = str(row.get("event_type") or "").strip().upper()
        if not event_type.startswith("EMBODIMENT_RECEIPT_"):
            continue
        raw_payload = row.get("payload")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw_payload, Mapping):
            continue
        kind = str(raw_payload.get("kind") or event_type.removeprefix("EMBODIMENT_RECEIPT_")).lower()
        payload = {
            "status": str(row.get("status") or "UNKNOWN").upper(),
            "success": bool(raw_payload.get("success", False)),
            "reason_code": row.get("reason_code"),
            "detail": _public_detail(row.get("detail")),
            "latency_ms": raw_payload.get("latency_ms"),
            "event": {
                "kind": kind,
                "action": str(raw_payload.get("action") or ""),
                **_public_receipt_event(
                    raw_payload.get("event")
                    if isinstance(raw_payload.get("event"), Mapping)
                    else {}
                ),
            },
            "command_id": str(raw_payload.get("command_id") or ""),
            "receipt_ref": raw_payload.get("receipt_ref"),
            "observed_at_ms": _safe_int(
                raw_payload.get("completed_at_ms")
                or raw_payload.get("received_at_ms")
                or row.get("ts_ms")
            ),
        }
        keys = {kind}
        if kind == "avatar":
            keys.add("vts")
        if kind == "mateengine":
            keys.add("mate")
        cursor = _safe_int(row.get("ts_ms") or payload["observed_at_ms"])
        for key in keys:
            current = sources.get(key)
            if current is None or cursor >= current[1]:
                sources[key] = (dict(payload), cursor)
    return sources


def refresh_device_status_query_projections() -> dict[str, int]:
    from host_brain.composition.process_services import get_ledger, get_query_projection_store

    sources = load_device_status_projection_sources(get_ledger())
    store = get_query_projection_store()
    for key, (payload, cursor) in sources.items():
        store.upsert(
            DEVICE_STATUS_KIND,
            key,
            payload,
            source_cursor=cursor,
            source_digest=digest_json(payload),
            source_ref="embodiment-receipt-background-worker",
        )
    return {"device_count": len(sources)}


def _state_metrics(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = snapshot.get("state")
    source = state if isinstance(state, Mapping) else snapshot
    metrics: dict[str, Any] = {}
    for key, value in source.items():
        normalized_key = str(key)
        if normalized_key.endswith("_ts_ms") or not isinstance(value, (str, int, float, bool)):
            continue
        metrics[normalized_key] = value
        if len(metrics) >= 6:
            break
    return metrics


def load_organ_tribe_projection_source(
    *,
    host_state_loader: Callable[[], Mapping[str, Any] | None] | None = None,
    scheduler_loader: Callable[[], Mapping[str, Any] | None] | None = None,
    now_ms: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Inspect already-loaded process state; never import/get an Agent singleton."""

    from host_brain.tribe import tribe_state

    now = max(0, int(now_ms if now_ms is not None else _now_ms()))
    registry = getattr(tribe_state, "_GLOBAL_REGISTRY", None)
    snapshots = registry.snapshot() if registry is not None else {}
    remote_state = dict(host_state_loader() or {}) if host_state_loader is not None else {}
    for item in list(remote_state.get("namespaces") or []):
        if not isinstance(item, Mapping):
            continue
        namespace = str(item.get("namespace") or "").strip()
        if namespace:
            snapshots[namespace] = dict(item)
    agents: list[dict[str, Any]] = []
    active = 0
    cursor = 0
    for agent_id, family, module_path, singleton_name in ORGAN_AGENT_REGISTRY:
        snapshot = snapshots.get(agent_id) or snapshots.get(family)
        module = sys.modules.get(module_path)
        singleton = getattr(module, singleton_name, None) if module is not None else None
        if isinstance(snapshot, Mapping):
            status = "active"
            metrics = _state_metrics(snapshot)
            last_ts = _safe_int(
                snapshot.get("updated_at_ms")
                or snapshot.get("last_updated_ts_ms")
                or snapshot.get("last_dispatch_ts_ms")
            ) or None
            active += 1
        elif singleton is not None:
            status = "loaded"
            metrics = {"process_instance": True}
            last_ts = None
            active += 1
        else:
            status = "inactive"
            metrics = {"process_instance": False}
            last_ts = None
        cursor = max(cursor, int(last_ts or 0))
        agents.append(
            {
                "agent_id": agent_id,
                "family": family,
                "status": status,
                "key_metrics": metrics,
                "last_updated_ts_ms": last_ts,
            }
        )

    scheduler = dict(scheduler_loader() or {}) if scheduler_loader is not None else {}
    decay = next(
        (
            dict(item)
            for item in list(scheduler.get("tasks") or [])
            if isinstance(item, Mapping) and str(item.get("task_id") or "") == "emotion_decay_tick"
        ),
        None,
    )
    runtime = scheduler.get("runtime") if isinstance(scheduler.get("runtime"), Mapping) else {}
    cursor = max(
        cursor,
        _safe_int(remote_state.get("timestamp") or remote_state.get("ts_ms")),
        _safe_int(scheduler.get("timestamp") or scheduler.get("ts_ms")),
        now,
    )
    payload = {
        "total_agents": len(agents),
        "active_agents": active,
        "agents": agents,
        "continuous_system_active": bool(runtime.get("active", decay and decay.get("enabled"))),
        "emotion_decay_tick_enabled": bool(decay and decay.get("enabled", True)),
        "source_process": "host" if remote_state else "projection-worker-local-fallback",
        "timestamp": now,
    }
    return payload, cursor


def refresh_organ_tribe_query_projection() -> dict[str, int]:
    from host_brain.composition.process_services import get_query_projection_store
    from host_brain.app.obs_host_proxy import fetch_host_json

    payload, cursor = load_organ_tribe_projection_source(
        host_state_loader=lambda: fetch_host_json("/internal/tribe_state/all", timeout_s=1.5),
        scheduler_loader=lambda: fetch_host_json("/scheduler/health", timeout_s=1.5),
    )
    get_query_projection_store().upsert(
        ORGAN_TRIBE_KIND,
        ORGAN_TRIBE_KEY,
        payload,
        source_cursor=cursor,
        source_digest=digest_json(payload),
        source_ref="organ-tribe-process-background-worker",
        degraded_state=(
            ProjectionDegradedState.HEALTHY
            if int(payload.get("active_agents") or 0) > 0
            else ProjectionDegradedState.SOURCE_UNAVAILABLE
        ),
        degraded_detail=(
            None
            if int(payload.get("active_agents") or 0) > 0
            else "no Host Organ-Tribe process state was observable"
        ),
    )
    return {"agent_count": len(payload["agents"])}


def read_projection_payload(
    store: QueryProjectionStore,
    *,
    kind: str,
    key: str,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    document = store.get(kind, key)
    meta = projection_meta(kind, key, document, max_age_ms=max_age_ms)
    return (dict(document.payload) if document is not None and meta["available"] else None), meta


def _run_job_if_due(
    name: str,
    interval_seconds: float,
    callback: Callable[[], dict[str, int]],
) -> dict[str, int | bool]:
    now = time.monotonic()
    with _job_lock:
        previous = _last_job_run.get(name, 0.0)
        if now - previous < max(0.1, float(interval_seconds)):
            return {"skipped": True}
        _last_job_run[name] = now
    return callback()


def run_health_projection_job() -> dict[str, int | bool]:
    return _run_job_if_due("health-passive", 30.0, refresh_health_query_projections)


def run_device_status_projection_job() -> dict[str, int | bool]:
    return _run_job_if_due("device-status", 1.0, refresh_device_status_query_projections)


def run_organ_tribe_projection_job() -> dict[str, int | bool]:
    return _run_job_if_due("organ-tribe", 10.0, refresh_organ_tribe_query_projection)


register_embodiment_receipt_observer(_observe_embodiment_receipt)
register_projection_job("health-passive", run_health_projection_job)
register_projection_job("device-status", run_device_status_projection_job)
register_projection_job("organ-tribe", run_organ_tribe_projection_job)


__all__ = [
    "DEVICE_MAX_AGE_MS", "DEVICE_STATUS_KIND", "HEALTH_MAX_AGE_MS",
    "LLM_NODE_HEALTH_KEY", "LLM_NODE_HEALTH_KIND", "ORGAN_AGENT_REGISTRY",
    "ORGAN_TRIBE_KEY", "ORGAN_TRIBE_KIND", "ORGAN_TRIBE_MAX_AGE_MS",
    "SERVICE_HEALTH_KEY", "SERVICE_HEALTH_KIND", "load_device_status_projection_sources",
    "load_health_projection_sources", "load_organ_tribe_projection_source",
    "read_projection_payload", "refresh_device_status_query_projections",
    "refresh_health_query_projections", "refresh_organ_tribe_query_projection",
    "run_device_status_projection_job", "run_health_projection_job",
    "run_organ_tribe_projection_job",
]
