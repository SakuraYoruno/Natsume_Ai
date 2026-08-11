from __future__ import annotations

from typing import Any

import pytest

from host_brain.app.governed_skill_execution_ports import (
    build_governed_skill_execution_ports,
)
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE,
    GovernedHostAdapterError,
    _FrozenSkillWorker,
    _build_contracts,
    _require_frozen_skill_dependencies,
)
from host_brain.runtime.degradation_authority import LegacyModuleGlobalDegradationAdapter


def _ports(
    *,
    resource_manager: Any | None = None,
    episodic_store: Any | None = None,
    semantic_recaller: Any | None = None,
):  # noqa: ANN201
    return build_governed_skill_execution_ports(
        skill_registry=object(),
        capability_broker=object(),
        model_attempt_service=object(),
        production_authority_verifier=lambda **_kwargs: True,
        resource_manager=resource_manager,
        episodic_store=episodic_store,
        semantic_recaller=semantic_recaller,
        degradation_capability_port=LegacyModuleGlobalDegradationAdapter(),
    )


@pytest.mark.parametrize(
    ("target", "args", "resource_manager", "episodic_store", "semantic_recaller"),
    [
        ("resource_manager_status", {}, None, object(), object()),
        ("memory_store", {"content": "remember"}, object(), None, object()),
        ("memory_forget", {"memory_id": "memory-1"}, object(), None, object()),
        (
            "memory_search",
            {"search_scope": "recent_episode"},
            object(),
            None,
            object(),
        ),
        (
            "memory_search",
            {"search_scope": "long_term"},
            object(),
            object(),
            None,
        ),
        (
            "memory_search",
            {"search_scope": "line"},
            object(),
            object(),
            None,
        ),
        (
            "memory_search",
            {"search_scope": "hybrid"},
            object(),
            None,
            None,
        ),
        (
            "memory_search",
            {"memory_type": "semantic"},
            object(),
            object(),
            None,
        ),
        (
            "memory_search",
            {"tags": ["quote"]},
            object(),
            object(),
            None,
        ),
    ],
)
def test_frozen_skill_dependency_preflight_rejects_missing_required_snapshot(
    target: str,
    args: dict[str, Any],
    resource_manager: Any | None,
    episodic_store: Any | None,
    semantic_recaller: Any | None,
) -> None:
    with pytest.raises(GovernedHostAdapterError) as raised:
        _require_frozen_skill_dependencies(
            ports=_ports(
                resource_manager=resource_manager,
                episodic_store=episodic_store,
                semantic_recaller=semantic_recaller,
            ),
            target=target,
            args=args,
        )

    assert raised.value.reason_code == GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize(
    ("target", "args", "resource_manager", "episodic_store", "semantic_recaller"),
    [
        ("resource_manager_status", {}, object(), None, None),
        ("memory_store", {"content": "remember"}, None, object(), None),
        ("memory_forget", {"memory_id": "memory-1"}, None, object(), None),
        ("memory_forget", {"dry_run": True}, None, None, None),
        ("memory_search", {"search_scope": "recent"}, None, object(), None),
        ("memory_search", {"search_scope": "semantic"}, None, None, object()),
        ("memory_search", {"search_scope": "line"}, None, None, object()),
        ("memory_search", {"search_scope": "hybrid"}, None, object(), None),
        ("memory_search", {"search_scope": "mixed"}, None, None, object()),
        ("memory_preference_extract", {}, None, None, None),
        ("memory_consolidate", {}, None, None, None),
    ],
)
def test_frozen_skill_dependency_preflight_allows_satisfied_or_backend_free_skill(
    target: str,
    args: dict[str, Any],
    resource_manager: Any | None,
    episodic_store: Any | None,
    semantic_recaller: Any | None,
) -> None:
    _require_frozen_skill_dependencies(
        ports=_ports(
            resource_manager=resource_manager,
            episodic_store=episodic_store,
            semantic_recaller=semantic_recaller,
        ),
        target=target,
        args=args,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("node_grant_ref", ["", "governor-grant://forged"])
async def test_invalid_node_grant_is_rejected_before_dependency_availability(
    node_grant_ref: str,
) -> None:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-d7b-authority-first",
        "route": "skill",
        "target_id": "memory_search",
        "task": "search memory without authority",
        "args": {"query": "secret", "search_scope": "recent_episode"},
    }
    command, order, graph = _build_contracts(
        request_body={},
        request_id="request-d7b-authority-first",
        session_id="session-d7b-authority-first",
        ts_ms=1_800_000_000_000,
        intent_draft=intent,
        governor_result={
            "request_id": "request-d7b-authority-first",
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": 1_800_000_000_000,
        },
        person_id="person-d7b-authority-first",
        sanitized_message="search memory without authority",
        emotion_context={},
        render_target_lang="zh",
        command_authority=None,
    )

    class _ForbiddenGrantVerifier:
        @staticmethod
        def verify(**_kwargs):  # noqa: ANN003, ANN201
            if not node_grant_ref:
                pytest.fail("missing node grant reached the durable verifier")
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "forged node grant",
            )

    worker = _FrozenSkillWorker(
        ledger=object(),
        execution_ports=_ports(),
        node_grant_verifier=_ForbiddenGrantVerifier(),  # type: ignore[arg-type]
    )
    node = graph.nodes[0]
    execution_context = {
        "command_id": command.command_id,
        "work_order_id": order.work_order_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "worker_route": node.route,
        "node_input_refs": list(node.input_refs),
        "attempt_id": "attempt-d7b-authority-first",
        "lease_id": "lease-d7b-authority-first",
        "lease_owner_id": "owner-d7b-authority-first",
        "fencing_token": 1,
    }
    if node_grant_ref:
        execution_context["node_grant_ref"] = node_grant_ref

    with pytest.raises(GovernedHostAdapterError) as raised:
        _ = [
            event
            async for event in worker.execute_stream(
                command=command,
                work_order=order,
                execution_context=execution_context,
            )
        ]

    assert raised.value.reason_code == GOVERNED_HOST_AUTHORITY_INVALID
