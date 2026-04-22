from __future__ import annotations

import asyncio

from host_brain.app.executive_router import executive_router_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect(gen):
    return [item async for item in gen]


def test_executive_router_calls_canonical_runtime_entry(monkeypatch) -> None:
    async def _fake_runtime_impl(**kwargs):  # noqa: ANN001
        assert kwargs["request_id"] == "req-router-1"
        assert kwargs["person_id"] == "person-1"
        yield 'data: {"phase":"COMMIT","done":true}\n\n'

    monkeypatch.setattr(
        "host_brain.app.executive_runtime.executive_runtime_sse_generator",
        _fake_runtime_impl,
    )

    events = _run(
        _collect(
            executive_router_sse_generator(
                request_body=object(),
                request_id="req-router-1",
                session_id="sess-router-1",
                ts_ms=2,
                intent_draft={},
                emotion_context={},
                emotion_snapshot={},
                governor_result={},
                intent_id="intent-router-1",
                metrics=object(),
                person_id="person-1",
                sanitized_message="hello",
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime=None,
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    assert '"phase":"COMMIT"' in events[0]
