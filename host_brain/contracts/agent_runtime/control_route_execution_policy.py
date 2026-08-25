"""Immutable execution authority for governed control transports.

Scheduler, Internal and Operator commands all reuse the ``delegate`` worker
route as an internal transport.  They are not child-agent Delegates.  This
contract freezes the exact control command, target and Task Runtime policy so
that admission, replay, Review continuation, recovery and Acceptance Effect
compilation reopen the same authority instead of trusting the route string.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetExceededAction,
)
from host_brain.core.runtime_helpers import canonical_mapping_sha256 as _sha256_json
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import HostCommand, HostCommandSurface


CONTROL_ROUTE_EXECUTION_POLICY_SCHEMA_VERSION = (
    "Natsume.host.control_route_execution_policy.v1"
)
CONTROL_ROUTE_EXECUTION_POLICY_KEY = "control_route_execution_policy"
CONTROL_ROUTE_EXECUTION_MODE = "FROZEN_TASK_RUNTIME_CONTROL_V1"
CONTROL_ROUTE_BUDGET_MODE = "TASK_BOUND_FINITE_LEDGER"
CONTROL_ROUTE_VERIFICATION_MODE = "DURABLE_WORKER_PROJECTION"
CONTROL_ROUTE_EFFECT_MODE = "GRAPH_ACCEPTANCE_ONLY"
CONTROL_ROUTE_REVIEW_REQUIRED = "DURABLE_PRE_EXECUTION_REQUIRED"
CONTROL_ROUTE_INTENT_REVIEW_REQUIRED = "DURABLE_INTENT_ADMISSION_REQUIRED"
CONTROL_ROUTE_REVIEW_WAIVER = "GOVERNOR_GRANT_WAIVER"
CONTROL_ROUTE_EXECUTION_POLICY_INVALID = (
    "CONTROL_ROUTE_EXECUTION_POLICY_INVALID"
)

_SUPPORTED_SURFACES = frozenset(
    {
        HostCommandSurface.INTERNAL.value,
        HostCommandSurface.OPERATOR.value,
        HostCommandSurface.SCHEDULER.value,
    }
)
_REVIEW_MODES = frozenset(
    {
        CONTROL_ROUTE_REVIEW_REQUIRED,
        CONTROL_ROUTE_INTENT_REVIEW_REQUIRED,
        CONTROL_ROUTE_REVIEW_WAIVER,
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_POLICY_REF = re.compile(
    r"^control-route-policy://sha256/(?P<digest>[0-9a-f]{64})$"
)
_BASE_KEYS = frozenset(
    {
        "schema_version",
        "surface",
        "control_command_id",
        "transport_route",
        "target_id",
        "execution_owner",
        "execution_mode",
        "review_mode",
        "budget_mode",
        "verification_mode",
        "effect_mode",
    }
)


class ControlRouteExecutionPolicyError(ValueError):
    """Stable fail-closed error for a missing or drifted control policy."""

    def __init__(self, detail: str) -> None:
        self.reason_code = CONTROL_ROUTE_EXECUTION_POLICY_INVALID
        self.detail = str(detail or "control route execution policy is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def freeze_control_route_execution_policy(
    *,
    surface: HostCommandSurface | str,
    control_command_id: str,
    target_id: str,
    review_required: bool,
    intent_review_required: bool = False,
) -> dict[str, Any]:
    """Create one content-addressed, secret-free control policy."""

    if review_required and intent_review_required:
        raise ControlRouteExecutionPolicyError(
            "control route cannot require both Intent and TaskNode Review"
        )
    surface_value = (
        surface.value if isinstance(surface, HostCommandSurface) else str(surface)
    )
    target = _identifier(target_id, field_name="target_id")
    base = _normalized_base(
        {
            "schema_version": CONTROL_ROUTE_EXECUTION_POLICY_SCHEMA_VERSION,
            "surface": surface_value,
            "control_command_id": control_command_id,
            "transport_route": "delegate",
            "target_id": target,
            "execution_owner": (
                f"task-runtime:{str(surface_value).strip().lower()}:{target}"
            ),
            "execution_mode": CONTROL_ROUTE_EXECUTION_MODE,
            "review_mode": (
                CONTROL_ROUTE_REVIEW_REQUIRED
                if review_required
                else CONTROL_ROUTE_INTENT_REVIEW_REQUIRED
                if intent_review_required
                else CONTROL_ROUTE_REVIEW_WAIVER
            ),
            "budget_mode": CONTROL_ROUTE_BUDGET_MODE,
            "verification_mode": CONTROL_ROUTE_VERIFICATION_MODE,
            "effect_mode": CONTROL_ROUTE_EFFECT_MODE,
        }
    )
    return {
        **base,
        "policy_ref": "control-route-policy://sha256/" + _sha256_json(base),
    }


def validate_control_route_execution_policy(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen one persisted policy and verify its exact content address."""

    raw = _mapping(payload)
    if set(raw) != {*_BASE_KEYS, "policy_ref"}:
        raise ControlRouteExecutionPolicyError(
            "control policy has missing or unknown fields"
        )
    policy_ref = _text(raw.get("policy_ref"), field_name="policy_ref")
    match = _POLICY_REF.fullmatch(policy_ref)
    if match is None:
        raise ControlRouteExecutionPolicyError(
            "control policy ref is not content-addressed"
        )
    base = _normalized_base({key: raw[key] for key in _BASE_KEYS})
    if match.group("digest") != _sha256_json(base):
        raise ControlRouteExecutionPolicyError(
            "control policy digest does not match its payload"
        )
    return {**base, "policy_ref": policy_ref}


