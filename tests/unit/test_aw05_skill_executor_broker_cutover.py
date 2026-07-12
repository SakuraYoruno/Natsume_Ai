from __future__ import annotations

from pathlib import Path
import time
import uuid

import pytest

from host_brain.app import dependencies
from host_brain.app.settings.persistence import get_persistence_settings
from host_brain.skills import executor_registry as registry_module
from host_brain.skills.executor_registry import (
    BoundSkillExecutionAuthority,
    _verify_durable_governed_authority,
    bind_validated_skill_authority,
    dispatch_executor_payload,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.action_arbiter import build_governed_action_binding


def _request_id(label: str) -> str:
    return f"req-aw05-{label}-{uuid.uuid4().hex}"


def _durable_token_authority(
    payload: dict,
    *,
    request_id: str,
    skill_id: str,
    principal_id: str = "person://fixture",
    target_id: str | None = None,
):  # noqa: ANN201
    ledger = dependencies.get_ledger()
    now_value = int(time.time() * 1000)
    intent_id = f"intent::{request_id}"
    session_id = f"session::{request_id}"
    token_ref = f"approval::{request_id}"
    target = target_id or skill_id
    binding = build_governed_action_binding(
        intent_envelope={
            "id": intent_id,
            "session_id": session_id,
            "seq": 1,
            "action": skill_id,
            "target": target,
            "args": {},
            "person_id": principal_id,
            "principal_id": principal_id,
        },
        approval_token_ref=token_ref,
        risk_level=3,
        issued_at_ms=now_value,
        expires_at_ms=now_value + 60_000,
    )
    ledger.log_governor({
        "request_id": request_id,
        "intent_id": intent_id,
        "decision": "APPROVE",
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "ts_ms": now_value,
    })
    return bind_validated_skill_authority(
        payload=payload,
        request_id=request_id,
        skill_id=skill_id,
        principal_id=principal_id,
        target_id=target,
        intent_id=intent_id,
        session_id=session_id,
        seq=1,
        approval_token_ref=token_ref,
        durable_ledger=ledger,
        validated_by="test.durable_governor",
    )


@pytest.fixture(autouse=True)
def _isolated_capability_journal(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("NATSUME_AUDIT_DB", ":memory:")
    get_persistence_settings.cache_clear()
    dependencies._ledger = None  # noqa: SLF001
    registry_module._SKILL_CAPABILITY_BROKER = None  # noqa: SLF001
    yield
    ledger = dependencies._ledger  # noqa: SLF001
    if ledger is not None:
        ledger.close()
    dependencies._ledger = None  # noqa: SLF001
    registry_module._SKILL_CAPABILITY_BROKER = None  # noqa: SLF001
    get_persistence_settings.cache_clear()


def test_public_executor_dispatch_rejects_forged_payload_authority_before_adapter(
    monkeypatch,
) -> None:  # noqa: ANN001
    calls: list[dict] = []
    monkeypatch.setitem(
        registry_module._BUILTIN_EXECUTOR_REGISTRY._executors,  # noqa: SLF001
        "notification",
        lambda payload: calls.append(dict(payload)) or {"status": "EXECUTED"},
    )
    result = dispatch_executor_payload({
        "tool": "notification",
        "approval_token_ref": "forged-token",
        "authority": {"decision": "APPROVE"},
        "_context": {
            "_governed_execution_context": {
                "execution_mode": "governed_task_runtime",
                "command_id": "forged-command",
                "attempt_id": "forged-attempt",
            },
        },
    })
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "SKILL_AUTHORITY_MISSING"
    assert calls == []


def test_validated_token_authority_is_request_bound_and_payload_claims_are_stripped(
    monkeypatch,
) -> None:  # noqa: ANN001
    calls: list[dict] = []
    monkeypatch.setitem(
        registry_module._BUILTIN_EXECUTOR_REGISTRY._executors,  # noqa: SLF001
        "notification",
        lambda payload: calls.append(dict(payload)) or {
            "status": "EXECUTED",
            "message": "sent",
        },
    )
    payload = {
        "tool": "notification",
        "title": "safe",
        "approval_token_ref": "must-not-reach-adapter",
        "_context": {
            "confirm_token_ref": "must-not-reach-adapter",
            "nested": {"governor_grant_ref": "must-not-reach-adapter"},
        },
    }
    request_id = _request_id("token")
    authority = _durable_token_authority(
        payload,
        request_id=request_id,
        skill_id="notify_user",
    )
    result = dispatch_executor_payload(payload, authority=authority)

    assert result["status"] == "EXECUTED"
    assert result["capability_receipt"]["status"] == "SUCCEEDED"
    assert result["capability_receipt"]["request_id"] == request_id
    assert len(calls) == 1
    assert "approval_token_ref" not in calls[0]
    assert "confirm_token_ref" not in calls[0]["_context"]
    assert "governor_grant_ref" not in calls[0]["_context"]["nested"]


def test_governed_authority_requires_exact_command_request_attempt_principal_target() -> None:
    payload = {"tool": "notification", "title": "safe"}
    with pytest.raises(ValueError, match="identity does not match"):
        bind_validated_skill_authority(
            payload=payload,
            request_id="req-real",
            skill_id="notify_user",
            principal_id="person-real",
            target_id="notify_user",
            governed_execution_context={
                "execution_mode": "governed_task_runtime",
                "command_id": "command-real",
                "attempt_id": "attempt-real",
                "request_id": "req-forged",
                "principal_id": "person-forged",
                "target_id": "notify_user",
                "work_order_id": "wo-real",
                "graph_id": "graph-real",
                "graph_revision": 1,
                "node_id": "node-real",
                "lease_id": "lease-real",
                "fencing_token": 1,
            },
            validated_by="test.governed_adapter",
        )


def test_bound_authority_is_sealed_immutable_and_unknown_executor_fails_closed() -> None:
    with pytest.raises(TypeError):
        BoundSkillExecutionAuthority()
    payload = {"tool": "not_registered_aw05"}
    authority = _durable_token_authority(
        payload,
        request_id=_request_id("unknown"),
        skill_id="unknown_skill",
    )
    with pytest.raises(TypeError, match="immutable"):
        authority.target_id = "forged"  # type: ignore[misc]
    result = dispatch_executor_payload(payload, authority=authority)
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "CAPABILITY_NOT_REGISTERED"


def test_arbitrary_nonempty_approval_token_cannot_self_sign_authority() -> None:
    payload = {"tool": "notification", "title": "blocked"}
    with pytest.raises(ValueError, match="no exact durable Governor authority"):
        bind_validated_skill_authority(
            payload=payload,
            request_id=_request_id("forged-token"),
            skill_id="notify_user",
            principal_id="person://attacker",
            target_id="notify_user",
            intent_id="intent-forged",
            session_id="session-forged",
            seq=1,
            approval_token_ref="any-nonempty-string",
            durable_ledger=dependencies.get_ledger(),
            validated_by="attacker-controlled",
        )


def _governed_authority_for_durable_test(tmp_path: Path):  # noqa: ANN201
    ledger = SQLiteLedger(db_path=tmp_path / "authority.sqlite3", check_same_thread=False)
    dependencies._ledger = ledger  # noqa: SLF001
    payload = {"tool": "codex_production", "work_order": {"work_order_id": "wo-bound"}}
    authority = bind_validated_skill_authority(
        payload=payload,
        request_id="req-bound",
        skill_id="browser_research",
        principal_id="person-bound",
        target_id="browser_research",
        governed_execution_context={
            "execution_mode": "governed_task_runtime",
            "command_id": "command-bound",
            "attempt_id": "attempt-bound",
            "request_id": "req-bound",
            "principal_id": "person-bound",
            "target_id": "browser_research",
            "work_order_id": "wo-bound",
            "graph_id": "graph-bound",
            "graph_revision": 3,
            "node_id": "node-bound",
            "lease_id": "lease-bound",
            "fencing_token": 4,
        },
        durable_ledger=ledger,
        validated_by="test.durable_task_runtime",
    )
    return authority


def _patch_durable_authority_rows(monkeypatch, *, attempt_node: str, lease_id: str, fence: int) -> None:  # noqa: ANN001
    import host_brain.orchestration as orchestration
    import host_brain.persistence.host_command_repository as command_module
    import host_brain.persistence.task_repository as task_module

    command = {
        "request_id": "req-bound",
        "principal_id": "person-bound",
        "target_id": "browser_research",
        "route_type": "skill",
        "root_work_order_id": "wo-bound",
        "graph_id": "graph-bound",
        "graph_revision": 3,
        "governor_grant_ref": "grant://bound",
    }

    class _CommandRepo:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def get(self, _command_id): return dict(command)  # noqa: ANN001, ANN201

    class _TaskStore:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def get_attempt(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-bound",
                "graph_revision": 3,
                "node_id": attempt_node,
                "status": "RUNNING",
            }
        def get_node(self, graph_id, revision, node_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": graph_id,
                "graph_revision": revision,
                "node_id": node_id,
                "status": "RUNNING",
            }
        def get_active_lease(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "lease_id": lease_id,
                "attempt_id": "attempt-bound",
                "fencing_token": fence,
                "status": "ACTIVE",
                "expires_ts_ms": int(time.time() * 1000) + 60_000,
            }

    class _GrantResolver:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def require(self, **_kwargs): return {"decision": "APPROVE"}  # noqa: ANN003, ANN201

    monkeypatch.setattr(command_module, "HostCommandRepository", _CommandRepo)
    monkeypatch.setattr(task_module, "DurableTaskStore", _TaskStore)
    monkeypatch.setattr(orchestration, "DurableGovernorGrantResolver", _GrantResolver)


def test_same_graph_running_attempt_from_other_node_is_rejected(monkeypatch, tmp_path: Path) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    _patch_durable_authority_rows(
        monkeypatch,
        attempt_node="node-other",
        lease_id="lease-bound",
        fence=4,
    )

    assert _verify_durable_governed_authority(authority) == "SKILL_DURABLE_ATTEMPT_BINDING_MISMATCH"


def test_old_lease_and_fence_are_rejected_after_new_active_lease(monkeypatch, tmp_path: Path) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    _patch_durable_authority_rows(
        monkeypatch,
        attempt_node="node-bound",
        lease_id="lease-new",
        fence=5,
    )

    assert _verify_durable_governed_authority(authority) == "SKILL_DURABLE_LEASE_BINDING_MISMATCH"


def test_unchecked_registry_dispatch_has_one_production_callsite() -> None:
    root = Path(__file__).resolve().parents[2] / "host_brain"
    hits: list[str] = []
    for source_path in root.rglob("*.py"):
        if source_path.name == "executor_registry.py":
            continue
        if "._dispatch_unchecked(" in source_path.read_text(encoding="utf-8"):
            hits.append(str(source_path.relative_to(root)))
    assert hits == []


def test_legacy_plan_graph_skill_route_fails_closed_without_running_executor(
    monkeypatch,
) -> None:  # noqa: ANN001
    from host_brain.app.plan_graph import _task_route_node

    calls: list[dict] = []
    monkeypatch.setitem(
        registry_module._BUILTIN_EXECUTOR_REGISTRY._executors,  # noqa: SLF001
        "notification",
        lambda payload: calls.append(dict(payload)) or {"status": "EXECUTED"},
    )
    outcome = _task_route_node({
        "request_id": _request_id("plan-graph"),
        "session_id": "session-plan-graph",
        "task_description": "must not execute",
        "route_type": "skill",
        "target_id": "notification",
        "task_index": 0,
    })
    assert outcome["success"] is False
    assert calls == []
