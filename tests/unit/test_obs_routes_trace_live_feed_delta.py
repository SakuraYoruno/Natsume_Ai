from host_brain.app.obs_routes import (
    LiveTraceFeedItem,
    SessionItem,
    TraceLiveFeedSnapshot,
    TraceNode,
    TraceResponse,
    _build_trace_live_feed_delta,
)


def _make_session(request_id: str, *, status: str = "OK", ts_ms: int = 1000) -> SessionItem:
    return SessionItem(
        request_id=request_id,
        session_id=f"sess-{request_id}",
        ts_ms=ts_ms,
        status=status,
        total_latency_ms=120,
        input_preview=f"input-{request_id}",
    )


def _make_trace(request_id: str, nodes, *, status: str = "OK", ts_ms: int = 1000) -> TraceResponse:
    return TraceResponse(
        request_id=request_id,
        session_id=f"sess-{request_id}",
        status=status,
        total_latency_ms=120,
        node_count=len(nodes),
        nodes=nodes,
        edges=[],
        summary={"input_text": f"input-{request_id}"},
        ts_ms=ts_ms,
        timestamp=ts_ms,
    )


def _make_node(node_id: str, *, status: str = "OK", ts_ms: int = 1000, latency_ms: int = 10) -> TraceNode:
    return TraceNode(
        node_id=node_id,
        node_type="system",
        display_name=node_id,
        status=status,
        latency_ms=latency_ms,
        ts_ms=ts_ms,
    )


def test_trace_live_feed_delta_detects_added_and_updated_nodes():
    previous = TraceLiveFeedSnapshot(
        items=[
            LiveTraceFeedItem(
                session=_make_session("req-live-1"),
                trace=_make_trace(
                    "req-live-1",
                    [
                        _make_node("governor", status="OK", ts_ms=1100),
                        _make_node("recall_fusion", status="RUNNING", ts_ms=1200),
                    ],
                    ts_ms=1200,
                ),
            )
        ],
        count=1,
        timestamp=1200,
    )

    current = TraceLiveFeedSnapshot(
        items=[
            LiveTraceFeedItem(
                session=_make_session("req-live-1", ts_ms=1400),
                trace=_make_trace(
                    "req-live-1",
                    [
                        _make_node("governor", status="OK", ts_ms=1100),
                        _make_node("recall_fusion", status="OK", ts_ms=1400, latency_ms=32),
                        _make_node("llm_gateway", status="RUNNING", ts_ms=1500),
                    ],
                    ts_ms=1500,
                ),
            )
        ],
        count=1,
        timestamp=1500,
    )

    delta = _build_trace_live_feed_delta(previous, current)

    assert delta is not None
    assert delta.type == "delta"
    assert delta.count == 1
    assert delta.items[0].request_id == "req-live-1"
    assert delta.items[0].change_count == 2

    changes = {
        (change.change_type, change.node.node_id): change
        for change in delta.items[0].changes
    }
    assert ("updated", "recall_fusion") in changes
    assert changes[("updated", "recall_fusion")].previous_status == "RUNNING"
    assert ("added", "llm_gateway") in changes
    assert delta.items[0].latest_node.node_id == "llm_gateway"


def test_trace_live_feed_delta_returns_none_without_node_changes():
    nodes = [
        _make_node("governor", status="OK", ts_ms=1100),
        _make_node("llm_gateway", status="OK", ts_ms=1500),
    ]
    previous = TraceLiveFeedSnapshot(
        items=[LiveTraceFeedItem(session=_make_session("req-live-2"), trace=_make_trace("req-live-2", nodes, ts_ms=1500))],
        count=1,
        timestamp=1500,
    )
    current = TraceLiveFeedSnapshot(
        items=[LiveTraceFeedItem(session=_make_session("req-live-2", ts_ms=1600), trace=_make_trace("req-live-2", nodes, ts_ms=1500))],
        count=1,
        timestamp=1600,
    )

    assert _build_trace_live_feed_delta(previous, current) is None
