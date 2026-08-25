from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.degradation_authority import GlobalDegradationTestPort

from host_brain.app.governed_skill_execution_ports import (
    GovernedSkillExecutionPorts,
    build_governed_skill_execution_ports,
)
from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)


ROOT = Path(__file__).resolve().parents[2]


def _ports() -> GovernedSkillExecutionPorts:
    return GovernedSkillExecutionPorts(
        skill_registry=object(),
        capability_broker=object(),
        model_attempt_service=object(),
        production_authority_verifier=lambda **_kwargs: True,
        resource_manager=object(),
        episodic_store=object(),
        semantic_recaller=object(),
        degradation_capability_port=GlobalDegradationTestPort(),
    )


def test_governed_skill_ports_are_immutable_and_authority_minimal() -> None:
    ports = _ports()
    assert {field.name for field in fields(GovernedSkillExecutionPorts)} == {
        "skill_registry",
        "capability_broker",
        "model_attempt_service",
        "production_authority_verifier",
        "resource_manager",
        "episodic_store",
        "semantic_recaller",
        "degradation_capability_port",
        "agent_artifact_store",
        "agent_workspace_runtime",
    }
    with pytest.raises(FrozenInstanceError):
        ports.skill_registry = object()  # type: ignore[misc]

    exposed = set(dir(ports))
    assert exposed.isdisjoint(
        {
            "planner",
            "get_planning_agent",
            "runtime_task_registry",
            "get_runtime_task_registry",
            "replan",
            "delivery",
            "surface_reply_sse_generator",
            "get_sub_agent_dispatcher",
        }
    )


def test_governed_adapter_never_composes_or_imports_executive_ports() -> None:
    adapter_path = ROOT / "host_brain/app/governed_task_adapter.py"
    source = adapter_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(adapter_path))

    assert "build_executive_runtime_application_ports" not in source
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module.startswith("host_brain.executive_runtime") for module in imported_modules)


