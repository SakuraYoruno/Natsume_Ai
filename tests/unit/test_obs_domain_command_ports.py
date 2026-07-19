from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from natsume_contracts.task import HostCommandSurface

from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandContractError,
    ObsCommandEnvelope,
    ObsCommandPortExecutionError,
    bind_server_authority,
)
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.observability.command_router import (
    ObsCommandRouter,
    ObsCommandRoutingError,
)
from host_brain.composition.runtime_group import GraphCancellationOperatorError
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationError,
)
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationError,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    BLOCKED_OPERATOR_COMMAND_IDS,
    GOVERNED_OPERATOR_COMMAND_IDS,
    OBS_DOMAIN_RESULT_SCHEMA,
    ObsDomainCommandError,
    build_default_obs_domain_command_registry,
)
from host_brain.app.observability_runtime import domain_command_ports
from host_brain.app.host_route_ports import HostRoutePorts


def _envelope(
    command_id: str,
    payload: dict[str, object],
    *,
    principal_id: str = "principal.authenticated",
    body_sha256: str = "0" * 64,
) -> ObsCommandEnvelope:
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec(command_id)
    assert spec is not None
    authority = ObsCommandAuthority.bind(
        principal_id=principal_id,
        tenant_id="tenant-test",
        request_id=f"request-{command_id}",
    )
    validated = spec.payload_schema.validate(payload)
    bound = bind_server_authority(validated, spec=spec, authority=authority)
    return ObsCommandEnvelope(
        schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
        command_id=command_id,
        action=spec.action,
        required_scopes=spec.required_scopes,
        payload=bound,
        authority=authority,
        claim_ref=f"secure-ingress://claim/{command_id}",
        audit_ref=f"audit://obs-command/{command_id}",
        body_sha256=body_sha256,
    )


def _execute(
    command_id: str,
    payload: dict[str, object],
    *,
    route_ports: HostRoutePorts | None = None,
):
    registry = build_default_obs_domain_command_registry(route_ports=route_ports)
    port = registry.resolve_port(command_id)
    assert port is not None
    return asyncio.run(port.execute(_envelope(command_id, payload)))


def test_default_registry_binds_all_active_manifest_commands() -> None:
    registry = build_default_obs_domain_command_registry()
    expected = tuple(sorted(spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS))

    assert len(expected) == 37
    assert registry.bound_command_ids == expected
    assert all(registry.resolve_port(command_id) is not None for command_id in expected)
    assert GOVERNED_OPERATOR_COMMAND_IDS == frozenset({"scheduler.task.trigger"})
    assert len(BLOCKED_OPERATOR_COMMAND_IDS) == 36
    assert BLOCKED_OPERATOR_COMMAND_IDS | GOVERNED_OPERATOR_COMMAND_IDS == set(
        expected
    )


def test_scheduler_trigger_enters_operator_surface_and_reports_review_pause() -> None:
    captured: dict[str, object] = {}

    class _SchedulerCommandService:
        @staticmethod
        async def trigger_manual(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "done": True,
                "execution_receipt": {
                    "status": "WAITING_APPROVAL",
                    "projection": {
                        "review": {"review_id": "task_review_operator_1"},
                    },
                },
            }

    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port("scheduler.task.trigger")
    assert port is not None
    state = SimpleNamespace(
        scheduler_container=SimpleNamespace(
            command_service=_SchedulerCommandService(),
        ),
    )
    envelope = _envelope(
        "scheduler.task.trigger",
        {"task_id": "operator_governed_task", "payload": {"reason": "test"}},
    )

    with bind_operator_command_runtime(state, raw_body=b"authenticated-body"):
        result = asyncio.run(port.execute(envelope))

    assert captured["command_surface"] is HostCommandSurface.OPERATOR
    assert captured["task_id"] == "operator_governed_task"
    assert captured["payload"] == {"reason": "test"}
    authority = captured["authority"]
    assert isinstance(authority, dict)
    assert authority["principal_id"] == "principal.authenticated"
    assert authority["claim_ref"] == envelope.claim_ref
    assert authority["audit_ref"] == envelope.audit_ref
    assert result.success is True
    assert result.side_effects_executed is False
    assert result.result["output"]["execution_receipt"]["status"] == (
        "WAITING_APPROVAL"
    )


