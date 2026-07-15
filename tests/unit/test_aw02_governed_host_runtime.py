from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_AUTHORITY_INVALID,
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


class _ReplayCommandRepository:
    def __init__(self) -> None:
        self.record = None

    def get_by_request(self, *, surface: str, request_id: str):
        del surface, request_id
        return self.record


class _CapturingApplication:
    def __init__(self, repository: _ReplayCommandRepository) -> None:
        self._repository = repository
        self.calls = 0

    async def execute_stream(self, **kwargs):
        self.calls += 1
        if self._repository.record is None:
            self._repository.record = {
                "command": kwargs["command"],
                "work_order": kwargs["work_order"],
                "assignment": kwargs["assignment"],
                "graph": kwargs["graph"],
            }
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "target_id", "drift"),
    (
        ("chat", "surface_reply", "operation_fingerprint"),
        ("chat", "surface_reply", "worker_route"),
        ("chat", "surface_reply", "target_id"),
        ("delegate", "intent_agent", "operation_fingerprint"),
        ("delegate", "intent_agent", "worker_route"),
        ("delegate", "intent_agent", "target_id"),
    ),
)
async def test_chat_and_delegate_exact_replay_use_route_aware_authority_validation(
    route: str,
    target_id: str,
    drift: str,
) -> None:
    repository = _ReplayCommandRepository()
    application = _CapturingApplication(repository)
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=repository,
        supported_routes=frozenset({"chat", "delegate"}),
    )
    await runtime.activate()
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-runtime-replay-{route}",
        "session_id": f"session-runtime-replay-{route}",
        "seq": 1,
        "nonce": f"nonce-runtime-replay-{route}",
        "route": route,
        "target_id": target_id,
        "task": f"replay the governed {route} route",
        "args": {},
    }
    governor = {
        "request_id": f"request-runtime-replay-{route}",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "risk_level": 1,
        "tokens": {},
        "ts_ms": 1_800_000_000_000,
    }
    request = {
        "request_body": {},
        "request_id": governor["request_id"],
        "session_id": intent["session_id"],
        "ts_ms": governor["ts_ms"],
        "intent_draft": intent,
        "governor_result": governor,
        "person_id": f"person-runtime-replay-{route}",
        "sanitized_message": intent["task"],
    }

    first = await _collect(runtime.execute_stream(**request))
    replay = await _collect(runtime.execute_stream(**request))

    assert first[-1]["decision"] == "APPROVE"
    assert replay[-1]["decision"] == "APPROVE"
    assert application.calls == 2

    durable = repository.record["work_order"]
    if drift == "operation_fingerprint":
        repository.record["work_order"] = replace(
            durable,
            operation_fingerprint="sha256:" + "b" * 64,
        )
    else:
        metadata = dict(durable.metadata)
        planning_lineage = dict(metadata["planning_lineage"])
        node_bindings = [dict(item) for item in planning_lineage["node_bindings"]]
        node_bindings[0][drift] = "skill" if drift == "worker_route" else "drifted-target"
        planning_lineage["node_bindings"] = node_bindings
        metadata["planning_lineage"] = planning_lineage
        repository.record["work_order"] = replace(durable, metadata=metadata)
    with pytest.raises(GovernedHostAdapterError) as exc_info:
        await _collect(runtime.execute_stream(**request))
    assert exc_info.value.reason_code == GOVERNED_HOST_AUTHORITY_INVALID
    assert application.calls == 2
    await runtime.aclose()


async def _collect(stream):
    return [event async for event in stream]
