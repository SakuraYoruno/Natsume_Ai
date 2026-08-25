"""Default domain adapters for authenticated OBS commands.

The command layer has already validated the manifest payload and replaced all
caller-supplied authority fields before these ports run.  Adapters therefore
consume authority only from ``envelope.payload`` and never reconstruct it from
legacy HTTP parameters or environment fallbacks.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from dataclasses import asdict, dataclass, is_dataclass
from functools import partial
from typing import Any, Callable, Mapping

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.contracts.operator_command_retirement import (
    RETIRED_OPERATOR_COMMAND_IDS,
)
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    ObsCommandEnvelope,
    ObsCommandExecutionResult,
    ObsCommandPortExecutionError,
    ObsCommandPortRegistry,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationError,
)
from host_brain.task_runtime.acceptance_effect_reconciliation import (
    AcceptanceEffectReconciliationError,
)
from host_brain.observability.operator_command_gateway import operator_runtime_value
from host_brain.runtime.degradation_authority import (
    DegradationCapabilityPort,
    evaluate_degradation_capability,
)
from natsume_contracts.task import HostCommandSurface


OBS_DOMAIN_RESULT_SCHEMA = "natsume.obs-domain-command-result.v1"
_LOGGER = logging.getLogger(__name__)


class ObsDomainCommandError(RuntimeError):
    """Fail-closed domain adapter failure with a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


DomainHandler = Callable[[Mapping[str, Any], str], Any]


# The active manifest is an allow-list, not a graveyard for dormant direct
# writers.  Every advertised command must already enter the canonical
# HostCommand -> immutable TaskGraph spine.  Retired identities live in a
# separate data-only contract so neither Host nor Monitor can route them.
GOVERNED_OPERATOR_COMMAND_IDS = frozenset(
    spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS
)
BLOCKED_OPERATOR_COMMAND_IDS = frozenset()
if (
    len(GOVERNED_OPERATOR_COMMAND_IDS) != 16
    or GOVERNED_OPERATOR_COMMAND_IDS & RETIRED_OPERATOR_COMMAND_IDS
):
    raise RuntimeError("OPERATOR_COMMAND_ACTIVE_AND_RETIRED_SETS_INVALID")


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ObsDomainCommandError("OBS_DOMAIN_BOUND_PAYLOAD_INVALID")
    return value


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


