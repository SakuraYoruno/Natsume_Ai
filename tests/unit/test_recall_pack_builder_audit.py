from __future__ import annotations

from host_brain.app.recall.recall_pack_builder import RecallPackBuilder


def test_recall_pack_trace_audit_emitted() -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, detail: dict) -> None:
        events.append((event, detail))

    builder = RecallPackBuilder(audit_writer=_capture)
    pack = builder.build(
        {"world_canon": "test world canon"},
        task_type="chat",
        request_id="req_test_trace",
    )

    trace_events = [detail for event, detail in events if event == "RECALL_PACK_TRACE"]
    assert trace_events
    detail = trace_events[-1]
    assert detail["request_id"] == "req_test_trace"
    assert detail["task_type"] == "chat"
    assert detail["total_tokens"] == pack.total_token_count
    assert detail["trace"]
