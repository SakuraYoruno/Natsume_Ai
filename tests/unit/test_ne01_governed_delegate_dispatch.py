from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from host_brain.app.governed_delegate_dispatch import (
    GOVERNED_DELEGATE_AUTHORITY_INVALID,
    GOVERNED_DELEGATE_TARGET_UNAVAILABLE,
    GovernedDelegateDispatchError,
    GovernedDelegateInvocation,
    GovernedDelegateTarget,
    GovernedDelegateDispatcher,
    build_governed_delegate_dispatcher,
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
    assert outcome.audit_refs["delegate_execution_mode"] == "READ_ONLY_HANDOFF"
    assert outcome.audit_refs["side_effects_executed"] is False
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
