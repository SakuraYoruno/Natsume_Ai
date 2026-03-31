from __future__ import annotations

import pytest

from host_brain.app import obs_routes
import host_brain.app.obs_routes_core as obs_routes_core
from host_brain.app.obs_routes import (
    LiveTraceFeedItem,
    SessionItem,
    SessionsResponse,
    TraceLiveFeedSnapshot,
    TraceNode,
    TraceResponse,
)


def _make_session(request_id: str, *, ts_ms: int = 1000) -> SessionItem:
    return SessionItem(
        request_id=request_id,
        session_id=f"sess-{request_id}",
        ts_ms=ts_ms,
        status="OK",
        total_latency_ms=12,
        input_preview=f"input-{request_id}",
    )


def _make_trace(request_id: str, *, ts_ms: int = 1000) -> TraceResponse:
    nodes = [
        TraceNode(
            node_id="governor",
            node_type="system",
            display_name="governor",
            status="OK",
            latency_ms=10,
            ts_ms=ts_ms,
        )
    ]
    return TraceResponse(
        request_id=request_id,
        session_id=f"sess-{request_id}",
        status="OK",
        total_latency_ms=10,
        node_count=1,
        nodes=nodes,
        edges=[],
        summary={"input_text": f"input-{request_id}"},
        ts_ms=ts_ms,
        timestamp=ts_ms,
    )


def test_obs_routes_keeps_legacy_exports_for_split_modules():
    required_symbols = [
        "router",
        "set_ledger_provider",
        "_fetch_remote_gateway_health",
        "_build_trace_live_feed_snapshot",
        "_build_trace_live_feed_delta",
        "SessionItem",
        "SessionsResponse",
        "TraceNode",
        "TraceResponse",
        "LiveTraceFeedItem",
        "TraceLiveFeedSnapshot",
    ]
    for symbol in required_symbols:
        assert hasattr(obs_routes, symbol), f"missing legacy symbol: {symbol}"


def test_trace_live_feed_snapshot_wrapper_uses_obs_routes_monkeypatch_and_restores_core(monkeypatch):
    original_build_sessions = obs_routes_core._build_sessions_response
    original_build_trace = obs_routes_core._build_trace_response
    original_now_ms = obs_routes_core._now_ms

    now_ms = 10_000
    sessions = SessionsResponse(
        total=1,
        page=1,
        page_size=1,
        items=[_make_session("req-compat", ts_ms=9_100)],
        timestamp=now_ms,
    )
    traces = {
        "req-compat": _make_trace("req-compat", ts_ms=9_500),
    }

    monkeypatch.setattr(obs_routes, "_now_ms", lambda: now_ms)
    monkeypatch.setattr(obs_routes, "_build_sessions_response", lambda *args, **kwargs: sessions)
    monkeypatch.setattr(obs_routes, "_build_trace_response", lambda _ledger, request_id: traces[request_id])

    snapshot = obs_routes._build_trace_live_feed_snapshot(
        object(),
        limit=1,
        window_minutes=1440,
        keyword=None,
        active_within_ms=1_000,
    )

    assert isinstance(snapshot, TraceLiveFeedSnapshot)
    assert snapshot.count == 1
    assert isinstance(snapshot.items[0], LiveTraceFeedItem)
    assert snapshot.items[0].session.request_id == "req-compat"
    assert snapshot.items[0].trace is not None

    assert obs_routes_core._build_sessions_response is original_build_sessions
    assert obs_routes_core._build_trace_response is original_build_trace
    assert obs_routes_core._now_ms is original_now_ms


def test_trace_live_feed_snapshot_wrapper_restores_core_on_exception(monkeypatch):
    original_build_sessions = obs_routes_core._build_sessions_response
    original_build_trace = obs_routes_core._build_trace_response
    original_now_ms = obs_routes_core._now_ms

    def _raise_error(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(obs_routes, "_build_sessions_response", _raise_error)

    with pytest.raises(RuntimeError):
        obs_routes._build_trace_live_feed_snapshot(
            object(),
            limit=1,
            window_minutes=60,
            keyword=None,
            active_within_ms=1_000,
        )

    assert obs_routes_core._build_sessions_response is original_build_sessions
    assert obs_routes_core._build_trace_response is original_build_trace
    assert obs_routes_core._now_ms is original_now_ms

