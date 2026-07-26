from __future__ import annotations

import asyncio
import inspect
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
    assert GOVERNED_OPERATOR_COMMAND_IDS == frozenset(
        {
            "edge.command.push",
            "scheduler.task.trigger",
            "sys_mode.switch",
            "task_effect.reconcile",
            "task_operation.reconcile",
            "task_terminal.reconcile",
        }
    )
    assert len(BLOCKED_OPERATOR_COMMAND_IDS) == 31
    assert BLOCKED_OPERATOR_COMMAND_IDS | GOVERNED_OPERATOR_COMMAND_IDS == set(
        expected
    )
    authority = ObsCommandAuthority.bind(
        principal_id="principal.blocked-matrix",
        tenant_id="tenant-test",
        request_id="request-blocked-matrix",
    )
    for command_id in sorted(BLOCKED_OPERATOR_COMMAND_IDS):
        spec = registry.resolve_spec(command_id)
        port = registry.resolve_port(command_id)
        assert spec is not None
        assert port is not None
        envelope = ObsCommandEnvelope(
            schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
            command_id=command_id,
            action=spec.action,
            required_scopes=spec.required_scopes,
            payload={},
            authority=authority,
            claim_ref=f"secure-ingress://claim/{command_id}",
            audit_ref=f"audit://obs-command/{command_id}",
            body_sha256="0" * 64,
        )
        with pytest.raises(ObsCommandPortExecutionError) as blocked:
            asyncio.run(port.execute(envelope))
        assert blocked.value.reason_code == "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED"
        assert blocked.value.side_effects_may_have_run is False


def test_blocked_canon_commands_retain_no_dormant_route_writer() -> None:
    from host_brain.app.observability_runtime import domain_command_ports

    source = inspect.getsource(domain_command_ports)
    assert "canon_routes.submit_proposal" not in source
    assert "canon_routes.approve_proposal" not in source
    assert "canon_routes.reject_proposal" not in source
    assert "canon_routes.rollback_proposal" not in source


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


def test_capability_forge_adapter_stays_blocked_without_legacy_locator() -> None:
    from host_brain.composition import process_services as dependencies

    assert not hasattr(dependencies, "get_capability_forge_service")
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


