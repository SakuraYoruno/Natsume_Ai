"""Default domain adapters for authenticated OBS commands.

The command layer has already validated the manifest payload and replaced all
caller-supplied authority fields before these ports run.  Adapters therefore
consume authority only from ``envelope.payload`` and never reconstruct it from
legacy HTTP parameters or environment fallbacks.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Mapping

from .command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    ObsCommandEnvelope,
    ObsCommandExecutionResult,
    ObsCommandPortRegistry,
)


OBS_DOMAIN_RESULT_SCHEMA = "natsume.obs-domain-command-result.v1"


class ObsDomainCommandError(RuntimeError):
    """Fail-closed domain adapter failure with a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


DomainHandler = Callable[[Mapping[str, Any], str], Any]


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ObsDomainCommandError("OBS_DOMAIN_BOUND_PAYLOAD_INVALID")
    return value


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = str(payload.get(field) or "").strip()
    return value or None


def _command_request_id(command_id: str, claim_ref: str) -> str:
    digest = hashlib.sha256(
        f"{command_id}\n{claim_ref}".encode("utf-8")
    ).hexdigest()
    return f"obs-command-{digest[:32]}"


def _result_ref(command_id: str, claim_ref: str) -> str:
    digest = hashlib.sha256(
        f"{command_id}\n{claim_ref}".encode("utf-8")
    ).hexdigest()
    command_path = command_id.replace("_", "-").replace(".", "/")
    return f"obs-command://result/{command_path}/{digest}"


def _result_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if is_dataclass(value):
        dumped = asdict(value)
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return {"value": value}


@dataclass(frozen=True, slots=True)
class _DomainCommandPort:
    command_id: str
    handler: DomainHandler

    async def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        if envelope.command_id != self.command_id:
            raise ObsDomainCommandError("OBS_DOMAIN_COMMAND_ID_MISMATCH")
        payload = dict(envelope.payload)
        # This value was injected by bind_server_authority.  Do not read
        # envelope.authority here: the payload is the command/domain boundary.
        principal_id = _required_text(payload, "principal_id")
        request_id = _command_request_id(self.command_id, envelope.claim_ref)
        output = self.handler(payload, request_id)
        if inspect.isawaitable(output):
            output = await output
        normalized = _result_mapping(output)
        explicit_success = normalized.get("success")
        success = explicit_success if type(explicit_success) is bool else True
        return ObsCommandExecutionResult(
            success=success,
            result_ref=_result_ref(self.command_id, envelope.claim_ref),
            result={
                "schema_version": OBS_DOMAIN_RESULT_SCHEMA,
                "command_id": self.command_id,
                "principal_id": principal_id,
                "output": normalized,
            },
            side_effects_executed=True,
        )


def _bio_module() -> Any:
    from host_brain.app import obs_routes_agent_bio_ops

    return obs_routes_agent_bio_ops


def _bio_decision_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owner_organ": _required_text(payload, "owner_organ"),
        "operator": _required_text(payload, "operator"),
        "reason": _optional_text(payload, "reason"),
        "dry_run": bool(payload.get("dry_run", False)),
    }


def _bio_daily_cycle(payload: Mapping[str, Any], _request_id: str) -> Any:
    body = {
        "run_id": _optional_text(payload, "run_id"),
        "inputs": dict(payload.get("inputs") or {}),
    }
    return _bio_module().run_codex_bio_ops_daily_cycle(body=body)


def _bio_adoption(payload: Mapping[str, Any], *, accept: bool) -> Any:
    module = _bio_module()
    handler = (
        module.accept_codex_bio_ops_adoption
        if accept
        else module.reject_codex_bio_ops_adoption
    )
    return handler(
        candidate_id=_required_text(payload, "candidate_id"),
        run_id=_optional_text(payload, "run_id"),
        body=_bio_decision_body(payload),
    )


