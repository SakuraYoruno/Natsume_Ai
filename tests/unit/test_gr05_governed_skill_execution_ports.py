from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from host_brain.app.governed_skill_execution_ports import (
    GovernedSkillExecutionPorts,
    execute_frozen_governed_skill,
)


ROOT = Path(__file__).resolve().parents[2]


def _ports() -> GovernedSkillExecutionPorts:
    return GovernedSkillExecutionPorts(
        skill_registry=object(),
        capability_broker=object(),
        model_attempt_service=object(),
        production_authority_verifier=lambda **_kwargs: True,
        resource_manager_provider=lambda **_kwargs: object(),
        episodic_store_provider=lambda: object(),
        semantic_recaller_provider=lambda: object(),
        refresh_skill_registry=lambda: None,
        activate_hub_skill=lambda **_kwargs: None,
    )


def test_governed_skill_ports_are_immutable_and_authority_minimal() -> None:
    ports = _ports()
    assert {field.name for field in fields(GovernedSkillExecutionPorts)} == {
        "skill_registry",
        "capability_broker",
        "model_attempt_service",
        "production_authority_verifier",
        "resource_manager_provider",
        "episodic_store_provider",
        "semantic_recaller_provider",
        "refresh_skill_registry",
        "activate_hub_skill",
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


def test_low_level_skill_executor_receives_only_explicit_governed_collaborators(
    monkeypatch,
) -> None:
    ports = _ports()
    ledger = object()
    captured: dict = {}
    expected = object()

    def fake_execute_skill(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        "host_brain.executive_runtime.skill_execution._execute_skill",
        fake_execute_skill,
    )
    result = execute_frozen_governed_skill(
        ports=ports,
        durable_authority_ledger=ledger,
        request_body={"args": {}},
        request_id="request-gr05-narrow-skill",
        session_id="session-gr05-narrow-skill",
        person_id="person-gr05",
        intent_id="intent-gr05",
        skill_id="memory_store",
        task_text="store admitted memory",
        user_message="store admitted memory",
        governor_result={"decision": "APPROVE"},
        intent_draft={"route": "skill", "target_id": "memory_store"},
        emotion_context={},
        render_target_lang="zh",
        governed_execution_context={"execution_mode": "governed_task_runtime"},
        execution_args_override={"content": "admitted"},
        verified_node_grant={"grant_ref": "grant://node/gr05"},
    )

    assert result is expected
    assert captured["ports"] is ports
    assert captured["skill_registry"] is ports.skill_registry
    assert captured["capability_broker_override"] is ports.capability_broker
    assert captured["model_attempt_service_override"] is ports.model_attempt_service
    assert captured["production_authority_verifier"] is ports.production_authority_verifier
    assert captured["durable_authority_ledger"] is ledger
    assert captured["ledger"] is False
    assert "token_service_override" not in captured
