from __future__ import annotations

import asyncio

from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect(gen):
    return [item async for item in gen]


def test_cortex_orchestrator_calls_canonical_runtime_impl(monkeypatch) -> None:
    async def _fake_cortex_impl(**kwargs):  # noqa: ANN001
        assert kwargs["request_id"] == "req-cortex-1"
        yield 'data: {"phase":"COMMIT","done":true}\n\n'

    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._cortex_orchestrator_sse_generator_impl",
        _fake_cortex_impl,
    )

    events = _run(
        _collect(
            cortex_orchestrator_sse_generator(
                request_body=object(),
                request_id="req-cortex-1",
                session_id="sess-cortex-1",
                ts_ms=1,
                intent_draft={},
                emotion_context={},
                emotion_snapshot={},
                governor_result={},
                intent_id="intent-cortex-1",
                metrics=object(),
            )
        )
    )

    assert len(events) == 1
    assert '"phase":"COMMIT"' in events[0]
