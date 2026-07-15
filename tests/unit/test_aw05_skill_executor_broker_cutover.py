from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace
import uuid

import pytest

from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.composition import process_services as dependencies
from host_brain.app.settings.persistence import get_persistence_settings
from host_brain.skills import executor_registry as registry_module
from host_brain.skills.executor_registry import (
    BoundSkillExecutionAuthority,
    _authority_error,
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
def _isolated_capability_journal(monkeypatch, tmp_path: Path):  # noqa: ANN001, ANN201
    # The production Capability journal deliberately rejects transient SQLite
    # databases.  Keep this contract test isolated with a real durable file
    # instead of weakening the production composition to accept ``:memory:``.
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(tmp_path / "aw05-authority.sqlite3"))
    get_persistence_settings.cache_clear()
    dependencies.get_process_service_container().ledger = None  # noqa: SLF001
    dependencies.get_process_service_container().capability_runtime_composition = None  # noqa: SLF001
    yield
    dependencies.reset_capability_runtime_composition()
    ledger = dependencies.get_process_service_container().ledger  # noqa: SLF001
    if ledger is not None:
        ledger.close()
    dependencies.get_process_service_container().ledger = None  # noqa: SLF001
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
    }, capability_broker=dependencies.get_capability_broker())
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
    result = dispatch_executor_payload(
        payload,
        authority=authority,
        capability_broker=dependencies.get_capability_broker(),
    )

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
            durable_ledger=dependencies.get_ledger(),
            validated_by="test.governed_adapter",
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "command_target_id",
        "reservation_id",
        "node_grant_ref",
        "parent_grant_ref",
        "lease_owner_id",
        "business_scope",
        "operation_fingerprint",
    ],
)
def test_governed_authority_requires_complete_operation_and_node_grant_binding(
    missing_field: str,
) -> None:
    context = _governed_context()
    context.pop(missing_field)
    with pytest.raises(ValueError, match="identity does not match"):
        bind_validated_skill_authority(
            payload={"tool": "notification", "work_order": {"work_order_id": "wo-bound"}},
            request_id="req-bound",
            skill_id="browser_research",
            principal_id="person-bound",
            target_id="browser_research",
            session_id="session-bound",
            governed_execution_context=context,
            durable_ledger=dependencies.get_ledger(),
            validated_by="test.fail_closed",
        )


def test_governed_authority_separates_command_target_from_plan_node_target(tmp_path: Path) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)

    assert authority.command_target_id == "plan-bound"
    assert authority.target_id == authority.skill_id == "browser_research"
    assert authority.command_target_id != authority.target_id


@pytest.mark.parametrize(
    ("field", "reason_code"),
    [
        ("command_target_id", "SKILL_AUTHORITY_COMMAND_TARGET_MISMATCH"),
        ("reservation_id", "SKILL_AUTHORITY_RESERVATION_MISMATCH"),
        ("node_grant_ref", "SKILL_AUTHORITY_NODE_GRANT_MISMATCH"),
        ("parent_grant_ref", "SKILL_AUTHORITY_PARENT_GRANT_MISMATCH"),
        ("lease_owner_id", "SKILL_AUTHORITY_LEASE_OWNER_MISMATCH"),
        ("business_scope", "SKILL_AUTHORITY_BUSINESS_SCOPE_MISMATCH"),
        ("operation_fingerprint", "SKILL_AUTHORITY_OPERATION_FINGERPRINT_MISMATCH"),
    ],
)
def test_capability_request_context_must_exactly_match_sealed_operation_authority(
    tmp_path: Path,
    field: str,
    reason_code: str,
) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    payload = {"tool": "codex_production", "work_order": {"work_order_id": "wo-bound"}}
    envelope = registry_module._SkillInvocationEnvelope(payload=payload, authority=authority)  # noqa: SLF001
    context = {
        "principal_id": authority.principal_id,
        "target_id": authority.target_id,
        "command_target_id": authority.command_target_id,
        "command_id": authority.command_id,
        "work_order_id": authority.work_order_id,
        "graph_id": authority.graph_id,
        "graph_revision": authority.graph_revision,
        "node_id": authority.node_id,
        "governed_attempt_id": authority.attempt_id,
        "lease_id": authority.lease_id,
        "fencing_token": authority.fencing_token,
        "reservation_id": authority.reservation_id,
        "node_grant_ref": authority.node_grant_ref,
        "parent_grant_ref": authority.parent_grant_ref,
        "lease_owner_id": authority.lease_owner_id,
        "business_scope": authority.business_scope,
        "operation_fingerprint": authority.operation_fingerprint,
    }
    context[field] = "forged" if field != "fencing_token" else 99
    request = CapabilityRequest(
        request_id=authority.request_id,
        attempt_id="capability-attempt-bound",
        idempotency_key="capability-idem-bound",
        capability_id=authority.executor_id,
        kind=CapabilityKind.SKILL,
        args={},
        context=context,
        created_at_ms=int(time.time() * 1000),
        deadline_at_ms=int(time.time() * 1000) + 60_000,
    )

    assert _authority_error(
        authority,
        request,
        envelope,
        now_ms=int(time.time() * 1000),
    ) == reason_code


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
    result = dispatch_executor_payload(
        payload,
        authority=authority,
        capability_broker=dependencies.get_capability_broker(),
    )
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


