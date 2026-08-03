"""Background-only health, device and Organ-Tribe query projections.

Source loaders live in this module so HTTP query handlers cannot accidentally
probe a service, instantiate an Agent, submit a command, or scan a ledger.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.projection_runtime import projection_meta
from host_brain.observability.projection_store import QueryProjectionStore, digest_json
from host_brain.observability.model_attempt_projection import (
    latest_terminal_model_attempts,
    model_attempt_node_id,
)
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


# ``service_health_log`` uses canonical runtime service ids while the public
# Monitor contract predates that registry.  Keep this translation explicit:
# an unregistered canonical service must never overwrite an unrelated card.
_DURABLE_SERVICE_HEALTH_PUBLIC_IDS: Mapping[str, tuple[str, ...]] = {
    "host.audit.sqlite_ledger": ("sqlite_ledger",),
    "host.integrations.llm_gateway": ("llm_gateway",),
    "edge.rpc.gateway": ("edge_rpc",),
    "edge.rpc.asr": ("asr",),
    "edge.rpc.tts": ("tts",),
    "edge.rpc.vts": ("vts",),
    "edge.rpc.audio_playback": ("audio_playback",),
}
_EXPLICIT_HEALTH_STATUSES = frozenset(
    {"UP", "DEGRADED", "DOWN", "NOT_TESTED", "UNKNOWN"}
)
_SAME_MOMENT_HEALTH_PRECEDENCE = {
    "UNKNOWN": 0,
    "UP": 1,
    "NOT_TESTED": 2,
    "DEGRADED": 3,
    "DOWN": 4,
}


ORGAN_AGENT_REGISTRY: tuple[tuple[str, str], ...] = (
    ("limbic_agent", "limbic"),
    ("social_brain_agent", "social_brain"),
    ("prefrontal_agent", "prefrontal"),
    ("ans_agent", "ans"),
    ("immune_agent", "immune"),
    ("pain_reward_agent", "pain_reward"),
    ("hippocampus_agent", "hippocampus"),
    ("cortex_agent", "cortex"),
    ("peripheral_agent", "peripheral"),
    ("motor_cortex_agent", "motor_cortex"),
    ("thalamus_agent", "thalamus"),
    ("language_system_agent", "language"),
    ("endocrine_agent", "endocrine"),
    ("initiative_governor_agent", "initiative_governor"),
    ("circadian_agent", "circadian"),
    ("mirror_neuron_agent", "mirror_neuron"),
    ("somatic_agent", "somatic"),
    ("proprioception_agent", "proprioception"),
    ("interoception_agent", "interoception"),
    ("vestibular_agent", "vestibular"),
    ("glymphatic_agent", "glymphatic"),
    ("cerebellum_agent", "cerebellum"),
    ("default_mode_agent", "default_mode"),
)

# Public Organ projection is deny-by-default.  Registry state may contain
# conversation, emotion, immune, or identity material, so a new metric cannot
# cross this boundary until its exact field name and scalar type are reviewed.
_SAFE_OPERATIONAL_AGENT_METRICS = frozenset(
    {
        "active",
        "enabled",
        "error_count",
        "pending_count",
        "queue_depth",
        "running",
    }
)
ORGAN_AGENT_PUBLIC_METRIC_ALLOWLIST: Mapping[str, frozenset[str]] = {
    agent_id: _SAFE_OPERATIONAL_AGENT_METRICS
    for agent_id, _family in ORGAN_AGENT_REGISTRY
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
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


def _explicit_health_overrides(
    rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project the latest durable runtime report into public service ids.

    Several canonical Edge services intentionally collapse into the older
    ``audio_playback`` card.  For rows written in the same report millisecond,
    the least healthy result wins deterministically.  A newer report always
    supersedes an older one.
    """

    selected: dict[str, tuple[tuple[int, int, int, str], Mapping[str, Any]]] = {}
    gateway_not_tested: tuple[tuple[int, int, int, str], Mapping[str, Any]] | None = None
    for row in rows:
        canonical_id = str(row.get("service_id") or "").strip()
        status = str(row.get("status") or "UNKNOWN").strip().upper()
        public_ids = _DURABLE_SERVICE_HEALTH_PUBLIC_IDS.get(canonical_id, ())
        if not public_ids or status not in _EXPLICIT_HEALTH_STATUSES:
            continue
        ts_ms = _safe_int(row.get("ts_ms"))
        identity = str(row.get("request_id") or row.get("id") or canonical_id)
        rank = (
            ts_ms,
            _SAME_MOMENT_HEALTH_PRECEDENCE.get(status, 0),
            _safe_int(row.get("id")),
            identity,
        )
        for public_id in public_ids:
            previous = selected.get(public_id)
            if previous is None or rank > previous[0]:
                selected[public_id] = (rank, row)
        if canonical_id == "edge.rpc.gateway" and status == "NOT_TESTED":
            if gateway_not_tested is None or rank > gateway_not_tested[0]:
                gateway_not_tested = (rank, row)

    # VTS is owned by the remote Edge but is not a member of the five-service
    # RPC admission contract.  Only the explicit whole-Edge NOT_TESTED fact may
    # fill this gap; an ordinary TTS/ASR failure must never be generalized to
    # VTS.
    if gateway_not_tested is not None:
        for public_id in ("vts", "audio_playback"):
            previous = selected.get(public_id)
            if previous is None or gateway_not_tested[0] > previous[0]:
                selected[public_id] = gateway_not_tested

    projected: dict[str, dict[str, Any]] = {}
    for public_id, (rank, row) in selected.items():
        status = str(row.get("status") or "UNKNOWN").strip().upper()
        detail = _public_detail(row.get("detail"))
        projected[public_id] = {
            "status": status,
            "success_rate": None,
            "p95_ms": None,
            "last_call_ts": rank[0] or None,
            "call_count": 0,
            "reason_code": str(row.get("reason_code") or "").strip() or None,
            # ``detail`` is retained for in-process callers; ``public_summary``
            # is the reviewed field that survives the public projection
            # serializer into the standalone Obs process.
            "detail": detail or None,
            "public_summary": detail or None,
        }
    return projected


