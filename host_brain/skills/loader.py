"""Retired token-only Skill execution boundary.

``SkillLoader`` remains importable for migration diagnostics, but it cannot
invoke a Skill handler or executor.  Production execution must enter through a
governed HostCommand and immutable TaskGraph node.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
from host_brain.core.ids import now_ms


@dataclass(frozen=True, slots=True)
class SkillExecutionResult:
    status: str
    result_code: str
    skill_name: str
    intent_id: str
    evidence_ref: str | None
    manifest_ref: str | None
    undo_token_ref: str | None
    detail: dict[str, Any]
    runtime_payload: dict[str, Any] | None = None


class SkillLoader:
    """Compatibility facade that rejects every direct execution attempt."""

    def __init__(
        self,
        *,
        registry: Any,
        token_service: Any,
        ledger: Any,
        capability_broker: Any | None = None,
        degradation_capability_port: Any | None = None,
    ) -> None:
        self.registry = registry
        self.token_service = token_service
        self.ledger = ledger
        self.capability_broker = capability_broker
        self.degradation_capability_port = degradation_capability_port

    def execute(
        self,
        *,
        intent: dict[str, Any],
        governor_output: dict[str, Any],
        context: dict[str, Any] | None = None,
        now_ts_ms: int | None = None,
    ) -> SkillExecutionResult:
        """Reject before registry, token, handler, Broker, or external I/O."""

        del context
        now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        request_id = str(
            intent.get("request_id")
            or governor_output.get("request_id")
            or ""
        ).strip()
        intent_id = str(intent.get("id") or "").strip()
        skill_name = str(intent.get("action") or "").strip()
        row = {
            "request_id": request_id or None,
            "thread_id": (
                intent.get("thread_id")
                or governor_output.get("thread_id")
            ),
            "turn_id": (
                intent.get("turn_id")
                or governor_output.get("turn_id")
            ),
            "intent_id": intent_id,
            "skill_name": skill_name,
            "approval_token_ref": None,
            "confirm_token_ref": None,
            "result_status": "REJECTED",
            "result_code": GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
            "result_digest": (
                "direct SkillLoader execution is retired; "
                "submit a governed HostCommand/TaskGraph"
            ),
            "evidence_ref": None,
            "manifest_ref": None,
            "undo_token_ref": None,
            "ts_ms": now_value,
        }
        self.ledger.log_skill_exec(row)
        runtime_payload = {
            "required_execution_surface": "governed_task_runtime",
            "side_effects_executed": False,
        }
        return SkillExecutionResult(
            status="REJECTED",
            result_code=GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
            skill_name=skill_name,
            intent_id=intent_id,
            evidence_ref=None,
            manifest_ref=None,
            undo_token_ref=None,
            detail=row,
            runtime_payload=runtime_payload,
        )

__all__ = [
    "SkillExecutionResult",
    "SkillLoader",
]
