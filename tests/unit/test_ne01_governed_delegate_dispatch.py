from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from host_brain.app.governed_delegate_dispatch import (
    GOVERNED_DELEGATE_AUTHORITY_INVALID,
    GOVERNED_DELEGATE_OUTPUT_INVALID,
    GOVERNED_DELEGATE_TARGET_UNAVAILABLE,
    GovernedDelegateDispatchError,
    GovernedDelegateInvocation,
    GovernedDelegateTarget,
    GovernedDelegateDispatcher,
    build_governed_delegate_dispatcher,
)
from host_brain.app.handlers.pentest_agent_handler import pentest_agent_handler
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    READONLY_DELEGATE_EXECUTION_MODE,
    ReadonlyDelegateUsageAttestationError,
    build_readonly_delegate_usage_attestation,
    validate_readonly_delegate_usage_attestation,
)
from host_brain.contracts.agent_runtime.delegate_target_policy import (
    GovernedDelegateTargetPolicyError,
    validate_governed_delegate_target_policy,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput


ROOT = Path(__file__).resolve().parents[2]


def _frozen(*, target_id: str = "pentest_agent") -> FrozenNodeInput:
    return FrozenNodeInput(
        command_id="command-governed-delegate",
        request_id="request-governed-delegate",
        session_id="session-governed-delegate",
        principal_id="principal-governed-delegate",
        work_order_id="work-order-governed-delegate",
        root_work_order_id="work-order-governed-delegate",
        graph_id="graph-governed-delegate",
        graph_revision=2,
        node_id="node-governed-delegate",
        attempt_id="attempt-governed-delegate",
        lease_id="lease-governed-delegate",
        lease_owner_id="owner-governed-delegate",
        fencing_token=7,
        node_risk_level=3,
        route="delegate",
        target_id=target_id,
        objective="prepare a read-only threat model for example.test",
        input_refs=("input://sha256/" + "a" * 64,),
        _input_pack_json=json.dumps({"context": {"skill_family": "pentest"}}),
        _input_artifacts_json="[]",
        node_grant_ref="governor-grant://child/governed-delegate",
        parent_grant_ref="governor-grant://parent/governed-delegate",
    )


class _Verifier:
    def __init__(self, *, drift: str | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._drift = drift

    def verify(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        result = {
            "grant_ref": kwargs["grant_ref"],
            "parent_grant_ref": kwargs["parent_grant_ref"],
            "request_id": kwargs["request_id"],
            "session_id": kwargs["session_id"],
            "principal_id": kwargs["principal_id"],
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "root_work_order_id": kwargs["root_work_order_id"],
            "node_id": kwargs["node_id"],
            "route": kwargs["route"],
            "target_id": kwargs["target_id"],
        }
        if self._drift:
            result[self._drift] = "drifted"
        return result


@pytest.mark.asyncio
async def test_readonly_pentest_delegate_revalidates_the_full_frozen_authority_tuple() -> None:
    verifier = _Verifier()
    dispatcher = build_governed_delegate_dispatcher(node_grants=verifier)
    invocation = GovernedDelegateInvocation.from_frozen(
        frozen=_frozen(),
        user_message="prepare a read-only threat model for example.test",
        context={"skill_family": "pentest"},
    )

    outcome = await dispatcher.dispatch(invocation)

    assert outcome.success is True
    assert outcome.target_id == "pentest_agent"
    assert outcome.audit_refs["delegate_execution_mode"] == "PORT_FREE_READ_ONLY"
    target_policy = dispatcher.require_target("pentest_agent")
    assert outcome.audit_refs["delegate_target_policy_ref"] == (
        target_policy["policy_ref"]
    )
    assert outcome.audit_refs["delegate_review_mode"] == (
        "DURABLE_PRE_EXECUTION_REQUIRED"
    )
    assert outcome.audit_refs["delegate_effect_mode"] == (
        "GRAPH_ACCEPTANCE_RESULT_ONLY"
    )
    assert outcome.audit_refs["side_effects_executed"] is False
    usage = validate_readonly_delegate_usage_attestation(
        outcome.audit_refs["delegate_runtime_usage_attestation"]
    )
    assert usage["command_id"] == invocation.command_id
    assert usage["graph_id"] == invocation.graph_id
    assert usage["graph_revision"] == invocation.graph_revision
    assert usage["node_id"] == invocation.node_id
    assert usage["attempt_id"] == invocation.attempt_id
    assert usage["node_grant_ref"] == invocation.node_grant_ref
    assert usage["target_id"] == invocation.target_id
    assert usage["adapter_id"] == outcome.audit_refs["delegate_adapter"]
    assert usage["execution_mode"] == outcome.audit_refs["delegate_execution_mode"]
    assert usage["invocation_ref"] == invocation.invocation_ref
    assert usage["measurements"] == {
        "model_cost_micros": 0,
        "retry_count": 0,
        "token_count": 0,
        "tool_call_count": 0,
    }
    assert len(verifier.calls) == 1
    assert verifier.calls[0] == {
        "grant_ref": invocation.node_grant_ref,
        "parent_grant_ref": invocation.parent_grant_ref,
        "request_id": invocation.request_id,
        "session_id": invocation.session_id,
        "principal_id": invocation.principal_id,
        "graph_id": invocation.graph_id,
        "graph_revision": invocation.graph_revision,
        "root_work_order_id": invocation.root_work_order_id,
        "node_id": invocation.node_id,
        "route": "delegate",
        "target_id": invocation.target_id,
        "attempt_id": invocation.attempt_id,
        "lease_id": invocation.lease_id,
        "lease_owner_id": invocation.lease_owner_id,
        "fencing_token": invocation.fencing_token,
    }


@pytest.mark.asyncio
async def test_unregistered_or_effectful_delegate_target_is_rejected_before_grant_verification() -> None:
    verifier = _Verifier()
    dispatcher = build_governed_delegate_dispatcher(node_grants=verifier)
    invocation = GovernedDelegateInvocation.from_frozen(
        frozen=_frozen(target_id="repair_agent"),
        user_message="repair the local environment",
        context={},
    )

    with pytest.raises(GovernedDelegateDispatchError) as raised:
        await dispatcher.dispatch(invocation)

    assert raised.value.reason_code == GOVERNED_DELEGATE_TARGET_UNAVAILABLE
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_readonly_delegate_rejects_effect_descriptor_instead_of_discarding_it() -> None:
    verifier = _Verifier()

    def effect_claiming_handler(payload, context):  # noqa: ANN001
        result = dict(pentest_agent_handler(payload, context))
        result["acceptance_effect_intents"] = [
            {
                "effect_type": "gateway.reply",
                "payload": {"message": "must not escape"},
            }
        ]
        return result

    dispatcher = GovernedDelegateDispatcher(
        node_grants=verifier,
        targets={
            "pentest_agent": GovernedDelegateTarget(
                target_id="pentest_agent",
                handler=effect_claiming_handler,
                adapter_id="test-readonly",
                execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
            )
        },
    )
    invocation = GovernedDelegateInvocation.from_frozen(
        frozen=_frozen(),
        user_message="prepare a read-only threat model for example.test",
        context={},
    )

    with pytest.raises(GovernedDelegateDispatchError) as raised:
        await dispatcher.dispatch(invocation)

    assert raised.value.reason_code == GOVERNED_DELEGATE_OUTPUT_INVALID


def test_delegate_target_policy_reopens_exact_content_address() -> None:
    dispatcher = build_governed_delegate_dispatcher(node_grants=_Verifier())
    policy = dispatcher.require_target("pentest_agent")

    assert validate_governed_delegate_target_policy(policy) == policy
    tampered = dict(policy)
    tampered["effect_mode"] = "DIRECT_EFFECT_ALLOWED"
    with pytest.raises(GovernedDelegateTargetPolicyError):
        validate_governed_delegate_target_policy(tampered)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    (
        "grant_ref",
        "parent_grant_ref",
        "request_id",
        "session_id",
        "principal_id",
        "graph_id",
        "graph_revision",
        "root_work_order_id",
        "node_id",
        "route",
        "target_id",
    ),
)
async def test_authority_drift_never_reaches_a_registered_delegate_handler(drift: str) -> None:
    verifier = _Verifier(drift=drift)
    calls = 0

    def handler(_payload, _context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return {}

    dispatcher = GovernedDelegateDispatcher(
        node_grants=verifier,
        targets={
            "pentest_agent": GovernedDelegateTarget(
                target_id="pentest_agent",
                handler=handler,
                adapter_id="test-readonly",
                execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
            )
        },
    )
    invocation = GovernedDelegateInvocation.from_frozen(
        frozen=_frozen(),
        user_message="prepare a read-only threat model for example.test",
        context={},
    )

    with pytest.raises(GovernedDelegateDispatchError) as raised:
        await dispatcher.dispatch(invocation)

    assert raised.value.reason_code == GOVERNED_DELEGATE_AUTHORITY_INVALID
    assert calls == 0


def test_inner_delegate_module_has_no_legacy_dispatcher_or_thread_timeout_dependency() -> None:
    source_path = ROOT / "host_brain/app/governed_delegate_dispatch.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
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
    assert "host_brain.app.sub_agent_protocol" not in imported_modules
    assert "host_brain.app.agent_runtime_shared" not in imported_modules
    assert "asyncio" not in imported_names


def test_production_pentest_adapter_imports_no_model_tool_network_or_process_runtime() -> None:
    source_path = ROOT / "host_brain/app/handlers/pentest_agent_handler.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
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
    forbidden_prefixes = (
        "host_brain.model_runtime",
        "host_brain.tools",
        "host_brain.agent_runtime",
        "host_brain.channels",
        "host_brain.pentest.tools",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    assert imported_names.isdisjoint(
        {"asyncio", "httpx", "requests", "socket", "subprocess"}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("attempt_id", "attempt-drifted"),
        ("execution_mode", "MODEL_ENABLED"),
        ("side_effects_executed", True),
    ),
)
def test_readonly_delegate_usage_attestation_rejects_tamper(
    field: str,
    value: object,
) -> None:
    frozen = _frozen()
    attestation = build_readonly_delegate_usage_attestation(
        command_id=frozen.command_id,
        graph_id=frozen.graph_id,
        graph_revision=frozen.graph_revision,
        node_id=frozen.node_id,
        attempt_id=frozen.attempt_id,
        node_grant_ref=frozen.node_grant_ref,
        target_id=frozen.target_id,
        adapter_id="readonly_pentest_domain_handoff.v1",
        invocation_ref="governed-delegate://sha256/" + "e" * 64,
    )
    attestation[field] = value

    with pytest.raises(ReadonlyDelegateUsageAttestationError):
        validate_readonly_delegate_usage_attestation(attestation)


def test_readonly_delegate_usage_attestation_rejects_nonzero_measurement() -> None:
    frozen = _frozen()
    attestation = build_readonly_delegate_usage_attestation(
        command_id=frozen.command_id,
        graph_id=frozen.graph_id,
        graph_revision=frozen.graph_revision,
        node_id=frozen.node_id,
        attempt_id=frozen.attempt_id,
        node_grant_ref=frozen.node_grant_ref,
        target_id=frozen.target_id,
        adapter_id="readonly_pentest_domain_handoff.v1",
        invocation_ref="governed-delegate://sha256/" + "f" * 64,
    )
    attestation["measurements"]["tool_call_count"] = 1

    with pytest.raises(ReadonlyDelegateUsageAttestationError):
        validate_readonly_delegate_usage_attestation(attestation)
