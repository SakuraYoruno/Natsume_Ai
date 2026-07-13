import time

import pytest

from host_brain.app.obs_routes_trace import (
    LiveTraceFeedItem,
    TraceLiveFeedSnapshot,
    _refresh_trace_feed_warm_once,
    _trace_feed_warm_lock,
    _trace_feed_warm_state,
    get_trace_live_feed_snapshot,
)
from host_brain.app.obs_routes import SessionItem
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_runtime import TRACE_FEED_INDEX_KEY, TRACE_FEED_KIND
from host_brain.observability.projection_store import QueryProjectionStore


@pytest.fixture
def projection_store(tmp_path, monkeypatch):
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="trace-feed-unit")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_query_projection_store",
        lambda: store,
    )
    return store


def _reset_trace_feed_warm_state() -> None:
    with _trace_feed_warm_lock:
        _trace_feed_warm_state["value"] = None
        _trace_feed_warm_state["updated_ts_ms"] = 0
        _trace_feed_warm_state["last_duration_ms"] = 0
        _trace_feed_warm_state["last_error"] = None
        _trace_feed_warm_state["session_signature"] = None


def test_trace_feed_snapshot_prefers_durable_projection(monkeypatch, projection_store):
    now = int(time.time() * 1000)
    warmed = TraceLiveFeedSnapshot(
        items=[
            LiveTraceFeedItem(
                session=SessionItem(
                    request_id="req-warm-trace",
                    session_id="sess-warm-trace",
                    ts_ms=now,
                    status="OK",
                    total_latency_ms=120,
                    input_preview="warm trace",
                ),
                trace=None,
                error=None,
            )
        ],
        count=1,
        timestamp=now,
    )
    projection_store.upsert(
        TRACE_FEED_KIND,
        TRACE_FEED_INDEX_KEY,
        warmed.model_dump() if hasattr(warmed, "model_dump") else warmed.dict(),
        source_cursor=now,
    )

    def _unexpected_builder(**kwargs):
        raise AssertionError("trace feed builder should not run when warm payload is available")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    result = get_trace_live_feed_snapshot(limit=6, window_minutes=1440, active_within_ms=60000, keyword=None)

    assert result.count == 1
    assert result.items[0].session["request_id"] == "req-warm-trace"


def test_trace_feed_snapshot_ignores_warm_payload_under_pressure(monkeypatch, projection_store):
    now = int(time.time() * 1000)
    projected = TraceLiveFeedSnapshot(items=[], count=0, timestamp=now)
    projection_store.upsert(
        TRACE_FEED_KIND,
        TRACE_FEED_INDEX_KEY,
        projected.model_dump() if hasattr(projected, "model_dump") else projected.dict(),
        source_cursor=now,
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._trace_feed_should_defer_build",
        lambda: (True, "natsume_runtime_cpu_pressure"),
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._get_warmed_trace_feed_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("GET read warm cache")),
    )

    def _unexpected_builder(**kwargs):
        raise AssertionError("trace feed builder should not run under pressure with stale warm payload")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    result = get_trace_live_feed_snapshot(limit=6, window_minutes=1440, active_within_ms=60000, keyword=None)

    assert result.count == 0
    assert result.projection["available"] is True


def test_trace_feed_snapshot_returns_degraded_when_projection_empty(monkeypatch, projection_store):
    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._trace_feed_should_defer_build",
        lambda: (True, "natsume_runtime_cpu_pressure"),
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._get_warmed_trace_feed_snapshot",
        lambda **kwargs: None,
    )

    def _unexpected_builder(**kwargs):
        raise AssertionError("trace feed builder should not run under pressure without cache")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    result = get_trace_live_feed_snapshot(limit=6, window_minutes=1440, active_within_ms=60000, keyword=None)

    assert result.count == 0
    assert result.items == []
    assert result.projection["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"


def test_trace_feed_warm_refresh_skips_full_build_without_active_sessions(monkeypatch):
    _reset_trace_feed_warm_state()
    monkeypatch.setattr(
        "host_brain.app.obs_resource_guard.should_defer_obs_warm_refresh",
        lambda *, task_name: (False, ""),
    )
    monkeypatch.setattr(
        "host_brain.app.obs_resource_guard.try_acquire_obs_warm_slot",
        lambda *, task_name: (True, ""),
    )
    monkeypatch.setattr("host_brain.app.obs_resource_guard.release_obs_warm_slot", lambda: None)
    monkeypatch.setattr("host_brain.app.obs_routes_trace._trace_feed_session_signature", lambda **kwargs: ())

    def _unexpected_builder(**kwargs):
        raise AssertionError("full trace feed builder should not run when no sessions are active")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    _refresh_trace_feed_warm_once()

    with _trace_feed_warm_lock:
        warmed = _trace_feed_warm_state["value"]
        last_duration_ms = _trace_feed_warm_state["last_duration_ms"]
        last_error = _trace_feed_warm_state["last_error"]

    assert warmed.count == 0
    assert warmed.items == []
    assert last_duration_ms == 0
    assert last_error is None


def test_trace_feed_warm_refresh_reuses_snapshot_for_unchanged_sessions(monkeypatch):
    _reset_trace_feed_warm_state()
    signature = (("req-stable", 1700000000000, "OK", 123),)
    warmed = TraceLiveFeedSnapshot(
        items=[
            LiveTraceFeedItem(
                session=SessionItem(
                    request_id="req-stable",
                    session_id="sess-stable",
                    ts_ms=1700000000000,
                    status="OK",
                    total_latency_ms=123,
                    input_preview="stable",
                ),
                trace=None,
                error=None,
            )
        ],
        count=1,
        timestamp=1700000000001,
    )
    with _trace_feed_warm_lock:
        _trace_feed_warm_state["value"] = warmed
        _trace_feed_warm_state["updated_ts_ms"] = 1700000000100
        _trace_feed_warm_state["session_signature"] = signature

    monkeypatch.setattr(
        "host_brain.app.obs_resource_guard.should_defer_obs_warm_refresh",
        lambda *, task_name: (False, ""),
    )
    monkeypatch.setattr(
        "host_brain.app.obs_resource_guard.try_acquire_obs_warm_slot",
        lambda *, task_name: (True, ""),
    )
    monkeypatch.setattr("host_brain.app.obs_resource_guard.release_obs_warm_slot", lambda: None)
    monkeypatch.setattr("host_brain.app.obs_routes_trace._trace_feed_session_signature", lambda **kwargs: signature)
    monkeypatch.setattr("host_brain.app.obs_routes_trace._now_ms", lambda: 1700000000200)

    def _unexpected_builder(**kwargs):
        raise AssertionError("full trace feed builder should not run while session signature is unchanged")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    _refresh_trace_feed_warm_once()

    with _trace_feed_warm_lock:
        refreshed = _trace_feed_warm_state["value"]
        last_duration_ms = _trace_feed_warm_state["last_duration_ms"]
        last_error = _trace_feed_warm_state["last_error"]

    assert refreshed.count == 1
    assert refreshed.items[0].session.request_id == "req-stable"
    assert last_duration_ms == 0
    assert last_error is None
