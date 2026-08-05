"""NE-01 Skill execution has exactly one governed production owner."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.governed_skill_execution import (
    GOVERNED_MEMORY_ACCEPTANCE_EFFECT_REQUIRED,
    GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
    execute_governed_skill,
    record_governed_skill_execution,
)
from host_brain.lifecycle_runtime.scheduler_handlers import (
    _run_personality_scheduler_skill,
)


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_executive_and_loader_execution_authorities_are_physically_retired() -> None:
    assert not (
        ROOT / "host_brain/executive_runtime/skill_execution.py"
    ).exists()
    loader_source = _source("host_brain/skills/loader.py")
    for forbidden in (
        "meta.handler",
        "dispatch_executor_payload",
        "bind_validated_skill_authority",
        "_dispatch_executor",
    ):
        assert forbidden not in loader_source
    assert GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED in loader_source


def test_skill_handler_and_broker_calls_have_one_production_owner() -> None:
    handler_callers: list[str] = []
    broker_callers: list[str] = []
    authority_callers: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "handler"
                and isinstance(function.value, ast.Name)
                and function.value.id in {"meta", "skill_meta"}
            ):
                handler_callers.append(relative)
            if isinstance(function, ast.Name):
                if function.id == "dispatch_executor_payload":
                    broker_callers.append(relative)
                elif function.id == "bind_validated_skill_authority":
                    authority_callers.append(relative)

    expected = ["host_brain/app/governed_skill_execution.py"]
    assert handler_callers == expected
    assert broker_callers == expected
    assert authority_callers == expected


def test_governed_executor_rejects_before_registry_without_node_authority() -> None:
    class _ForbiddenPorts:
        @property
        def skill_registry(self) -> None:
            raise AssertionError("missing authority reached Skill registry")

    with pytest.raises(
        PermissionError,
        match=GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
    ):
        execute_governed_skill(
            ports=_ForbiddenPorts(),
            durable_authority_ledger=object(),
            request_id="request-missing-node-authority",
            session_id="session-missing-node-authority",
            person_id="person-missing-node-authority",
            intent_id="intent-missing-node-authority",
            skill_id="memory_store",
            task_text="must not execute",
            user_message="must not execute",
            governor_result={"decision": "APPROVE"},
            intent_draft={},
            emotion_context={},
            render_target_lang="zh",
            governed_execution_context={},
            execution_args={},
            verified_node_grant={},
        )


def test_memory_store_cannot_bypass_graph_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ForbiddenPorts:
        @property
        def skill_registry(self) -> None:
            raise AssertionError("memory proposal reached the eager Skill writer")

    monkeypatch.setattr(
        "host_brain.app.governed_skill_execution._governed_node_grant_error",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        PermissionError,
        match=GOVERNED_MEMORY_ACCEPTANCE_EFFECT_REQUIRED,
    ):
        execute_governed_skill(
            ports=_ForbiddenPorts(),
            durable_authority_ledger=object(),
            request_id="request-memory-acceptance-boundary",
            session_id="session-memory-acceptance-boundary",
            person_id="person-memory-acceptance-boundary",
            intent_id="intent-memory-acceptance-boundary",
            skill_id="memory_store",
            task_text="记住我喜欢蓝色",
            user_message="记住我喜欢蓝色",
            governor_result={"decision": "APPROVE"},
            intent_draft={"route": "skill", "target_id": "memory_store"},
            emotion_context={},
            render_target_lang="zh",
            governed_execution_context={"node_id": "node-memory-store"},
            execution_args={"content": "我喜欢蓝色"},
            verified_node_grant={"grant_ref": "grant://memory-store"},
        )


def test_governed_receipt_write_failure_is_not_swallowed() -> None:
    class _FailingLedger:
        def log_skill_exec(self, _row):  # noqa: ANN001, ANN201
            raise RuntimeError("receipt write failed")

    with pytest.raises(RuntimeError, match="receipt write failed"):
        record_governed_skill_execution(
            ledger=_FailingLedger(),
            request_id="request-receipt-failure",
            intent_id="intent-receipt-failure",
            skill_name="memory_store",
            result_status="FAILED",
            result_code="EXPECTED_FAILURE",
            result_digest="expected",
            approval_token_ref=None,
            ts_ms=1_800_000_000_000,
            evidence_ref="skillreceipt://sha256/" + "a" * 64,
        )


def test_scheduler_personality_metadata_cannot_invoke_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_handler(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        pytest.fail("Scheduler invoked a SkillMeta handler")

    registry = SimpleNamespace(
        get=lambda _name: SimpleNamespace(
            scheduler_allowed=True,
            handler=forbidden_handler,
        )
    )
    monkeypatch.setattr(
        "host_brain.skills.personality_registry."
        "build_personality_skill_registry",
        lambda: registry,
    )

    result = _run_personality_scheduler_skill(
        ports=object(),
        system_intent={"skill_name": "emergent_growth_weekly"},
        logger=SimpleNamespace(
            warning=lambda *_args, **_kwargs: None,
            debug=lambda *_args, **_kwargs: None,
        ),
        get_ledger=lambda: object(),
        ui_pusher=lambda *_args, **_kwargs: {},
    )

    assert result == {
        "status": "REJECTED",
        "reason_code": GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
        "skill_name": "emergent_growth_weekly",
    }