def _governed_context() -> dict:
    return {
        "execution_mode": "governed_task_runtime",
        "command_id": "command-bound",
        "attempt_id": "attempt-bound",
        "request_id": "req-bound",
        "principal_id": "person-bound",
        "target_id": "browser_research",
        "command_target_id": "plan-bound",
        "work_order_id": "wo-bound",
        "graph_id": "graph-bound",
        "graph_revision": 3,
        "node_id": "node-bound",
        "lease_id": "lease-bound",
        "fencing_token": 4,
        "reservation_id": "reservation-bound",
        "node_grant_ref": "grant://node-bound",
        "parent_grant_ref": "grant://parent-bound",
        "lease_owner_id": "worker-bound",
        "business_scope": "scope-bound",
        "operation_fingerprint": "sha256:" + "b" * 64,
    }


def _governed_authority_for_durable_test(tmp_path: Path):  # noqa: ANN201
    ledger = SQLiteLedger(db_path=tmp_path / "authority.sqlite3", check_same_thread=False)
    dependencies.get_process_service_container().ledger = ledger  # noqa: SLF001
    payload = {"tool": "codex_production", "work_order": {"work_order_id": "wo-bound"}}
    authority = bind_validated_skill_authority(
        payload=payload,
        request_id="req-bound",
        skill_id="browser_research",
        principal_id="person-bound",
        target_id="browser_research",
        session_id="session-bound",
        governed_execution_context=_governed_context(),
        durable_ledger=ledger,
        validated_by="test.durable_task_runtime",
    )
    return authority


def _patch_durable_authority_rows(
    monkeypatch,
    *,
    attempt_node: str,
    lease_id: str,
    fence: int,
    reservation_overrides: dict | None = None,
    grant_calls: list[dict] | None = None,
) -> None:  # noqa: ANN001
    import host_brain.persistence.host_command_repository as command_module
    import host_brain.persistence.task_repository as task_module

    command = {
        "request_id": "req-bound",
        "session_id": "session-bound",
        "principal_id": "person-bound",
        "target_id": "plan-bound",
        "root_work_order_id": "wo-bound",
        "graph_id": "graph-bound",
        "graph_revision": 3,
        "business_scope": "scope-bound",
        "governor_grant_ref": "grant://parent-bound",
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
                "operation_scope": "scope-bound",
                "operation_fingerprint": "sha256:" + "b" * 64,
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
                "owner_id": "worker-bound",
                "fencing_token": fence,
                "status": "ACTIVE",
                "expires_ts_ms": int(time.time() * 1000) + 60_000,
            }
        def get_operation_reservation(self, _reservation_id):  # noqa: ANN001, ANN201
            row = {
                "reservation_id": "reservation-bound",
                "status": "RESERVED",
                "expires_ts_ms": int(time.time() * 1000) + 60_000,
                "attempt_id": "attempt-bound",
                "lease_id": "lease-bound",
                "owner_id": "worker-bound",
                "fencing_token": 4,
                "business_scope": "scope-bound",
                "operation_fingerprint": "sha256:" + "b" * 64,
            }
            row.update(dict(reservation_overrides or {}))
            return row

    class _NodeGrantVerifier:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def verify(self, **kwargs):  # noqa: ANN003, ANN201
            if grant_calls is not None:
                grant_calls.append(dict(kwargs))
            return SimpleNamespace(
                grant_ref=kwargs["grant_ref"],
                parent_grant_ref=kwargs["parent_grant_ref"],
                route=kwargs["route"],
                target_id=kwargs["target_id"],
            )

    monkeypatch.setattr(command_module, "HostCommandRepository", _CommandRepo)
    monkeypatch.setattr(task_module, "DurableTaskStore", _TaskStore)
    import host_brain.orchestration.node_grants as node_grants

    monkeypatch.setattr(node_grants, "DurableNodeGrantVerifier", _NodeGrantVerifier)


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


