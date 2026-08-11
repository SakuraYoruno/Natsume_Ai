"""Typed, authority-free worker budget measurements.

Workers may report only observations produced by the already-admitted
execution.  The coordinator remains authoritative for wall-clock time and for
the durable charge.  Missing measurements stay missing: this module never
estimates tokens or model cost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount


WORKER_BUDGET_USAGE_SCHEMA_VERSION = "Natsume.task_runtime.worker_budget_usage.v1"
MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION = "Natsume.model_runtime.budget_measurement.v1"

_DURABLE_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MEASUREMENT_FIELDS = frozenset(
    {"token_count", "tool_call_count", "retry_count", "model_cost_micros"}
)
_MODEL_FIELDS = frozenset(
    {
        "schema_version",
        "measurement_ref",
        "attempt_count",
        "retry_count",
        "token_count",
        "model_cost_micros",
    }
)
_MAX_SCAN_ITEMS = 1_024
_MAX_SCAN_DEPTH = 12


class WorkerBudgetUsageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerBudgetUsage:
    """Measurements a worker can prove without owning budget authority."""

    usage_id: str
    measurements: Mapping[str, int]
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = WORKER_BUDGET_USAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORKER_BUDGET_USAGE_SCHEMA_VERSION:
            raise WorkerBudgetUsageError("worker budget usage schema version is invalid")
        usage_id = str(self.usage_id or "").strip()
        if not _DURABLE_REF.fullmatch(usage_id):
            raise WorkerBudgetUsageError("worker budget usage_id must be a durable reference")
        raw = dict(self.measurements or {})
        if not raw or not set(raw).issubset(_MEASUREMENT_FIELDS):
            raise WorkerBudgetUsageError(
                "worker budget measurements must contain one or more supported dimensions"
            )
        normalized: dict[str, int] = {}
        for key, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkerBudgetUsageError(
                    f"worker budget measurement {key} must be a non-negative integer"
                )
            normalized[str(key)] = int(value)
        refs = tuple(dict.fromkeys(str(item or "").strip() for item in self.evidence_refs))
        if any(not _DURABLE_REF.fullmatch(item) for item in refs):
            raise WorkerBudgetUsageError(
                "worker budget evidence_refs must be durable references"
            )
        object.__setattr__(self, "usage_id", usage_id)
        object.__setattr__(self, "measurements", normalized)
        object.__setattr__(self, "evidence_refs", refs)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkerBudgetUsage":
        data = dict(payload or {})
        expected = {"schema_version", "usage_id", "measurements", "evidence_refs"}
        if set(data) != expected or not isinstance(data.get("measurements"), Mapping):
            raise WorkerBudgetUsageError("worker budget usage payload shape is invalid")
        refs = data.get("evidence_refs")
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
            raise WorkerBudgetUsageError("worker budget evidence_refs must be a sequence")
        return cls(
            schema_version=str(data.get("schema_version") or ""),
            usage_id=str(data.get("usage_id") or ""),
            measurements={str(key): value for key, value in dict(data["measurements"]).items()},
            evidence_refs=tuple(str(item or "") for item in refs),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "usage_id": self.usage_id,
            "measurements": dict(self.measurements),
            "evidence_refs": list(self.evidence_refs),
        }

    def to_budget_amount(self, *, elapsed_ms: int) -> BudgetAmount:
        values = dict(self.measurements)
        return BudgetAmount(
            token_count=values.get("token_count", 0),
            tool_call_count=values.get("tool_call_count", 0),
            time_ms=max(0, int(elapsed_ms)),
            retry_count=values.get("retry_count", 0),
            model_cost_micros=values.get("model_cost_micros", 0),
        )

    def measured_dimensions(self) -> tuple[str, ...]:
        return tuple(sorted({"time_ms", *self.measurements}))


def stable_worker_usage_id(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    route: str,
) -> str:
    material = "\n".join(
        (
            str(graph_id or "").strip(),
            str(int(graph_revision)),
            str(node_id or "").strip(),
            str(attempt_id or "").strip(),
            str(route or "").strip().lower(),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"worker-budget://{digest}"


def build_worker_budget_usage(
    *,
    usage_id: str,
    observations: Any,
    include_agent_tool_calls: bool = False,
    evidence_refs: Sequence[str] = (),
) -> WorkerBudgetUsage | None:
    """Aggregate only explicit canonical model/agent measurements.

    Token and cost totals are emitted only when every discovered model
    measurement reports that dimension.  Retry counts are owned by the model
    attempt service and are therefore always exact for discovered traces.
    """

    model_rows = _model_measurements(observations)
    measurements: dict[str, int] = {}
    refs = list(evidence_refs)
    if model_rows:
        measurements["retry_count"] = sum(row["retry_count"] for row in model_rows)
        if all("token_count" in row for row in model_rows):
            measurements["token_count"] = sum(row["token_count"] for row in model_rows)
        if all("model_cost_micros" in row for row in model_rows):
            measurements["model_cost_micros"] = sum(
                row["model_cost_micros"] for row in model_rows
            )
        refs.extend(row["measurement_ref"] for row in model_rows)
    if include_agent_tool_calls:
        tool_count = _agent_tool_call_count(observations)
        if tool_count is not None:
            measurements["tool_call_count"] = tool_count
    if not measurements:
        return None
    return WorkerBudgetUsage(
        usage_id=usage_id,
        measurements=measurements,
        evidence_refs=tuple(refs),
    )


def _model_measurements(value: Any) -> tuple[dict[str, int | str], ...]:
    rows: dict[str, dict[str, int | str]] = {}
    for item in _walk(value):
        if not isinstance(item, Mapping):
            continue
        data = dict(item)
        if data.get("schema_version") != MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION:
            continue
        if not set(data).issubset(_MODEL_FIELDS):
            raise WorkerBudgetUsageError("model budget measurement contains unknown fields")
        ref = str(data.get("measurement_ref") or "").strip()
        if not _DURABLE_REF.fullmatch(ref):
            raise WorkerBudgetUsageError("model budget measurement_ref is invalid")
        required = ("attempt_count", "retry_count")
        normalized: dict[str, int | str] = {"measurement_ref": ref}
        for key in required:
            normalized[key] = _measurement_int(data, key, required=True)
        for key in ("token_count", "model_cost_micros"):
            if key in data:
                normalized[key] = _measurement_int(data, key, required=False)
        previous = rows.get(ref)
        if previous is not None and previous != normalized:
            raise WorkerBudgetUsageError("model budget measurement replay drift")
        rows[ref] = normalized
    return tuple(rows[key] for key in sorted(rows))


def _measurement_int(data: Mapping[str, Any], key: str, *, required: bool) -> int:
    if required and key not in data:
        raise WorkerBudgetUsageError(f"model budget measurement {key} is required")
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkerBudgetUsageError(
            f"model budget measurement {key} must be a non-negative integer"
        )
    return int(value)


def _agent_tool_call_count(value: Any) -> int | None:
    traces: dict[str, int] = {}
    for item in _walk(value):
        if not isinstance(item, Mapping) or item.get("schema_version") != "nle.trace.redacted.v1":
            continue
        payload_hash = str(item.get("payload_hash") or "").strip()
        steps = item.get("steps")
        if not payload_hash or not isinstance(steps, Sequence) or isinstance(
            steps, (str, bytes, bytearray)
        ):
            continue
        count = sum(
            1
            for step in steps
            if isinstance(step, Mapping)
            and str(step.get("action_type") or "").strip().lower() == "tool_call"
            and str(step.get("tool_id") or "").strip().lower() not in {"", "agent_finish"}
        )
        previous = traces.get(payload_hash)
        if previous is not None and previous != count:
            raise WorkerBudgetUsageError("agent trace replay drift")
        traces[payload_hash] = count
    return sum(traces.values()) if traces else None


def _walk(value: Any):
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if visited > _MAX_SCAN_ITEMS:
            raise WorkerBudgetUsageError("budget measurement observation is too large")
        yield item
        if depth >= _MAX_SCAN_DEPTH:
            continue
        if isinstance(item, Mapping):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            stack.extend((child, depth + 1) for child in item)


__all__ = [
    "MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION",
    "WORKER_BUDGET_USAGE_SCHEMA_VERSION",
    "WorkerBudgetUsage",
    "WorkerBudgetUsageError",
    "build_worker_budget_usage",
    "stable_worker_usage_id",
]
