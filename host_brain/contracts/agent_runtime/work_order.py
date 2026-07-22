"""Legacy WorkOrder v2 and fail-closed canonical WorkOrder v3 contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from host_brain.contracts.agent_runtime.budget_policy import BudgetPolicy
from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    CONTRACT_VERSION_UNSUPPORTED,
    ContractViolation,
)


WORK_ORDER_V2_SCHEMA_VERSION = "Natsume.agent.work_order.v2"
WORK_ORDER_V3_SCHEMA_VERSION = "Natsume.agent.work_order.v3"

_WORK_ORDER_V2_VERSION_ALIASES = frozenset(
    {
        "2",
        "v2",
        "work_order.v2",
        "natsume.agent.work_order.v2",
    }
)

_WORK_ORDER_V3_METADATA_KEY = "_work_order_v3_metadata"
_WORK_ORDER_V3_ADAPTER_FIELDS = frozenset(
    {
        "root_work_order_id",
        "parent_work_order_id",
        "task_graph_id",
        "task_node_id",
        "graph_revision",
        "attempt",
        "capability_requirements",
        "risk_level",
        "input_refs",
        "output_contract",
        "budget_policy",
        "business_idempotency_scope",
        "operation_fingerprint",
        "status_version",
        "approval_ref",
        "review_ref",
    }
)


ALLOWED_AGENT_DOMAINS = {
    "chat",
    "code",
    "finance",
    "translation",
    "creative",
    "skill",
    "tool",
    "immune",
    "evolution",
    "organ_ops",
    "embodiment",
    "memory",
    "bio_ops",
    "research",
}

ALLOWED_AGENT_RUN_MODES = {
    "inspect",
    "worker",
    "self_heal",
    "self_evolve",
    "research",
    "translate",
    "audit",
}

WRITE_RUN_MODES = {"worker", "self_heal", "self_evolve"}
WRITE_AGENT_ACTIONS = {"write_files", "agent_write_file"}


def _list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(slots=True)
class AgentWorkOrder:
    """The public v2 contract; intentionally unchanged for compatibility."""

    work_order_id: str
    domain: str
    run_mode: str
    objective: str
    workspace: dict[str, Any] = field(default_factory=dict)
    input_pack: dict[str, Any] = field(default_factory=dict)
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_plan: dict[str, Any] = field(default_factory=dict)
    approval_policy: dict[str, Any] = field(default_factory=dict)
    review_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AgentWorkOrder":
        data = dict(payload or {})
        objective = str(
            data.get("objective")
            or _dict_value(data.get("input_pack")).get("user_request")
            or data.get("task")
            or ""
        ).strip()
        return cls(
            work_order_id=str(data.get("work_order_id") or f"wo_{uuid4().hex}").strip(),
            domain=str(data.get("domain") or "code").strip().lower(),
            run_mode=str(data.get("run_mode") or "inspect").strip().lower(),
            objective=objective,
            workspace=_dict_value(data.get("workspace")),
            input_pack=_dict_value(data.get("input_pack")),
            allowed_actions=_list_of_text(data.get("allowed_actions")),
            forbidden_actions=_list_of_text(data.get("forbidden_actions")),
            acceptance_criteria=_list_of_text(data.get("acceptance_criteria")),
            verification_plan=_dict_value(data.get("verification_plan")),
            approval_policy=_dict_value(data.get("approval_policy")),
            review_policy=_dict_value(data.get("review_policy")),
            metadata=_dict_value(data.get("metadata")),
        )

    def validate(self) -> None:
        if not self.work_order_id:
            raise ValueError("work_order_id is required")
        if self.domain not in ALLOWED_AGENT_DOMAINS:
            raise ValueError(f"unsupported agent domain: {self.domain}")
        if self.run_mode not in ALLOWED_AGENT_RUN_MODES:
            raise ValueError(f"unsupported agent run_mode: {self.run_mode}")
        if not self.objective:
            raise ValueError("objective is required")
        if not isinstance(self.workspace, dict):
            raise ValueError("workspace must be an object")

    @property
    def requires_write(self) -> bool:
        actions = {str(action).strip().lower() for action in self.allowed_actions}
        return self.run_mode in WRITE_RUN_MODES or bool(actions & WRITE_AGENT_ACTIONS)

    @property
    def review_required(self) -> bool:
        if "review_required" in self.review_policy:
            return bool(self.review_policy.get("review_required"))
        return bool(self.requires_write)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tuple_of_text(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _non_negative_int(value: Any, *, field_name: str, default: int = 0) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be non-negative")
    return parsed


@dataclass(slots=True)
class WorkOrderV3:
    """Canonical WorkOrder v3.

    ``from_mapping`` is the only version-dispatching parser. Unversioned payloads
    are treated as the existing v2 contract, while every explicit unknown
    version is rejected. The legacy ``AgentWorkOrder`` remains the v2 public API.
    """

    work_order_id: str
    domain: str
    run_mode: str
    objective: str
    workspace: dict[str, Any] = field(default_factory=dict)
    input_pack: dict[str, Any] = field(default_factory=dict)
    allowed_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    verification_plan: dict[str, Any] = field(default_factory=dict)
    approval_policy: dict[str, Any] = field(default_factory=dict)
    review_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    root_work_order_id: str = ""
    parent_work_order_id: str | None = None
    task_graph_id: str | None = None
    task_node_id: str | None = None
    graph_revision: int = 1
    attempt: int = 0
    capability_requirements: tuple[str, ...] = ()
    risk_level: int = 1
    input_refs: tuple[str, ...] = ()
    output_contract: dict[str, Any] = field(default_factory=dict)
    budget_policy: BudgetPolicy | None = None
    business_idempotency_scope: str = ""
    operation_fingerprint: str = ""
    status_version: int = 0
    approval_ref: str | None = None
    review_ref: str | None = None
    schema_version: str = WORK_ORDER_V3_SCHEMA_VERSION
    _legacy_v2_payload: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WorkOrderV3":
        data = dict(payload or {})
        raw_version = str(data.get("schema_version") or "").strip()
        normalized_version = raw_version.lower()
        if not raw_version or normalized_version in _WORK_ORDER_V2_VERSION_ALIASES:
            return cls.from_v2_mapping(data)
        if raw_version != WORK_ORDER_V3_SCHEMA_VERSION:
            raise ContractViolation(
                CONTRACT_VERSION_UNSUPPORTED,
                f"unsupported WorkOrder.schema_version: {raw_version}",
            )

        budget_payload = data.get("budget_policy")
        budget_policy = (
            BudgetPolicy.from_mapping(budget_payload)
            if isinstance(budget_payload, Mapping)
            else None
        )
        order = cls(
            work_order_id=str(data.get("work_order_id") or "").strip(),
            domain=str(data.get("domain") or "").strip().lower(),
            run_mode=str(data.get("run_mode") or "").strip().lower(),
            objective=str(data.get("objective") or "").strip(),
            workspace=_dict_value(data.get("workspace")),
            input_pack=_dict_value(data.get("input_pack")),
            allowed_actions=_tuple_of_text(data.get("allowed_actions")),
            forbidden_actions=_tuple_of_text(data.get("forbidden_actions")),
            acceptance_criteria=_tuple_of_text(data.get("acceptance_criteria")),
            verification_plan=_dict_value(data.get("verification_plan")),
            approval_policy=_dict_value(data.get("approval_policy")),
            review_policy=_dict_value(data.get("review_policy")),
            metadata=_dict_value(data.get("metadata")),
            root_work_order_id=str(data.get("root_work_order_id") or "").strip(),
            parent_work_order_id=_optional_text(data.get("parent_work_order_id")),
            task_graph_id=_optional_text(data.get("task_graph_id")),
            task_node_id=_optional_text(data.get("task_node_id")),
            graph_revision=_non_negative_int(
                data.get("graph_revision"),
                field_name="graph_revision",
                default=1,
            ),
            attempt=_non_negative_int(data.get("attempt"), field_name="attempt"),
            capability_requirements=_tuple_of_text(data.get("capability_requirements")),
            risk_level=_non_negative_int(data.get("risk_level"), field_name="risk_level", default=1),
            input_refs=_tuple_of_text(data.get("input_refs")),
            output_contract=_dict_value(data.get("output_contract")),
            budget_policy=budget_policy,
            business_idempotency_scope=str(data.get("business_idempotency_scope") or "").strip(),
            operation_fingerprint=str(data.get("operation_fingerprint") or "").strip(),
            status_version=_non_negative_int(data.get("status_version"), field_name="status_version"),
            approval_ref=_optional_text(data.get("approval_ref")),
            review_ref=_optional_text(data.get("review_ref")),
        )
        order.validate()
        return order

    @classmethod
    def from_v2_mapping(cls, payload: Mapping[str, Any]) -> "WorkOrderV3":
        data = dict(payload or {})
        raw_version = str(data.get("schema_version") or "").strip()
        if raw_version and raw_version.lower() not in _WORK_ORDER_V2_VERSION_ALIASES:
            raise ContractViolation(
                CONTRACT_VERSION_UNSUPPORTED,
                f"unsupported WorkOrder v2 schema_version: {raw_version}",
            )
        legacy = AgentWorkOrder.from_mapping(data)
        legacy.validate()
        metadata = dict(legacy.metadata)
        original_metadata_payload = metadata.get(_WORK_ORDER_V3_METADATA_KEY)
        canonical_metadata = (
            dict(original_metadata_payload)
            if isinstance(original_metadata_payload, Mapping)
            else {
                key: deepcopy(value)
                for key, value in metadata.items()
                if key not in _WORK_ORDER_V3_ADAPTER_FIELDS
                and key != _WORK_ORDER_V3_METADATA_KEY
            }
        )
        graph_revision = _non_negative_int(
            metadata.get("graph_revision"),
            field_name="graph_revision",
            default=1,
        )
        risk_level = _non_negative_int(
            metadata.get("risk_level"),
            field_name="risk_level",
            default=1,
        )
        budget_payload = metadata.get("budget_policy")
        budget_policy = (
            BudgetPolicy.from_mapping(budget_payload)
            if isinstance(budget_payload, Mapping)
            else None
        )
        order = cls(
            work_order_id=legacy.work_order_id,
            domain=legacy.domain,
            run_mode=legacy.run_mode,
            objective=legacy.objective,
            workspace=dict(legacy.workspace),
            input_pack=dict(legacy.input_pack),
            allowed_actions=tuple(legacy.allowed_actions),
            forbidden_actions=tuple(legacy.forbidden_actions),
            acceptance_criteria=tuple(legacy.acceptance_criteria),
            verification_plan=dict(legacy.verification_plan),
            approval_policy=dict(legacy.approval_policy),
            review_policy=dict(legacy.review_policy),
            metadata=canonical_metadata,
            root_work_order_id=str(metadata.get("root_work_order_id") or legacy.work_order_id).strip(),
            parent_work_order_id=_optional_text(metadata.get("parent_work_order_id")),
            task_graph_id=_optional_text(metadata.get("task_graph_id")),
            task_node_id=_optional_text(metadata.get("task_node_id")),
            graph_revision=graph_revision,
            attempt=_non_negative_int(metadata.get("attempt"), field_name="attempt"),
            capability_requirements=_tuple_of_text(metadata.get("capability_requirements")),
            risk_level=risk_level,
            input_refs=_tuple_of_text(metadata.get("input_refs")),
            output_contract=_dict_value(metadata.get("output_contract")),
            budget_policy=budget_policy,
            business_idempotency_scope=str(metadata.get("business_idempotency_scope") or "").strip(),
            operation_fingerprint=str(metadata.get("operation_fingerprint") or "").strip(),
            status_version=_non_negative_int(metadata.get("status_version"), field_name="status_version"),
            approval_ref=_optional_text(metadata.get("approval_ref")),
            review_ref=_optional_text(metadata.get("review_ref")),
            _legacy_v2_payload=deepcopy(data),
        )
        order.validate()
        return order

    def validate(self) -> None:
        if self.schema_version != WORK_ORDER_V3_SCHEMA_VERSION:
            raise ContractViolation(CONTRACT_VERSION_UNSUPPORTED, "WorkOrder v3 schema version is invalid")
        AgentWorkOrder(
            work_order_id=self.work_order_id,
            domain=self.domain,
            run_mode=self.run_mode,
            objective=self.objective,
            workspace=dict(self.workspace),
        ).validate()
        if not self.root_work_order_id:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "root_work_order_id is required")
        if self.graph_revision < 1:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "graph_revision must be at least 1")
        if self.risk_level < 1 or self.risk_level > 5:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "risk_level must be between 1 and 5")
        if bool(self.task_graph_id) != bool(self.task_node_id):
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                "task_graph_id and task_node_id must be set together",
            )

    @property
    def requires_write(self) -> bool:
        actions = {str(action).strip().lower() for action in self.allowed_actions}
        return self.run_mode in WRITE_RUN_MODES or bool(actions & WRITE_AGENT_ACTIONS)

    @property
    def review_required(self) -> bool:
        if "review_required" in self.review_policy:
            return bool(self.review_policy.get("review_required"))
        return bool(self.requires_write)

    @property
    def is_child(self) -> bool:
        return bool(self.parent_work_order_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "work_order_id": self.work_order_id,
            "domain": self.domain,
            "run_mode": self.run_mode,
            "objective": self.objective,
            "workspace": deepcopy(self.workspace),
            "input_pack": deepcopy(self.input_pack),
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_plan": deepcopy(self.verification_plan),
            "approval_policy": deepcopy(self.approval_policy),
            "review_policy": deepcopy(self.review_policy),
            "metadata": deepcopy(self.metadata),
            "root_work_order_id": self.root_work_order_id,
            "parent_work_order_id": self.parent_work_order_id,
            "task_graph_id": self.task_graph_id,
            "task_node_id": self.task_node_id,
            "graph_revision": self.graph_revision,
            "attempt": self.attempt,
            "capability_requirements": list(self.capability_requirements),
            "risk_level": self.risk_level,
            "input_refs": list(self.input_refs),
            "output_contract": deepcopy(self.output_contract),
            "budget_policy": self.budget_policy.to_dict() if self.budget_policy is not None else None,
            "business_idempotency_scope": self.business_idempotency_scope,
            "operation_fingerprint": self.operation_fingerprint,
            "status_version": self.status_version,
            "approval_ref": self.approval_ref,
            "review_ref": self.review_ref,
        }

    def to_v2_mapping(self) -> dict[str, Any]:
        """Project to the v2 payload without mutating the canonical contract."""

        if self._legacy_v2_payload is not None:
            return deepcopy(self._legacy_v2_payload)
        metadata = deepcopy(self.metadata)
        metadata.update(
            {
                _WORK_ORDER_V3_METADATA_KEY: deepcopy(self.metadata),
                "root_work_order_id": self.root_work_order_id,
                "parent_work_order_id": self.parent_work_order_id,
                "task_graph_id": self.task_graph_id,
                "task_node_id": self.task_node_id,
                "graph_revision": self.graph_revision,
                "attempt": self.attempt,
                "capability_requirements": list(self.capability_requirements),
                "risk_level": self.risk_level,
                "input_refs": list(self.input_refs),
                "output_contract": deepcopy(self.output_contract),
                "budget_policy": (
                    self.budget_policy.to_dict() if self.budget_policy is not None else None
                ),
                "business_idempotency_scope": self.business_idempotency_scope,
                "operation_fingerprint": self.operation_fingerprint,
                "status_version": self.status_version,
                "approval_ref": self.approval_ref,
                "review_ref": self.review_ref,
            }
        )
        return AgentWorkOrder(
            work_order_id=self.work_order_id,
            domain=self.domain,
            run_mode=self.run_mode,
            objective=self.objective,
            workspace=deepcopy(self.workspace),
            input_pack=deepcopy(self.input_pack),
            allowed_actions=list(self.allowed_actions),
            forbidden_actions=list(self.forbidden_actions),
            acceptance_criteria=list(self.acceptance_criteria),
            verification_plan=deepcopy(self.verification_plan),
            approval_policy=deepcopy(self.approval_policy),
            review_policy=deepcopy(self.review_policy),
            metadata=metadata,
        ).to_dict()

    def to_agent_work_order(self) -> AgentWorkOrder:
        return AgentWorkOrder.from_mapping(self.to_v2_mapping())


def _validate_legacy_graph_metadata(order: WorkOrderV3) -> None:
    """Reject a v3 order whose legacy metadata disagrees with its fields.

    ``metadata`` is intentionally extensible, but the graph identity fields
    stopped being metadata once WorkOrder v3 became canonical.  Accepting both
    representations with different values would let a downstream compatibility
    adapter choose a different graph epoch from the one admitted by the Host.
    Empty legacy values remain harmless so a caller can omit them while moving
    to v3.
    """

    metadata = dict(order.metadata or {})
    expected_text = {
        "root_work_order_id": order.root_work_order_id,
        "task_graph_id": order.task_graph_id or "",
        "task_node_id": order.task_node_id or "",
    }
    for field_name, expected in expected_text.items():
        if field_name not in metadata or metadata[field_name] in (None, ""):
            continue
        actual = str(metadata[field_name]).strip()
        if actual != expected:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"metadata.{field_name} conflicts with canonical WorkOrder v3 field",
            )

    if "graph_revision" not in metadata or metadata["graph_revision"] in (None, ""):
        return
    raw_revision = metadata["graph_revision"]
    if isinstance(raw_revision, bool):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "metadata.graph_revision conflicts with canonical WorkOrder v3 field",
        )
    try:
        legacy_revision = int(raw_revision)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "metadata.graph_revision conflicts with canonical WorkOrder v3 field",
        ) from exc
    if legacy_revision != order.graph_revision:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "metadata.graph_revision conflicts with canonical WorkOrder v3 field",
        )


def canonicalize_work_order(
    value: AgentWorkOrder | WorkOrderV3 | Mapping[str, Any],
) -> WorkOrderV3:
    """Return the one WorkOrder representation used after runtime admission.

    The public v2 payload remains an ingress compatibility format.  Once the
    runtime begins execution it is normalized to v3 and must never be projected
    back to v2 just because an older worker helper is still present.
    """

    if isinstance(value, WorkOrderV3):
        order = value
    elif isinstance(value, AgentWorkOrder):
        order = WorkOrderV3.from_mapping(value.to_dict())
    elif isinstance(value, Mapping):
        order = WorkOrderV3.from_mapping(value)
    else:
        raise TypeError("WorkOrder must be AgentWorkOrder, WorkOrderV3, or a mapping")
    order.validate()
    _validate_legacy_graph_metadata(order)
    return order


WorkOrder = WorkOrderV3


def build_work_order_id(prefix: str = "wo") -> str:
    return f"{prefix}_{uuid4().hex}"


__all__ = [
    "ALLOWED_AGENT_DOMAINS",
    "ALLOWED_AGENT_RUN_MODES",
    "AgentWorkOrder",
    "WORK_ORDER_V2_SCHEMA_VERSION",
    "WORK_ORDER_V3_SCHEMA_VERSION",
    "WRITE_RUN_MODES",
    "WorkOrder",
    "WorkOrderV3",
    "build_work_order_id",
    "canonicalize_work_order",
]
