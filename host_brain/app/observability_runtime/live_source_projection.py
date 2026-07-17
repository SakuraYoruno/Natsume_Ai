"""Background projection for the remaining live OBS query sources.

The single registered job owns all live reads in this slice: Edge gateway
health, the writer-capable mode service and the canonical Life snapshot.
Route handlers only read the materialized documents.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from .process_state_projection import read_process_projection
from .projection_ledger_view import get_projection_ledger_view
from host_brain.observability.projection_store import QueryProjectionStore, digest_json


GATEWAY_STATS_SOURCE_KIND = "gateway_stats_source"
GATEWAY_STATS_SOURCE_KEY = "current"
GATEWAY_STATS_MAX_AGE_MS = 60_000
SYS_MODE_STATUS_KIND = "sys_mode_status"
SYS_MODE_STATUS_KEY = "current"
SYS_MODE_HISTORY_KIND = "sys_mode_history"
SYS_MODE_HISTORY_KEY = "recent"
SYS_MODE_MAX_AGE_MS = 45_000
PROACTIVE_AUTONOMY_KIND = "proactive_autonomy"
PROACTIVE_AUTONOMY_DEFAULT_KEY = "default"
PROACTIVE_AUTONOMY_MAX_AGE_MS = 45_000

_job_lock = threading.Lock()
_last_job_run = 0.0


def read_operator_live_projection(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    *,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    return read_process_projection(store, kind, key, max_age_ms=max_age_ms)


def _public_gateway_health(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    body = source.get("body")
    adapters = body.get("adapters") if isinstance(body, Mapping) else {}
    projected: dict[str, Any] = {}
    if isinstance(adapters, Mapping):
        for platform, raw in adapters.items():
            if not isinstance(raw, Mapping):
                continue
            last_outbound = raw.get("last_outbound")
            projected[str(platform)] = {
                "status": str(raw.get("status") or "unknown"),
                "bridge_backend": str(raw.get("bridge_backend") or ""),
                "last_outbound": {
                    "ts_ms": int(last_outbound.get("ts_ms") or 0),
                }
                if isinstance(last_outbound, Mapping)
                else {},
            }
    return {
        "base_url": str(source.get("base_url") or ""),
        "body": {"adapters": projected},
    }


def _public_gateway_voice_row(value: Mapping[str, Any]) -> dict[str, Any]:
    """Re-wrap only the flattened, reviewed gateway dimensions.

    ``ProjectionLedgerView`` intentionally discards the source ``payload``.
    The gateway response shaper still consumes a payload-shaped value, so the
    background worker reconstructs that shape from four public scalars only.
    """

    row = dict(value)
    row["payload"] = {
        field: row.get(field)
        for field in ("platform", "source", "phase", "latency_ms")
        if row.get(field) is not None
    }
    return row


def _upsert(kind: str, key: str, payload: Mapping[str, Any], *, source_ref: str) -> None:
    from host_brain.composition.process_services import get_query_projection_store

    store = get_query_projection_store()
    now = int(time.time() * 1000)
    current = store.get(kind, key)
    cursor = max(now, int(current.source_cursor) + 1 if current is not None else now)
    store.upsert(
        kind,
        key,
        dict(payload),
        source_cursor=cursor,
        source_digest=digest_json(payload),
        source_ref=source_ref,
        freshness_ts_ms=now,
    )


def refresh_operator_live_state_projections(
    *,
    life_snapshot_reader: Any,
) -> dict[str, int]:
    """Materialize the three remaining live query sources in one fenced job."""

    from host_brain.app import obs_routes_platform as platform_routes
    from host_brain.app import obs_routes_proactive_autonomy as proactive_routes
    from host_brain.app import obs_routes_sysmode as sysmode_routes

    now = int(time.time() * 1000)
    ledger_view = get_projection_ledger_view()
    start_ms = now - 10_080 * 60 * 1000
    remote = asyncio.run(platform_routes._fetch_remote_gateway_health())
    channel_rows = ledger_view.fetch_by_time_range(
        "channel_io_log", start_ms=start_ms, end_ms=now, limit=5000
    )
    voice_rows = ledger_view.fetch_by_time_range(
        "voice_event_log", start_ms=start_ms, end_ms=now, limit=5000
    )
    gateway_payload = {
        "generated_at_ms": now,
        "channel_rows": channel_rows,
        "voice_rows": [_public_gateway_voice_row(row) for row in voice_rows],
        "remote_gateway_health": _public_gateway_health(remote),
        "runtime_config": {
            "wechat_bridge_backend": os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia"),
            "humanize_enabled": os.environ.get("EDGE_HUMANIZE_ENABLED", "false").lower() == "true",
            "humanize_typing_speed_cps": int(
                os.environ.get("EDGE_HUMANIZE_TYPING_SPEED_CPS", "12")
            ),
        },
    }
    _upsert(
        GATEWAY_STATS_SOURCE_KIND,
        GATEWAY_STATS_SOURCE_KEY,
        gateway_payload,
        source_ref="operator-live-state://gateway",
    )

    service = sysmode_routes._get_mode_transition_service()
    status = dict(service.get_current_status() or {})
    history = [dict(item) for item in list(service.get_history(limit=100) or [])]
    _upsert(
        SYS_MODE_STATUS_KIND,
        SYS_MODE_STATUS_KEY,
        status,
        source_ref="operator-live-state://sys-mode/status",
    )
    _upsert(
        SYS_MODE_HISTORY_KIND,
        SYS_MODE_HISTORY_KEY,
        {"items": history, "total": len(history), "timestamp": now},
        source_ref="operator-live-state://sys-mode/history",
    )

    proactive = proactive_routes.build_proactive_autonomy_snapshot(
        None,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
        life_snapshot_reader=life_snapshot_reader,
    )
    _upsert(
        PROACTIVE_AUTONOMY_KIND,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
        proactive,
        source_ref="operator-live-state://proactive-autonomy/default",
    )
    return {"document_count": 4}


def run_operator_live_state_projection_job(
    *,
    life_snapshot_reader: Any,
) -> dict[str, int | bool]:
    global _last_job_run
    now = time.monotonic()
    with _job_lock:
        if now - _last_job_run < 10.0:
            return {"skipped": True}
        _last_job_run = now
    return refresh_operator_live_state_projections(
        life_snapshot_reader=life_snapshot_reader,
    )


__all__ = [
    "GATEWAY_STATS_MAX_AGE_MS",
    "GATEWAY_STATS_SOURCE_KEY",
    "GATEWAY_STATS_SOURCE_KIND",
    "PROACTIVE_AUTONOMY_DEFAULT_KEY",
    "PROACTIVE_AUTONOMY_KIND",
    "PROACTIVE_AUTONOMY_MAX_AGE_MS",
    "SYS_MODE_HISTORY_KEY",
    "SYS_MODE_HISTORY_KIND",
    "SYS_MODE_MAX_AGE_MS",
    "SYS_MODE_STATUS_KEY",
    "SYS_MODE_STATUS_KIND",
    "read_operator_live_projection",
    "refresh_operator_live_state_projections",
    "run_operator_live_state_projection_job",
]
