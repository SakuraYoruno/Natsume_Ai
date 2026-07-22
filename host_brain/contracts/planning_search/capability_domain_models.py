"""Immutable capability-catalog evidence for Host PlanningSearch.

The Host must not treat an upstream ``planned_steps`` list as a search space.
This contract freezes a small, registry-derived capability inventory before
the generic planner starts.  It contains metadata only: no handler, model,
credential, mutable registry, or executable callback can cross this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from host_brain.contracts.planning_search.contracts import VirtualStateSnapshot


CAPABILITY_PLANNING_DOMAIN = "skill"
CAPABILITY_PLANNING_MODE = "CAPABILITY_SEARCH_V1"
CAPABILITY_PLANNING_SCHEMA = "natsume.capability-planning.snapshot.v1"
CAPABILITY_PLANNING_STATE_SCHEMA = "natsume.capability-planning.state.v1"
CAPABILITY_PLANNING_NODE_OUTPUT_SCHEMA = "natsume.capability-planning.node-output.v1"

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class CapabilityPlanningError(ValueError):
    """Raised before a registry-derived capability plan reaches search."""


@dataclass(frozen=True, slots=True)
class PlanningCapability:
    """One registry-proven, non-executable candidate capability."""

    capability_id: str
    family: str
    capability_tags: Sequence[str]
    risk_level: int
    reversible: bool
    parallelizable: bool
    estimated_cost: float

    def __post_init__(self) -> None:
        capability_id = _identifier(self.capability_id, field_name="capability_id")
        family = _identifier(self.family, field_name="family")
        tags = _tags(self.capability_tags, field_name="capability_tags", allow_empty=False)
        if isinstance(self.risk_level, bool) or not isinstance(self.risk_level, int):
            raise CapabilityPlanningError("risk_level must be an integer")
        risk_level = int(self.risk_level)
        if not 1 <= risk_level <= 5:
            raise CapabilityPlanningError("risk_level must be between 1 and 5")
        if not isinstance(self.reversible, bool) or not isinstance(self.parallelizable, bool):
            raise CapabilityPlanningError("reversible and parallelizable must be booleans")
        estimated_cost = _unit_interval(
            self.estimated_cost,
            field_name="estimated_cost",
        )
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "capability_tags", tags)
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "estimated_cost", estimated_cost)

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "family": self.family,
            "capability_tags": list(self.capability_tags),
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "parallelizable": self.parallelizable,
            "estimated_cost": _rounded(self.estimated_cost),
        }


@dataclass(frozen=True, slots=True)
class CapabilityPlanningSnapshot:
    """A redacted, immutable catalog selected from the live SkillRegistry.

    ``task_ref`` is a durable ingress reference.  The task text is retained as
    the governed objective, while skill handlers and descriptions deliberately
    stay outside the snapshot.
    """

    task_ref: str
    objective: str
    requested_tags: Sequence[str]
    max_risk_level: int
    capabilities: Sequence[PlanningCapability]
    source_refs: Sequence[str] = ()

    def __post_init__(self) -> None:
        task_ref = _governed_ref(self.task_ref, field_name="task_ref")
        objective = _required_text(self.objective, field_name="objective", max_length=4_000)
        requested_tags = _tags(
            self.requested_tags,
            field_name="requested_tags",
            allow_empty=False,
        )
        if isinstance(self.max_risk_level, bool) or not isinstance(
            self.max_risk_level,
            int,
        ):
            raise CapabilityPlanningError("max_risk_level must be an integer")
        max_risk_level = int(self.max_risk_level)
        if not 1 <= max_risk_level <= 3:
            raise CapabilityPlanningError(
                "max_risk_level must be between 1 and 3 for capability planning"
            )
        capabilities = tuple(self.capabilities)
        if len(capabilities) < 2 or not all(
            isinstance(item, PlanningCapability) for item in capabilities
        ):
            raise CapabilityPlanningError(
                "capabilities must contain at least two PlanningCapability values"
            )
        ids = tuple(item.capability_id for item in capabilities)
        if len(set(ids)) != len(ids):
            raise CapabilityPlanningError("capabilities contain duplicate ids")
        for capability in capabilities:
            if capability.risk_level > max_risk_level:
                raise CapabilityPlanningError(
                    "capability risk exceeds frozen max_risk_level"
                )
            if not set(capability.capability_tags).intersection(requested_tags):
                raise CapabilityPlanningError(
                    "capability does not match frozen requested_tags"
                )
        source_refs = _governed_refs(
            self.source_refs,
            field_name="source_refs",
            allow_empty=True,
        )
        object.__setattr__(self, "task_ref", task_ref)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "requested_tags", requested_tags)
        object.__setattr__(self, "max_risk_level", max_risk_level)
        object.__setattr__(
            self,
            "capabilities",
            tuple(sorted(capabilities, key=lambda item: item.capability_id)),
        )
        object.__setattr__(self, "source_refs", source_refs)

    @property
    def catalog_digest(self) -> str:
        payload = {
            "schema_version": CAPABILITY_PLANNING_SCHEMA,
            "task_ref": self.task_ref,
            "objective": self.objective,
            "requested_tags": list(self.requested_tags),
            "max_risk_level": self.max_risk_level,
            "capabilities": [item.to_state_dict() for item in self.capabilities],
            "source_refs": list(self.source_refs),
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    @property
    def catalog_ref(self) -> str:
        return "capability-catalog://sha256/" + self.catalog_digest

    def to_virtual_state(self) -> VirtualStateSnapshot:
        return VirtualStateSnapshot(
            schema_version=CAPABILITY_PLANNING_STATE_SCHEMA,
            domain=CAPABILITY_PLANNING_DOMAIN,
            capabilities=tuple(item.capability_id for item in self.capabilities),
            facts={
                "phase": "root",
                "catalog_digest": self.catalog_digest,
                "capabilities": [item.to_state_dict() for item in self.capabilities],
            },
            constraints={
                "mode": CAPABILITY_PLANNING_MODE,
                "requested_tags": list(self.requested_tags),
                "max_risk_level": self.max_risk_level,
                "candidate_count": len(self.capabilities),
            },
            source_refs=(self.task_ref, self.catalog_ref, *self.source_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAPABILITY_PLANNING_SCHEMA,
            "task_ref": self.task_ref,
            "objective": self.objective,
            "requested_tags": list(self.requested_tags),
            "max_risk_level": self.max_risk_level,
            "capabilities": [item.to_state_dict() for item in self.capabilities],
            "source_refs": list(self.source_refs),
            "catalog_digest": self.catalog_digest,
            "catalog_ref": self.catalog_ref,
        }


def _identifier(value: Any, *, field_name: str) -> str:
    normalized = _required_text(value, field_name=field_name, max_length=120).lower()
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise CapabilityPlanningError(f"{field_name} is invalid")
    return normalized


def _tags(value: Any, *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CapabilityPlanningError(f"{field_name} must be an explicit sequence")
    values: list[str] = []
    for index, raw in enumerate(value):
        normalized = _required_text(
            raw,
            field_name=f"{field_name}[{index}]",
            max_length=64,
        ).lower()
        if _TAG_RE.fullmatch(normalized) is None:
            raise CapabilityPlanningError(f"{field_name}[{index}] is invalid")
        if normalized not in values:
            values.append(normalized)
    if not values and not allow_empty:
        raise CapabilityPlanningError(f"{field_name} must not be empty")
    return tuple(sorted(values))


def _governed_refs(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CapabilityPlanningError(f"{field_name} must be an explicit sequence")
    values: list[str] = []
    for index, raw in enumerate(value):
        normalized = _governed_ref(raw, field_name=f"{field_name}[{index}]")
        if normalized not in values:
            values.append(normalized)
    if not values and not allow_empty:
        raise CapabilityPlanningError(f"{field_name} must not be empty")
    return tuple(values)


def _governed_ref(value: Any, *, field_name: str) -> str:
    normalized = _required_text(value, field_name=field_name, max_length=1_024)
    if "://" not in normalized:
        raise CapabilityPlanningError(f"{field_name} must be a governed ref")
    return normalized


def _required_text(value: Any, *, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise CapabilityPlanningError(f"{field_name} is required")
    return normalized


def _unit_interval(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise CapabilityPlanningError(f"{field_name} is invalid")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise CapabilityPlanningError(f"{field_name} is invalid") from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise CapabilityPlanningError(f"{field_name} must be within [0, 1]")
    return normalized


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rounded(value: float) -> float:
    return round(float(value), 10)


__all__ = [
    "CAPABILITY_PLANNING_DOMAIN",
    "CAPABILITY_PLANNING_MODE",
    "CAPABILITY_PLANNING_NODE_OUTPUT_SCHEMA",
    "CAPABILITY_PLANNING_SCHEMA",
    "CAPABILITY_PLANNING_STATE_SCHEMA",
    "CapabilityPlanningError",
    "CapabilityPlanningSnapshot",
    "PlanningCapability",
]
