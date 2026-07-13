"""Unified Host input route for ASR / Gateway events.

路由层（薄层）：只负责参数解析和响应格式化。
业务逻辑已拆分到以下 service 模块：
  - input_service_log.py      日志/审计
  - input_service_gateway.py  gateway 意图路由
  - input_service_ack.py      即时 ACK / 进度推送
  - input_service_quality.py  执行质量分析 / 偏航检测
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import inspect
import json
import logging
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from host_brain.core.ids import new_request_id
from host_brain.digital_life.application_boundary import (
    project_inner_life_snapshot,
    read_life_snapshot,
)
from host_brain.input_runtime.outbound_delivery import trigger_reply_performance
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.reply_envelope import can_send, coerce_reply_envelope, envelope_from_commit
from host_brain.input_runtime.reply_performance import coerce_reply_performance
from host_brain.persona.response_form_contract import (
    derive_render_profile_for_response_form,
    is_media_response_form,
    is_pure_media_response_form,
    normalize_response_form,
)
from host_brain.agent_runtime.skill_ids import is_agent_runtime_skill

# ── service 层 import ──────────────────────────────────────────────────────
from host_brain.input_runtime.audit import (
    get_ledger_safe as _get_ledger_safe,
    build_reply_preview as _build_reply_preview,
    build_input_preview as _build_input_preview,
    log_input_event as _log_input_event,
    get_request_skill_exec_rows as _get_request_skill_exec_rows,
)
from host_brain.input_runtime.ingress_policy import (
    HostGatewayIngressOutcome as _HostGatewayIngressOutcome,
    complete_host_gateway_ingress as _complete_host_gateway_ingress,
    evaluate_host_gateway_ingress as _evaluate_host_gateway_ingress,
    looks_like_progress_query as _looks_like_progress_query_for_ack_skip,
    looks_like_execution_intent as _looks_like_execution_intent_for_ack,
    normalize_route_confidence as _normalize_route_confidence,
)
from host_brain.tribe.thalamus_agent import get_thalamus_agent
from host_brain.input_runtime.progress import (
    resolve_bond_stage_for_ack as _resolve_bond_stage_for_ack,
    resolve_emotion_tag_for_ack as _resolve_emotion_tag_for_ack,
    format_elapsed_compact as _format_elapsed_compact,
    should_send_gateway_immediate_ack as _should_send_gateway_immediate_ack_impl,
    deliver_gateway_immediate_ack as _deliver_gateway_immediate_ack,
    log_gateway_ack_task_exception as _log_gateway_ack_task_exception,
    build_gateway_progress_reply_text as _build_gateway_progress_reply_text,
    log_progress_push_task_exception as _log_progress_push_task_exception,
)
from host_brain.input_runtime.quality import (
    analyze_request_execution_quality as _analyze_request_execution_quality,
    detect_gateway_execution_deviation as _detect_gateway_execution_deviation_impl,
    build_realign_user_message as _build_realign_user_message,
    maybe_send_gateway_realign_notice as _maybe_send_gateway_realign_notice_impl,
    compute_keyword_overlap as _compute_keyword_overlap,
)
from host_brain.input_runtime.feedback import (
    record_feedback_for_latest_persona_reply as _record_feedback_for_latest_persona_reply,
)
from host_brain.input_runtime.settings import get_input_settings

_logger = logging.getLogger("Natsume.input_routes")

GovernedCommandApplicationProvider = Callable[[], Any]

# ASR 相关常量（未迁移，仅在本文件使用）
_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat", "active chat", "current_chat", "current chat",
    "current_session", "default", "unknown", "none", "null",
    "-", "_", "na", "n/a", "当前聊天", "当前会话",
}
_ASR_PLACEHOLDER_PATTERN = re.compile(r"^\[[A-Z0-9_:\-]{3,}\]$")
_ASR_SPEECH_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_VOICE_TEXT_CHAR_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_VOICE_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_GATEWAY_DIRECT_OPEN_APP_RE = re.compile(r"(打开|启动|运行).*(酷狗|kugou|qq音乐|网易云|spotify|应用|软件|程序|播放器)", re.IGNORECASE)
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


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


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
    confidence = _coerce_float(payload_dict.get("confidence"))
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
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
    return get_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


class _NoopMetrics:
    """Minimal metrics shim for cortex orchestrator direct invocation."""
    def __getattr__(self, _name: str):
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None
        return _noop


def _build_gateway_route_preview(text: str) -> dict[str, Any] | None:
    """Rules-only route preview for non-authoritative Gateway acknowledgements."""
    try:
        from host_brain.tribe.thalamus_agent import classify_task_intent_by_rules  # noqa: PLC0415

        preview = classify_task_intent_by_rules(text)
        return dict(preview) if isinstance(preview, dict) else None
    except Exception:
        return None


async def _build_input_thalamus_intent_draft(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
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
    return await get_thalamus_agent().classify_and_compile_intent_draft(
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


def _should_send_gateway_immediate_ack(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
    intent_draft: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """包装 service 层函数，传入本文件的判断函数。"""
    normalized = str(text or payload.get("content") or "").strip()
    if _GATEWAY_DIRECT_OPEN_APP_RE.search(normalized):
        return False, "ACK_DIRECT_OPEN_APP"
    if _looks_like_progress_query_for_ack_skip(normalized):
        return False, "ACK_PROGRESS_QUERY"
    route_type = str((dict(intent_draft or {})).get("route") or "").strip().lower()
    if not route_type:
        return False, "ACK_ROUTE_UNRESOLVED"
    if route_type not in {"skill", "delegate", "plan", "task_progress"}:
        return False, "ACK_NON_EXECUTION_ROUTE"
    return _should_send_gateway_immediate_ack_impl(
        source=source,
        text=text,
        payload=payload,
        looks_like_progress_query_fn=_looks_like_progress_query_for_ack_skip,
        looks_like_execution_intent_fn=_looks_like_execution_intent_for_ack,
    )


def _should_send_gateway_route_started_voice_ack(
    *,
    source: str,
    payload: dict[str, Any],
    route_type: str,
    target_id: str,
) -> tuple[bool, str]:
    if str(source or "").strip().upper() != "GATEWAY":
        return False, "ROUTE_ACK_SOURCE_NOT_GATEWAY"
    if not get_input_settings().gateway_route_ack_voice_enabled:
        return False, "ROUTE_ACK_DISABLED"
    if not str(payload.get("platform") or "").strip() or not str(payload.get("conversation_id") or "").strip():
        return False, "ROUTE_ACK_TARGET_MISSING"

    normalized_route = str(route_type or "").strip().lower()
    normalized_target = str(target_id or "").strip().lower()
    if normalized_route == "plan":
        return True, "ROUTE_ACK_PLAN"
    if normalized_route == "delegate" and normalized_target in {"translate_agent", "intent_agent"}:
        return True, f"ROUTE_ACK_{normalized_target.upper()}"
    if normalized_route == "skill" and is_agent_runtime_skill(normalized_target):
        return True, "ROUTE_ACK_codex_runtime"
    return False, "ROUTE_ACK_FAST_ROUTE"


def _load_gateway_fallback_texts() -> dict[str, Any]:
    return {
        "route_started_voice_ack": {},
        "voice_summary": {},
    }


def _normalize_gateway_template_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _load_gateway_fallback_texts_localized(*, render_target_lang: str) -> dict[str, Any]:
    raw = _load_gateway_fallback_texts()
    localized = raw.get(_normalize_gateway_template_lang(render_target_lang))
    selected: dict[str, Any] = dict(localized) if isinstance(localized, dict) else {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_text = str(key)
            if key_text in {"zh", "ja"}:
                continue
            selected[key_text] = value
    return selected or (dict(raw) if isinstance(raw, dict) else {})


def _load_gateway_multimodal_runtime_texts() -> dict[str, Any]:
    return {}


def _load_gateway_multimodal_runtime_texts_localized(*, render_target_lang: str) -> dict[str, Any]:
    raw = _load_gateway_multimodal_runtime_texts()
    localized = raw.get(_normalize_gateway_template_lang(render_target_lang))
    selected: dict[str, Any] = dict(localized) if isinstance(localized, dict) else {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_text = str(key)
            if key_text in {"zh", "ja"}:
                continue
            selected[key_text] = value
    return selected or (dict(raw) if isinstance(raw, dict) else {})


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
                    "size_bytes": _coerce_int(item.get("size_bytes")),
                    "duration_ms": _coerce_int(item.get("duration_ms")),
                    "width": _coerce_int(item.get("width")),
                    "height": _coerce_int(item.get("height")),
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


def _build_gateway_multimodal_async_ack_text(*, attachments: list[dict[str, Any]], render_target_lang: str = "zh") -> str:
    templates = _load_gateway_multimodal_runtime_texts_localized(render_target_lang=render_target_lang)
    types = {str(item.get("type") or "").strip().lower() for item in attachments}
    if "video" in types:
        return str(templates.get("async_accept_video") or "")
    if "file" in types:
        return str(templates.get("async_accept_file") or "")
    return str(templates.get("async_accept_generic") or "")


def _build_gateway_route_started_voice_ack_text(
    *,
    route_type: str,
    target_id: str,
    render_target_lang: str = "ja",
) -> str:
    normalized_route = str(route_type or "").strip().lower()
    normalized_target = str(target_id or "").strip().lower()
    templates = _load_gateway_fallback_texts_localized(render_target_lang=render_target_lang)
    route_templates = dict(templates.get("route_started_voice_ack") or {})
    if normalized_route == "delegate" and normalized_target == "translate_agent":
        return str(route_templates.get("translate_agent") or "")
    if normalized_route == "delegate" and normalized_target == "intent_agent":
        return str(route_templates.get("intent_agent") or "")
    if normalized_route == "skill" and is_agent_runtime_skill(normalized_target):
        return str(route_templates.get("agent_worker") or "")
    if normalized_route == "plan":
        return str(route_templates.get("plan") or "")
    return str(route_templates.get("default") or "")

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


async def _maybe_send_gateway_realign_notice(
    *,
    request_id: str,
    payload: dict[str, Any],
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    """包装 service 层函数，传入本文件的上下文解析函数。"""
    return await _maybe_send_gateway_realign_notice_impl(
        request_id=request_id,
        payload=payload,
        ports=ports,
        resolve_bond_stage_fn=_resolve_bond_stage_for_ack,
        resolve_emotion_tag_fn=_resolve_emotion_tag_for_ack,
    )
def _gateway_auto_reply_enabled() -> bool:
    return get_input_settings().gateway_auto_reply


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


def _build_gateway_reply_text(
    *,
    decision: str,
    narrative: str,
    reason_code: str | None,
    render_target_lang: str = "zh",
) -> str:
    """构建 gateway 最终回复文本。"""
    reply_text = str(narrative or "").strip()
    if reply_text:
        return reply_text
    _ = reason_code
    return ""


def _build_gateway_voice_text(
    *,
    reply_text: str,
    tts_text_ja: str | None,
    render_target_lang: str = "ja",
) -> str:
    templates = _load_gateway_fallback_texts_localized(render_target_lang=render_target_lang)
    voice_templates = dict(templates.get("voice_summary") or {})

    normalized_reply = str(reply_text or "").strip()
    if normalized_reply and _VOICE_TEXT_CHAR_PATTERN.search(normalized_reply) and not _VOICE_LATIN_PATTERN.search(normalized_reply):
        return normalized_reply

    candidate = str(tts_text_ja or "").strip()
    if candidate and _VOICE_TEXT_CHAR_PATTERN.search(candidate) and not _VOICE_LATIN_PATTERN.search(candidate):
        return candidate

    if "```" in normalized_reply or _VOICE_LATIN_PATTERN.search(normalized_reply):
        return str(voice_templates.get("code_or_latin") or "")

    if normalized_reply:
        return str(voice_templates.get("reply_present") or "")

    return ""


# ── gateway 路由函数已迁移到 input_service_gateway.py ──────────────────────
# 本文件通过顶部 import 使用 service 层函数


def _coerce_bool_flag(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    raw = str(value).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_gateway_voice_reply_options(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_input_settings()
    raw_extra = payload.get("raw_extra")
    raw_extra_map = dict(raw_extra) if isinstance(raw_extra, dict) else {}

    def _pick_payload_bool(keys: tuple[str, ...], *, default: bool) -> tuple[bool, str]:
        for key in keys:
            if key in payload:
                return _coerce_bool_flag(payload.get(key), default=default), "payload"
        for key in keys:
            if key in raw_extra_map:
                return _coerce_bool_flag(raw_extra_map.get(key), default=default), "raw_extra"
        return bool(default), "default"

    def _pick_bool(
        keys: tuple[str, ...],
        *,
        configured: bool | None,
        default: bool,
    ) -> bool:
        value, source = _pick_payload_bool(keys, default=default)
        if source != "default":
            return value
        return default if configured is None else bool(configured)

    def _pick_mode() -> str:
        for key in ("voice_selection_mode", "gateway_voice_selection_mode"):
            if key in payload:
                return str(payload.get(key) or "").strip().lower()
        for key in ("voice_selection_mode", "gateway_voice_selection_mode"):
            if key in raw_extra_map:
                return str(raw_extra_map.get(key) or "").strip().lower()
        return str(settings.gateway_voice_selection_mode or "").strip().lower()

    selection_mode = _pick_mode()
    if selection_mode not in {"smart", "always", "off", "planned"}:
        selection_mode = ""

    force_dual_voice_reply, force_source = _pick_payload_bool(
        ("force_dual_voice_reply", "force_voice_reply"),
        default=False,
    )
    if force_source == "default":
        if selection_mode == "always":
            force_dual_voice_reply = True
            force_source = "selection_mode"
        elif selection_mode in {"smart", "off", "planned"}:
            force_dual_voice_reply = False
            force_source = "selection_mode"
        else:
            force_dual_voice_reply = settings.gateway_force_dual_voice_reply
            force_source = "settings" if force_dual_voice_reply else "default"

    effective_selection_mode = selection_mode or ("always" if force_dual_voice_reply else "smart")
    wait_voice_reply_result = _pick_bool(
        ("wait_voice_reply_result", "gateway_voice_wait_result"),
        configured=settings.gateway_voice_reply_wait_result,
        default=False,
    )
    allow_voice_reply_on_primary_failure = _pick_bool(
        ("allow_voice_reply_on_primary_failure", "gateway_voice_on_primary_failure"),
        configured=settings.gateway_voice_reply_on_primary_failure,
        default=force_dual_voice_reply,
    )
    send_gateway_voice_message = _pick_bool(
        ("send_gateway_voice_message", "gateway_voice_message", "send_voice_record", "voice_record_reply"),
        configured=settings.gateway_send_voice_message,
        default=False,
    )
    return {
        "force_dual_voice_reply": force_dual_voice_reply,
        "force_dual_voice_reply_source": force_source,
        "voice_selection_mode": effective_selection_mode,
        "wait_voice_reply_result": wait_voice_reply_result,
        "allow_voice_reply_on_primary_failure": allow_voice_reply_on_primary_failure,
        "send_gateway_voice_message": send_gateway_voice_message,
    }


def _extract_gateway_voice_result(result: Any) -> dict[str, Any]:
    event = getattr(result, "event", {})
    event_map = dict(event) if isinstance(event, dict) else {}
    mouth_sync = event_map.get("vts_mouth_sync")
    mouth_sync_map = dict(mouth_sync) if isinstance(mouth_sync, dict) else {}
    talk_result = event_map.get("vts_talk_result")
    talk_result_map = dict(talk_result) if isinstance(talk_result, dict) else {}
    expression_result = event_map.get("vts_expression_result")
    expression_result_map = dict(expression_result) if isinstance(expression_result, dict) else {}

    success = bool(getattr(result, "success", False))
    detail = str(getattr(result, "detail", "") or "")
    detail_lower = detail.lower()
    voice_started = (
        success
        and (
            event_map.get("event_type") == "TTS_PLAYBACK_STARTED"
            or "playback started" in detail_lower
            or bool(mouth_sync_map.get("started"))
        )
    )
    vts_talk_started = bool(mouth_sync_map.get("started")) or bool(talk_result_map.get("success"))
    vts_expression_success = bool(expression_result_map.get("success"))

    audio_path = _extract_voice_audio_path(event_map)
    return {
        "voice_success": success,
        "voice_status": str(getattr(result, "status", "") or ""),
        "voice_reason_code": getattr(result, "reason_code", None),
        "voice_detail": detail,
        "voice_latency_ms": int(getattr(result, "latency_ms", 0) or 0),
        "voice_started": bool(voice_started),
        "voice_vts_started": bool(vts_talk_started),
        "voice_vts_expression_success": bool(vts_expression_success),
        "voice_audio_path": audio_path,
        "voice_gateway_sent": False,
        "voice_gateway_status": None,
        "voice_gateway_detail": None,
        "voice_gateway_latency_ms": None,
        "voice_event": event_map,
    }


def _extract_voice_audio_path(event_map: dict[str, Any]) -> str:
    candidates: list[Any] = [event_map.get("audio_path")]
    for key in ("start_event", "queue_event"):
        nested = event_map.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get("audio_path"))
    for value in candidates:
        path = str(value or "").strip()
        if path:
            return path
    return ""


def _resolve_gateway_voice_emotion_tag(emotion_tag: str | None) -> str | None:
    normalized = str(emotion_tag or "").strip().upper()
    if normalized and normalized != "NEUTRAL":
        return normalized
    configured = str(get_input_settings().gateway_default_voice_emotion_tag or "").strip().upper()
    return configured or None


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
