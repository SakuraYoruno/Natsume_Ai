from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    bind_server_authority,
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
    )


def _execute(command_id: str, payload: dict[str, object]):
    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port(command_id)
    assert port is not None
    return asyncio.run(port.execute(_envelope(command_id, payload)))


def test_default_registry_binds_all_active_manifest_commands() -> None:
    registry = build_default_obs_domain_command_registry()
    expected = tuple(sorted(spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS))

    assert len(expected) == 34
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
