from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
import host_brain.app.governed_task_adapter as governed_task_adapter
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
    GOVERNED_HOST_RUNTIME_INACTIVE,
    GovernedHostAdapterError,
    GovernedHostRuntime,
)
from natsume_contracts.task import HostCommandSurface


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
        delegate_target_validator=lambda _target: None,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "target_id", "input_source", "expected_surface"),
    (
        ("chat", "surface_reply", "GATEWAY", HostCommandSurface.GATEWAY),
        ("delegate", "intent_agent", "ASR", HostCommandSurface.HOST_INPUT),
    ),
)
async def test_secure_ingress_direct_routes_preserve_surface_principal_and_digest(
    route: str,
    target_id: str,
    input_source: str,
    expected_surface: HostCommandSurface,
) -> None:
    repository = _ReplayCommandRepository()
    runtime = GovernedHostRuntime(
        application=_CapturingApplication(repository),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=repository,
        supported_routes=frozenset({"chat", "delegate"}),
        delegate_target_validator=lambda _target: None,
    )
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-secure-{route}",
        "session_id": f"session-secure-{route}",
        "seq": 1,
        "nonce": f"nonce-secure-{route}",
        "route": route,
        "target_id": target_id,
        "task": f"secure governed {route}",
        "args": {},
    }
    governor = {
        "request_id": f"request-secure-{route}",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "risk_level": 1,
        "tokens": {},
        "ts_ms": 1_800_000_000_000,
    }
    authority = {
        "principal_id": "service.edge.gateway",
        "claim_ref": f"secure-ingress-claim://aw02/{route}",
        "audit_ref": f"secure-ingress-audit://aw02/{route}",
        "body_sha256": "a" * 64,
    }
    await runtime.activate()
    try:
        events = await _collect(
            runtime.execute_stream(
                request_body=SimpleNamespace(input_source=input_source),
                request_id=governor["request_id"],
                session_id=intent["session_id"],
                ts_ms=governor["ts_ms"],
                intent_draft=intent,
                governor_result=governor,
                person_id="person-secure-route",
                sanitized_message=intent["task"],
                command_authority=authority,
            )
        )
        assert events[-1]["decision"] == "APPROVE"
        command = repository.record["command"]
        assert command.surface is expected_surface
        assert command.principal_id == authority["principal_id"]
        assert command.ingress_claim_ref == authority["claim_ref"]
        assert command.ingress_audit_ref == authority["audit_ref"]
        assert command.input_sha256 == "sha256:" + authority["body_sha256"]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_direct_delegate_rejects_unenabled_target_before_application_admission() -> None:
    repository = _ReplayCommandRepository()
    application = _CapturingApplication(repository)

    def reject(_target: str) -> None:
        raise RuntimeError("delegate target not enabled")

    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=repository,
        supported_routes=frozenset({"delegate"}),
        delegate_target_validator=reject,
    )
    request = _request()
    request["intent_draft"] = {
        **request["intent_draft"],
        "route": "delegate",
        "target_id": "repair_agent",
    }
    await runtime.activate()
    try:
        with pytest.raises(GovernedHostAdapterError) as raised:
            await _collect(runtime.execute_stream(**request))
        assert raised.value.reason_code == GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE
        assert application.calls == 0
        assert repository.record is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_secure_ingress_plan_preserves_surface_principal_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class _PlanService:
        def __init__(self, **_kwargs) -> None:  # noqa: ANN003
            pass

        async def prepare_initial(self, _request, *, authority, created_at):  # noqa: ANN001
            captured.extend((authority, created_at))
            return object()

        async def execute_stream(self, _prepared):  # noqa: ANN001
            yield {"phase": "COMMIT", "done": True, "decision": "APPROVE"}

    monkeypatch.setattr(
        governed_task_adapter,
        "build_plan_intent_search",
        lambda *_args, **_kwargs: SimpleNamespace(request=object()),
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "GovernedPlanningExecutionService",
        _PlanService,
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "GovernedPlanningAdmissionService",
        lambda **_kwargs: object(),
    )
    runtime = GovernedHostRuntime(
        application=_BlockingApplication(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=_EmptyCommandRepository(),
        planning_enabled=True,
    )
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-secure-plan",
        "session_id": "session-secure-plan",
        "seq": 1,
        "nonce": "nonce-secure-plan",
        "route": "plan",
        "target_id": "planning_search",
        "task": "secure governed plan",
        "planned_steps": [{"candidate_id": "step-1"}],
    }
    governor = {
        "request_id": "request-secure-plan",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "tokens": {},
        "ts_ms": 1_800_000_000_000,
    }
    authority = {
        "principal_id": "service.edge.gateway",
        "claim_ref": "secure-ingress-claim://aw02/plan",
        "audit_ref": "secure-ingress-audit://aw02/plan",
        "body_sha256": "b" * 64,
    }
    await runtime.activate()
    try:
        events = await _collect(
            runtime.execute_stream(
                request_body=SimpleNamespace(input_source="GATEWAY"),
                request_id=governor["request_id"],
                session_id=intent["session_id"],
                ts_ms=governor["ts_ms"],
                intent_draft=intent,
                governor_result=governor,
                person_id="person-secure-plan",
                sanitized_message=intent["task"],
                command_authority=authority,
            )
        )
        assert events[-1]["decision"] == "APPROVE"
        plan_authority = captured[0]
        assert plan_authority.surface is HostCommandSurface.GATEWAY
        assert plan_authority.principal_id == authority["principal_id"]
        assert plan_authority.ingress_claim_ref == authority["claim_ref"]
        assert plan_authority.ingress_audit_ref == authority["audit_ref"]
        assert plan_authority.input_sha256 == "sha256:" + authority["body_sha256"]
        assert plan_authority.input_ref.endswith(authority["body_sha256"])
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_capability_plan_route_uses_registry_search_without_legacy_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _PlanService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["resolver"] = kwargs.get("compilation_context_resolver")

        async def prepare_initial(self, _request, *, authority, created_at):  # noqa: ANN001
            captured["authority"] = authority
            captured["created_at"] = created_at
            return object()

        async def execute_stream(self, _prepared):  # noqa: ANN001
            yield {"phase": "COMMIT", "done": True, "decision": "APPROVE"}

    capability_adapter = SimpleNamespace(
        request=object(),
        compilation_context_for=lambda _result: object(),
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "build_capability_plan_search",
        lambda *_args, **_kwargs: capability_adapter,
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "build_plan_intent_search",
        lambda *_args, **_kwargs: pytest.fail("legacy plan adapter must not run"),
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "GovernedPlanningExecutionService",
        _PlanService,
    )
    monkeypatch.setattr(
        governed_task_adapter,
        "GovernedPlanningAdmissionService",
        lambda **_kwargs: object(),
    )
    runtime = GovernedHostRuntime(
        application=_BlockingApplication(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=_EmptyCommandRepository(),
        planning_enabled=True,
        planning_skill_registry=object(),
    )
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-capability-plan-route",
        "session_id": "session-capability-plan-route",
        "seq": 1,
        "nonce": "nonce-capability-plan-route",
        "route": "plan",
        "target_id": "planning_search",
        "task": "search registered research capabilities",
        "args": {
            "planning_mode": "CAPABILITY_SEARCH_V1",
            "planning_capability_tags": ["research"],
        },
    }
    governor = {
        "request_id": "request-capability-plan-route",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "tokens": {},
        "ts_ms": 1_800_000_000_000,
    }

    assert runtime.supports(intent) is True
    await runtime.activate()
    try:
        events = await _collect(
            runtime.execute_stream(
                request_body=SimpleNamespace(input_source="GATEWAY"),
                request_id=governor["request_id"],
                session_id=intent["session_id"],
                ts_ms=governor["ts_ms"],
                intent_draft=intent,
                governor_result=governor,
                person_id="person-capability-plan-route",
                sanitized_message=intent["task"],
                command_authority={"body_sha256": "e" * 64},
            )
        )
        assert events[-1]["decision"] == "APPROVE"
        assert captured["resolver"] is capability_adapter.compilation_context_for
        authority = captured["authority"]
        assert authority.target_id == "planning_search"
    finally:
        await runtime.aclose()


def test_capability_plan_requires_app_composed_skill_registry() -> None:
    intent = {
        "route": "plan",
        "args": {"planning_mode": "CAPABILITY_SEARCH_V1"},
    }
    runtime = GovernedHostRuntime(
        application=SimpleNamespace(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=SimpleNamespace(),
        planning_enabled=True,
    )

    assert runtime.supports(intent) is False


async def _collect(stream):
    return [event async for event in stream]
