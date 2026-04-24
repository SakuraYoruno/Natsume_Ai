"""Structured result contract for Codex runtime v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


CODEX_RESULT_STATUSES = {"EXECUTED", "FAILED", "DEGRADED", "NEEDS_INPUT", "BLOCKED"}


@dataclass(slots=True)
class CodexRuntimeResult:
    status: str
    work_order_id: str
    summary: str
    result_text: str = ""
    changed_files: list[str] = field(default_factory=list)
    git_diff_stat: str | None = None
    artifacts_dir: str = ""
    thread_id: str | None = None
    verification: dict[str, Any] = field(default_factory=lambda: {"status": "NOT_RUN"})
    risk_flags: list[str] = field(default_factory=list)
    review_required: bool = True
    next_actions: list[str] = field(default_factory=list)
    rollback_manifest_ref: str | None = None
    detail: str | None = None
    reason_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in CODEX_RESULT_STATUSES:
            raise ValueError(f"unsupported Codex result status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def blocked_result(
    *,
    work_order_id: str,
    summary: str,
    artifacts_dir: str = "",
    reason_code: str = "CODEX_WORKORDER_BLOCKED",
    detail: str | None = None,
    review_required: bool = True,
    risk_flags: list[str] | None = None,
) -> CodexRuntimeResult:
    return CodexRuntimeResult(
        status="BLOCKED",
        work_order_id=work_order_id,
        summary=summary,
        artifacts_dir=artifacts_dir,
        reason_code=reason_code,
        detail=detail,
        review_required=review_required,
        risk_flags=list(risk_flags or []),
    )


__all__ = ["CODEX_RESULT_STATUSES", "CodexRuntimeResult", "blocked_result"]