def _bio_adoption_accept(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_adoption(payload, accept=True)


def _bio_adoption_reject(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_adoption(payload, accept=False)


def _bio_owner_inbox(payload: Mapping[str, Any], *, packets: bool) -> Any:
    module = _bio_module()
    handler = (
        module.materialize_codex_bio_ops_owner_intake_packets
        if packets
        else module.materialize_codex_bio_ops_owner_inbox
    )
    owner = _required_text(payload, "owner_organ")
    return handler(
        run_id=_optional_text(payload, "run_id"),
        owner_organ=owner,
        body={"owner_organ": owner},
    )


def _bio_owner_inbox_materialize(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_owner_inbox(payload, packets=False)


def _bio_owner_inbox_packets(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_owner_inbox(payload, packets=True)


def _bio_owner_queue_consume(payload: Mapping[str, Any], _request_id: str) -> Any:
    owner = _required_text(payload, "owner_organ")
    return _bio_module().consume_codex_bio_ops_owner_queues(
        run_id=_optional_text(payload, "run_id"),
        owner_organ=owner,
        body={"owner_organ": owner, "dry_run": bool(payload.get("dry_run", False))},
    )


def _bio_owner_state_import(payload: Mapping[str, Any], _request_id: str) -> Any:
    owner = _required_text(payload, "owner_organ")
    return _bio_module().import_codex_bio_ops_owner_state(
        run_id=_optional_text(payload, "run_id"),
        owner_organ=owner,
        body={"owner_organ": owner, "dry_run": bool(payload.get("dry_run", False))},
    )


def _bio_owner_state(payload: Mapping[str, Any], *, accept: bool) -> Any:
    module = _bio_module()
    handler = (
        module.accept_codex_bio_ops_owner_state
        if accept
        else module.reject_codex_bio_ops_owner_state
    )
    return handler(
        candidate_id=_required_text(payload, "candidate_id"),
        run_id=_optional_text(payload, "run_id"),
        body=_bio_decision_body(payload),
    )


def _bio_owner_state_accept(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_owner_state(payload, accept=True)


def _bio_owner_state_reject(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _bio_owner_state(payload, accept=False)


def _capability_forge_service() -> Any:
    from host_brain.app.dependencies import get_capability_forge_service

    return get_capability_forge_service()


def _forge_create(payload: Mapping[str, Any], _request_id: str) -> Any:
    canary_percent = (
        int(payload["canary_traffic_percent"])
        if "canary_traffic_percent" in payload
        else 5
    )
    return _capability_forge_service().create_proposal(
        session_id=_required_text(payload, "session_id"),
        proposer=_required_text(payload, "proposer"),
        candidate_key=_optional_text(payload, "candidate_key"),
        canary_traffic_percent=canary_percent,
    )


def _forge_approve(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _capability_forge_service().approve_and_promote(
        proposal_id=_required_text(payload, "proposal_id"),
        approval_token_ref=_required_text(payload, "approval_token_ref"),
        approver=_required_text(payload, "approver"),
        target_stage=str(payload.get("target_stage") or "shadow").strip().lower(),
    )


def _forge_rollback(payload: Mapping[str, Any], _request_id: str) -> Any:
    return _capability_forge_service().rollback(
        proposal_id=_required_text(payload, "proposal_id"),
        operator=_required_text(payload, "operator"),
        reason=_optional_text(payload, "reason"),
    )


async def _avatar_behavior(payload: Mapping[str, Any], request_id: str) -> Any:
    from host_brain.channels.edge_rpc_client import post_avatar_behavior

    result = await post_avatar_behavior(
        behavior_id=_required_text(payload, "behavior_id"),
        emotion_tag=_optional_text(payload, "emotion_tag"),
        body_action_id=_optional_text(payload, "body_action_id"),
        emotion_intensity=payload.get("emotion_intensity"),
        duration_s=payload.get("duration_s"),
        talk_state=_optional_text(payload, "talk_state"),
        source=_optional_text(payload, "source") or "command",
        request_id=request_id,
    )
    return {
        "success": bool(result.success),
        "reason_code": result.reason_code,
        "detail": result.detail,
        "latency_ms": int(result.latency_ms or 0),
        "event": dict(result.event or {}),
    }


def _sysmode_service() -> Any:
    from host_brain.app import obs_routes_sysmode

    service = obs_routes_sysmode._get_mode_transition_service()
    if isinstance(service, obs_routes_sysmode._FallbackModeService):
        raise ObsDomainCommandError("OBS_SYS_MODE_DOMAIN_UNAVAILABLE")
    return service


def _sysmode_switch(payload: Mapping[str, Any], _request_id: str) -> Any:
    service = _sysmode_service()
    source: Any = None
    try:
        from host_brain.core.mode_transition import TransitionSource

        source = TransitionSource(str(payload.get("source") or "manual"))
    except (ImportError, ValueError):
        source = None
    kwargs: dict[str, Any] = {"reason": str(payload.get("reason") or "")}
    if source is not None:
        kwargs["source"] = source
    return service.switch_mode(_required_text(payload, "target_mode"), **kwargs)


def _sysmode_auto_rules(payload: Mapping[str, Any], _request_id: str) -> Any:
    updated = _sysmode_service().update_auto_rules(dict(payload.get("rules") or {}))
    return {"success": True, "auto_rules": updated}


def _sysmode_event(payload: Mapping[str, Any], _request_id: str) -> Any:
    event_type = _required_text(payload, "event_type")
    result = _sysmode_service().on_event(event_type)
    return {
        "success": True,
        "triggered": result is not None,
        "event_type": event_type,
        "result": _result_mapping(result) if result is not None else None,
    }


_HANDLERS: Mapping[str, DomainHandler] = {
    "bio.daily_cycle.run": _bio_daily_cycle,
    "bio.adoption.accept": _bio_adoption_accept,
    "bio.adoption.reject": _bio_adoption_reject,
    "bio.owner_inbox.materialize": _bio_owner_inbox_materialize,
    "bio.owner_inbox.intake_packets": _bio_owner_inbox_packets,
    "bio.owner_queue.consume": _bio_owner_queue_consume,
    "bio.owner_state.import": _bio_owner_state_import,
    "bio.owner_state.accept": _bio_owner_state_accept,
    "bio.owner_state.reject": _bio_owner_state_reject,
    "capability_forge.proposal.create": _forge_create,
    "capability_forge.proposal.approve": _forge_approve,
    "capability_forge.proposal.rollback": _forge_rollback,
    "avatar.behavior.apply": _avatar_behavior,
    "sys_mode.switch": _sysmode_switch,
    "sys_mode.auto_rules.update": _sysmode_auto_rules,
    "sys_mode.event.submit": _sysmode_event,
}


def build_default_obs_domain_command_registry() -> ObsCommandPortRegistry:
    """Bind every manifest command to its fail-closed domain adapter."""

    manifest_ids = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    if set(_HANDLERS) != manifest_ids:
        raise RuntimeError("OBS_DOMAIN_COMMAND_PORT_MANIFEST_MISMATCH")
    return ObsCommandPortRegistry(
        ports={
            command_id: _DomainCommandPort(command_id, handler)
            for command_id, handler in _HANDLERS.items()
        },
    )


# Explicit alias for composition code that uses the shorter port terminology.
build_default_obs_domain_port_registry = build_default_obs_domain_command_registry


__all__ = [
    "OBS_DOMAIN_RESULT_SCHEMA",
    "ObsDomainCommandError",
    "build_default_obs_domain_command_registry",
    "build_default_obs_domain_port_registry",
]
