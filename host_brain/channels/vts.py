"""Deterministic VTube Studio channel controls and audit logging."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.vts_runtime_mapping import load_runtime_emotion_mapping
from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    AVATAR_TRIGGER_INVALID,
    VTS_CONNECTION_FAILED,
    VTS_DEGRADED_FALLBACK,
)
from host_brain.core.ids import new_request_id, now_ms

_logger = logging.getLogger("Natsume.vts")


S_AVATAR_TALK_GATE = "S_AVATAR_TALK_GATE"
S_AVATAR_EXPRESSION_MAP = "S_AVATAR_EXPRESSION_MAP"

STATUS_APPLIED = "APPLIED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

TALK_START = "TALK_START"
TALK_END = "TALK_END"

TRIGGER_HOST = "HOST"
TRIGGER_LLM = "LLM"

EMOTION_TO_EXPRESSION_ID: dict[str, str] = load_runtime_emotion_mapping()

EMOTION_FALLBACK_MAP: dict[str, str] = {
    "POSITIVE": EMOTION_TO_EXPRESSION_ID.get("HAPPY", EMOTION_TO_EXPRESSION_ID["NEUTRAL"]),
    "NEGATIVE": EMOTION_TO_EXPRESSION_ID.get("SAD", EMOTION_TO_EXPRESSION_ID["NEUTRAL"]),
    "UNKNOWN": EMOTION_TO_EXPRESSION_ID["NEUTRAL"],
}

DEGRADED_FALLBACK_EXPRESSION_TAG = "NEUTRAL"
DEGRADED_FALLBACK_EXPRESSION_ID = EMOTION_TO_EXPRESSION_ID[DEGRADED_FALLBACK_EXPRESSION_TAG]


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
    edge_available: bool = True,
) -> VTSActionResult:
    """Resolve emotion tag to fixed expression mapping and block free actions.

    褰?``edge_available=False`` 鎴栨槧灏勫け璐ヤ絾鏈?neutral 闄嶇骇澶囬€夋椂锛岃繑鍥?    ``status=DEGRADED`` + neutral 琛ㄦ儏锛岃€岄潪鐩存帴 REJECTED锛涘璁″啓鍏ラ檷绾ц鎯呫€?    """

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    # 鈹€鈹€ Edge 涓嶅彲杈?鈫?DEGRADED 鈹€鈹€
    if not edge_available:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=_normalize_emotion_tag(emotion_tag) or DEGRADED_FALLBACK_EXPRESSION_TAG,
            expression_id=DEGRADED_FALLBACK_EXPRESSION_ID,
            status=STATUS_DEGRADED,
            reason_code=VTS_DEGRADED_FALLBACK,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="edge not available, degraded to neutral fallback",
            ts_ms=now_value,
        )

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
        # 鈹€鈹€ 鏄犲皠鏈煡 鈫?DEGRADED锛坣eutral 澶囬€夛級 鈹€鈹€
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=normalized_tag,
            expression_id=DEGRADED_FALLBACK_EXPRESSION_ID,
            status=STATUS_DEGRADED,
            reason_code=VTS_DEGRADED_FALLBACK,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail=f"unsupported emotion_tag '{normalized_tag}', degraded to neutral fallback",
            ts_ms=now_value,
        )

    result = _log_result(
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

    # 鈹€鈹€ 鐪熸椹卞姩 Edge VTS锛坒ire-and-forget锛屼笉闃诲璋冪敤鏂癸級鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    # Edge dispatch is best-effort and must not block audit resolution.
    try:
        from host_brain.channels.edge_rpc_client import post_vts as _post_vts  # noqa: PLC0415

        async def _fire_edge_vts() -> None:
            try:
                await _post_vts(
                    action="set_expression",
                    emotion_tag=normalized_tag,
                    request_id=req_id,
                )
            except Exception as exc:
                _logger.debug(
                    "Edge VTS HTTP 璋冪敤澶辫触锛堥檷绾э級: %s (request_id=%s)",
                    exc, req_id,
                )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_fire_edge_vts())
        except RuntimeError:
            pass  # 鏃犱簨浠跺惊鐜椂璺宠繃
    except ImportError:
        pass

    return result


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
        success=status in (STATUS_APPLIED, STATUS_DEGRADED),
        request_id=request_id,
        trigger=str(trigger),
        emotion_tag=emotion_tag,
        expression_id=expression_id,
        status=status,
        reason_code=reason_code,
        stage=stage,
        detail=detail,
    )

