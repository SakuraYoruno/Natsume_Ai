from host_brain.app.obs_routes_trace import (
    LiveTraceFeedItem,
    TraceLiveFeedSnapshot,
    get_trace_live_feed_snapshot,
)
from host_brain.app.obs_routes import SessionItem


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
