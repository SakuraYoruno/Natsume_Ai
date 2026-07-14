"""Durable artifact records shared by canonical production Domain Packs.

This module intentionally owns no scheduler, thread, in-memory job registry,
application service locator, conversation writer, or Experience authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.production_studio import (
    AGENT_PRODUCTION_SKILL_IDS,
    build_persona_boundary_contract,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.core.ids import now_ms


STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
GOVERNED_PRODUCTION_EXECUTION_MODE = "governed_task_runtime"


@dataclass(slots=True)
class AgentProductionJob:
    """Durable artifact projection; never an execution authority."""

    job_id: str
    work_order_id: str
    skill_id: str
    status: str
    artifacts_dir: str
    result_contract_ref: str
    request_id: str | None = None
    detail: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_ts_ms: int = field(default_factory=now_ms)
    updated_ts_ms: int = field(default_factory=now_ms)
    finished_ts_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result_contract(
    *,
    status: str,
    order: AgentWorkOrder,
    run_dir: Path,
    detail: str = "",
    error: str | None = None,
    result: Mapping[str, Any] | None = None,
) -> AgentResult:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    metadata = {
        "skill_id": skill_id,
        "agent_production": True,
        "agent_background": False,
        "surface_main_chain_required": True,
        "persona_boundary": dict(
            order.metadata.get("persona_boundary")
            or build_persona_boundary_contract()
        ),
        "result_contract": dict(order.metadata.get("result_contract") or {}),
        "trigger_policy": dict(order.metadata.get("trigger_policy") or {}),
    }
    if result:
        metadata["worker_result_status"] = str(result.get("status") or "")
        metadata["worker_reason_code"] = result.get("reason_code")
    return AgentResult(
        status=status,
        work_order_id=order.work_order_id,
        summary=f"Agent production WorkOrder {status.lower()}.",
        result_text="",
        artifacts_dir=str(run_dir),
        verification={"status": "NOT_RUN", "reason": "canonical production lifecycle"},
        risk_flags=[] if not error else ["agent_production_error"],
        review_required=False,
        detail=detail or None,
        reason_code=None if not error else "AGENT_PRODUCTION_ERROR",
        metadata=metadata,
    )


def _persist_job(
    store: AgentArtifactStore,
    run_dir: Path,
    job: AgentProductionJob,
) -> None:
    store.write_json(run_dir / "production_job.json", job.to_dict())


def _resolve_order_chat_context(order: AgentWorkOrder) -> dict[str, str]:
    """Resolve only explicit WorkOrder context; no application lookup."""

    metadata = dict(order.metadata or {})
    return {
        "request_id": str(metadata.get("request_id") or "").strip(),
        "intent_id": str(metadata.get("intent_id") or "").strip(),
        "session_id": str(metadata.get("session_id") or "").strip(),
        "person_id": str(metadata.get("person_id") or "").strip(),
    }


def _append_production_memory_event(**_event: Any) -> dict[str, Any]:
    """Retired projection: canonical ExperienceIngress owns memory writes."""

    return {
        "status": "SKIPPED",
        "reason_code": "CANONICAL_EXPERIENCE_INGRESS_REQUIRED",
    }


def _gateway_result_success(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status = str(payload.get("status") or "").strip().lower()
    return bool(payload.get("success", True)) and status not in {"failed", "error"}


def _record_gateway_visible_turn(
    *,
    gateway_result: Mapping[str, Any] | None,
    **_turn: Any,
) -> dict[str, Any]:
    """Retired projection: canonical channel writer owns visible turns."""

    if not _gateway_result_success(gateway_result):
        return {"status": "SKIPPED", "reason_code": "GATEWAY_NOT_SENT"}
    return {
        "status": "SKIPPED",
        "reason_code": "CANONICAL_CHANNEL_WRITER_REQUIRED",
    }


def _validate_production_boundary(order: AgentWorkOrder) -> None:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id not in AGENT_PRODUCTION_SKILL_IDS:
        raise ValueError(f"unknown Agent production skill: {skill_id or '<missing>'}")
    persona_boundary = order.metadata.get("persona_boundary")
    if not isinstance(persona_boundary, Mapping):
        raise ValueError("Agent production WorkOrder requires persona_boundary")
    result_contract = order.metadata.get("result_contract")
    if not isinstance(result_contract, Mapping):
        raise ValueError("Agent production WorkOrder requires result_contract")
    if not bool(result_contract.get("surface_main_chain_required")):
        raise ValueError("Agent production result must return through surface main chain")


def _purchase_confirmation_missing(order: AgentWorkOrder) -> bool:
    approval = dict(order.approval_policy or {})
    requires = bool(
        approval.get("requires_confirmation") or approval.get("require_confirm")
    )
    confirmed = bool(approval.get("confirmed"))
    may_purchase = bool(
        approval.get("may_purchase")
        or approval.get("may_spend_points")
        or approval.get("max_points")
        or str(approval.get("confirm_scope") or "").strip()
    )
    return may_purchase and requires and not confirmed


def _parse_bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _parse_int_env(
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), value))


def _parse_env_text(name: str, *, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    return value or str(default or "").strip()


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _short_text(value: Any, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)]}…"


__all__ = [
    "AgentProductionJob",
    "GOVERNED_PRODUCTION_EXECUTION_MODE",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
]
