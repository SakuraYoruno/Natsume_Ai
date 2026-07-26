"""Pure identities for one atomic terminal Review settlement."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from host_brain.persistence.task_store_types import (
    TASK_STORE_INVALID,
    TaskStoreError,
)


REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION = (
    "Natsume.task_runtime.review_denial_settlement.v1"
)
REVIEW_DENIAL_REQUIRED_COLUMNS = frozenset(
    {
        "settlement_ref",
        "schema_version",
        "review_id",
        "review_status",
        "review_status_version",
        "review_sha256",
        "resume_phase",
        "graph_id",
        "graph_revision",
        "node_id",
        "node_version_before",
        "node_version_after",
        "node_target_status",
        "graph_status_before",
        "graph_version_before",
        "graph_version_after",
        "graph_target_status",
        "budget_id",
        "budget_version_before",
        "budget_version_after",
        "acceptance_ref",
        "attempt_id",
        "blocking_decision_ref",
        "node_outbox_id",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)
REVIEW_DENIAL_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_runtime_review_denial_no_update",
        "trg_task_runtime_review_denial_no_delete",
        "trg_agent_runtime_settled_review_no_update",
        "trg_agent_runtime_settled_review_no_delete",
    }
)
TERMINAL_REVIEW_STATUSES = frozenset({"REJECTED", "EXPIRED", "CANCELLED"})


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_review_denial_settlement_ref(
    *,
    review_id: str,
    review_status: str,
    review_status_version: int,
    resume_phase: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    node_version_before: int,
    node_target_status: str,
    graph_status_before: str,
    graph_version_before: int,
    graph_target_status: str,
    budget_id: str,
    budget_version_before: int,
    acceptance_ref: str | None,
    attempt_id: str | None,
) -> str:
    """Build the public settlement identity before any Outbox is created."""

    payload = normalize_review_denial_identity(
        review_id=review_id,
        review_status=review_status,
        review_status_version=review_status_version,
        resume_phase=resume_phase,
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
        node_version_before=node_version_before,
        node_target_status=node_target_status,
        graph_status_before=graph_status_before,
        graph_version_before=graph_version_before,
        graph_target_status=graph_target_status,
        budget_id=budget_id,
        budget_version_before=budget_version_before,
        acceptance_ref=acceptance_ref,
        attempt_id=attempt_id,
    )
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"review-denial://sha256/{digest}"


def normalize_review_denial_identity(
    **values: Any,
) -> dict[str, Any]:
    review_status = _text(values.get("review_status")).upper()
    resume_phase = _text(values.get("resume_phase")).upper()
    node_target = _text(values.get("node_target_status")).upper()
    graph_before = _text(values.get("graph_status_before")).upper()
    graph_target = _text(values.get("graph_target_status")).upper()
    acceptance_ref = _optional_text(values.get("acceptance_ref"))
    attempt_id = _optional_text(values.get("attempt_id"))
    payload = {
        "schema_version": REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION,
        "review_id": _required_text(values.get("review_id"), "review_id"),
        "review_status": review_status,
        "review_status_version": _positive_int(
            values.get("review_status_version"),
            "review_status_version",
        ),
        "resume_phase": resume_phase,
        "graph_id": _required_text(values.get("graph_id"), "graph_id"),
        "graph_revision": _positive_int(
            values.get("graph_revision"),
            "graph_revision",
        ),
        "node_id": _required_text(values.get("node_id"), "node_id"),
        "node_version_before": _non_negative_int(
            values.get("node_version_before"),
            "node_version_before",
        ),
        "node_target_status": node_target,
        "graph_status_before": graph_before,
        "graph_version_before": _non_negative_int(
            values.get("graph_version_before"),
            "graph_version_before",
        ),
        "graph_target_status": graph_target,
        "budget_id": _required_text(values.get("budget_id"), "budget_id"),
        "budget_version_before": _non_negative_int(
            values.get("budget_version_before"),
            "budget_version_before",
        ),
        "acceptance_ref": acceptance_ref,
        "attempt_id": attempt_id,
    }
    if review_status not in TERMINAL_REVIEW_STATUSES:
        _invalid("review_status must be terminal")
    if graph_before not in {"ACTIVE", "BLOCKED"}:
        _invalid("graph_status_before must be ACTIVE or BLOCKED")
    if resume_phase == "PRE_EXECUTION":
        if (
            node_target != "CANCELLED"
            or graph_target != "CANCELLED"
            or acceptance_ref is not None
            or attempt_id is not None
        ):
            _invalid("pre-execution denial identity is inconsistent")
    elif resume_phase == "POST_EXECUTION_ACCEPTANCE":
        if (
            node_target != "FAILED"
            or graph_target != "FAILED"
            or acceptance_ref is None
            or attempt_id is None
        ):
            _invalid("post-execution denial identity is inconsistent")
    else:
        _invalid("resume_phase is unsupported")
    return payload


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value)
    if not text:
        _invalid(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    return _text(value) or None


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        _invalid(f"{field_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        _invalid(f"{field_name} must be a non-negative integer")
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    normalized = _non_negative_int(value, field_name)
    if normalized < 1:
        _invalid(f"{field_name} must be positive")
    return normalized


def _invalid(detail: str) -> None:
    raise TaskStoreError(TASK_STORE_INVALID, detail)


__all__ = [
    "REVIEW_DENIAL_REQUIRED_COLUMNS",
    "REVIEW_DENIAL_REQUIRED_TRIGGERS",
    "REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION",
    "TERMINAL_REVIEW_STATUSES",
    "build_review_denial_settlement_ref",
    "canonical_json",
    "normalize_review_denial_identity",
    "sha256_json",
]
