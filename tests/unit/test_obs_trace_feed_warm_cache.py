from host_brain.app.obs_routes_trace import (
    LiveTraceFeedItem,
    TraceLiveFeedSnapshot,
    _refresh_trace_feed_warm_once,
    _trace_feed_warm_lock,
    _trace_feed_warm_state,
    get_trace_live_feed_snapshot,
)
from host_brain.app.obs_routes import SessionItem


def _reset_trace_feed_warm_state() -> None:
    with _trace_feed_warm_lock:
        _trace_feed_warm_state["value"] = None
        _trace_feed_warm_state["updated_ts_ms"] = 0
        _trace_feed_warm_state["last_duration_ms"] = 0
        _trace_feed_warm_state["last_error"] = None
        _trace_feed_warm_state["session_signature"] = None


def test_trace_feed_snapshot_prefers_warmed_payload(monkeypatch):
    warmed = TraceLiveFeedSnapshot(
        items=[
            LiveTraceFeedItem(
                session=SessionItem(
                    request_id="req-warm-trace",
                    session_id="sess-warm-trace",
                    ts_ms=1700000000000,
                    status="OK",
                    total_latency_ms=120,
                    input_preview="warm trace",
                ),
                trace=None,
                error=None,
            )
        ],
        count=1,
        timestamp=1700000000001,
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._get_warmed_trace_feed_snapshot",
        lambda **kwargs: warmed,
    )

    def _unexpected_builder(**kwargs):
        raise AssertionError("trace feed builder should not run when warm payload is available")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    result = get_trace_live_feed_snapshot()

    assert result.count == 1
    assert result.items[0].session.request_id == "req-warm-trace"


def test_trace_feed_snapshot_uses_stale_warm_payload_under_pressure(monkeypatch):
    warmed = TraceLiveFeedSnapshot(items=[], count=0, timestamp=1700000000001)
    captured: dict[str, object] = {}

    def _fake_warmed(**kwargs):
        captured.update(kwargs)
        return warmed

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._trace_feed_should_defer_build",
        lambda: (True, "natsume_runtime_cpu_pressure"),
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._get_warmed_trace_feed_snapshot",
        _fake_warmed,
    )

    def _unexpected_builder(**kwargs):
        raise AssertionError("trace feed builder should not run under pressure with stale warm payload")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._build_trace_live_feed_snapshot_sync",
        _unexpected_builder,
    )

    result = get_trace_live_feed_snapshot()

    assert result is warmed
    assert int(captured["allow_stale_ms"]) > 45000


def test_trace_feed_snapshot_returns_empty_when_pressure_and_no_cache(monkeypatch):
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

    result = get_trace_live_feed_snapshot()

    assert result.count == 0
    assert result.items == []


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