def require_control_route_execution_policy(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph | None = None,
) -> dict[str, Any] | None:
    """Require the exact persisted policy for every control surface.

    Non-control routes must not smuggle this authority.  Control routes must
    bind identical copies in ``input_pack`` and ``metadata`` and bind the
    policy reference into the Budget ledger and node inputs.
    """

    if not isinstance(command, HostCommand):
        raise ControlRouteExecutionPolicyError("HostCommand is required")
    if not isinstance(work_order, WorkOrderV3):
        raise ControlRouteExecutionPolicyError("WorkOrderV3 is required")
    input_raw = work_order.input_pack.get(CONTROL_ROUTE_EXECUTION_POLICY_KEY)
    metadata_raw = work_order.metadata.get(CONTROL_ROUTE_EXECUTION_POLICY_KEY)
    is_control = command.surface.value in _SUPPORTED_SURFACES
    if not is_control:
        if input_raw is not None or metadata_raw is not None:
            raise ControlRouteExecutionPolicyError(
                "control policy is restricted to a control surface"
            )
        return None
    try:
        input_policy = validate_control_route_execution_policy(
            input_raw if isinstance(input_raw, Mapping) else {}
        )
        metadata_policy = validate_control_route_execution_policy(
            metadata_raw if isinstance(metadata_raw, Mapping) else {}
        )
    except ControlRouteExecutionPolicyError:
        raise
    if input_policy != metadata_policy:
        raise ControlRouteExecutionPolicyError(
            "input and metadata control policies differ"
        )
    policy = input_policy
    control_command_id = _text(
        work_order.metadata.get("control_command_id"),
        field_name="work_order.metadata.control_command_id",
    )
    review = _mapping(work_order.review_policy)
    review_required = bool(
        review.get("review_required")
        or review.get("durable_review_required")
    )
    if policy["review_mode"] == CONTROL_ROUTE_INTENT_REVIEW_REQUIRED:
        if (
            command.surface is not HostCommandSurface.OPERATOR
            or review_required
            or review.get("mode") != "governor_grant_review_waiver"
            or review.get("reason") != "durable_intent_admission_review"
            or review.get("frozen_control_policy_ref")
            != policy["policy_ref"]
        ):
            raise ControlRouteExecutionPolicyError(
                "Intent Review authority differs from the frozen control policy"
            )
    else:
        expected_review_mode = (
            CONTROL_ROUTE_REVIEW_REQUIRED
            if review_required
            else CONTROL_ROUTE_REVIEW_WAIVER
        )
        if policy["review_mode"] != expected_review_mode:
            raise ControlRouteExecutionPolicyError(
                "TaskNode Review authority differs from the frozen control policy"
            )
    budget = work_order.budget_policy
    budget_metadata = (
        _mapping(getattr(budget, "metadata", {})) if budget is not None else {}
    )
    limits = budget.limits.to_dict() if budget is not None else {}
    if (
        command.route_type != "delegate"
        or policy["surface"] != command.surface.value
        or policy["control_command_id"] != control_command_id
        or policy["transport_route"] != command.route_type
        or policy["target_id"] != command.target_id
        or policy["execution_owner"]
        != (
            f"task-runtime:{command.surface.value.lower()}:"
            f"{command.target_id}"
        )
        or budget is None
        or budget.on_exceeded != BudgetExceededAction.BLOCK
        or not limits
        or any(int(value) < 0 for value in limits.values())
        or int(limits.get("concurrency", 0)) < 1
        or budget_metadata.get("control_policy_ref") != policy["policy_ref"]
        or budget_metadata.get("measurement_mode")
        != CONTROL_ROUTE_BUDGET_MODE
        or policy["policy_ref"] not in set(work_order.input_refs)
    ):
        raise ControlRouteExecutionPolicyError(
            "control policy differs from command, Review, Budget or verification authority"
        )
    if graph is not None:
        if not isinstance(graph, TaskGraph):
            raise ControlRouteExecutionPolicyError("TaskGraph is invalid")
        matching = [
            node
            for node in graph.nodes
            if node.node_id == work_order.task_node_id
        ]
        if (
            graph.graph_id != command.graph_id
            or graph.revision != command.graph_revision
            or graph.root_work_order_id != work_order.root_work_order_id
            or len(matching) != 1
            or matching[0].route
            != f"delegate:{command.target_id}"
            or policy["policy_ref"] not in set(matching[0].input_refs)
        ):
            raise ControlRouteExecutionPolicyError(
                "control policy differs from the immutable TaskGraph"
            )
    return policy


