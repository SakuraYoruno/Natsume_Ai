"""Contracts for proposal-only Bio-Ops mechanisms."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from host_brain.core.ids import now_ms


BIO_OPS_RESULT_STATUSES = {"PROPOSED", "AUDITED", "CANDIDATE", "BLOCKED", "DEGRADED", "FAILED"}
BIO_OPS_MODES = {"audit", "proposal", "candidate", "recommendation", "report", "reflex", "policy"}


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def build_bio_ops_task_id(prefix: str = "bio") -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(slots=True)
class BioOpsArtifact:
    artifact_id: str
    artifact_type: str
    path: str
    digest: str
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsTask:
    task_id: str
    capability: str
    mode: str = "audit"
    owner_organ: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    work_order_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BioOpsTask":
        data = dict(payload or {})
        return cls(
            task_id=str(data.get("task_id") or build_bio_ops_task_id()).strip(),
            capability=str(data.get("capability") or "").strip().lower(),
            mode=str(data.get("mode") or "audit").strip().lower(),
            owner_organ=str(data.get("owner_organ") or "").strip() or None,
            inputs=_dict_value(data.get("inputs")),
            scope=_dict_value(data.get("scope")),
            work_order_ref=str(data.get("work_order_ref") or "").strip() or None,
            metadata=_dict_value(data.get("metadata")),
        )

    def validate(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.capability:
            raise ValueError("capability is required")
        if self.mode not in BIO_OPS_MODES:
            raise ValueError(f"unsupported BioOps mode: {self.mode}")
        if not isinstance(self.inputs, dict):
            raise ValueError("inputs must be an object")
        if not isinstance(self.scope, dict):
            raise ValueError("scope must be an object")

    @property
    def target_paths(self) -> list[str]:
        paths: list[str] = []
        paths.extend(_list_of_text(self.scope.get("target_paths")))
        paths.extend(_list_of_text(self.inputs.get("target_paths")))
        candidate = self.inputs.get("target_path") or self.scope.get("target_path")
        if candidate:
            paths.append(str(candidate))
        return paths

    @property
    def is_write_candidate(self) -> bool:
        actions = set(_list_of_text(self.inputs.get("proposed_actions")))
        actions.update(_list_of_text(self.scope.get("proposed_actions")))
        return bool(
            self.mode == "candidate"
            or self.inputs.get("write_candidate")
            or self.inputs.get("proposes_long_term_write")
            or "write_memory_candidate" in actions
            or "write_files" in actions
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsResult:
    status: str
    task_id: str
    capability: str
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    review_required: bool = False
    next_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in BIO_OPS_RESULT_STATUSES:
            raise ValueError(f"unsupported BioOps result status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsSchedule:
    schedule_id: str
    capability: str
    cadence: str
    enabled: bool = True
    last_run_ms: int | None = None
    next_run_ms: int | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BioOpsSchedule":
        data = dict(payload or {})
        return cls(
            schedule_id=str(data.get("schedule_id") or build_bio_ops_task_id("schedule")).strip(),
            capability=str(data.get("capability") or "").strip().lower(),
            cadence=str(data.get("cadence") or "manual").strip().lower(),
            enabled=bool(data.get("enabled", True)),
            last_run_ms=int(data["last_run_ms"]) if data.get("last_run_ms") is not None else None,
            next_run_ms=int(data["next_run_ms"]) if data.get("next_run_ms") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def blocked_bio_ops_result(
    *,
    task: BioOpsTask,
    summary: str,
    reason_code: str,
    detail: str | None = None,
    risk_flags: list[str] | None = None,
) -> BioOpsResult:
    return BioOpsResult(
        status="BLOCKED",
        task_id=task.task_id,
        capability=task.capability,
        summary=summary,
        risk_flags=list(risk_flags or ["bio_ops_policy_block"]),
        review_required=True,
        reason_code=reason_code,
        detail=detail,
    )


__all__ = [
    "BIO_OPS_MODES",
    "BIO_OPS_RESULT_STATUSES",
    "BioOpsArtifact",
    "BioOpsResult",
    "BioOpsSchedule",
    "BioOpsTask",
    "blocked_bio_ops_result",
    "build_bio_ops_task_id",
]
