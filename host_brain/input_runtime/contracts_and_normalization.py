"""Unified Host input route for ASR / Gateway events.

路由层（薄层）：只负责参数解析和响应格式化。
业务逻辑已拆分到以下 service 模块：
  - input_runtime.audit       日志/审计
  - input_runtime.ingress_policy  gateway 意图路由
  - input_runtime.quality      执行质量分析 / 偏航检测
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import inspect
import json
import re
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

from host_brain.core.runtime_helpers import (
    _coerce_bool,
    _coerce_optional_float,
    _coerce_optional_int,
)
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
# ── service 层 import ──────────────────────────────────────────────────────
from host_brain.input_runtime.audit import (
    log_input_event as _log_input_event,
)
from host_brain.input_runtime.ingress_policy import (
    HostGatewayIngressOutcome as _HostGatewayIngressOutcome,
    complete_host_gateway_ingress as _complete_host_gateway_ingress,
    looks_like_execution_intent as _looks_like_execution_intent_for_ack,
)
from host_brain.input_runtime.quality import (
    detect_gateway_execution_deviation as _detect_gateway_execution_deviation_impl,
)
from host_brain.input_runtime.settings import get_input_settings

GovernedCommandApplicationProvider = Callable[[], Any]

# ASR 相关常量（未迁移，仅在本文件使用）
_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat", "active chat", "current_chat", "current chat",
    "current_session", "default", "unknown", "none", "null",
    "-", "_", "na", "n/a", "当前聊天", "当前会话",
}
_ASR_PLACEHOLDER_PATTERN = re.compile(r"^\[[A-Z0-9_:\-]{3,}\]$")
_ASR_SPEECH_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_GATEWAY_RECENT_MULTIMODAL_CACHE: dict[str, dict[str, Any]] = {}
_GATEWAY_RECENT_MULTIMODAL_LOCK = threading.Lock()
_GATEWAY_SOURCE_PLATFORM_ALIASES: dict[str, str] = {
    "QQ": "qq",
    "WECHAT": "wechat",
    "WX": "wechat",
    "FEISHU": "feishu",
    "WECOM": "wecom",
    "TELEGRAM": "telegram",
    "TG": "telegram",
}
_GATEWAY_MULTIMODAL_FOLLOWUP_RE = re.compile(
    r"(这(张)?图|图片|这不是|表情包|贴图|梗图|截图|那个图|刚才那张|什么意思|啥意思|看得到|看得见|看懂|能看到|能看见|画的什么|内容)",
    re.IGNORECASE,
)


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or GATEWAY")
    person_id: str | None = Field(None, description="Optional canonical person id")

    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

    platform: str | None = Field(None, description="[GATEWAY] Platform: wechat/qq/feishu/wecom")
    msg_id: str | None = Field(None, description="[GATEWAY] Message ID")
    from_id: str | None = Field(None, description="[GATEWAY] Sender ID")
    to_id: str | None = Field(None, description="[GATEWAY] Receiver ID")
    conversation_id: str | None = Field(None, description="[GATEWAY] Conversation ID")
    content: str | None = Field(None, description="[GATEWAY] Message content")
    msg_type: str | None = Field(None, description="[GATEWAY] Message type")
    is_group: bool | None = Field(None, description="[GATEWAY] Is group message")
    sender_name: str | None = Field(None, description="[GATEWAY] Sender display name")
    media_url: str | None = Field(None, description="[GATEWAY] Media URL")
    attachments: list[dict[str, Any]] | None = Field(None, description="[GATEWAY] Structured attachments")
    multimodal_observations: dict[str, Any] | None = Field(None, description="[GATEWAY] Multimodal observer output")
    attachment_summary: str | None = Field(None, description="[GATEWAY] Multimodal summary for chat")
    reply_to_msg_id: str | None = Field(None, description="[GATEWAY] Reply-to message ID")
    raw_extra: dict[str, Any] | None = Field(None, description="[GATEWAY] Extra fields")

    ts_ms: int | None = Field(None, description="Event timestamp in ms")


class InputResponse(BaseModel):
    """Ingress response."""

    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int
    receipt_ref: str | None = None
    body_sha256: str | None = None
    idempotency_sha256: str | None = None


# ── 本文件保留的 ASR 相关工具函数（未迁移，仅在本文件使用）──────────────────


def _normalize_input_source(raw_source: str) -> tuple[str, str | None]:
    source = str(raw_source or "").upper().strip()
    alias_platform = _GATEWAY_SOURCE_PLATFORM_ALIASES.get(source)
    if alias_platform:
        return "GATEWAY", alias_platform
    return source, None


def _gateway_recent_multimodal_ttl_s() -> float:
    return float(get_input_settings().gateway_multimodal_recent_ttl_s)


async def _ensure_gateway_multimodal_observed_for_dispatch(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    ports: InputRuntimeApplicationPorts,
) -> bool:
    """Populate multimodal observations before governed command admission."""

    if str(source or "").upper().strip() != "GATEWAY":
        return True
    attachments = list(payload.get("attachments") or [])
    if not attachments:
        _maybe_restore_recent_gateway_multimodal(session_id=session_id, text=text, payload=payload)
        return True
    if payload.get("multimodal_observations"):
        _remember_recent_gateway_multimodal(session_id=session_id, payload=payload)
        return True

    try:
        observer_result = await asyncio.to_thread(
            ports.get_multimodal_observer().observe,
            request_id=request_id,
            platform=str(payload.get("platform") or "").strip(),
            conversation_id=session_id,
            user_text=text,
            attachments=attachments,
            render_target_lang="zh",
        )
    except Exception as exc:
        payload["multimodal_observations"] = {
            "items": [],
            "observed_types": [
                str(item.get("type") or "").strip()
                for item in attachments
                if isinstance(item, dict) and str(item.get("type") or "").strip()
            ],
            "primary_modality": str((attachments[0] or {}).get("type") or "image").strip(),
            "summary_for_chat": "",
            "social_signal": "",
            "confidence": 0.0,
        }
        payload["attachment_summary"] = ""
        payload["attachment_processing_mode"] = "degraded"
        payload["multimodal_safety_flags"] = ["OBSERVATION_DEGRADED"]
        payload["multimodal_confidence"] = 0.0
        payload["multimodal_followup_required"] = False
        payload["multimodal_artifacts"] = {}
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="MULTIMODAL_OBSERVE_DEGRADED",
            status="DEGRADED",
            reason_code="GATEWAY_MULTIMODAL_OBSERVE_FAILED",
            detail=f"{type(exc).__name__}: {str(exc)[:180]}",
            ts_ms=int(time.time() * 1000),
        )
        _remember_recent_gateway_multimodal(session_id=session_id, payload=payload)
        return True

    payload["multimodal_observations"] = dict(observer_result.get("observations") or {})
    payload["attachment_summary"] = str(observer_result.get("summary_for_chat") or "").strip()
    payload["attachment_processing_mode"] = str(observer_result.get("mode") or "sync")
    payload["multimodal_safety_flags"] = list(observer_result.get("safety_flags") or [])
    payload["multimodal_confidence"] = float(observer_result.get("confidence") or 0.0)
    payload["multimodal_followup_required"] = bool(observer_result.get("followup_required"))
    payload["multimodal_artifacts"] = dict(observer_result.get("artifacts") or {})
    _remember_recent_gateway_multimodal(session_id=session_id, payload=payload)

    if str(observer_result.get("mode") or "").strip().lower() == "async":
        # The input surface is now an awaited, governed admission boundary.  An
        # observer may label its strategy async, but it has already returned the
        # usable observation above; never fork a second ungoverned pipeline.
        payload["attachment_processing_mode"] = "governed_waited"
    return True


def _remember_recent_gateway_multimodal(*, session_id: str, payload: dict[str, Any]) -> None:
    cache_key = str(session_id or "").strip()
    if not cache_key:
        return
    attachments = list(payload.get("attachments") or [])
    observations = dict(payload.get("multimodal_observations") or {})
    summary = str(payload.get("attachment_summary") or "").strip()
    if not attachments or not observations or not summary:
        return
    expires_at = time.time() + _gateway_recent_multimodal_ttl_s()
    snapshot = {
        "attachments": attachments,
        "multimodal_observations": observations,
        "attachment_summary": summary,
        "attachment_processing_mode": str(payload.get("attachment_processing_mode") or "sync"),
        "expires_at": expires_at,
    }
    with _GATEWAY_RECENT_MULTIMODAL_LOCK:
        expired = [k for k, v in _GATEWAY_RECENT_MULTIMODAL_CACHE.items() if float(v.get("expires_at") or 0.0) <= time.time()]
        for expired_key in expired:
            _GATEWAY_RECENT_MULTIMODAL_CACHE.pop(expired_key, None)
        _GATEWAY_RECENT_MULTIMODAL_CACHE[cache_key] = snapshot


def _maybe_restore_recent_gateway_multimodal(*, session_id: str, text: str, payload: dict[str, Any]) -> bool:
    cache_key = str(session_id or "").strip()
    if not cache_key:
        return False
    if list(payload.get("attachments") or []) or dict(payload.get("multimodal_observations") or {}):
        return False
    normalized_text = str(text or "").strip()
    if not normalized_text or not _GATEWAY_MULTIMODAL_FOLLOWUP_RE.search(normalized_text):
        return False
    now = time.time()
    with _GATEWAY_RECENT_MULTIMODAL_LOCK:
        cached = dict(_GATEWAY_RECENT_MULTIMODAL_CACHE.get(cache_key) or {})
        if float(cached.get("expires_at") or 0.0) <= now:
            _GATEWAY_RECENT_MULTIMODAL_CACHE.pop(cache_key, None)
            return False
    payload["attachments"] = list(cached.get("attachments") or [])
    payload["multimodal_observations"] = dict(cached.get("multimodal_observations") or {})
    payload["attachment_summary"] = str(cached.get("attachment_summary") or "").strip()
    payload["attachment_processing_mode"] = str(cached.get("attachment_processing_mode") or "sync")
    payload["multimodal_context_restored"] = True
    return True


def _looks_like_asr_placeholder_text(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    if not normalized:
        return True
    return _ASR_PLACEHOLDER_PATTERN.fullmatch(normalized) is not None


def _contains_speech_chars(text: str) -> bool:
    return _ASR_SPEECH_CHAR_PATTERN.search(str(text or "")) is not None


def _evaluate_asr_noise_gate(
    *,
    text: str,
    payload: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    settings = get_input_settings()
    text_value = str(text or "").strip()
    if not text_value:
        return False, "ASR_TEXT_EMPTY"
    if _looks_like_asr_placeholder_text(text_value):
        return False, "ASR_PLACEHOLDER_TEXT"
    min_text_len = settings.asr_min_text_len
    if len(text_value) < min_text_len:
        return False, "ASR_TEXT_TOO_SHORT"
    require_speech_chars = settings.asr_require_speech_chars
    if require_speech_chars and not _contains_speech_chars(text_value):
        return False, "ASR_NON_SPEECH_TEXT"
    payload_dict = dict(payload or {})
    reason_code = str(payload_dict.get("reason_code") or "").strip().upper()
    if reason_code in {"ASR_VAD_NO_SPEECH", "ASR_LOW_CONFIDENCE"}:
        return False, reason_code
    low_confidence = _coerce_bool(payload_dict.get("low_confidence"), default=False)
    if low_confidence:
        return False, "ASR_LOW_CONFIDENCE"
    min_confidence = settings.asr_min_confidence
    require_confidence = settings.asr_require_confidence
    confidence = _coerce_optional_float(payload_dict.get("confidence"))
    if confidence is None and require_confidence:
        return False, "ASR_CONFIDENCE_MISSING"
    if confidence is not None and confidence < min_confidence:
        return False, "ASR_LOW_CONFIDENCE"
    return True, None


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


def _evaluate_immune_gate_for_input(
    *,
    text: str,
    source: str,
    session_id: str,
    ports: InputRuntimeApplicationPorts,
    person_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ingress through the explicit Host-composed ImmuneAgent port."""

    return ports.require_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


