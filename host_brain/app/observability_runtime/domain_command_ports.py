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
from functools import partial
from typing import Any, Callable, Mapping

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    ObsCommandEnvelope,
    ObsCommandExecutionResult,
    ObsCommandPortExecutionError,
    ObsCommandPortRegistry,
)
from host_brain.composition.runtime_group import GraphCancellationOperatorError
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationError,
)
from host_brain.observability.operator_command_gateway import operator_runtime_value
from host_brain.runtime.degradation_authority import (
    DegradationCapabilityPort,
    evaluate_degradation_capability,
)
from natsume_contracts.task import HostCommandSurface


OBS_DOMAIN_RESULT_SCHEMA = "natsume.obs-domain-command-result.v1"


class ObsDomainCommandError(RuntimeError):
    """Fail-closed domain adapter failure with a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


DomainHandler = Callable[[Mapping[str, Any], str], Any]


# These legacy Obs command adapters still invoke production writers/actuators
# directly instead of admitting an immutable TaskGraph.  Secure Ingress proves
# who asked; it is not an exact node-bound Preview Receipt.  Keep them visibly
# fail-closed until NE-01 gives each command a Governed Operator route.
GOVERNED_OPERATOR_COMMAND_IDS = frozenset({
    "scheduler.task.trigger",
    "sys_mode.switch",
    "task_terminal.reconcile",
})
BLOCKED_OPERATOR_COMMAND_IDS = frozenset(
    spec.command_id
    for spec in DEFAULT_OBS_COMMAND_SPECS
    if spec.command_id not in GOVERNED_OPERATOR_COMMAND_IDS
)
if (
    len(BLOCKED_OPERATOR_COMMAND_IDS) != 34
    or BLOCKED_OPERATOR_COMMAND_IDS & GOVERNED_OPERATOR_COMMAND_IDS
    or BLOCKED_OPERATOR_COMMAND_IDS | GOVERNED_OPERATOR_COMMAND_IDS
    != {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
):
    raise RuntimeError("OPERATOR_COMMAND_GOVERNANCE_SET_MUST_MATCH_MANIFEST")


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ObsDomainCommandError("OBS_DOMAIN_BOUND_PAYLOAD_INVALID")
    return value


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = str(payload.get(field) or "").strip()
    return value or None


def build_obs_domain_result_ref(command_id: str, claim_ref: str) -> str:
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
        if self.command_id in BLOCKED_OPERATOR_COMMAND_IDS:
            raise ObsCommandPortExecutionError(
                "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
                side_effects_may_have_run=False,
            )
        payload = dict(envelope.payload)
        canonical_request_id = str(envelope.authority.request_id or "").strip()
        if not canonical_request_id:
            raise ObsDomainCommandError("OBS_DOMAIN_AUTHORITY_INVALID")
        payload["_operator_authority"] = {
            "allowed": True,
            "audit_ref": envelope.audit_ref,
            "body_sha256": envelope.body_sha256,
            "claim_ref": envelope.claim_ref,
            "duplicate": False,
            "principal_id": envelope.authority.principal_id,
            "tenant_id": envelope.authority.tenant_id,
            "request_id": envelope.authority.request_id,
            "action": envelope.action,
            "required_scopes": tuple(envelope.required_scopes),
            "ingress_surface": "obs.command",
            "method": "POST",
            "normalized_path": (
                f"/v1/host/obs-commands/{self.command_id}"
            ),
            "operator_command_id": self.command_id,
        }
        # This value was injected by bind_server_authority.  Do not read
        # envelope.authority here: the payload is the command/domain boundary.
        principal_id = _required_text(payload, "principal_id")
        output = self.handler(payload, canonical_request_id)
        if inspect.isawaitable(output):
            output = await output
        normalized = _result_mapping(output)
        explicit_success = normalized.get("success")
        success = explicit_success if type(explicit_success) is bool else True
        explicit_side_effects = normalized.pop("side_effects_executed", None)
        side_effects_executed = (
            explicit_side_effects
            if type(explicit_side_effects) is bool
            else success
        )
        return ObsCommandExecutionResult(
            success=success,
            result_ref=build_obs_domain_result_ref(
                self.command_id,
                envelope.claim_ref,
            ),
            result={
                "schema_version": OBS_DOMAIN_RESULT_SCHEMA,
                "command_id": self.command_id,
                "principal_id": principal_id,
                "output": normalized,
            },
            side_effects_executed=side_effects_executed,
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
    from host_brain.composition.process_services import get_capability_forge_service

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
    from host_brain.channels.embodiment import submit_avatar_behavior

    result = await submit_avatar_behavior(
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


def _sysmode_service(route_ports: HostRoutePorts, *methods: str) -> Any:
    provider = route_ports.mode_transition_service_provider
    try:
        service = provider() if provider is not None else None
    except Exception as exc:
        raise ObsDomainCommandError("OBS_SYS_MODE_DOMAIN_UNAVAILABLE") from exc
    if service is None or any(
        not callable(getattr(service, method, None)) for method in methods
    ):
        raise ObsDomainCommandError("OBS_SYS_MODE_DOMAIN_UNAVAILABLE")
    return service


async def _sysmode_switch(
    payload: Mapping[str, Any],
    request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    del route_ports
    runtime = operator_runtime_value("governed_operator_runtime")
    execute = getattr(runtime, "execute_sys_mode_switch", None)
    if not callable(execute):
        raise ObsDomainCommandError("OBS_GOVERNED_OPERATOR_RUNTIME_UNAVAILABLE")
    return await execute(payload, request_id)


def _sysmode_auto_rules(
    payload: Mapping[str, Any],
    _request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    updated = _sysmode_service(route_ports, "update_auto_rules").update_auto_rules(
        dict(payload.get("rules") or {})
    )
    return {"success": True, "auto_rules": updated}


def _sysmode_event(
    payload: Mapping[str, Any],
    _request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    event_type = _required_text(payload, "event_type")
    result = _sysmode_service(route_ports, "on_event").on_event(event_type)
    return {
        "success": True,
        "triggered": result is not None,
        "event_type": event_type,
        "result": _result_mapping(result) if result is not None else None,
    }


def _forge_runtime_match(payload: Mapping[str, Any], request_id: str) -> Any:
    return _capability_forge_service().get_runtime_route_override(
        request_id=request_id,
        suggested_family=_required_text(payload, "suggested_family"),
        missing_capability_tags=list(payload.get("missing_capability_tags") or ()),
        reason_code=_optional_text(payload, "reason_code"),
    )


def _blocked_legacy_operator_handler(
    _payload: Mapping[str, Any],
    _request_id: str,
) -> Any:
    """Keep manifest coverage without retaining a dormant domain writer."""

    raise ObsCommandPortExecutionError(
        "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
        side_effects_may_have_run=False,
    )


def _scheduler_service() -> Any:
    container = operator_runtime_value("scheduler_container")
    service = getattr(container, "command_service", None) if container is not None else None
    if service is None:
        raise ObsDomainCommandError("OBS_SCHEDULER_DOMAIN_UNAVAILABLE")
    return service


def _scheduler_authority(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    authority = payload.get("_operator_authority")
    if not isinstance(authority, Mapping):
        raise ObsDomainCommandError("OBS_DOMAIN_BOUND_AUTHORITY_INVALID")
    return dict(authority)


async def _scheduler_set_enabled(payload: Mapping[str, Any], *, enabled: bool) -> Any:
    service = _scheduler_service()
    task_id = _required_text(payload, "task_id")
    expected = payload.get("expected_head_version")
    if expected is None:
        current = service.get_definition(task_id)
        if inspect.isawaitable(current):
            current = await current
        if not isinstance(current, Mapping):
            raise ObsDomainCommandError("SCHEDULER_DEFINITION_NOT_FOUND")
        expected = int(current.get("head_version") or 0)
    output = service.set_enabled(
        task_id=task_id,
        enabled=enabled,
        authority=_scheduler_authority(payload),
        expected_head_version=int(expected),
        received_at_ms=int(__import__("time").time() * 1000),
    )
    return await output if inspect.isawaitable(output) else output


async def _scheduler_enable(payload: Mapping[str, Any], _request_id: str) -> Any:
    return await _scheduler_set_enabled(payload, enabled=True)


async def _scheduler_disable(payload: Mapping[str, Any], _request_id: str) -> Any:
    return await _scheduler_set_enabled(payload, enabled=False)


async def _scheduler_trigger(payload: Mapping[str, Any], _request_id: str) -> Any:
    output = _scheduler_service().trigger_manual(
        task_id=_required_text(payload, "task_id"),
        authority=_scheduler_authority(payload),
        payload=dict(payload.get("payload") or {}),
        received_at_ms=int(__import__("time").time() * 1000),
        command_surface=HostCommandSurface.OPERATOR,
    )
    resolved = await output if inspect.isawaitable(output) else output
    if not isinstance(resolved, Mapping):
        raise ObsDomainCommandError("OBS_SCHEDULER_RESULT_INVALID")
    result = dict(resolved)
    receipt = result.get("execution_receipt")
    receipt_payload = dict(receipt) if isinstance(receipt, Mapping) else {}
    status = str(receipt_payload.get("status") or "").strip().upper()
    if status not in {
        "WAITING_APPROVAL",
        "WAITING_ACCEPTANCE_REVIEW",
        "ACCEPTED",
        "FAILED",
        "BLOCKED",
        "CANCELLED",
    }:
        raise ObsDomainCommandError("OBS_SCHEDULER_RESULT_INVALID")
    effect_status = str(result.get("scheduler_effect_status") or "").strip().upper()
    effect_committed = status == "ACCEPTED" and effect_status == "COMMITTED"
    # The outer OBS outcome records admission as success.  A TaskNode being
    # accepted is not proof that its post-Acceptance Scheduler operation ran;
    # only the committed Effect receipt may assert an executed side effect.
    return {
        **result,
        "success": status
        in {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
        or effect_committed,
        "side_effects_executed": effect_committed,
    }


def _tool_execute(
    payload: Mapping[str, Any],
    _request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    from host_brain.app import tool_routes

    body = tool_routes.ToolExecuteRequest(**_canon_payload(payload, (
        "tool_id", "intent_id", "session_id", "seq", "args", "context",
        "approval_token_ref", "confirm_token_ref", "thread_id", "turn_id",
    )))
    return tool_routes.execute_tool(body, _route_ports=route_ports)


def _mate_action(payload: Mapping[str, Any], request_id: str) -> Any:
    from host_brain.app import mateengine_routes

    body_values = _canon_payload(payload, (
        "action", "state", "touch_type", "emotion_tag", "expression", "preset",
        "text", "enabled", "ttl", "duration", "intensity", "scale",
        "position_x", "position_y",
    ))
    body_values["request_id"] = request_id
    return mateengine_routes.mateengine_action(
        mateengine_routes.MateEngineActionRequest(**body_values)
    )


def _mate_say(payload: Mapping[str, Any], _request_id: str) -> Any:
    from host_brain.app import mateengine_routes

    return mateengine_routes.mateengine_say(
        mateengine_routes.MateEngineSayRequest(**_canon_payload(payload, (
            "text", "emotion", "ttl",
        )))
    )


def _bond_event(
    payload: Mapping[str, Any],
    request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    from host_brain.app import bond_routes

    body_values = _canon_payload(payload, (
        "user_id", "update_kind", "evidence_refs", "emotion_tag", "emotion_intensity",
    ))
    body_values["request_id"] = request_id
    return bond_routes.submit_bond_event(
        bond_routes.BondEventRequest(**body_values),
        _route_ports=route_ports,
    )


def _edge_command_push(payload: Mapping[str, Any], _request_id: str) -> Any:
    from host_brain.tribe.edge_command_pusher import get_edge_command_pusher

    pusher = get_edge_command_pusher()
    if pusher is None:
        raise ObsDomainCommandError("OBS_EDGE_COMMAND_DOMAIN_UNAVAILABLE")
    return pusher.push_command(
        command_type=_required_text(payload, "command_type"),
        target_agent=_required_text(payload, "target_agent"),
        source_agent=_required_text(payload, "source_agent"),
        payload=dict(payload.get("payload") or {}),
    )


async def _security_hunt(
    payload: Mapping[str, Any],
    _request_id: str,
    *,
    route_ports: HostRoutePorts,
) -> Any:
    """Use the same Host-bound Security Immune provider as the HTTP adapter."""

    provider = route_ports.security_immune_agent_provider
    if not callable(provider):
        raise ObsDomainCommandError("OBS_SECURITY_IMMUNE_AGENT_UNAVAILABLE")
    try:
        agent = provider()
        run_once = getattr(agent, "run_once", None)
        if agent is None or not callable(run_once):
            raise TypeError("security immune agent is unavailable")
        result = run_once(
            reason=_optional_text(payload, "reason") or "operator_command",
        )
        if inspect.isawaitable(result):
            result = await result
    except ObsDomainCommandError:
        raise
    except Exception as exc:
        raise ObsDomainCommandError("OBS_SECURITY_IMMUNE_AGENT_UNAVAILABLE") from exc
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_SECURITY_IMMUNE_AGENT_RESULT_INVALID")
    return dict(result)


async def _task_graph_cancel(payload: Mapping[str, Any], _request_id: str) -> Any:
    del _request_id
    service = operator_runtime_value("task_graph_cancellation_command_service")
    request_cancellation = getattr(service, "request_graph_cancellation", None)
    if not callable(request_cancellation):
        raise ObsDomainCommandError("OBS_TASK_GRAPH_CANCELLATION_UNAVAILABLE")
    authority = payload.get("_operator_authority")
    if not isinstance(authority, Mapping):
        raise ObsDomainCommandError("OBS_DOMAIN_BOUND_AUTHORITY_INVALID")
    try:
        result = request_cancellation(
            graph_id=_required_text(payload, "graph_id"),
            graph_revision=int(payload["graph_revision"]),
            reason_code=_required_text(payload, "reason_code").upper(),
            authority=dict(authority),
        )
        if inspect.isawaitable(result):
            result = await result
    except GraphCancellationOperatorError as exc:
        raise ObsCommandPortExecutionError(
            exc.reason_code,
            side_effects_may_have_run=exc.side_effects_may_have_run,
        ) from exc
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_TASK_GRAPH_CANCELLATION_RESULT_INVALID")
    return {
        "success": True,
        "side_effects_executed": True,
        "cancellation": dict(result),
    }


async def _task_operation_reconcile(
    payload: Mapping[str, Any],
    _request_id: str,
) -> Any:
    del _request_id
    service = operator_runtime_value("task_operation_reconciliation_command_service")
    reconcile = getattr(service, "reconcile_graph_cancellation_operation", None)
    if not callable(reconcile):
        raise ObsDomainCommandError("OBS_TASK_OPERATION_RECONCILIATION_UNAVAILABLE")
    try:
        result = reconcile(
            intent_ref=_required_text(payload, "intent_ref"),
            reservation_id=_required_text(payload, "reservation_id"),
            expected_version=int(payload["expected_version"]),
            action=_required_text(payload, "action").upper(),
            reconciler_actor_id=_required_text(payload, "reconciler_actor_id"),
            downstream_receipt_ref=_optional_text(
                payload,
                "downstream_receipt_ref",
            ),
            attestation_ref=_required_text(payload, "attestation_ref"),
            attestation_key_id=_required_text(payload, "attestation_key_id"),
            attestation_signature=_required_text(payload, "attestation_signature"),
        )
        if inspect.isawaitable(result):
            result = await result
    except GraphCancellationOperationReconciliationError as exc:
        raise ObsCommandPortExecutionError(
            exc.reason_code,
            side_effects_may_have_run=exc.side_effects_may_have_run,
        ) from exc
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_TASK_OPERATION_RECONCILIATION_RESULT_INVALID")
    return {
        "success": True,
        "side_effects_executed": True,
        "reconciliation": dict(result),
    }


async def _task_effect_reconcile(
    payload: Mapping[str, Any],
    _request_id: str,
) -> Any:
    del _request_id
    service = operator_runtime_value("task_effect_reconciliation_command_service")
    reconcile = getattr(service, "reconcile_graph_cancellation_effect", None)
    if not callable(reconcile):
        raise ObsDomainCommandError("OBS_TASK_EFFECT_RECONCILIATION_UNAVAILABLE")
    try:
        result = reconcile(
            intent_ref=_required_text(payload, "intent_ref"),
            effect_id=_required_text(payload, "effect_id"),
            case_id=_required_text(payload, "case_id"),
            expected_case_snapshot_sha256=_required_text(
                payload,
                "expected_case_snapshot_sha256",
            ),
            expected_subject_snapshot_sha256=_required_text(
                payload,
                "expected_subject_snapshot_sha256",
            ),
            purpose=_required_text(payload, "purpose").upper(),
            outcome=_required_text(payload, "outcome").upper(),
            reconciler_actor_id=_required_text(payload, "reconciler_actor_id"),
            issued_ts_ms=int(payload["issued_ts_ms"]),
            downstream_receipt_ref=_optional_text(
                payload,
                "downstream_receipt_ref",
            ),
            executor_quiescence_ref=_optional_text(
                payload,
                "executor_quiescence_ref",
            ),
            attestation_ref=_required_text(payload, "attestation_ref"),
            attestation_key_id=_required_text(payload, "attestation_key_id"),
            attestation_signature=_required_text(payload, "attestation_signature"),
        )
        if inspect.isawaitable(result):
            result = await result
    except GraphCancellationEffectReconciliationError as exc:
        raise ObsCommandPortExecutionError(
            exc.reason_code,
            side_effects_may_have_run=exc.side_effects_may_have_run,
        ) from exc
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_TASK_EFFECT_RECONCILIATION_RESULT_INVALID")
    return {
        "success": True,
        "side_effects_executed": True,
        "reconciliation": dict(result),
    }


async def _task_terminal_reconcile(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    if operator_runtime_value(
        "task_terminal_reconciliation_operator_enabled"
    ) is not True:
        raise ObsDomainCommandError("OBS_TASK_TERMINAL_RECONCILIATION_DISABLED")
    runtime = operator_runtime_value("governed_operator_runtime")
    reconcile = getattr(runtime, "execute_terminal_reconciliation", None)
    if not callable(reconcile):
        raise ObsDomainCommandError("OBS_TASK_TERMINAL_RECONCILIATION_UNAVAILABLE")
    return await reconcile(payload, request_id)


def _task_review_approve(payload: Mapping[str, Any], _request_id: str) -> Any:
    service = _task_review_service()
    review_id = _required_text(payload, "review_id")
    approved = service.approve_review(
        review_id,
        actor_id=_required_text(payload, "actor_id"),
        expected_subject_ref=_required_text(payload, "subject_ref"),
        expected_subject_version=int(payload["subject_version"]),
        expected_resume_phase=_required_text(payload, "resume_phase"),
        expected_checkpoint_id=_required_text(payload, "checkpoint_id"),
    )
    return {"accepted": True, **dict(approved)}


def _task_review_reject(payload: Mapping[str, Any], _request_id: str) -> Any:
    service = _task_review_service()
    review_id = _required_text(payload, "review_id")
    record = service.reject_review(
        review_id,
        actor_id=_required_text(payload, "actor_id"),
    )
    return {"accepted": True, "review": dict(record)}


def _task_review_resume(
    payload: Mapping[str, Any],
    _request_id: str,
    *,
    degradation_capability_port_provider: (
        Callable[[], DegradationCapabilityPort | None] | None
    ) = None,
) -> Any:
    try:
        port = (
            degradation_capability_port_provider()
            if degradation_capability_port_provider is not None
            else None
        )
    except Exception:
        port = None
    degradation = evaluate_degradation_capability(
        port,
        capability_id="remote_review_resume",
        static_risk_level=4,
    )
    if not bool(degradation.get("allowed")):
        raise ObsDomainCommandError(
            str(
                degradation.get("reason_code")
                or "DEGRADATION_STATE_UNAVAILABLE"
            )
        )
    service = _task_review_service()
    review_id = _required_text(payload, "review_id")
    resume = service.resume_review(
        review_id,
        resume_token_ref=_required_text(payload, "resume_token_ref"),
        subject_ref=_required_text(payload, "subject_ref"),
        subject_version=int(payload["subject_version"]),
        resume_phase=_required_text(payload, "resume_phase"),
        checkpoint_id=_required_text(payload, "checkpoint_id"),
        resume_result_ref=_optional_text(payload, "resume_result_ref"),
    )
    return {"accepted": True, "resume": dict(resume)}


def _task_review_service() -> Any:
    service = operator_runtime_value("task_review_command_service")
    required = ("approve_review", "reject_review", "resume_review")
    if service is None or any(
        not callable(getattr(service, method_name, None))
        for method_name in required
    ):
        raise ObsDomainCommandError("OBS_TASK_REVIEW_DOMAIN_UNAVAILABLE")
    return service


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
    "capability_forge.runtime.match": _forge_runtime_match,
    "canon.proposal.submit": _blocked_legacy_operator_handler,
    "canon.proposal.approve": _blocked_legacy_operator_handler,
    "canon.proposal.reject": _blocked_legacy_operator_handler,
    "canon.proposal.rollback": _blocked_legacy_operator_handler,
    "scheduler.task.enable": _scheduler_enable,
    "scheduler.task.disable": _scheduler_disable,
    "scheduler.task.trigger": _scheduler_trigger,
    "tool.execute": _tool_execute,
    "mate.action.submit": _mate_action,
    "mate.say.submit": _mate_say,
    "bond.event.submit": _bond_event,
    "edge.command.push": _edge_command_push,
    "security.hunt.run_once": _security_hunt,
    "task_graph.cancel": _task_graph_cancel,
    "task_operation.reconcile": _task_operation_reconcile,
    "task_effect.reconcile": _task_effect_reconcile,
    "task_terminal.reconcile": _task_terminal_reconcile,
    "task_review.approve": _task_review_approve,
    "task_review.reject": _task_review_reject,
    "task_review.resume": _task_review_resume,
}


_ROUTE_PORT_HANDLERS: Mapping[str, Callable[..., Any]] = {
    "tool.execute": _tool_execute,
    "bond.event.submit": _bond_event,
    "sys_mode.switch": _sysmode_switch,
    "sys_mode.auto_rules.update": _sysmode_auto_rules,
    "sys_mode.event.submit": _sysmode_event,
    "security.hunt.run_once": _security_hunt,
}


def build_default_obs_domain_command_registry(
    *,
    route_ports: HostRoutePorts | None = None,
    degradation_capability_port_provider: (
        Callable[[], DegradationCapabilityPort | None] | None
    ) = None,
) -> ObsCommandPortRegistry:
    """Bind every manifest command to its fail-closed domain adapter."""

    manifest_ids = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    if set(_HANDLERS) != manifest_ids:
        raise RuntimeError("OBS_DOMAIN_COMMAND_PORT_MANIFEST_MISMATCH")
    effective_ports = route_ports or HostRoutePorts()
    handlers = dict(_HANDLERS)
    handlers.update({
        command_id: partial(handler, route_ports=effective_ports)
        for command_id, handler in _ROUTE_PORT_HANDLERS.items()
    })
    handlers["task_review.resume"] = partial(
        _task_review_resume,
        degradation_capability_port_provider=(
            degradation_capability_port_provider
        ),
    )
    return ObsCommandPortRegistry(
        ports={
            command_id: _DomainCommandPort(command_id, handler)
            for command_id, handler in handlers.items()
        },
    )


# Explicit alias for composition code that uses the shorter port terminology.
build_default_obs_domain_port_registry = build_default_obs_domain_command_registry


__all__ = [
    "BLOCKED_OPERATOR_COMMAND_IDS",
    "GOVERNED_OPERATOR_COMMAND_IDS",
    "OBS_DOMAIN_RESULT_SCHEMA",
    "ObsDomainCommandError",
    "build_obs_domain_result_ref",
    "build_default_obs_domain_command_registry",
    "build_default_obs_domain_port_registry",
]
