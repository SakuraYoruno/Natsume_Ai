"""WorkOrder contract for Codex runtime v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from uuid import uuid4


ALLOWED_CODEX_DOMAINS = {
    "code",
    "finance",
    "translation",
    "creative",
    "skill",
    "immune",
    "evolution",
    "organ_ops",
    "embodiment",
    "memory",
    "bio_ops",
    "research",
}

ALLOWED_CODEX_RUN_MODES = {
    "inspect",
    "worker",
    "self_heal",
    "self_evolve",
    "research",
    "translate",
    "audit",
}

WRITE_RUN_MODES = {"worker", "self_heal", "self_evolve"}


def _list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(slots=True)
class CodexWorkOrder:
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
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CodexWorkOrder":
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
        if self.domain not in ALLOWED_CODEX_DOMAINS:
            raise ValueError(f"unsupported Codex domain: {self.domain}")
        if self.run_mode not in ALLOWED_CODEX_RUN_MODES:
            raise ValueError(f"unsupported Codex run_mode: {self.run_mode}")
        if not self.objective:
            raise ValueError("objective is required")
        if not isinstance(self.workspace, dict):
            raise ValueError("workspace must be an object")

    @property
    def requires_write(self) -> bool:
        return self.run_mode in WRITE_RUN_MODES or "write_files" in set(self.allowed_actions)

    @property
    def review_required(self) -> bool:
        if "review_required" in self.review_policy:
            return bool(self.review_policy.get("review_required"))
        return bool(self.requires_write)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_work_order_id(prefix: str = "wo") -> str:
    return f"{prefix}_{uuid4().hex}"


__all__ = [
    "ALLOWED_CODEX_DOMAINS",
    "ALLOWED_CODEX_RUN_MODES",
    "CodexWorkOrder",
    "WRITE_RUN_MODES",
    "build_work_order_id",
]
