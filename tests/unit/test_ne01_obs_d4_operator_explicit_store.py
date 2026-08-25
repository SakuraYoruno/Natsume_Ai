from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDocument

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes_platform import get_gateway_stats
from host_brain.app.obs_routes_proactive_autonomy import get_proactive_autonomy
from host_brain.app.obs_routes_sysmode import get_sys_mode, get_sys_mode_history
from host_brain.app.observability_runtime.live_source_projection import (
    GATEWAY_STATS_SOURCE_KEY,
    GATEWAY_STATS_SOURCE_KIND,
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)


class _ProjectionStore:
    def __init__(self, documents: dict[tuple[str, str], ProjectionDocument]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, str]] = []

    def get(self, kind: str, key: str) -> ProjectionDocument | None:
        slot = (kind, key)
        self.calls.append(slot)
        return self.documents.get(slot)


def _document(kind: str, key: str, payload: dict[str, Any]) -> ProjectionDocument:
    now = int(time.time() * 1000)
    return ProjectionDocument(
        kind=kind,
        key=key,
        payload=payload,
        projection_version=1,
        projection_cursor=1,
        source_cursor=1,
        source_digest="sha256:source",
        payload_digest="sha256:payload",
        freshness_ts_ms=now,
    )


def _store(marker: str, *, generated_at_ms: int) -> _ProjectionStore:
    history = {
        "items": [
            {
                "event_id": f"event-{marker}",
                "from_mode": "normal",
                "to_mode": "focus",
                "source": "manual",
                "reason": marker,
                "ts_ms": generated_at_ms,
                "success": True,
            }
        ],
        "total": 1,
        "timestamp": generated_at_ms,
    }
    status = {
        "sys_mode": f"mode-{marker}",
        "auto_rules": {},
        "idle_ms": 0,
        "idle_minutes": 0.0,
        "last_interaction_ts_ms": generated_at_ms,
        "is_night_time": False,
        "valid_transitions": [],
        "ts_ms": generated_at_ms,
    }
    proactive = {
        "person_id": f"person-{marker}",
        "available": True,
        "inner_weather": {},
        "reach_out_calibration": {},
        "active_projects": [],
        "proactive_readiness": 0.5,
        "top_intention": None,
        "dialogue_closure": {},
        "timestamp": generated_at_ms,
    }
    gateway = {
        "generated_at_ms": generated_at_ms,
        "channel_rows": [],
        "voice_rows": [],
        "remote_gateway_health": {},
        "runtime_config": {},
    }
    documents = {
        (GATEWAY_STATS_SOURCE_KIND, GATEWAY_STATS_SOURCE_KEY): _document(
            GATEWAY_STATS_SOURCE_KIND,
            GATEWAY_STATS_SOURCE_KEY,
            gateway,
        ),
        (PROACTIVE_AUTONOMY_KIND, PROACTIVE_AUTONOMY_DEFAULT_KEY): _document(
            PROACTIVE_AUTONOMY_KIND,
            PROACTIVE_AUTONOMY_DEFAULT_KEY,
            proactive,
        ),
        (SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY): _document(
            SYS_MODE_STATUS_KIND,
            SYS_MODE_STATUS_KEY,
            status,
        ),
        (SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY): _document(
            SYS_MODE_HISTORY_KIND,
            SYS_MODE_HISTORY_KEY,
            history,
        ),
    }
    return _ProjectionStore(documents)


def _request(store: _ProjectionStore) -> SimpleNamespace:
    ports = ObsRoutePorts(query_projection_store_provider=lambda: store)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(obs_route_ports=ports)),
    )


def _read_all(request: SimpleNamespace) -> dict[str, Any]:
    gateway = get_gateway_stats(request, window_minutes=60)
    proactive = get_proactive_autonomy(request, person_id="default")
    status = get_sys_mode(request)
    history = get_sys_mode_history(request, limit=20)
    return {
        "gateway_timestamp": gateway.timestamp,
        "proactive_person_id": proactive["person_id"],
        "sys_mode": status.sys_mode,
        "history_event_id": history.items[0].event_id,
    }


def test_four_operator_gets_use_explicit_app_store() -> None:
    now = int(time.time() * 1000)
    store = _store("local", generated_at_ms=now)

    assert _read_all(_request(store)) == {
        "gateway_timestamp": now,
        "proactive_person_id": "person-local",
        "sys_mode": "mode-local",
        "history_event_id": "event-local",
    }
    assert store.calls == [
        (GATEWAY_STATS_SOURCE_KIND, GATEWAY_STATS_SOURCE_KEY),
        (PROACTIVE_AUTONOMY_KIND, PROACTIVE_AUTONOMY_DEFAULT_KEY),
        (SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY),
        (SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY),
    ]


def test_four_operator_gets_isolate_projection_store_per_obs_app() -> None:
    now = int(time.time() * 1000)
    first = _store("first", generated_at_ms=now)
    second = _store("second", generated_at_ms=now + 1)

    assert _read_all(_request(first)) == {
        "gateway_timestamp": now,
        "proactive_person_id": "person-first",
        "sys_mode": "mode-first",
        "history_event_id": "event-first",
    }
    assert _read_all(_request(second)) == {
        "gateway_timestamp": now + 1,
        "proactive_person_id": "person-second",
        "sys_mode": "mode-second",
        "history_event_id": "event-second",
    }
    assert len(first.calls) == 4
    assert len(second.calls) == 4
