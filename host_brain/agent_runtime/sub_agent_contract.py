"""Pure SubAgent specification and child-WorkOrder request adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    SUB_AGENT_ADAPTER_INVALID,
    SUB_AGENT_LINEAGE_INVALID,
    ContractViolation,
    require_version,
)
from host_brain.agent_runtime.work_order import WorkOrderV3


SUB_AGENT_SPEC_SCHEMA_VERSION = "Natsume.agent.sub_agent_spec.v1"
SUB_AGENT_REQUEST_SCHEMA_VERSION = "Natsume.agent.sub_agent_request.v1"


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an integer") from exc
    if parsed < 1:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be positive")
    return parsed


def validate_planner_target_binding(
    child_work_order: WorkOrderV3,
    *,
    agent_id: str,
    require_binding: bool = False,
) -> None:
    """Reject cross-agent replay of a planner-authorized child WorkOrder."""

    from host_brain.agent_runtime.planner_adapter import (  # noqa: PLC0415
        PLANNER_ROUTE_TYPE_METADATA_KEY,
        PLANNER_TARGET_BINDING_METADATA_KEY,
        PLANNER_TARGET_ID_METADATA_KEY,
        build_planner_target_binding,
    )

    metadata = dict(child_work_order.metadata or {})
    route_type = str(metadata.get(PLANNER_ROUTE_TYPE_METADATA_KEY) or "").strip().lower()
    target_id = str(metadata.get(PLANNER_TARGET_ID_METADATA_KEY) or "").strip()
    binding = str(metadata.get(PLANNER_TARGET_BINDING_METADATA_KEY) or "").strip()
    has_any_binding = bool(route_type or target_id or binding)
    if require_binding and not (route_type and target_id and binding):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "canonical child WorkOrder requires a complete planner target binding",
        )
    if not has_any_binding:
        return
    if not (route_type and target_id and binding):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "planner target binding metadata is incomplete",
        )
    if route_type not in {"delegate", "chat"}:
        raise ContractViolation(
            SUB_AGENT_ADAPTER_INVALID,
            f"planner route {route_type} cannot dispatch a sub-agent",
        )
    if str(agent_id or "").strip() != target_id:
        raise ContractViolation(
            SUB_AGENT_ADAPTER_INVALID,
            f"child WorkOrder is bound to {target_id}, not {agent_id}",
        )
    if target_id not in set(child_work_order.capability_requirements):
        raise ContractViolation(
            SUB_AGENT_ADAPTER_INVALID,
            "child WorkOrder capability requirements do not bind the target agent",
        )
    expected = build_planner_target_binding(
        child_work_order_id=child_work_order.work_order_id,
        root_work_order_id=child_work_order.root_work_order_id,
        parent_work_order_id=str(child_work_order.parent_work_order_id or ""),
        graph_id=str(child_work_order.task_graph_id or ""),
        graph_revision=child_work_order.graph_revision,
        node_id=str(child_work_order.task_node_id or ""),
        route_type=route_type,
        target_id=target_id,
    )
    if binding != expected:
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "planner target binding digest does not match child lineage",
        )


@dataclass(frozen=True, slots=True)
class SubAgentSpec:
    agent_id: str
    capability_requirements: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    timeout_ms: int = 30_000
    max_attempts: int = 1
    context_contract: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SUB_AGENT_SPEC_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SubAgentSpec":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=SUB_AGENT_SPEC_SCHEMA_VERSION,
            contract_name="SubAgentSpec",
        )
        spec = cls(
            agent_id=str(data.get("agent_id") or "").strip(),
            capability_requirements=_text_tuple(data.get("capability_requirements")),
            allowed_actions=_text_tuple(data.get("allowed_actions")),
            timeout_ms=_positive_int(data.get("timeout_ms", 30_000), field_name="timeout_ms"),
            max_attempts=_positive_int(data.get("max_attempts", 1), field_name="max_attempts"),
            context_contract=(
                dict(data.get("context_contract") or {})
                if isinstance(data.get("context_contract"), Mapping)
                else {}
            ),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.schema_version != SUB_AGENT_SPEC_SCHEMA_VERSION:
            raise ContractViolation(SUB_AGENT_ADAPTER_INVALID, "SubAgentSpec schema version is invalid")
        if not self.agent_id:
            raise ContractViolation(SUB_AGENT_ADAPTER_INVALID, "SubAgentSpec.agent_id is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "capability_requirements": list(self.capability_requirements),
            "allowed_actions": list(self.allowed_actions),
            "timeout_ms": self.timeout_ms,
            "max_attempts": self.max_attempts,
            "context_contract": dict(self.context_contract),
        }


@dataclass(frozen=True, slots=True)
class SubAgentRequestContract:
    request_id: str
    session_id: str
    agent_id: str
    child_work_order: WorkOrderV3
    context_refs: tuple[str, ...]
    timeout_ms: int
    schema_version: str = SUB_AGENT_REQUEST_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SubAgentRequestContract":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=SUB_AGENT_REQUEST_SCHEMA_VERSION,
            contract_name="SubAgentRequest",
        )
        child_payload = data.get("child_work_order")
        if not isinstance(child_payload, Mapping):
            raise ContractViolation(
                SUB_AGENT_ADAPTER_INVALID,
                "SubAgentRequest.child_work_order must be an object",
            )
        request = cls(
            request_id=str(data.get("request_id") or "").strip(),
            session_id=str(data.get("session_id") or "").strip(),
            agent_id=str(data.get("agent_id") or "").strip(),
            child_work_order=WorkOrderV3.from_mapping(child_payload),
            context_refs=_text_tuple(data.get("context_refs")),
            timeout_ms=_positive_int(data.get("timeout_ms", 30_000), field_name="timeout_ms"),
        )
        request.validate()
        return request

    @classmethod
    def from_child_work_order(
        cls,
        *,
        child_work_order: WorkOrderV3,
        spec: SubAgentSpec,
        request_id: str,
        session_id: str = "",
        context_refs: tuple[str, ...] | list[str] = (),
    ) -> "SubAgentRequestContract":
        spec.validate()
        child_work_order.validate()
        validate_planner_target_binding(
            child_work_order,
            agent_id=spec.agent_id,
        )
        request = cls(
            request_id=str(request_id or "").strip(),
            session_id=str(session_id or "").strip(),
            agent_id=spec.agent_id,
            child_work_order=child_work_order,
            context_refs=_text_tuple(context_refs),
            timeout_ms=spec.timeout_ms,
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema_version != SUB_AGENT_REQUEST_SCHEMA_VERSION:
            raise ContractViolation(
                SUB_AGENT_ADAPTER_INVALID,
                "SubAgentRequest schema version is invalid",
            )
        if not self.request_id or not self.agent_id:
            raise ContractViolation(
                SUB_AGENT_ADAPTER_INVALID,
                "SubAgentRequest.request_id and agent_id are required",
            )
        child = self.child_work_order
        if (
            not child.parent_work_order_id
            or not child.root_work_order_id
            or not child.task_graph_id
            or not child.task_node_id
        ):
            raise ContractViolation(
                SUB_AGENT_LINEAGE_INVALID,
                "child WorkOrder requires root, parent, graph, and node lineage",
            )
        if child.work_order_id in {child.parent_work_order_id, child.root_work_order_id}:
            raise ContractViolation(
                SUB_AGENT_LINEAGE_INVALID,
                "child work_order_id must differ from parent and root WorkOrder IDs",
            )
        validate_planner_target_binding(child, agent_id=self.agent_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "child_work_order": self.child_work_order.to_dict(),
            "context_refs": list(self.context_refs),
            "timeout_ms": self.timeout_ms,
        }

    def to_legacy_mapping(self) -> dict[str, Any]:
        """Return the existing app-level ``SubAgentRequest`` constructor shape."""

        user_message = str(self.child_work_order.input_pack.get("user_request") or "").strip()
        return {
            "agent_id": self.agent_id,
            "task": self.child_work_order.objective,
            "user_message": user_message,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "context": {
                "child_work_order": self.child_work_order.to_dict(),
                "context_refs": list(self.context_refs),
            },
            "timeout_ms": self.timeout_ms,
        }


class SubAgentRequestAdapter:
    """Named pure adapter kept separate from the active dispatcher."""

    @staticmethod
    def adapt(
        *,
        child_work_order: WorkOrderV3,
        spec: SubAgentSpec,
        request_id: str,
        session_id: str = "",
        context_refs: tuple[str, ...] | list[str] = (),
    ) -> SubAgentRequestContract:
        return SubAgentRequestContract.from_child_work_order(
            child_work_order=child_work_order,
            spec=spec,
            request_id=request_id,
            session_id=session_id,
            context_refs=context_refs,
        )


def adapt_child_work_order_to_sub_agent_request(
    *,
    child_work_order: WorkOrderV3,
    spec: SubAgentSpec,
    request_id: str,
    session_id: str = "",
    context_refs: tuple[str, ...] | list[str] = (),
) -> SubAgentRequestContract:
    return SubAgentRequestAdapter.adapt(
        child_work_order=child_work_order,
        spec=spec,
        request_id=request_id,
        session_id=session_id,
        context_refs=context_refs,
    )


CanonicalSubAgentRequest = SubAgentRequestContract


__all__ = [
    "SUB_AGENT_REQUEST_SCHEMA_VERSION",
    "SUB_AGENT_SPEC_SCHEMA_VERSION",
    "CanonicalSubAgentRequest",
    "SubAgentRequestAdapter",
    "SubAgentRequestContract",
    "SubAgentSpec",
    "adapt_child_work_order_to_sub_agent_request",
    "validate_planner_target_binding",
]
