from __future__ import annotations

import asyncio

from host_brain.app.executive_runtime import executive_runtime_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect(gen):
    return [item async for item in gen]


def test_executive_runtime_bridges_runtime_core_impl(monkeypatch) -> None:
    async def _fake_runtime_core_impl(**kwargs):  # noqa: ANN001
        assert kwargs["request_id"] == "req-runtime-1"
        assert kwargs["person_id"] == "person-runtime-1"
        yield 'data: {"phase":"COMMIT","done":true}\n\n'

    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core._executive_runtime_core_impl",
        _fake_runtime_core_impl,
    )

    events = _run(
        _collect(
            executive_runtime_sse_generator(
                request_body=object(),
                request_id="req-runtime-1",
                session_id="sess-runtime-1",
                ts_ms=4,
                intent_draft={},
                emotion_context={},
                emotion_snapshot={},
                governor_result={},
                intent_id="intent-runtime-1",
                metrics=object(),
                person_id="person-runtime-1",
                sanitized_message="hello",
                recall_slot_text={},
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

