from __future__ import annotations

import asyncio

from host_brain.app.chat_retrieval_helpers import _run_retrieval


def _run(coro):
    return asyncio.run(coro)


def test_run_retrieval_routes_to_hippocampus_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Hippocampus:
        async def recall(self, **kwargs):
            captured.update(kwargs)
            return {"status": "OK", "reason_code": "NONE"}, [{"snippet": "from_hippocampus"}]

    monkeypatch.setattr(
        "host_brain.tribe.hippocampus_agent.get_hippocampus_agent",
        lambda: _Hippocampus(),
    )

    summary, evidence = _run(
        _run_retrieval(
            request_id="req-retrieval",
            message="继续推进",
            person_id="person-1",
            session_id="session-1",
            ts_ms=1000,
            emotion_tag="NEUTRAL",
            render_target_lang="zh",
            mood_state="steady",
            bond_summary={"stage": "friend"},
            motive_state={"focus": "task"},
        )
    )

    assert summary["status"] == "OK"
    assert evidence[0]["snippet"] == "from_hippocampus"
    assert captured["request_id"] == "req-retrieval"
    assert captured["message"] == "继续推进"
    assert captured["render_target_lang"] == "zh"