def test_bio_adapter_uses_bound_principal_not_caller_owner(monkeypatch) -> None:
    from host_brain.app import obs_routes_agent_bio_ops as bio_routes

    captured: dict[str, object] = {}

    def _accept(*, candidate_id, run_id, body):  # noqa: ANN001
        captured.update(candidate_id=candidate_id, run_id=run_id, body=dict(body))
        return {"ok": True, "decision": "OWNER_ACCEPTED"}

    monkeypatch.setattr(bio_routes, "accept_codex_bio_ops_adoption", _accept)
    with pytest.raises(ObsCommandPortExecutionError) as blocked:
        _execute(
            "bio.adoption.accept",
            {
                "candidate_id": "candidate-1",
                "run_id": "run-1",
                "owner_organ": "caller-forged-owner",
                "operator": "caller-forged-operator",
                "reason": "reviewed",
            },
        )
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_capability_forge_adapter_maps_bound_proposer(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies

    captured: dict[str, object] = {}

    class _Forge:
        @staticmethod
        def create_proposal(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"}

    monkeypatch.setattr(dependencies, "get_capability_forge_service", lambda: _Forge())
    with pytest.raises(ObsCommandPortExecutionError) as blocked:
        _execute(
            "capability_forge.proposal.create",
            {
                "session_id": "session-1",
                "candidate_key": "candidate-key",
                "proposer": "caller-forged-proposer",
            },
        )
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_avatar_adapter_fails_before_async_edge_port_without_task_preview(monkeypatch) -> None:
    from host_brain.channels import embodiment

    captured: dict[str, object] = {}

    async def _post_avatar_behavior(**kwargs):  # noqa: ANN003
        await asyncio.sleep(0)
        captured.update(kwargs)
        return SimpleNamespace(
            success=True,
            reason_code="OK",
            detail="applied",
            latency_ms=3,
            event={"behavior_id": kwargs["behavior_id"]},
        )

    monkeypatch.setattr(embodiment, "submit_avatar_behavior", _post_avatar_behavior)
    with pytest.raises(ObsCommandPortExecutionError) as blocked:
        _execute(
            "avatar.behavior.apply",
            {"behavior_id": "listening", "source": "owner-command"},
        )
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_sysmode_adapter_fails_closed_until_governed_operator_cutover() -> None:
    captured: dict[str, object] = {}

    class _ModeService:
        @staticmethod
        def switch_mode(target_mode, **kwargs):  # noqa: ANN001, ANN003
            captured.update(target_mode=target_mode, **kwargs)
            return {"success": True, "from_mode": "normal", "to_mode": target_mode}

    with pytest.raises(ObsCommandPortExecutionError) as blocked:
        _execute(
            "sys_mode.switch",
            {"target_mode": "focus", "reason": "work", "source": "manual"},
            route_ports=HostRoutePorts(
                mode_transition_service_provider=lambda: _ModeService(),
            ),
        )
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert captured == {}


def test_sysmode_adapter_prepares_stable_request_identity_behind_gate() -> None:
    captured: dict[str, object] = {}

    class _ModeService:
        @staticmethod
        def switch_mode(target_mode, **kwargs):  # noqa: ANN001, ANN003
            captured.update(target_mode=target_mode, **kwargs)
            return {"success": True}

    result = domain_command_ports._sysmode_switch(  # noqa: SLF001
        {"target_mode": "focus", "reason": "work", "source": "manual"},
        "authenticated-command-request",
        route_ports=HostRoutePorts(
            mode_transition_service_provider=lambda: _ModeService(),
        ),
    )
    assert result == {"success": True}
    assert captured["event_id"] == (
        "obs-sys-mode:"
        + hashlib.sha256(b"authenticated-command-request").hexdigest()
    )


def test_direct_avatar_exception_is_unreachable_without_task_preview(monkeypatch) -> None:
    from host_brain.channels import embodiment

    async def _fail(**_kwargs):  # noqa: ANN003
        raise RuntimeError("edge unavailable")

    monkeypatch.setattr(embodiment, "submit_avatar_behavior", _fail)
    with pytest.raises(
        ObsCommandPortExecutionError,
        match="ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
    ):
        _execute("avatar.behavior.apply", {"behavior_id": "thinking"})


def test_task_graph_cancel_manifest_owns_scope_and_denies_caller_authority() -> None:
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("task_graph.cancel")
    assert spec is not None
    assert spec.action == "obs:command:task_graph.cancel"
    assert spec.required_scopes == ("obs:write", "task-runtime:cancel")
    assert "task-review:write" not in spec.required_scopes

    base_payload: dict[str, object] = {
        "graph_id": "graph-cancel-1",
        "graph_revision": 3,
        "reason_code": "OPERATOR_REQUESTED",
    }
    for denied_field in ("source", "request_identity"):
        with pytest.raises(
            ObsCommandContractError,
            match="OBS_COMMAND_PAYLOAD_FIELD_DENIED",
        ):
            spec.payload_schema.validate({
                **base_payload,
                denied_field: "caller-forged-authority",
            })

    authority = ObsCommandAuthority.bind(
        principal_id="principal.authenticated",
        tenant_id="tenant-test",
        request_id="request-task-graph-cancel",
    )
    validated = spec.payload_schema.validate({
        **base_payload,
        "principal_id": "principal.caller-forged",
    })
    bound = bind_server_authority(validated, spec=spec, authority=authority)
    assert bound["principal_id"] == "principal.authenticated"
    assert "source" not in bound
    assert "request_identity" not in bound


def test_task_graph_cancel_passes_authenticated_authority_and_marks_pending_intent_committed() -> None:
    captured: dict[str, object] = {}

    class _CancellationService:
        @staticmethod
        async def request_graph_cancellation(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "graph_id": kwargs["graph_id"],
                "graph_revision": kwargs["graph_revision"],
                "status": "PENDING",
            }

    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port("task_graph.cancel")
    assert port is not None
    authenticated_body_sha256 = "a" * 64
    envelope = _envelope(
        "task_graph.cancel",
        {
            "graph_id": "graph-cancel-2",
            "graph_revision": 7,
            "reason_code": "operator_requested",
            "principal_id": "principal.caller-forged",
        },
        principal_id="principal.authenticated",
        body_sha256=authenticated_body_sha256,
    )
    state = SimpleNamespace(
        task_graph_cancellation_command_service=_CancellationService(),
    )
    with bind_operator_command_runtime(
        state,
        raw_body=b"different-untrusted-runtime-fallback",
    ):
        with pytest.raises(ObsCommandPortExecutionError) as blocked:
            asyncio.run(port.execute(envelope))

    assert envelope.payload["principal_id"] == "principal.authenticated"
    assert envelope.body_sha256 == authenticated_body_sha256
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_task_graph_cancel_translates_only_trusted_runtime_certainty() -> None:
    class _CancellationService:
        @staticmethod
        async def request_graph_cancellation(**_kwargs):  # noqa: ANN003
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_REJECTED",
                side_effects_may_have_run=False,
            )

    registry = build_default_obs_domain_command_registry()
    envelope = _envelope(
        "task_graph.cancel",
        {
            "graph_id": "graph-cancel-rejected",
            "graph_revision": 3,
            "reason_code": "OPERATOR_REQUESTED",
        },
    )
    state = SimpleNamespace(
        task_graph_cancellation_command_service=_CancellationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"trusted-body"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(ObsCommandRouter(registry).route(envelope))

    assert raised.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert raised.value.side_effects_may_have_run is False


def test_task_operation_reconcile_binds_actor_and_forwards_signed_proof() -> None:
    captured: dict[str, object] = {}

    class _ReconciliationService:
        @staticmethod
        async def reconcile_graph_cancellation_operation(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "intent_ref": kwargs["intent_ref"],
                "reservation_id": kwargs["reservation_id"],
                "status": "RELEASED",
            }

    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("task_operation.reconcile")
    assert spec is not None
    assert spec.required_scopes == ("obs:write", "task-runtime:reconcile")
    envelope = _envelope(
        "task_operation.reconcile",
        {
            "intent_ref": "taskcancel-intent://sha256/" + "a" * 64,
            "reservation_id": "reservation-1",
            "expected_version": 4,
            "action": "release",
            "attestation_ref": "reconciliation://operation/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
        principal_id="reconciler.authenticated",
    )
    state = SimpleNamespace(
        task_operation_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-proof"):
        with pytest.raises(ObsCommandPortExecutionError) as blocked:
            asyncio.run(registry.resolve_port("task_operation.reconcile").execute(envelope))

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_task_operation_reconcile_translates_only_nominal_certainty() -> None:
    class _ReconciliationService:
        @staticmethod
        def reconcile_graph_cancellation_operation(**_kwargs):  # noqa: ANN003
            raise GraphCancellationOperationReconciliationError(
                "OPERATION_RECONCILIATION_UNCERTAIN",
                "response lost",
                side_effects_may_have_run=True,
            )

    registry = build_default_obs_domain_command_registry()
    envelope = _envelope(
        "task_operation.reconcile",
        {
            "intent_ref": "taskcancel-intent://sha256/" + "a" * 64,
            "reservation_id": "reservation-1",
            "expected_version": 4,
            "action": "RELEASE",
            "attestation_ref": "reconciliation://operation/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
    )
    state = SimpleNamespace(
        task_operation_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-proof"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(ObsCommandRouter(registry).route(envelope))

    assert raised.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert raised.value.side_effects_may_have_run is False


def test_task_effect_reconcile_binds_actor_and_forwards_exact_signed_snapshot() -> None:
    captured: dict[str, object] = {}

    class _ReconciliationService:
        @staticmethod
        async def reconcile_graph_cancellation_effect(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "intent_ref": kwargs["intent_ref"],
                "status": "PROOF_RECORDED",
                "effect": {"effect_id": kwargs["effect_id"]},
            }

    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("task_effect.reconcile")
    assert spec is not None
    assert spec.required_scopes == ("obs:write", "task-runtime:reconcile")
    case_digest = "a" * 64
    subject_digest = "b" * 64
    envelope = _envelope(
        "task_effect.reconcile",
        {
            "intent_ref": "taskcancel-intent://sha256/" + "c" * 64,
            "effect_id": "acceptance-effect://sha256/" + "d" * 64,
            "case_id": "taskcancel-effect-case://sha256/" + "e" * 64,
            "expected_case_snapshot_sha256": case_digest,
            "expected_subject_snapshot_sha256": subject_digest,
            "purpose": "pending_cancellation",
            "outcome": "no_side_effect",
            "issued_ts_ms": 1_800_000_000_000,
            "attestation_ref": "reconciliation://effect/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
        principal_id="reconciler.authenticated",
    )
    state = SimpleNamespace(
        task_effect_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-effect-proof"):
        with pytest.raises(ObsCommandPortExecutionError) as blocked:
            asyncio.run(registry.resolve_port("task_effect.reconcile").execute(envelope))

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert envelope.payload["expected_case_snapshot_sha256"] == case_digest
    assert envelope.payload["expected_subject_snapshot_sha256"] == subject_digest
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_task_effect_reconcile_preserves_unknown_write_certainty() -> None:
    class _ReconciliationService:
        @staticmethod
        def reconcile_graph_cancellation_effect(**_kwargs):  # noqa: ANN003
            raise GraphCancellationEffectReconciliationError(
                "EFFECT_RECONCILIATION_UNCERTAIN",
                "response lost",
                side_effects_may_have_run=True,
            )

    registry = build_default_obs_domain_command_registry()
    envelope = _envelope(
        "task_effect.reconcile",
        {
            "intent_ref": "taskcancel-intent://sha256/" + "c" * 64,
            "effect_id": "acceptance-effect://sha256/" + "d" * 64,
            "case_id": "taskcancel-effect-case://sha256/" + "e" * 64,
            "expected_case_snapshot_sha256": "a" * 64,
            "expected_subject_snapshot_sha256": "b" * 64,
            "purpose": "PENDING_CANCELLATION",
            "outcome": "NO_SIDE_EFFECT",
            "issued_ts_ms": 1_800_000_000_000,
            "attestation_ref": "reconciliation://effect/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
    )
    state = SimpleNamespace(
        task_effect_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-effect-proof"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(ObsCommandRouter(registry).route(envelope))

    assert raised.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert raised.value.side_effects_may_have_run is False


def test_task_terminal_reconcile_binds_actor_and_forwards_signed_targets() -> None:
    captured: dict[str, object] = {}

    class _ReconciliationService:
        @staticmethod
        async def reconcile_terminal_state(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "status": "RECONCILED",
                "case": {"case_id": kwargs["case_id"]},
                "replayed": False,
            }

    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("task_terminal.reconcile")
    assert spec is not None
    assert spec.required_scopes == ("obs:write", "task-runtime:reconcile")
    assert spec.authority_field == "reconciler_actor_id"
    envelope = _envelope(
        "task_terminal.reconcile",
        {
            "case_id": "terminal-reconciliation://case/" + "a" * 64,
            "expected_case_snapshot_sha256": "sha256:" + "b" * 64,
            "expected_subject_snapshot_sha256": "sha256:" + "c" * 64,
            "action": "fail_graph_and_release_budget",
            "target_graph_status": "failed",
            "target_budget_status": "released",
            "issued_ts_ms": 1_800_000_000_000,
            "attestation_ref": "terminal-reconciliation://attestation/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
        principal_id="reconciler.authenticated",
    )
    state = SimpleNamespace(
        task_terminal_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-terminal-proof"):
        with pytest.raises(ObsCommandPortExecutionError) as blocked:
            asyncio.run(registry.resolve_port("task_terminal.reconcile").execute(envelope))

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert str(envelope.payload["target_graph_status"]).upper() == "FAILED"
    assert str(envelope.payload["target_budget_status"]).upper() == "RELEASED"
    assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert blocked.value.side_effects_may_have_run is False
    assert captured == {}


def test_task_terminal_reconcile_preserves_unknown_write_certainty() -> None:
    class _ReconciliationService:
        @staticmethod
        def reconcile_terminal_state(**_kwargs):  # noqa: ANN003
            raise TerminalStateReconciliationError(
                "TERMINAL_RECONCILIATION_UNCERTAIN",
                "response lost",
                side_effects_may_have_run=True,
            )

    registry = build_default_obs_domain_command_registry()
    envelope = _envelope(
        "task_terminal.reconcile",
        {
            "case_id": "terminal-reconciliation://case/" + "a" * 64,
            "expected_case_snapshot_sha256": "sha256:" + "b" * 64,
            "expected_subject_snapshot_sha256": "sha256:" + "c" * 64,
            "action": "RELEASE_BUDGET",
            "target_budget_status": "RELEASED",
            "issued_ts_ms": 1_800_000_000_000,
            "attestation_ref": "terminal-reconciliation://attestation/proof-1",
            "attestation_key_id": "reconciler-v1",
            "attestation_signature": "ed25519:" + "A" * 86 + "==",
        },
    )
    state = SimpleNamespace(
        task_terminal_reconciliation_command_service=_ReconciliationService(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-terminal-proof"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(
                ObsCommandRouter(registry).route(envelope)
            )

    assert raised.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
    assert raised.value.side_effects_may_have_run is False
