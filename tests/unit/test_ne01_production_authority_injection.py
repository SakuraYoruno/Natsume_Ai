from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.production_authority import (
    build_durable_production_authority_verifier,
)


def test_production_authority_verifier_uses_its_explicit_ledger_provider() -> None:
    ledger = SimpleNamespace(canonical_db_path=":memory:")
    calls: list[str] = []

    def ledger_provider():  # noqa: ANN201
        calls.append("ledger")
        return ledger

    verifier = build_durable_production_authority_verifier(
        ledger_provider=ledger_provider,
    )

    result = verifier(
        {"command_id": "command-test"},
        SimpleNamespace(metadata={}, work_order_id="work-order-test"),
    )

    assert result == "AGENT_PRODUCTION_DURABLE_AUTHORITY_UNAVAILABLE"
    assert calls == ["ledger"]


def test_production_authority_verifier_rejects_non_callable_provider() -> None:
    with pytest.raises(TypeError, match="ledger_provider must be callable"):
        build_durable_production_authority_verifier(ledger_provider=None)  # type: ignore[arg-type]


def test_production_authority_verifier_fails_closed_when_provider_raises() -> None:
    def unavailable_provider():  # noqa: ANN201
        raise RuntimeError("ledger unavailable")

    verifier = build_durable_production_authority_verifier(
        ledger_provider=unavailable_provider,
    )

    result = verifier(
        {"command_id": "command-test"},
        SimpleNamespace(metadata={}, work_order_id="work-order-test"),
    )

    assert result == "AGENT_PRODUCTION_DURABLE_AUTHORITY_INVALID"


def test_production_authority_verifier_accepts_bound_planning_skill_node(
    monkeypatch,
) -> None:
    authority = {
        "command_id": "command-plan",
        "request_id": "request-plan",
        "principal_id": "service.edge.gateway",
        "target_id": "agent_frontend_acceptance",
        "work_order_id": "work-order-plan",
        "graph_id": "graph-plan",
        "graph_revision": "1",
        "node_id": "node-plan-1",
        "attempt_id": "attempt-plan-1",
        "lease_id": "lease-plan-1",
        "fencing_token": "7",
    }

    class CommandRepository:
        def __init__(self, _db_path) -> None:  # noqa: ANN001
            pass

        def get(self, _command_id):  # noqa: ANN001, ANN201
            return {
                "request_id": "request-plan",
                "principal_id": "service.edge.gateway",
                "target_id": "planning_search",
                "root_work_order_id": "work-order-plan",
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "route_type": "plan",
                "governor_grant_ref": "governor-decision://plan",
            }

    class TaskStore:
        def __init__(self, _db_path) -> None:  # noqa: ANN001
            pass

        def get_attempt(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "node_id": "node-plan-1",
                "status": "RUNNING",
            }

        def get_node(self, _graph_id, _revision, _node_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "node_id": "node-plan-1",
                "status": "RUNNING",
                "node": {"route": "skill:agent_frontend_acceptance"},
            }

        def get_active_lease(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "lease_id": "lease-plan-1",
                "attempt_id": "attempt-plan-1",
                "fencing_token": 7,
                "status": "ACTIVE",
                "expires_ts_ms": 2_000,
            }

    class GrantResolver:
        def __init__(self, _ledger) -> None:  # noqa: ANN001
            pass

        def require(self, *, request_id, expected_ref):  # noqa: ANN001, ANN201
            assert request_id == "request-plan"
            assert expected_ref == "governor-decision://plan"
            return {}

    monkeypatch.setattr(
        "host_brain.persistence.host_command_repository.HostCommandRepository",
        CommandRepository,
    )
    monkeypatch.setattr(
        "host_brain.persistence.task_repository.DurableTaskStore",
        TaskStore,
    )
    monkeypatch.setattr(
        "host_brain.orchestration.DurableGovernorGrantResolver",
        GrantResolver,
    )
    verifier = build_durable_production_authority_verifier(
        ledger_provider=lambda: SimpleNamespace(canonical_db_path="task.sqlite3"),
        clock_ms=lambda: 1_000,
    )
    order = SimpleNamespace(
        metadata={
            "request_id": "request-plan",
            "skill_id": "agent_frontend_acceptance",
        },
        work_order_id="work-order-plan",
    )

    assert verifier(authority, order) is None


def test_production_authority_verifier_rejects_planning_node_target_drift(
    monkeypatch,
) -> None:
    class CommandRepository:
        def __init__(self, _db_path) -> None:  # noqa: ANN001
            pass

        def get(self, _command_id):  # noqa: ANN001, ANN201
            return {
                "request_id": "request-plan",
                "principal_id": "service.edge.gateway",
                "target_id": "planning_search",
                "root_work_order_id": "work-order-plan",
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "route_type": "plan",
                "governor_grant_ref": "governor-decision://plan",
            }

    class TaskStore:
        def __init__(self, _db_path) -> None:  # noqa: ANN001
            pass

        def get_attempt(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "node_id": "node-plan-1",
                "status": "RUNNING",
            }

        def get_node(self, _graph_id, _revision, _node_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-plan",
                "graph_revision": 1,
                "node_id": "node-plan-1",
                "status": "RUNNING",
                "node": {"route": "skill:agent_frontend_review"},
            }

    monkeypatch.setattr(
        "host_brain.persistence.host_command_repository.HostCommandRepository",
        CommandRepository,
    )
    monkeypatch.setattr(
        "host_brain.persistence.task_repository.DurableTaskStore",
        TaskStore,
    )
    verifier = build_durable_production_authority_verifier(
        ledger_provider=lambda: SimpleNamespace(canonical_db_path="task.sqlite3"),
        clock_ms=lambda: 1_000,
    )
    order = SimpleNamespace(
        metadata={
            "request_id": "request-plan",
            "skill_id": "agent_frontend_acceptance",
        },
        work_order_id="work-order-plan",
    )
    authority = {
        "command_id": "command-plan",
        "request_id": "request-plan",
        "principal_id": "service.edge.gateway",
        "target_id": "agent_frontend_acceptance",
        "work_order_id": "work-order-plan",
        "graph_id": "graph-plan",
        "graph_revision": "1",
        "node_id": "node-plan-1",
        "attempt_id": "attempt-plan-1",
        "lease_id": "lease-plan-1",
        "fencing_token": "7",
    }

    assert (
        verifier(authority, order)
        == "AGENT_PRODUCTION_DURABLE_NODE_BINDING_MISMATCH"
    )


def test_production_authority_module_has_no_process_service_locator() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "app"
        / "production_authority.py"
    ).read_text(encoding="utf-8")

    assert "host_brain.composition.process_services" not in source
    assert "get_process_service_container" not in source
    assert "get_ledger" not in source