async def _edge_command_push(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    runtime = operator_runtime_value("governed_operator_runtime")
    execute = getattr(runtime, "execute_edge_command_push", None)
    if not callable(execute):
        raise ObsDomainCommandError("OBS_GOVERNED_OPERATOR_RUNTIME_UNAVAILABLE")
    return await execute(payload, request_id)


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


async def _task_graph_cancel(payload: Mapping[str, Any], request_id: str) -> Any:
    if operator_runtime_value(
        "task_graph_cancellation_operator_enabled"
    ) is not True:
        raise ObsCommandPortExecutionError(
            "OBS_TASK_GRAPH_CANCELLATION_DISABLED",
            side_effects_may_have_run=False,
        )
    return await _execute_governed_operator_control(payload, request_id)


async def _task_operation_reconcile(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    if operator_runtime_value(
        "task_operation_reconciliation_operator_enabled"
    ) is not True:
        raise ObsDomainCommandError("OBS_TASK_OPERATION_RECONCILIATION_DISABLED")
    runtime = operator_runtime_value("governed_operator_runtime")
    reconcile = getattr(runtime, "execute_operation_reconciliation", None)
    if not callable(reconcile):
        raise ObsDomainCommandError("OBS_TASK_OPERATION_RECONCILIATION_UNAVAILABLE")
    return await reconcile(payload, request_id)


async def _task_effect_reconcile(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    if operator_runtime_value(
        "task_effect_reconciliation_operator_enabled"
    ) is not True:
        raise ObsDomainCommandError(
            "OBS_TASK_EFFECT_RECONCILIATION_DISABLED"
        )
    runtime = operator_runtime_value("governed_operator_runtime")
    reconcile = getattr(runtime, "execute_effect_reconciliation", None)
    if not callable(reconcile):
        raise ObsDomainCommandError("OBS_TASK_EFFECT_RECONCILIATION_UNAVAILABLE")
    try:
        result = await reconcile(payload, request_id)
    except (
        GraphCancellationEffectReconciliationError,
        AcceptanceEffectReconciliationError,
    ) as exc:
        raise ObsCommandPortExecutionError(
            exc.reason_code,
            side_effects_may_have_run=exc.side_effects_may_have_run,
        ) from exc
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_TASK_EFFECT_RECONCILIATION_RESULT_INVALID")
    return dict(result)


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


async def _task_review_resume(
    payload: Mapping[str, Any],
    request_id: str,
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
    return await _execute_governed_operator_control(payload, request_id)


async def _execute_governed_operator_control(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    runtime = operator_runtime_value("governed_operator_runtime")
    execute = getattr(runtime, "execute_operator_control", None)
    if not callable(execute):
        raise ObsDomainCommandError("OBS_TASK_CONTROL_RUNTIME_UNAVAILABLE")
    try:
        result = execute(payload, request_id)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        # Operator payloads can contain private review context, so diagnostics
        # emit only code-owned exception metadata and never the payload/body.
        reason_code = str(getattr(exc, "reason_code", "") or "").strip()
        detail = str(getattr(exc, "detail", "") or "").strip()
        if len(detail) > 240 or any(ord(char) < 32 for char in detail):
            detail = ""
        _LOGGER.error(
            "governed Operator control failed before OBS result binding: "
            "exception_type=%s reason_code=%s detail=%s",
            type(exc).__name__,
            reason_code or "UNCLASSIFIED",
            detail or "UNAVAILABLE",
        )
        raise
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError("OBS_TASK_CONTROL_RESULT_INVALID")
    return dict(result)


async def _execute_governed_operator_state_control(
    payload: Mapping[str, Any],
    request_id: str,
) -> Any:
    runtime = operator_runtime_value("governed_operator_runtime")
    execute = getattr(runtime, "execute_operator_state_control", None)
    if not callable(execute):
        raise ObsDomainCommandError(
            "OBS_OPERATOR_STATE_CONTROL_RUNTIME_UNAVAILABLE"
        )
    result = execute(payload, request_id)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise ObsDomainCommandError(
            "OBS_OPERATOR_STATE_CONTROL_RESULT_INVALID"
        )
    return dict(result)


_HANDLERS: Mapping[str, DomainHandler] = {
    "sys_mode.switch": _sysmode_switch,
    "sys_mode.auto_rules.update": _execute_governed_operator_state_control,
    "sys_mode.event.submit": _execute_governed_operator_state_control,
    "long_term_goal.activate": _execute_governed_operator_state_control,
    "long_term_goal.revoke": _execute_governed_operator_state_control,
    "scheduler.task.enable": _execute_governed_operator_state_control,
    "scheduler.task.disable": _execute_governed_operator_state_control,
    "scheduler.task.trigger": _scheduler_trigger,
    "edge.command.push": _edge_command_push,
    "task_graph.cancel": _task_graph_cancel,
    "task_operation.reconcile": _task_operation_reconcile,
    "task_effect.reconcile": _task_effect_reconcile,
    "task_terminal.reconcile": _task_terminal_reconcile,
    "task_review.approve": _execute_governed_operator_control,
    "task_review.reject": _execute_governed_operator_control,
    "task_review.resume": _task_review_resume,
}


_ROUTE_PORT_HANDLERS: Mapping[str, Callable[..., Any]] = {
    "sys_mode.switch": _sysmode_switch,
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

__all__ = [
    "BLOCKED_OPERATOR_COMMAND_IDS",
    "GOVERNED_OPERATOR_COMMAND_IDS",
    "RETIRED_OPERATOR_COMMAND_IDS",
    "OBS_DOMAIN_RESULT_SCHEMA",
    "ObsDomainCommandError",
    "build_obs_domain_result_ref",
    "build_default_obs_domain_command_registry",
]
