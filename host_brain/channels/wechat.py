"""WeChat channel event normalization and auditable I/O logging."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CHANNEL_DUPLICATE_MESSAGE, CHANNEL_SEND_FAILED
from host_brain.core.ids import new_request_id, now_ms


CHANNEL_WECHAT = "wechat"
DIRECTION_INGRESS = "INGRESS"
DIRECTION_EGRESS = "EGRESS"

S_CHANNEL_INGRESS = "S_CHANNEL_INGRESS"
S_CHANNEL_EGRESS = "S_CHANNEL_EGRESS"

STATUS_RECEIVED = "RECEIVED"
STATUS_DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"


@dataclass(frozen=True)
class WeChatIngressResult:
    accepted: bool
    duplicate: bool
    request_id: str
    thread_key: str
    message_id: str
    status: str
    reason_code: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class WeChatEgressResult:
    success: bool
    request_id: str
    thread_key: str
    message_id: str
    status: str
    reason_code: str | None
    detail: str


def receive_wechat_event(
    raw_event: dict[str, Any],
    *,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> WeChatIngressResult:
    """Normalize ingress event and persist a channel_io_log row."""

    normalized = _normalize_ingress(raw_event)
    req_id = request_id or str(raw_event.get("request_id") or new_request_id())
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    duplicate = ledger.has_channel_message(
        channel=CHANNEL_WECHAT,
        direction=DIRECTION_INGRESS,
        message_id=normalized["message_id"],
    )

    status = STATUS_DUPLICATE_IGNORED if duplicate else STATUS_RECEIVED
    reason_code = CHANNEL_DUPLICATE_MESSAGE if duplicate else None
    detail = "duplicate ingress ignored" if duplicate else "ingress received"
    payload_digest = _digest_payload(normalized)

    ledger.log_channel_io(
        {
            "request_id": req_id,
            "channel": CHANNEL_WECHAT,
            "direction": DIRECTION_INGRESS,
            "thread_key": normalized["thread_key"],
            "message_id": normalized["message_id"],
            "user_id": normalized["from_user_id"],
            "status": status,
            "reason_code": reason_code,
            "stage": S_CHANNEL_INGRESS,
            "detail": detail,
            "payload_digest": payload_digest,
            "ts_ms": now_value,
        }
    )

    return WeChatIngressResult(
        accepted=not duplicate,
        duplicate=duplicate,
        request_id=req_id,
        thread_key=normalized["thread_key"],
        message_id=normalized["message_id"],
        status=status,
        reason_code=reason_code,
        payload=normalized,
    )


def send_wechat_message(
    *,
    thread_key: str,
    message_id: str,
    content: str,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    user_id: str | None = None,
    ack_success: bool = True,
    reason_code: str | None = None,
    stage: str = S_CHANNEL_EGRESS,
    detail: str | None = None,
    ts_ms: int | None = None,
) -> WeChatEgressResult:
    """Persist egress send/ack event to channel_io_log."""

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    ok = bool(ack_success)
    status = STATUS_SENT if ok else STATUS_FAILED
    code = None if ok else (reason_code or CHANNEL_SEND_FAILED)
    event_detail = detail or ("message sent" if ok else "message send failed")
    payload_digest = _digest_payload({"message_id": message_id, "content": content, "thread_key": thread_key})

    ledger.log_channel_io(
        {
            "request_id": req_id,
            "channel": CHANNEL_WECHAT,
            "direction": DIRECTION_EGRESS,
            "thread_key": str(thread_key),
            "message_id": str(message_id),
            "user_id": user_id,
            "status": status,
            "reason_code": code,
            "stage": stage,
            "detail": event_detail,
            "payload_digest": payload_digest,
            "ts_ms": now_value,
        }
    )

    return WeChatEgressResult(
        success=ok,
        request_id=req_id,
        thread_key=str(thread_key),
        message_id=str(message_id),
        status=status,
        reason_code=code,
        detail=event_detail,
    )


def _normalize_ingress(raw_event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_event, dict):
        raise ValueError("raw_event must be object")

    message_id = str(raw_event.get("message_id") or raw_event.get("msg_id") or "").strip()
    if not message_id:
        raise ValueError("wechat ingress requires message_id")

    from_user_id = str(raw_event.get("from_user_id") or raw_event.get("from_user") or raw_event.get("openid") or "").strip()
    if not from_user_id:
        raise ValueError("wechat ingress requires from_user_id")

    to_user_id = str(raw_event.get("to_user_id") or raw_event.get("to_user") or "bot").strip() or "bot"
    content = str(raw_event.get("content") or raw_event.get("text") or "").strip()
    thread_key = str(raw_event.get("thread_key") or build_thread_key(from_user_id=from_user_id, to_user_id=to_user_id))

    return {
        "message_id": message_id,
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "thread_key": thread_key,
        "content": content,
    }


def build_thread_key(*, from_user_id: str, to_user_id: str) -> str:
    return f"wechat::{str(from_user_id).strip()}::{str(to_user_id).strip()}"


def _digest_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