def _newer_explicit_health(
    computed: Mapping[str, Any],
    explicit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not explicit:
        return dict(computed)
    computed_ts = _safe_int(computed.get("last_call_ts"))
    explicit_ts = _safe_int(explicit.get("last_call_ts"))
    if str(computed.get("status") or "UNKNOWN").upper() == "UNKNOWN" or explicit_ts >= computed_ts:
        return dict(explicit)
    return dict(computed)


def load_health_projection_sources(
    ledger: Any,
    *,
    now_ms: int | None = None,
    window_minutes: int = 60,
    include_process_cache: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Rebuild health only from durable audit rows; never launch a probe.

    ``include_process_cache`` remains source-compatible for callers compiled
    before the Guardian corrective slice.  A module cache cannot cross a Host
    restart or the dedicated Obs process boundary, so it deliberately has no
    effect on this production read model.
    """

    del include_process_cache

    now = max(0, int(now_ms if now_ms is not None else _now_ms()))
    since = now - max(1, int(window_minutes)) * 60_000
    voice_rows = _ledger_rows(ledger, "voice_event_log", start_ms=since, end_ms=now)
    service_health_rows = _ledger_rows(
        ledger,
        "service_health_log",
        start_ms=since,
        end_ms=now,
    )
    explicit_health = _explicit_health_overrides(service_health_rows)
    model_attempt_rows = latest_terminal_model_attempts(
        _ledger_rows(ledger, "model_attempt_log", start_ms=since, end_ms=now)
    )
    pool_rows = _ledger_rows(ledger, "llm_pool_call_log", start_ms=since, end_ms=now)
    llm_gateway_rows: list[Mapping[str, Any]] = list(model_attempt_rows)
    llm_gateway_rows.extend(
        {
            "status": "failed" if bool(row.get("pool_exhausted")) else "succeeded",
            "latency_ms": row.get("total_latency_ms"),
            "ts_ms": row.get("ts_ms"),
            "request_id": row.get("request_id"),
        }
        for row in pool_rows
    )
    # Sources are loaded independently and therefore arrive in source-grouped
    # order.  Health's rolling window must be chronological across both the
    # canonical ModelAttempt path and the explicit legacy pool path; otherwise
    # the second appended source wins regardless of recency.
    llm_gateway_rows.sort(
        key=lambda row: (
            _safe_int(
                row.get("ended_at_ms")
                if row.get("ended_at_ms") is not None
                else row.get("ts_ms")
            ),
            str(row.get("attempt_id") or row.get("request_id") or ""),
            _safe_int(row.get("id")),
        )
    )

    services: list[dict[str, Any]] = []
    for service in ALL_SERVICES:
        service_id = service["service_id"]
        if service_id in EDGE_SERVICE_EVENT_PREFIXES:
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
            health = _compute_health(llm_gateway_rows)
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
        health = _newer_explicit_health(health, explicit_health.get(service_id))
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
        matching = [
            row
            for row in model_attempt_rows
            if model_attempt_node_id(row) == node_id
        ]
        health = _compute_health(matching)
        nodes.append({"node_id": node_id, **meta, **health})

    counts = {
        key: 0
        for key in ("UP", "DEGRADED", "DOWN", "NOT_TESTED", "UNKNOWN")
    }
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
        "not_tested": counts["NOT_TESTED"],
        "unknown": counts["UNKNOWN"],
        "services": services,
        "timestamp": now,
    }
    node_payload = {"nodes": nodes, "timestamp": now}
    return service_payload, node_payload, cursor


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


def load_device_status_projection_sources(
    ledger: Any | None = None,
    *,
    include_process_cache: bool = False,
) -> dict[str, tuple[dict[str, Any], int]]:
    """Rebuild device status exclusively from durable Host receipt facts.

    ``include_process_cache`` remains a temporary source-compatible argument for
    callers compiled before D8.  It deliberately has no effect: a process
    cache cannot be a cross-restart or cross-application observation source.
    """

    del include_process_cache
    sources: dict[str, tuple[dict[str, Any], int]] = {}
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


def _state_metrics(snapshot: Mapping[str, Any], *, agent_id: str) -> dict[str, Any]:
    state = snapshot.get("state")
    source = state if isinstance(state, Mapping) else snapshot
    allowed = ORGAN_AGENT_PUBLIC_METRIC_ALLOWLIST.get(agent_id, frozenset())
    metrics: dict[str, Any] = {}
    for key, value in source.items():
        normalized_key = str(key)
        if (
            normalized_key not in allowed
            or isinstance(value, str)
            or not isinstance(value, (int, float, bool))
        ):
            continue
        metrics[normalized_key] = value
        if len(metrics) >= 6:
            break
    return metrics


def _snapshot_evidence_ts_ms(
    snapshot: Mapping[str, Any],
    *,
    observed_at_ms: int,
    candidate_keys: tuple[str, ...],
) -> int:
    """Return bounded source evidence, never the time of this observation."""

    state = snapshot.get("state")
    sources = (snapshot, state) if isinstance(state, Mapping) else (snapshot,)
    evidence = 0
    for source in sources:
        for key in candidate_keys:
            candidate = _safe_int(source.get(key))
            if 0 < candidate <= observed_at_ms:
                evidence = max(evidence, candidate)
    return evidence


def build_organ_tribe_public_snapshot(
    *,
    registry_snapshots: Mapping[str, Any],
    scheduler_snapshot: Mapping[str, Any],
    observed_at_ms: int,
) -> tuple[dict[str, Any], int]:
    """Build the bounded Host Organ-Tribe read model from explicit snapshots.

    This function is deliberately composition-free: it does not inspect loaded
    modules, discover a process registry, or manufacture evidence from the
    observation clock.  A catalog entry is active only when its supplied state
    has a positive, non-future update/evidence timestamp.
    """

    observed = max(0, _safe_int(observed_at_ms))
    snapshots = dict(registry_snapshots)
    scheduler = dict(scheduler_snapshot)
    agents: list[dict[str, Any]] = []
    active = 0
    evidence_ts_ms = 0

    for agent_id, family in ORGAN_AGENT_REGISTRY:
        candidate = snapshots.get(agent_id)
        if not isinstance(candidate, Mapping):
            candidate = snapshots.get(family)
        snapshot = dict(candidate) if isinstance(candidate, Mapping) else None
        if snapshot is None:
            status = "inactive"
            metrics = {"process_instance": False}
            last_ts = None
        else:
            candidate_ts = _snapshot_evidence_ts_ms(
                snapshot,
                observed_at_ms=observed,
                candidate_keys=(
                    "evidence_ts_ms",
                    "updated_at_ms",
                    "last_updated_ts_ms",
                ),
            )
            metrics = _state_metrics(snapshot, agent_id=agent_id)
            if candidate_ts > 0:
                status = "active"
                last_ts = candidate_ts
                active += 1
                evidence_ts_ms = max(evidence_ts_ms, candidate_ts)
            else:
                # A supplied state proves only that a snapshot was available;
                # without bounded evidence it must not count as live activity.
                status = "loaded"
                last_ts = None

        agents.append(
            {
                "agent_id": agent_id,
                "family": family,
                "status": status,
                "key_metrics": metrics,
                "last_updated_ts_ms": last_ts,
            }
        )

    runtime = scheduler.get("runtime")
    runtime = dict(runtime) if isinstance(runtime, Mapping) else {}
    scheduler_evidence_ts_ms = max(
        _snapshot_evidence_ts_ms(
            scheduler,
            observed_at_ms=observed,
            candidate_keys=(
                "evidence_ts_ms",
                "updated_at_ms",
                "last_updated_ts_ms",
                "timestamp",
                "ts_ms",
            ),
        ),
        _snapshot_evidence_ts_ms(
            runtime,
            observed_at_ms=observed,
            candidate_keys=(
                "evidence_ts_ms",
                "updated_at_ms",
                "last_updated_ts_ms",
                "last_tick_ms",
            ),
        ),
    )
    evidence_ts_ms = max(evidence_ts_ms, scheduler_evidence_ts_ms)

    tasks = list(scheduler.get("tasks") or [])
    decay = next(
        (
            dict(item)
            for item in tasks
            if isinstance(item, Mapping)
            and str(item.get("task_id") or "") == "emotion_decay_tick"
        ),
        None,
    )
    seeded_task_ids = {
        str(item)
        for item in list(scheduler.get("seeded_task_ids") or [])
        if str(item).strip()
    }
    payload = {
        "total_agents": len(agents),
        "active_agents": active,
        "agents": agents,
        "continuous_system_active": bool(
            runtime.get("active", scheduler.get("clock_active", False))
        ),
        "emotion_decay_tick_enabled": bool(
            (decay and decay.get("enabled", True))
            or "emotion_decay_tick" in seeded_task_ids
        ),
        "source_process": "host-runtime",
        "timestamp": observed,
        "observed_at_ms": observed,
        "evidence_ts_ms": evidence_ts_ms,
    }
    return payload, evidence_ts_ms


def load_organ_tribe_projection_source(
    *,
    registry_snapshots: Mapping[str, Any] | None = None,
    scheduler_snapshot: Mapping[str, Any] | None = None,
    host_state_loader: Callable[[], Mapping[str, Any] | None] | None = None,
    scheduler_loader: Callable[[], Mapping[str, Any] | None] | None = None,
    observed_at_ms: int | None = None,
    now_ms: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Compatibility adapter around the explicit public snapshot builder."""

    observed = max(
        0,
        _safe_int(
            observed_at_ms
            if observed_at_ms is not None
            else now_ms if now_ms is not None else _now_ms()
        ),
    )
    snapshots = dict(registry_snapshots or {})
    remote_state = dict(host_state_loader() or {}) if host_state_loader is not None else {}
    for item in list(remote_state.get("namespaces") or []):
        if not isinstance(item, Mapping):
            continue
        namespace = str(item.get("namespace") or "").strip()
        if namespace:
            snapshots[namespace] = dict(item)
    scheduler = dict(scheduler_snapshot or {})
    if scheduler_loader is not None:
        scheduler.update(dict(scheduler_loader() or {}))
    return build_organ_tribe_public_snapshot(
        registry_snapshots=snapshots,
        scheduler_snapshot=scheduler,
        observed_at_ms=observed,
    )


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


__all__ = [
    "DEVICE_MAX_AGE_MS", "DEVICE_STATUS_KIND", "HEALTH_MAX_AGE_MS",
    "LLM_NODE_HEALTH_KEY", "LLM_NODE_HEALTH_KIND", "ORGAN_AGENT_REGISTRY",
    "ORGAN_AGENT_PUBLIC_METRIC_ALLOWLIST",
    "ORGAN_TRIBE_KEY", "ORGAN_TRIBE_KIND", "ORGAN_TRIBE_MAX_AGE_MS",
    "SERVICE_HEALTH_KEY", "SERVICE_HEALTH_KIND", "load_device_status_projection_sources",
    "build_organ_tribe_public_snapshot", "load_health_projection_sources",
    "load_organ_tribe_projection_source",
    "read_projection_payload",
]