def _normalized_base(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload)
    if set(raw) != _BASE_KEYS:
        raise ControlRouteExecutionPolicyError(
            "control policy payload has missing or unknown fields"
        )
    surface = _text(raw.get("surface"), field_name="surface").lower()
    if surface not in _SUPPORTED_SURFACES:
        raise ControlRouteExecutionPolicyError(
            "control policy surface is unsupported"
        )
    control_command_id = _identifier(
        raw.get("control_command_id"),
        field_name="control_command_id",
    )
    target_id = _identifier(raw.get("target_id"), field_name="target_id")
    expected = {
        "schema_version": CONTROL_ROUTE_EXECUTION_POLICY_SCHEMA_VERSION,
        "transport_route": "delegate",
        "execution_owner": f"task-runtime:{surface}:{target_id}",
        "execution_mode": CONTROL_ROUTE_EXECUTION_MODE,
        "budget_mode": CONTROL_ROUTE_BUDGET_MODE,
        "verification_mode": CONTROL_ROUTE_VERIFICATION_MODE,
        "effect_mode": CONTROL_ROUTE_EFFECT_MODE,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ControlRouteExecutionPolicyError(
            "control policy declares unsupported execution authority"
        )
    review_mode = _text(raw.get("review_mode"), field_name="review_mode")
    if review_mode not in _REVIEW_MODES:
        raise ControlRouteExecutionPolicyError(
            "control policy review mode is unsupported"
        )
    return {
        "schema_version": CONTROL_ROUTE_EXECUTION_POLICY_SCHEMA_VERSION,
        "surface": surface,
        "control_command_id": control_command_id,
        "transport_route": "delegate",
        "target_id": target_id,
        "execution_owner": f"task-runtime:{surface}:{target_id}",
        "execution_mode": CONTROL_ROUTE_EXECUTION_MODE,
        "review_mode": review_mode,
        "budget_mode": CONTROL_ROUTE_BUDGET_MODE,
        "verification_mode": CONTROL_ROUTE_VERIFICATION_MODE,
        "effect_mode": CONTROL_ROUTE_EFFECT_MODE,
    }


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ControlRouteExecutionPolicyError(
            "control policy must be an object"
        )
    return dict(value)


def _text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ControlRouteExecutionPolicyError(f"{field_name} is required")
    return text


def _identifier(value: Any, *, field_name: str) -> str:
    text = _text(value, field_name=field_name)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ControlRouteExecutionPolicyError(
            f"{field_name} is not a canonical identifier"
        )
    return text


__all__ = [
    "CONTROL_ROUTE_BUDGET_MODE",
    "CONTROL_ROUTE_EFFECT_MODE",
    "CONTROL_ROUTE_EXECUTION_MODE",
    "CONTROL_ROUTE_EXECUTION_POLICY_INVALID",
    "CONTROL_ROUTE_EXECUTION_POLICY_KEY",
    "CONTROL_ROUTE_EXECUTION_POLICY_SCHEMA_VERSION",
    "CONTROL_ROUTE_INTENT_REVIEW_REQUIRED",
    "CONTROL_ROUTE_REVIEW_REQUIRED",
    "CONTROL_ROUTE_REVIEW_WAIVER",
    "CONTROL_ROUTE_VERIFICATION_MODE",
    "ControlRouteExecutionPolicyError",
    "freeze_control_route_execution_policy",
    "require_control_route_execution_policy",
    "validate_control_route_execution_policy",
]
