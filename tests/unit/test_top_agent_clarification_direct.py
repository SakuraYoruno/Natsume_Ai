from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.app import top_agent
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.app.top_agent import top_agent_sse_generator
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


async def _noop_async(*_args, **_kwargs):  # noqa: ANN001
    return None


def _install_store(monkeypatch, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "top_agent.runtime_texts",
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_maybe_build_previous_meaning_answer_only_handles_short_followup(monkeypatch) -> None:
    monkeypatch.setattr(
        top_agent,
        "_load_continuity_turns",
        lambda **_kwargs: [{"role": "assistant", "content": "很高兴认识你"}],
    )

    assert top_agent._maybe_build_previous_meaning_answer(
        request_id="req",
        session_id="session",
        person_id="person",
        user_message="什么意思",
        render_target_lang="zh",
    ) == "我刚才的意思是：很高兴认识你"

    assert top_agent._maybe_build_previous_meaning_answer(
        request_id="req",
        session_id="session",
        person_id="person",
        user_message="这个错误是什么意思？",
        render_target_lang="zh",
    ) is None


def test_maybe_build_previous_meaning_answer_uses_template_override(monkeypatch) -> None:
    store = _install_store(
        monkeypatch,
        {
            "clarification_direct_answer": {
                "assistant_meaning_template": "换句话说：{latest_assistant}",
            }
        },
    )
    monkeypatch.setattr(
        top_agent,
        "_load_continuity_turns",
        lambda **_kwargs: [{"role": "assistant", "content": "很高兴认识你"}],
    )

    with patch("host_brain.app.dependencies.get_prompt_template_store", return_value=store):
        assert top_agent._maybe_build_previous_meaning_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="这句什么意思",
            render_target_lang="zh",
        ) == "换句话说：很高兴认识你"


def test_clarification_query_is_answered_directly_without_llm() -> None:
    request_body = SimpleNamespace(message="什么意思", session_id="session-001", thread_id=None)
    metrics = MagicMock()

    with patch(
        "host_brain.app.top_agent.chat_lite_governor",
        return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
    ), patch(
        "host_brain.app.top_agent._maybe_build_task_progress_fastpath",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_previous_meaning_answer",
        return_value="我刚才的意思是：很高兴认识你",
    ), patch(
        "host_brain.app.top_agent._async_post_process",
        new=_noop_async,
    ), patch(
        "host_brain.app.chat_agent.chat_agent_sse_generator",
        side_effect=AssertionError("chat_agent should not run for clarification_direct"),
    ):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-clarification-direct",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.1},
                    emotion_snapshot={"emotion_tag": "NEUTRAL"},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                ),
            ),
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "top_agent:clarification_direct"
    assert commit["narrative"] == "我刚才的意思是：很高兴认识你"
