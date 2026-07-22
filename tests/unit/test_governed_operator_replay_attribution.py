from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.acceptance_effect_handlers import (
    sys_mode_transition_handler_receipt_ref,
)
from host_brain.app.governed_operator_runtime import (
    GovernedOperatorRuntime,
    GovernedOperatorRuntimeError,
    _require_durable_sys_mode_outcome,
)
from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateEventV1,
    HostHardStateSnapshotV1,
)
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from natsume_contracts.task import HostCommandSurface


_REQUEST_A = "request-operator-history-a"
_REASON_A = "historical focus request"
_BODY_SHA = "a" * 64
_CLAIM_REF = "secure-ingress-claim://operator-history-a"
_AUDIT_REF = "secure-ingress-audit://operator-history-a"
_RUNTIME_SCOPE_ID = "runtime-scope-history"


def _authority() -> dict[str, Any]:
    return {
        "allowed": True,
        "duplicate": False,
        "request_id": _REQUEST_A,
        "principal_id": "principal.operator.history",
        "tenant_id": "tenant.operator.history",
        "claim_ref": _CLAIM_REF,
        "audit_ref": _AUDIT_REF,
        "body_sha256": _BODY_SHA,
        "action": "obs:command:sys_mode.switch",
        "required_scopes": ("obs:write", "obs:sys-mode:write"),
        "ingress_surface": "obs.command",
        "method": "POST",
        "normalized_path": "/v1/host/obs-commands/sys_mode.switch",
        "operator_command_id": "sys_mode.switch",
    }


def _durable_authority() -> dict[str, Any]:
    return {
        "audit_ref": _AUDIT_REF,
        "claim_ref": _CLAIM_REF,
        "request_id": _REQUEST_A,
        "surface": "obs.command",
        "action": "obs:command:sys_mode.switch",
        "method": "POST",
        "normalized_path": "/v1/host/obs-commands/sys_mode.switch",
        "principal_id": "principal.operator.history",
        "tenant_id": "tenant.operator.history",
        "requested_scopes": ("obs:write", "obs:sys-mode:write"),
        "required_scopes": ("obs:write", "obs:sys-mode:write"),
        "body_sha256": _BODY_SHA,
        "decision": "ALLOW",
        "reason_code": "SECURE_INGRESS_ALLOWED",
        "stage": "COMPLETE",
        "side_effects_executed": False,
        "claim_status": "COMMITTED",
        "claim_result_ref": "obs-command-outcome://sha256/" + "b" * 64,
    }


def _contracts():
    governor = {
        "request_id": _REQUEST_A,
        "intent_id": "intent-operator-history-a",
        "decision": "APPROVE",
        "risk_level": 4,
        "ts_ms": 100,
    }
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.OPERATOR,
        request_id=_REQUEST_A,
        session_id=f"operator-{_REQUEST_A}",
        principal_id="principal.operator.history",
        ts_ms=100,
        intent_draft={
            "route": "delegate",
            "target_id": "operator_sys_mode_switch",
            "domain": "organ_ops",
            "task": "switch mode to focus",
            "action": "operator_sys_mode_transition",
            "target": "system",
            "args": {
                "text": "switch mode to focus",
                "route": "delegate",
                "target_id": "operator_sys_mode_switch",
                "operator_command_id": "sys_mode.switch",
                "target_mode": "focus",
                "source": "manual",
                "reason": _REASON_A,
            },
        },
        governor_result=governor,
        sanitized_message="switch mode to focus",
        person_id="principal.operator.history",
        input_sha256=_BODY_SHA,
        ingress_claim_ref=_CLAIM_REF,
        ingress_audit_ref=_AUDIT_REF,
        operator_command_id="sys_mode.switch",
    )
    return contracts, governor


class _Runtime:
    def __init__(self, terminal: Mapping[str, Any]) -> None:
        self._terminal = dict(terminal)
        self.intent_sources: list[str] = []

    async def execute_stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        args = dict(dict(kwargs["intent_draft"])["args"])
        self.intent_sources.append(str(args["source"]))
        yield dict(self._terminal)


