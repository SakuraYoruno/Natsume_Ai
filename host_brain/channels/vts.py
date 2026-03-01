"""Deterministic VTube Studio channel controls and audit logging."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    AVATAR_TRIGGER_INVALID,
)
from host_brain.core.ids import new_request_id, now_ms


S_AVATAR_TALK_GATE = "S_AVATAR_TALK_GATE"
S_AVATAR_EXPRESSION_MAP = "S_AVATAR_EXPRESSION_MAP"

STATUS_APPLIED = "APPLIED"
STATUS_REJECTED = "REJECTED"

TALK_START = "TALK_START"
TALK_END = "TALK_END"

TRIGGER_HOST = "HOST"
TRIGGER_LLM = "LLM"

EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
}


@dataclass(frozen=True)
class VTSActionResult:
    success: bool
    request_id: str
    trigger: str
    emotion_tag: str | None
    expression_id: str | None
    status: str
    reason_code: str | None
    stage: str
    detail: str


def request_talk_gate(
    *,
    action: str,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_HOST,
    ts_ms: int | None = None,
) -> VTSActionResult:
    """Apply deterministic talk gate transitions."""

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    normalized = str(action or "").strip().upper()

    if normalized not in {TALK_START, TALK_END}:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=None,
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_TRIGGER_INVALID,
            stage=S_AVATAR_TALK_GATE,
            detail=f"unsupported talk gate action: {action!r}",
            ts_ms=now_value,
        )

    expression_id = f"talk_gate::{normalized.lower()}"
    return _log_result(
        ledger=ledger,
        request_id=req_id,
        trigger=trigger,
        emotion_tag=None,
        expression_id=expression_id,
        status=STATUS_APPLIED,
        reason_code=None,
        stage=S_AVATAR_TALK_GATE,
        detail=f"talk gate {normalized.lower()} applied",
        ts_ms=now_value,
    )


def resolve_vts_expression(
    *,
    emotion_tag: str | None,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_LLM,
    raw_expression_id: str | None = None,
    raw_action_params: dict[str, Any] | None = None,
    ts_ms: int | None = None,
) -> VTSActionResult:
    """Resolve emotion tag to fixed expression mapping and block free actions."""

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    if raw_expression_id or raw_action_params:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=_normalize_emotion_tag(emotion_tag),
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_FREE_ACTION_BLOCKED,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="free-form avatar action parameters are blocked",
            ts_ms=now_value,
        )

    normalized_tag = _normalize_emotion_tag(emotion_tag)
    if not normalized_tag:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=None,
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_TAG_INVALID,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="emotion_tag is required",
            ts_ms=now_value,
        )

    expression_id = EMOTION_TO_EXPRESSION_ID.get(normalized_tag)
    if not expression_id:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=normalized_tag,
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_TAG_INVALID,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail=f"unsupported emotion_tag: {normalized_tag}",
            ts_ms=now_value,
        )

    return _log_result(
        ledger=ledger,
        request_id=req_id,
        trigger=trigger,
        emotion_tag=normalized_tag,
        expression_id=expression_id,
        status=STATUS_APPLIED,
        reason_code=None,
        stage=S_AVATAR_EXPRESSION_MAP,
        detail="expression resolved via fixed mapping",
        ts_ms=now_value,
    )


def _normalize_emotion_tag(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    return normalized


def _log_result(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    trigger: str,
    emotion_tag: str | None,
    expression_id: str | None,
    status: str,
    reason_code: str | None,
    stage: str,
    detail: str,
    ts_ms: int,
) -> VTSActionResult:
    ledger.log_avatar_action(
        {
            "request_id": request_id,
            "trigger": str(trigger),
            "emotion_tag": emotion_tag,
            "expression_id": expression_id,
            "status": status,
            "reason_code": reason_code,
            "stage": stage,
            "detail": detail,
            "ts_ms": ts_ms,
        }
    )
    return VTSActionResult(
        success=status == STATUS_APPLIED,
        request_id=request_id,
        trigger=str(trigger),
        emotion_tag=emotion_tag,
        expression_id=expression_id,
        status=status,
        reason_code=reason_code,
        stage=stage,
        detail=detail,
    )
