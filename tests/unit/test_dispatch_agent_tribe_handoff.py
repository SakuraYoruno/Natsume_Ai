from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from host_brain.app.dispatch_agent import dispatch_agent_sse_generator
from host_brain.app.runtime_task_registry import RuntimeTaskRegistry


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


def _parse_sse_event(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw[len("data: ") :].strip())


def test_dispatch_agent_records_handoff_for_chat_route(monkeypatch) -> None:
    records: list[dict] = []

    class _Coordinator:
        def record_dispatch_handoff(self, **kwargs):
            records.append(dict(kwargs))
            return kwargs

    async def _fake_chat_agent(_chat_input):
        yield 'data: {"phase":"CHAT","route":"chat"}\n\n'

    monkeypatch.setattr(
        "host_brain.tribe.handoff.get_tribe_handoff_coordinator",
        lambda: _Coordinator(),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("chat", "", "default_chat"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_runtime_task_registry",
        lambda: RuntimeTaskRegistry(),
    )
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(reply_channel="WEB", thread_id="thread-handoff"),
                request_id="req-handoff",
                session_id="sess-handoff",
                ts_ms=9_000,
                intent_draft={},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-handoff",
                metrics=SimpleNamespace(),
                person_id="person-handoff",
                sanitized_message="just chatting",
                recall_slot_text={},
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    assert _parse_sse_event(events[0])["route"] == "chat"
    assert len(records) == 1
    assert records[0]["request_id"] == "req-handoff"
    assert records[0]["session_id"] == "sess-handoff"
    assert records[0]["route"] == "chat"
    assert records[0]["target_id"] is None
    assert records[0]["reason"] == "default_chat"
