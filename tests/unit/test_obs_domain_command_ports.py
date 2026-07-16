from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandContractError,
    ObsCommandEnvelope,
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
from host_brain.app.observability_runtime.domain_command_ports import (
    OBS_DOMAIN_RESULT_SCHEMA,
    ObsDomainCommandError,
    build_default_obs_domain_command_registry,
)


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


def _execute(command_id: str, payload: dict[str, object]):
    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port(command_id)
    assert port is not None
    return asyncio.run(port.execute(_envelope(command_id, payload)))


def test_default_registry_binds_all_active_manifest_commands() -> None:
    registry = build_default_obs_domain_command_registry()
    expected = tuple(sorted(spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS))

    assert len(expected) == 35
    assert registry.bound_command_ids == expected
    assert all(registry.resolve_port(command_id) is not None for command_id in expected)


def test_bio_adapter_uses_bound_principal_not_caller_owner(monkeypatch) -> None:
    from host_brain.app import obs_routes_agent_bio_ops as bio_routes

    captured: dict[str, object] = {}

    def _accept(*, candidate_id, run_id, body):  # noqa: ANN001
        captured.update(candidate_id=candidate_id, run_id=run_id, body=dict(body))
        return {"ok": True, "decision": "OWNER_ACCEPTED"}

    monkeypatch.setattr(bio_routes, "accept_codex_bio_ops_adoption", _accept)
    result = _execute(
        "bio.adoption.accept",
        {
            "candidate_id": "candidate-1",
            "run_id": "run-1",
            "owner_organ": "caller-forged-owner",
            "operator": "caller-forged-operator",
            "reason": "reviewed",
        },
    )

    assert captured["body"]["owner_organ"] == "principal.authenticated"
    assert captured["body"]["operator"] == "principal.authenticated"
    assert result.result["principal_id"] == "principal.authenticated"
    assert result.result["schema_version"] == OBS_DOMAIN_RESULT_SCHEMA
    assert result.result_ref.startswith("obs-command://result/bio/adoption/accept/")


def test_capability_forge_adapter_maps_bound_proposer(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies

    captured: dict[str, object] = {}

    class _Forge:
        @staticmethod
        def create_proposal(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"}

    monkeypatch.setattr(dependencies, "get_capability_forge_service", lambda: _Forge())
    result = _execute(
        "capability_forge.proposal.create",
        {
            "session_id": "session-1",
            "candidate_key": "candidate-key",
            "proposer": "caller-forged-proposer",
        },
    )

    assert captured["proposer"] == "principal.authenticated"
    assert captured["session_id"] == "session-1"
    assert result.result["output"]["proposal_id"] == "proposal-1"


def test_avatar_adapter_awaits_async_edge_port(monkeypatch) -> None:
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
    result = _execute(
        "avatar.behavior.apply",
        {"behavior_id": "listening", "source": "owner-command"},
    )

    assert captured["behavior_id"] == "listening"
    assert str(captured["request_id"]).startswith("obs-command-")
    assert result.success is True
    assert result.result["output"]["event"] == {"behavior_id": "listening"}


def test_sysmode_adapter_maps_service_and_rejects_fallback(monkeypatch) -> None:
    from host_brain.app import obs_routes_sysmode as sysmode_routes

    captured: dict[str, object] = {}

    class _ModeService:
        @staticmethod
        def switch_mode(target_mode, **kwargs):  # noqa: ANN001, ANN003
            captured.update(target_mode=target_mode, **kwargs)
            return {"success": True, "from_mode": "normal", "to_mode": target_mode}

    monkeypatch.setattr(sysmode_routes, "_get_mode_transition_service", lambda: _ModeService())
    result = _execute(
        "sys_mode.switch",
        {"target_mode": "focus", "reason": "work", "source": "manual"},
    )
    assert captured["target_mode"] == "focus"
    assert result.result["output"]["to_mode"] == "focus"

    fallback = sysmode_routes._FallbackModeService()
    monkeypatch.setattr(sysmode_routes, "_get_mode_transition_service", lambda: fallback)
    with pytest.raises(ObsDomainCommandError, match="OBS_SYS_MODE_DOMAIN_UNAVAILABLE"):
        _execute("sys_mode.auto_rules.update", {"rules": {"gaming_detect": False}})


def test_domain_exception_propagates_without_legacy_degraded_result(monkeypatch) -> None:
    from host_brain.channels import embodiment

    async def _fail(**_kwargs):  # noqa: ANN003
        raise RuntimeError("edge unavailable")

    monkeypatch.setattr(embodiment, "submit_avatar_behavior", _fail)
    with pytest.raises(RuntimeError, match="edge unavailable"):
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
        result = asyncio.run(port.execute(envelope))

    assert captured["graph_id"] == "graph-cancel-2"
    assert captured["graph_revision"] == 7
    assert captured["reason_code"] == "OPERATOR_REQUESTED"
    assert set(captured) == {
        "graph_id",
        "graph_revision",
        "reason_code",
        "authority",
    }
    authority = captured["authority"]
    assert isinstance(authority, dict)
    assert authority == {
        "allowed": True,
        "audit_ref": "audit://obs-command/task_graph.cancel",
        "body_sha256": authenticated_body_sha256,
        "claim_ref": "secure-ingress://claim/task_graph.cancel",
        "duplicate": False,
        "principal_id": "principal.authenticated",
        "tenant_id": "tenant-test",
        "request_id": "request-task_graph.cancel",
        "action": "obs:command:task_graph.cancel",
        "required_scopes": ("obs:write", "task-runtime:cancel"),
    }
    assert result.success is True
    assert result.side_effects_executed is True
    assert result.result["principal_id"] == "principal.authenticated"
    assert result.result["output"]["cancellation"] == {
        "graph_id": "graph-cancel-2",
        "graph_revision": 7,
        "status": "PENDING",
    }


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

    assert raised.value.reason_code == "GRAPH_CANCELLATION_REJECTED"
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
        result = asyncio.run(registry.resolve_port("task_operation.reconcile").execute(envelope))

    assert captured == {
        "intent_ref": "taskcancel-intent://sha256/" + "a" * 64,
        "reservation_id": "reservation-1",
        "expected_version": 4,
        "action": "RELEASE",
        "reconciler_actor_id": "reconciler.authenticated",
        "downstream_receipt_ref": None,
        "attestation_ref": "reconciliation://operation/proof-1",
        "attestation_key_id": "reconciler-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }
    assert result.success is True
    assert result.side_effects_executed is True
    assert result.result["output"]["reconciliation"]["status"] == "RELEASED"


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

    assert raised.value.reason_code == "OPERATION_RECONCILIATION_UNCERTAIN"
    assert raised.value.side_effects_may_have_run is True
