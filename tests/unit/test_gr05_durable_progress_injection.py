from __future__ import annotations

from types import SimpleNamespace

from host_brain.input_runtime.progress import build_gateway_progress_reply_text


class _Projection:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def summarize_for_chat(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return self._summary()

    def summarize_for_session(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return self._summary()

    @staticmethod
    def _summary():
        return {
            "status": "RUNNING",
            "target_id": "research_agent",
            "progress_message": "节点正在执行",
            "elapsed_ms": 2_000,
            "result_text": "",
        }


def _render(**kwargs):  # noqa: ANN003
    return f"{kwargs['status']}:{kwargs.get('target_id', '')}"


def test_input_runtime_progress_reads_injected_durable_projection() -> None:
    projection = _Projection()
    ports = SimpleNamespace(
        get_task_status_projection=lambda: projection,
        render_quick_gateway_progress_text=_render,
    )

    text, reason, summary = build_gateway_progress_reply_text(
        request_id="request-progress",
        session_id="session-progress",
        payload={"person_id": "person-progress"},
        exclude_request_id="request-progress",
        ports=ports,
    )

    assert text == "RUNNING:research_agent"
    assert reason == "PROGRESS_SUMMARY_FOUND"
    assert summary is not None and summary["status"] == "RUNNING"
    assert projection.calls == [
        {
            "person_id": "person-progress",
            "session_id": "session-progress",
            "exclude_request_id": "request-progress",
        }
    ]


def test_input_runtime_progress_can_read_by_session_without_person_id() -> None:
    projection = _Projection()
    ports = SimpleNamespace(
        get_task_status_projection=lambda: projection,
        render_quick_gateway_progress_text=_render,
    )

    _text, reason, summary = build_gateway_progress_reply_text(
        request_id="request-progress",
        session_id="session-progress",
        payload={},
        ports=ports,
    )

    assert reason == "PROGRESS_SUMMARY_FOUND"
    assert summary is not None
    assert projection.calls == [
        {"session_id": "session-progress", "exclude_request_id": None}
    ]