@pytest.mark.asyncio
async def test_replay_a_uses_committed_effect_after_newer_request_b_changed_mode() -> None:
    contracts, governor = _contracts()
    event_id = (
        "obs-sys-mode:" + hashlib.sha256(_REQUEST_A.encode("utf-8")).hexdigest()
    )
    effect_payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": _REQUEST_A,
        "target_mode": "focus",
        "source": "manual",
        "reason": _REASON_A,
        "event_id": event_id,
        "runtime_scope_id": _RUNTIME_SCOPE_ID,
        "_action_preview_context": {"schema_version": "test-preview"},
    }
    effect_receipt = sys_mode_transition_handler_receipt_ref(
        effect_payload,
        outcome_status="APPLIED",
    )
    execution_receipt = {
        "command_id": contracts.command.command_id,
        "graph_id": contracts.graph.graph_id,
        "graph_revision": contracts.graph.revision,
        "node_id": contracts.graph.nodes[0].node_id,
        "attempt_id": "attempt-operator-history-a",
        "receipt_ref": "worker-receipt://sha256/" + "c" * 64,
        "status": "ACCEPTED",
        "accepted": True,
        "replayed": True,
    }
    runtime = _Runtime(
        {
            "done": True,
            "status": "ACCEPTED",
            "execution_receipt": execution_receipt,
            # The public summary is deliberately stale.  Runtime attribution
            # must reopen the canonical effect row instead of trusting it.
            "audit_refs": {"acceptance_effects": {"committed": 0}},
        }
    )
    task_store = SimpleNamespace(
        list_acceptance_effect_intents=lambda **_kwargs: [
            {
                "effect_id": "acceptance-effect://operator-history-a",
                "effect_type": "host.sys_mode_transition",
                "status": "COMMITTED",
                "graph_id": contracts.graph.graph_id,
                "graph_revision": contracts.graph.revision,
                "node_id": contracts.graph.nodes[0].node_id,
                "attempt_id": "attempt-operator-history-a",
                "payload": effect_payload,
                "receipt_ref": effect_receipt,
                "committed_ts_ms": 200,
            }
        ]
    )
    mode_service = SimpleNamespace(
        get_current_status=lambda: {"sys_mode": "maintenance"},
        reopen_transition_commit=lambda **_kwargs: HostHardStateCommitV1(
            snapshot=HostHardStateSnapshotV1(
                runtime_scope_id=_RUNTIME_SCOPE_ID,
                revision=4,
                sys_mode="maintenance",
                event_id="obs-sys-mode:request-b",
                event_digest="sha256:" + "d" * 64,
                updated_at_ms=400,
            ),
            event=HostHardStateEventV1(
                runtime_scope_id=_RUNTIME_SCOPE_ID,
                revision=2,
                event_id=event_id,
                event_kind="MODE_CHANGED",
                from_mode="normal",
                to_mode="focus",
                source="manual",
                reason=_REASON_A,
                previous_event_digest="sha256:" + "a" * 64,
                event_digest="sha256:" + "e" * 64,
                runtime_owner_id="runtime-owner-history",
                runtime_lease_id="runtime-lease-history",
                runtime_fencing_token=1,
                runtime_issued_at_ms=100,
                runtime_expires_at_ms=1_000,
                transition_ts_ms=200,
                created_at_ms=200,
            ),
            exact_replay=True,
        ),
    )
    operator = GovernedOperatorRuntime(
        runtime=runtime,
        command_repository=SimpleNamespace(
            get_by_request=lambda **_kwargs: {
                "command": contracts.command,
                "work_order": contracts.work_order,
            }
        ),
        intent_engine=SimpleNamespace(),
        governor_grants=SimpleNamespace(require=lambda **_kwargs: governor),
        review_store=SimpleNamespace(),
        task_store=task_store,
        terminal_operator_command_store=SimpleNamespace(read=lambda _ref: {}),
        mode_transition_service_provider=lambda: mode_service,
        secure_ingress_authority_resolver=SimpleNamespace(
            resolve_committed_authority_by_audit=(
                lambda **_kwargs: _durable_authority()
            )
        ),
        runtime_scope_id=_RUNTIME_SCOPE_ID,
    )

    result = await operator.execute_sys_mode_switch(
        {
            "target_mode": "focus",
            # A caller cannot rewrite the canonical HardState source.
            "source": "skill",
            "reason": _REASON_A,
            "principal_id": "principal.operator.history",
            "_operator_authority": _authority(),
        },
        _REQUEST_A,
    )

    assert result["success"] is True
    assert result["exact_replay"] is True
    assert result["side_effects_executed"] is False
    assert result["historical_side_effects_executed"] is True
    assert result["current_mode"] == "maintenance"
    assert result["target_mode"] == "focus"
    assert result["execution_receipt"]["receipt_ref"] == (
        "worker-receipt://sha256/" + "c" * 64
    )
    assert result["acceptance_effect_receipt"]["handler_receipt_ref"] == (
        effect_receipt
    )
    assert result["acceptance_effect_receipt"]["hard_state_event"][
        "event_id"
    ] == event_id
    assert runtime.intent_sources == ["manual"]


