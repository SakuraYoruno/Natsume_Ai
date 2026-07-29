from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from host_brain.input_runtime.progress import build_gateway_progress_reply_text


class _Projection:
    def summarize_for_chat(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "RUNNING",
            "target_id": "research_agent",
            "progress_message": "节点正在执行",
            "elapsed_ms": 2_000,
            "result_text": "",
        }


def test_retired_progress_reply_never_reads_legacy_bond_or_user_emotion() -> None:
    captured: dict[str, Any] = {}

    def _render(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "安全进度文本"

    ports = SimpleNamespace(
        get_task_status_projection=lambda: _Projection(),
        render_quick_gateway_progress_text=_render,
    )

    text, reason, summary = build_gateway_progress_reply_text(
        request_id="request-progress",
        session_id="session-progress",
        payload={
            "person_id": "person-progress",
            "bond_summary": {"stage": "SOULMATE", "score": 1.0},
            "emotion_tag": "SAD",
        },
        ports=ports,
    )

    assert text == "安全进度文本"
    assert reason == "PROGRESS_SUMMARY_FOUND"
    assert summary is not None
    assert captured["bond_stage"] == "FRIEND"
    assert captured["emotion_tag"] == "CALM"