def test_sysmode_switch_uses_request_bound_governed_operator_runtime() -> None:
    governed_calls: list[dict[str, object]] = []
    direct_mode_calls: list[dict[str, object]] = []

    class _ModeService:
        @staticmethod
        def switch_mode(target_mode, **kwargs):  # noqa: ANN001, ANN003
            direct_mode_calls.append({"target_mode": target_mode, **kwargs})
            raise AssertionError("route_ports ModeTransitionService must not run")

    class _GovernedOperatorRuntime:
        @staticmethod
        async def execute_sys_mode_switch(payload, request_id):  # noqa: ANN001
            governed_calls.append(
                {"payload": dict(payload), "request_id": request_id}
            )
            return {
                "success": True,
                "side_effects_executed": False,
                "target_mode": "focus",
                "current_mode": "focus",
                "execution_receipt": {"status": "ACCEPTED"},
            }

    registry = build_default_obs_domain_command_registry(
        route_ports=HostRoutePorts(
            mode_transition_service_provider=lambda: _ModeService(),
        )
    )
    port = registry.resolve_port("sys_mode.switch")
    assert port is not None
    envelope = _envelope(
        "sys_mode.switch",
        {"target_mode": "focus", "reason": "work", "source": "manual"},
    )
    state = SimpleNamespace(
        governed_operator_runtime=_GovernedOperatorRuntime(),
    )

    with bind_operator_command_runtime(state, raw_body=b"authenticated-mode-switch"):
        result = asyncio.run(port.execute(envelope))

    assert result.success is True
    assert result.side_effects_executed is False
    assert result.result["output"]["execution_receipt"] == {"status": "ACCEPTED"}
    assert len(governed_calls) == 1
    call = governed_calls[0]
    assert call["request_id"] == envelope.authority.request_id
    payload = call["payload"]
    assert isinstance(payload, dict)
    assert payload["target_mode"] == "focus"
    assert payload["principal_id"] == "principal.authenticated"
    authority = payload["_operator_authority"]
    assert isinstance(authority, dict)
    assert authority["operator_command_id"] == "sys_mode.switch"
    assert authority["principal_id"] == "principal.authenticated"
    assert authority["request_id"] == envelope.authority.request_id
    assert authority["tenant_id"] == envelope.authority.tenant_id
    assert authority["action"] == "obs:command:sys_mode.switch"
    assert authority["required_scopes"] == (
        "obs:write",
        "obs:sys-mode:write",
    )
    assert authority["ingress_surface"] == "obs.command"
    assert authority["method"] == "POST"
    assert authority["normalized_path"] == (
        "/v1/host/obs-commands/sys_mode.switch"
    )
    assert direct_mode_calls == []


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

    class _GovernedOperatorRuntime:
        @staticmethod
        async def execute_operation_reconciliation(payload, request_id):
            captured.update(payload)
            captured["request_id"] = request_id
            return {
                "success": True,
                "status": "RECONCILED",
                "reservation_id": payload["reservation_id"],
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
        task_operation_reconciliation_operator_enabled=True,
        governed_operator_runtime=_GovernedOperatorRuntime(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-proof"):
        result = asyncio.run(
            registry.resolve_port("task_operation.reconcile").execute(envelope)
        )

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert result.success is True
    assert captured["reconciler_actor_id"] == "reconciler.authenticated"
    assert captured["reservation_id"] == "reservation-1"
    assert captured["request_id"] == "request-task_operation.reconcile"


def test_task_operation_reconcile_is_fail_closed_when_feature_is_disabled() -> None:
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
        task_operation_reconciliation_operator_enabled=False,
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-proof"):
        with pytest.raises(ObsDomainCommandError) as raised:
            asyncio.run(
                registry.resolve_port("task_operation.reconcile").execute(
                    envelope
                )
            )

    assert raised.value.reason_code == "OBS_TASK_OPERATION_RECONCILIATION_DISABLED"


def test_task_effect_reconcile_binds_actor_and_forwards_exact_signed_snapshot() -> None:
    captured: dict[str, object] = {}

    class _GovernedRuntime:
        @staticmethod
        async def execute_effect_reconciliation(payload, request_id):  # noqa: ANN001
            captured.update(payload)
            captured["request_id"] = request_id
            return {
                "success": True,
                "status": "RECONCILED",
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
        task_effect_reconciliation_operator_enabled=True,
        governed_operator_runtime=_GovernedRuntime(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-effect-proof"):
        result = asyncio.run(
            registry.resolve_port("task_effect.reconcile").execute(envelope)
        )

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert envelope.payload["expected_case_snapshot_sha256"] == case_digest
    assert envelope.payload["expected_subject_snapshot_sha256"] == subject_digest
    assert result.success is True
    assert captured["reconciler_actor_id"] == "reconciler.authenticated"
    assert captured["request_id"] == "request-task_effect.reconcile"
    assert captured["_operator_authority"]["operator_command_id"] == (
        "task_effect.reconcile"
    )


def test_task_effect_reconcile_preserves_unknown_write_certainty() -> None:
    class _GovernedRuntime:
        @staticmethod
        async def execute_effect_reconciliation(_payload, _request_id):  # noqa: ANN001
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
        task_effect_reconciliation_operator_enabled=True,
        governed_operator_runtime=_GovernedRuntime(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-effect-proof"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(ObsCommandRouter(registry).route(envelope))

    assert raised.value.reason_code == "EFFECT_RECONCILIATION_UNCERTAIN"
    assert raised.value.side_effects_may_have_run is True


def test_task_terminal_reconcile_binds_actor_and_forwards_signed_targets() -> None:
    captured: dict[str, object] = {}

    class _GovernedRuntime:
        @staticmethod
        async def execute_terminal_reconciliation(payload, request_id):  # noqa: ANN001
            captured.update(payload)
            captured["request_id"] = request_id
            return {
                "success": True,
                "status": "RECONCILED",
                "side_effects_executed": True,
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
        task_terminal_reconciliation_operator_enabled=True,
        governed_operator_runtime=_GovernedRuntime(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-terminal-proof"):
        result = asyncio.run(
            registry.resolve_port("task_terminal.reconcile").execute(envelope)
        )

    assert envelope.payload["reconciler_actor_id"] == "reconciler.authenticated"
    assert str(envelope.payload["target_graph_status"]).upper() == "FAILED"
    assert str(envelope.payload["target_budget_status"]).upper() == "RELEASED"
    assert captured["reconciler_actor_id"] == "reconciler.authenticated"
    assert captured["request_id"] == "request-task_terminal.reconcile"
    assert result.side_effects_executed is True


def test_task_terminal_reconcile_preserves_unknown_write_certainty() -> None:
    class _GovernedRuntime:
        @staticmethod
        async def execute_terminal_reconciliation(_payload, _request_id):  # noqa: ANN001
            raise RuntimeError("response lost")

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
        task_terminal_reconciliation_operator_enabled=True,
        governed_operator_runtime=_GovernedRuntime(),
    )
    with bind_operator_command_runtime(state, raw_body=b"signed-terminal-proof"):
        with pytest.raises(ObsCommandRoutingError) as raised:
            asyncio.run(
                ObsCommandRouter(registry).route(envelope)
            )

    assert raised.value.reason_code == "OBS_COMMAND_EXECUTION_UNCERTAIN"
    assert raised.value.side_effects_may_have_run is True