def test_governed_skill_ports_never_import_process_services() -> None:
    for relative_path in (
        "host_brain/app/governed_skill_execution_ports.py",
        "host_brain/app/governed_task_adapter.py",
    ):
        path = ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        imported_modules = {
            str(node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "process_services" not in source
        if relative_path.endswith("governed_skill_execution_ports.py"):
            assert "execute_governed_skill" not in source
            assert "record_governed_skill_execution" not in source
            assert not any(
                module.startswith("host_brain.composition")
                for module in imported_modules
            )
            assert not any(
                name.startswith("host_brain.composition")
                for name in imported_names
            )


def test_governed_skill_ports_require_explicit_service_snapshots() -> None:
    with pytest.raises(TypeError):
        build_governed_skill_execution_ports(  # type: ignore[call-arg]
            skill_registry=object(),
            capability_broker=object(),
            model_attempt_service=object(),
            production_authority_verifier=lambda **_kwargs: True,
        )


def test_governed_skill_ports_reject_invalid_authority_verifier() -> None:
    values = {
        "skill_registry": object(),
        "capability_broker": object(),
        "model_attempt_service": object(),
        "production_authority_verifier": None,
        "resource_manager": object(),
        "episodic_store": object(),
        "semantic_recaller": object(),
        "degradation_capability_port": GlobalDegradationTestPort(),
    }

    with pytest.raises(
        TypeError,
        match=r"^production_authority_verifier must be callable$",
    ):
        build_governed_skill_execution_ports(**values)


def test_governed_skill_ports_freeze_two_independent_service_snapshots(
) -> None:
    services_a = (object(), object(), object())
    services_b = (object(), object(), object())
    ports_a = build_governed_skill_execution_ports(
        skill_registry=object(),
        capability_broker=object(),
        model_attempt_service=object(),
        production_authority_verifier=lambda **_kwargs: True,
        resource_manager=services_a[0],
        episodic_store=services_a[1],
        semantic_recaller=services_a[2],
        degradation_capability_port=GlobalDegradationTestPort(),
    )
    ports_b = build_governed_skill_execution_ports(
        skill_registry=object(),
        capability_broker=object(),
        model_attempt_service=object(),
        production_authority_verifier=lambda **_kwargs: True,
        resource_manager=services_b[0],
        episodic_store=services_b[1],
        semantic_recaller=services_b[2],
        degradation_capability_port=GlobalDegradationTestPort(),
    )

    assert ports_a.get_resource_manager(create_if_missing=True) is services_a[0]
    assert ports_b.get_episodic_store() is services_b[1]
    assert ports_a.get_semantic_recaller() is services_a[2]
    assert ports_b.get_resource_manager() is services_b[0]
    assert ports_a.get_episodic_store() is services_a[1]
    assert ports_b.get_semantic_recaller() is services_b[2]


def test_frozen_executor_payload_skips_post_admission_skill_normalization(
    monkeypatch,
) -> None:
    """A governed worker must dispatch the payload frozen with its TaskGraph."""

    from host_brain.app import governed_skill_execution

    dispatched: dict = {}

    def forbidden_handler(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        pytest.fail("production Skill handler rebuilt a payload after admission")

    registry = SimpleNamespace(
        require=lambda _skill_id: SimpleNamespace(
            require_confirm=False,
            family="code",
            scope="host",
            risk_level=2,
            fallback_policy="strict_fail",
            handler=forbidden_handler,
            model_budget_required=False,
            version_binding=SimpleNamespace(
                to_dict=lambda: {
                    "bundle_id": "skill://ne05/agent-memory-curator/2",
                    "bundle_digest": "sha256:" + "a" * 64,
                    "bundle_version": "2.0.0",
                    "runtime_adapter_id": "builtin-skill-alias.v1",
                    "runtime_protocol_version": "v1",
                    "maturity_level": 2,
                }
            ),
            execution_policy=(
                SkillExecutionPolicy.PRE_ACCEPTANCE_PROPOSAL_ONLY
            ),
            budget_policy=SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL,
        )
    )
    monkeypatch.setattr(
        governed_skill_execution,
        "_governed_node_grant_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        governed_skill_execution,
        "bind_validated_skill_authority",
        lambda **_kwargs: {"authority_id": "authority://frozen-payload"},
    )
    monkeypatch.setattr(
        governed_skill_execution,
        "dispatch_executor_payload",
        lambda payload, **_kwargs: (
            dispatched.update(payload) or {
                "status": "EXECUTED",
                "result_text": "frozen payload dispatched",
            }
        ),
    )
    result = governed_skill_execution.execute_governed_skill(
        ports=SimpleNamespace(
            skill_registry=registry,
            capability_broker=object(),
            model_attempt_service=object(),
            production_authority_verifier=lambda *_args, **_kwargs: True,
            agent_artifact_store=None,
            agent_workspace_runtime=None,
        ),
        request_id="request-frozen-payload",
        session_id="session-frozen-payload",
        person_id="person-frozen-payload",
        intent_id="intent-frozen-payload",
        skill_id="agent_memory_curator",
        task_text="frozen task",
        user_message="frozen task",
        governor_result={"decision": "APPROVE", "tokens": {}},
        intent_draft={"route": "skill", "target_id": "agent_memory_curator"},
        emotion_context={},
        render_target_lang="zh",
        durable_authority_ledger=object(),
        governed_execution_context={
            "execution_mode": "governed_task_runtime",
            "target_id": "agent_memory_curator",
        },
        execution_args={"task": "must not be normalized"},
        verified_node_grant={"grant_ref": "grant://node/frozen-payload"},
        frozen_executor_payload={
            "schema_version": "Natsume.governed.production_executor_payload.v1",
            "tool": "codex_production",
            "action": "enqueue",
            "start_background": False,
            "work_order": {"work_order_id": "wo-frozen-payload"},
        },
    )

    assert result.success is True
    assert dispatched["tool"] == "codex_production"
    assert dispatched["work_order"] == {"work_order_id": "wo-frozen-payload"}
    assert dispatched["_context"]["_governed_execution_context"]["target_id"] == (
        "agent_memory_curator"
    )
    assert dispatched["_context"]["skill_version_binding"] == {
        "bundle_id": "skill://ne05/agent-memory-curator/2",
        "bundle_digest": "sha256:" + "a" * 64,
        "bundle_version": "2.0.0",
        "runtime_adapter_id": "builtin-skill-alias.v1",
        "runtime_protocol_version": "v1",
        "maturity_level": 2,
    }
    assert result.payload["skill_version_binding"] == dispatched["_context"][
        "skill_version_binding"
    ]