def test_durable_governed_authority_verifies_plan_command_operation_and_child_grant(
    monkeypatch,
    tmp_path: Path,
) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    grant_calls: list[dict] = []
    _patch_durable_authority_rows(
        monkeypatch,
        attempt_node="node-bound",
        lease_id="lease-bound",
        fence=4,
        grant_calls=grant_calls,
    )

    assert _verify_durable_governed_authority(authority) is None
    assert grant_calls == [{
        "grant_ref": "grant://node-bound",
        "request_id": "req-bound",
        "session_id": "session-bound",
        "principal_id": "person-bound",
        "graph_id": "graph-bound",
        "root_work_order_id": "wo-bound",
        "graph_revision": 3,
        "node_id": "node-bound",
        "route": "skill",
        "target_id": "browser_research",
        "parent_grant_ref": "grant://parent-bound",
    }]


@pytest.mark.parametrize(
    "reservation_overrides",
    [
        {"status": "COMMITTED"},
        {"expires_ts_ms": 1},
        {"attempt_id": "attempt-other"},
        {"lease_id": "lease-other"},
        {"owner_id": "worker-other"},
        {"fencing_token": 5},
        {"business_scope": "scope-other"},
        {"operation_fingerprint": "sha256:" + "c" * 64},
    ],
)
def test_durable_governed_authority_rejects_nonreserved_or_drifting_operation(
    monkeypatch,
    tmp_path: Path,
    reservation_overrides: dict,
) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    _patch_durable_authority_rows(
        monkeypatch,
        attempt_node="node-bound",
        lease_id="lease-bound",
        fence=4,
        reservation_overrides=reservation_overrides,
    )

    assert (
        _verify_durable_governed_authority(authority)
        == "SKILL_DURABLE_OPERATION_BINDING_MISMATCH"
    )


def test_durable_governed_authority_fails_closed_when_child_grant_verifier_rejects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    authority = _governed_authority_for_durable_test(tmp_path)
    _patch_durable_authority_rows(
        monkeypatch,
        attempt_node="node-bound",
        lease_id="lease-bound",
        fence=4,
    )
    import host_brain.orchestration.node_grants as node_grants

    class _RejectingVerifier:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def verify(self, **_kwargs):  # noqa: ANN003, ANN201
            raise PermissionError("forged child grant")

    monkeypatch.setattr(node_grants, "DurableNodeGrantVerifier", _RejectingVerifier)

    assert _verify_durable_governed_authority(authority) == "SKILL_DURABLE_NODE_GRANT_INVALID"


def test_unchecked_registry_dispatch_has_one_production_callsite() -> None:
    root = Path(__file__).resolve().parents[2] / "host_brain"
    hits: list[str] = []
    for source_path in root.rglob("*.py"):
        if source_path.name == "executor_registry.py":
            continue
        if "._dispatch_unchecked(" in source_path.read_text(encoding="utf-8"):
            hits.append(str(source_path.relative_to(root)))
    assert hits == []