def test_no_change_requires_its_positive_durable_handler_receipt() -> None:
    event_id = "obs-sys-mode:" + hashlib.sha256(
        _REQUEST_A.encode("utf-8")
    ).hexdigest()
    payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": _REQUEST_A,
        "target_mode": "focus",
        "source": "manual",
        "reason": _REASON_A,
        "event_id": event_id,
        "runtime_scope_id": _RUNTIME_SCOPE_ID,
        "_action_preview_context": {"schema_version": "test-preview"},
    }
    no_change_receipt = sys_mode_transition_handler_receipt_ref(
        payload,
        outcome_status="NO_CHANGE",
    )
    execution_receipt = {
        "graph_id": "graph-no-change",
        "graph_revision": 1,
        "node_id": "node-no-change",
        "attempt_id": "attempt-no-change",
    }

    def store_with(receipt_ref: str) -> SimpleNamespace:
        return SimpleNamespace(
            list_acceptance_effect_intents=lambda **_kwargs: [
                {
                    "effect_id": "acceptance-effect://operator-no-change",
                    "effect_type": "host.sys_mode_transition",
                    "status": "COMMITTED",
                    **execution_receipt,
                    "payload": payload,
                    "receipt_ref": receipt_ref,
                    "committed_ts_ms": 200,
                }
            ]
        )

    no_change_service = SimpleNamespace(
        reopen_transition_commit=lambda **_kwargs: None,
    )
    outcome = _require_durable_sys_mode_outcome(
        task_store=store_with(no_change_receipt),
        mode_transition_service_provider=lambda: no_change_service,
        execution_receipt=execution_receipt,
        request_id=_REQUEST_A,
        target_mode="focus",
        source="manual",
        reason=_REASON_A,
        runtime_scope_id=_RUNTIME_SCOPE_ID,
    )
    assert outcome["historical_status"] == "NO_CHANGE"
    assert outcome["hard_state_event"] is None

    applied_receipt = sys_mode_transition_handler_receipt_ref(
        payload,
        outcome_status="APPLIED",
    )
    with pytest.raises(
        GovernedOperatorRuntimeError,
        match="lost its durable HardState event",
    ):
        _require_durable_sys_mode_outcome(
            task_store=store_with(applied_receipt),
            mode_transition_service_provider=lambda: no_change_service,
            execution_receipt=execution_receipt,
            request_id=_REQUEST_A,
            target_mode="focus",
            source="manual",
            reason=_REASON_A,
            runtime_scope_id=_RUNTIME_SCOPE_ID,
        )