async def _build_input_thalamus_intent_draft(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ports: InputRuntimeApplicationPorts,
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    resolved_person_id = resolve_person_id(
        str(person_id or payload.get("person_id") or "").strip() or None,
        session_id=session_id,
        platform=reply_channel,
        from_id=str(payload.get("from_id") or "").strip() or None,
        conversation_id=reply_target,
    )
    return await ports.require_thalamus_agent().classify_and_compile_intent_draft(
        request_id=request_id,
        session_id=session_id,
        user_message=text,
        person_id=resolved_person_id,
        source="input_routes",
        input_source=source,
        ts_ms=ts_ms,
        attachments=list(payload.get("attachments") or []),
        multimodal_observations=dict(payload.get("multimodal_observations") or {}),
    )


def _gateway_attachment_type_from_msg_type(msg_type: str | None) -> str:
    normalized = str(msg_type or "").strip().lower()
    if normalized in {"image", "emoji", "voice", "video", "file"}:
        return normalized
    return "file"


def _normalize_gateway_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    attachments_raw = payload.get("attachments")
    if isinstance(attachments_raw, list):
        for index, raw in enumerate(attachments_raw):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            attachment_id = str(item.get("attachment_id") or "").strip() or f"att_{index + 1}"
            attachment_type = _gateway_attachment_type_from_msg_type(str(item.get("type") or payload.get("msg_type") or "file"))
            local_path = str(item.get("local_path") or "").strip() or None
            remote_url = str(item.get("remote_url") or "").strip() or None
            normalized_items.append(
                {
                    "attachment_id": attachment_id,
                    "type": attachment_type,
                    "mime_type": str(item.get("mime_type") or "").strip() or None,
                    "local_path": local_path,
                    "remote_url": remote_url,
                    "file_name": str(item.get("file_name") or "").strip() or None,
                    "size_bytes": _coerce_optional_int(item.get("size_bytes")),
                    "duration_ms": _coerce_optional_int(item.get("duration_ms")),
                    "width": _coerce_optional_int(item.get("width")),
                    "height": _coerce_optional_int(item.get("height")),
                    "sha256": str(item.get("sha256") or "").strip() or None,
                    "caption": str(item.get("caption") or "").strip() or None,
                    "ocr_text": str(item.get("ocr_text") or "").strip() or None,
                    "transcript": str(item.get("transcript") or "").strip() or None,
                    "extra": dict(item.get("extra") or {}),
                }
            )

    if not normalized_items:
        media_url = str(payload.get("media_url") or "").strip()
        if media_url:
            attachment_type = _gateway_attachment_type_from_msg_type(payload.get("msg_type"))
            is_remote = media_url.startswith("http://") or media_url.startswith("https://")
            normalized_items.append(
                {
                    "attachment_id": "legacy_media_1",
                    "type": attachment_type,
                    "mime_type": None,
                    "local_path": (None if is_remote else media_url),
                    "remote_url": (media_url if is_remote else None),
                    "file_name": None,
                    "size_bytes": None,
                    "duration_ms": None,
                    "width": None,
                    "height": None,
                    "sha256": None,
                    "caption": str(payload.get("content") or "").strip() or None,
                    "ocr_text": None,
                    "transcript": None,
                    "extra": {},
                }
            )

    asr_info = dict((payload.get("raw_extra") or {}).get("gateway_voice_asr") or {}) if isinstance(payload.get("raw_extra"), dict) else {}
    transcript = str(asr_info.get("text") or "").strip()
    if transcript:
        for item in normalized_items:
            if item.get("type") == "voice" and not str(item.get("transcript") or "").strip():
                item["transcript"] = transcript

    return normalized_items


def _build_gateway_multimodal_placeholder_text(*, content: str, attachments: list[dict[str, Any]]) -> str:
    normalized = str(content or "").strip()
    if normalized:
        return normalized
    if not attachments:
        return normalized
    types = [str(item.get("type") or "").strip().lower() for item in attachments]
    if len(types) > 3:
        return "[多附件消息]"
    if "video" in types:
        return "[视频消息]"
    if "voice" in types:
        return "[语音消息]"
    if "image" in types or "emoji" in types:
        if "emoji" in types and "image" not in types:
            return "[表情消息]"
        return "[图片消息]"
    if "file" in types:
        return "[文件消息]"
    return "[附件消息]"


def _detect_gateway_execution_deviation(
    *,
    source: str,
    user_text: str,
    decision: str,
    narrative: str,
) -> dict[str, Any]:
    """包装 service 层函数，传入本文件的判断函数。"""
    return _detect_gateway_execution_deviation_impl(
        source=source,
        user_text=user_text,
        decision=decision,
        narrative=narrative,
        looks_like_execution_intent_fn=_looks_like_execution_intent_for_ack,
    )


def _parse_sse_data_event(raw_event: str) -> dict[str, Any] | None:
    """解析 SSE data 事件行，返回 JSON 对象或 None。"""
    line = str(raw_event or "").strip()
    if not line.startswith("data:"):
        return None
    payload_str = line.removeprefix("data:").strip()
    if not payload_str:
        return None
    import json  # noqa: PLC0415
    try:
        obj = json.loads(payload_str)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


# ── gateway 路由函数已迁移到 ingress_policy.py ────────────────────────────
# 本文件通过顶部 import 使用 service 层函数


def _gateway_ingress_rejection_http_status(reason_code: str) -> int:
    reason = str(reason_code or "").strip().upper()
    if any(marker in reason for marker in ("UNAVAILABLE", "DISABLED", "BACKEND", "INTERNAL_ERROR")):
        return 503
    if "RATE_LIMIT" in reason:
        return 429
    if "SCOPE" in reason:
        return 403
    if any(marker in reason for marker in ("REPLAY", "CONFLICT", "DUPLICATE")):
        return 409
    return 401


def _persist_gateway_ingress_result(
    ingress: _HostGatewayIngressOutcome | None,
    *,
    request_id: str,
    status: str,
    at_ms: int,
) -> bool:
    if ingress is None:
        return True
    result_ref = _host_input_result_ref(request_id=request_id, status=status)
    return _complete_host_gateway_ingress(
        claim_ref=ingress.claim_ref,
        result_ref=result_ref,
        at_ms=at_ms,
    )


def _host_input_result_ref(*, request_id: str, status: str) -> str:
    return f"host-input-result://{request_id}/{str(status).strip().upper()}"


def _host_input_receipt_fields(
    ingress: _HostGatewayIngressOutcome,
    *,
    receipt_ref: str,
) -> dict[str, str | None]:
    idempotency_key = str(ingress.idempotency_key or "")
    return {
        "receipt_ref": str(receipt_ref or "") or None,
        "body_sha256": str(ingress.body_sha256 or "") or None,
        "idempotency_sha256": (
            hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            if idempotency_key
            else None
        ),
    }


async def _read_input_raw_body(body: InputPayload, request: Any) -> bytes:
    """Read exact HTTP bytes; retain a deterministic direct-call test seam."""

    reader = getattr(request, "body", None)
    if callable(reader):
        candidate = reader()
        if inspect.isawaitable(candidate):
            candidate = await candidate
        if isinstance(candidate, (bytes, bytearray, memoryview)):
            return bytes(candidate)
    return json.dumps(
        body.model_dump(exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")



__all__ = [name for name in globals() if not name.startswith("__")]
