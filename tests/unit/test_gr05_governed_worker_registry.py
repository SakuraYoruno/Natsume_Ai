from __future__ import annotations

import pytest

from host_brain.task_runtime.worker_registry import (
    GOVERNED_WORKER_EXECUTOR_UNSUPPORTED,
    GOVERNED_WORKER_ROUTE_INVALID,
    GOVERNED_WORKER_ROUTE_UNBOUND,
    GovernedWorkerRegistry,
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)


class _Worker:
    async def execute_stream(self, **_):  # noqa: ANN003
        if False:
            yield {}


def test_route_contract_decodes_compiled_skill_and_delegate_targets() -> None:
    skill = GovernedWorkerRoute.parse("skill:code%2Fresearch")
    delegate = GovernedWorkerRoute.parse("delegate:research_agent")

    assert skill.kind == "skill"
    assert skill.target_id == "code/research"
    assert delegate.kind == "delegate"
    assert delegate.target_id == "research_agent"
    assert GovernedWorkerRoute.parse("chat").target_id is None
    assert GovernedWorkerRoute.parse("chat:surface_reply").target_id == "surface_reply"
    with pytest.raises(GovernedWorkerRegistryError) as legacy:
        GovernedWorkerRoute.parse("host_skill")
    assert legacy.value.reason_code == GOVERNED_WORKER_ROUTE_INVALID


def test_registry_resolves_all_bound_route_kinds_and_only_canonical_executor() -> None:
    chat, skill, delegate = _Worker(), _Worker(), _Worker()
    registry = GovernedWorkerRegistry(chat=chat, skill=skill, delegate=delegate)

    assert registry.resolve(executor="canonical", worker_route="chat") is chat
    assert registry.resolve(executor="canonical", worker_route="chat:surface_reply") is chat
    assert registry.resolve(executor="canonical", worker_route="skill:echo") is skill
    assert registry.resolve(executor="canonical", worker_route="delegate:research_agent") is delegate

    with pytest.raises(GovernedWorkerRegistryError) as raised:
        registry.resolve(executor="codex", worker_route="chat")
    assert raised.value.reason_code == GOVERNED_WORKER_EXECUTOR_UNSUPPORTED


def test_unbound_or_malformed_routes_fail_closed() -> None:
    registry = GovernedWorkerRegistry(skill=_Worker())

    with pytest.raises(GovernedWorkerRegistryError) as unbound:
        registry.resolve(executor="canonical", worker_route="delegate:research_agent")
    assert unbound.value.reason_code == GOVERNED_WORKER_ROUTE_UNBOUND

    for route in ("", "plan", "skill", "delegate:", "skill:%00bad"):
        with pytest.raises(GovernedWorkerRegistryError) as invalid:
            registry.resolve(executor="canonical", worker_route=route)
        assert invalid.value.reason_code == GOVERNED_WORKER_ROUTE_INVALID

    assert registry.supports("skill:echo") is True
    assert registry.supports("chat") is False
    assert registry.supports("plan") is False
