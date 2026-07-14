from __future__ import annotations

import asyncio

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_RUNTIME_INACTIVE,
    GovernedHostAdapterError,
    GovernedHostRuntime,
)


class _EmptyCommandRepository:
    def get_by_request(self, *, surface: str, request_id: str):
        del surface, request_id
        return None


class _BlockingApplication:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_stream(self, **_):
        self.started.set()
        await self.release.wait()
        yield {"phase": "TERMINAL", "done": True, "decision": "APPROVE"}


def _request(ts_ms: int = 1_800_000_000_000) -> dict:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-runtime-drain",
        "session_id": "session-runtime-drain",
        "seq": 1,
        "nonce": "nonce-runtime-drain",
        "route": "skill",
        "target_id": "echo_skill",
        "task": "drain the governed runtime",
        "args": {"text": "in-flight"},
    }
    governor = {
        "request_id": "request-runtime-drain",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "tokens": {},
        "ts_ms": ts_ms,
    }
    return {
        "request_body": {},
        "request_id": governor["request_id"],
        "session_id": intent["session_id"],
        "ts_ms": ts_ms,
        "intent_draft": intent,
        "governor_result": governor,
        "person_id": "person-runtime-drain",
        "sanitized_message": "drain the governed runtime",
    }


@pytest.mark.asyncio
async def test_close_admission_rejects_new_work_and_drain_waits_for_in_flight() -> None:
    application = _BlockingApplication()
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=_EmptyCommandRepository(),
        drain_timeout_s=1.0,
    )
    await runtime.activate()

    consuming = asyncio.create_task(
        _collect(runtime.execute_stream(**_request())),
    )
    await application.started.wait()
    await runtime.close_admission()

    draining = asyncio.create_task(runtime.drain())
    await asyncio.sleep(0)
    assert draining.done() is False

    with pytest.raises(GovernedHostAdapterError) as exc_info:
        await anext(runtime.execute_stream(**_request(ts_ms=1_800_000_005_000)))
    assert exc_info.value.reason_code == GOVERNED_HOST_RUNTIME_INACTIVE

    application.release.set()
    assert (await consuming)[-1]["decision"] == "APPROVE"
    await draining
    await runtime.aclose()


async def _collect(stream):
    return [event async for event in stream]
