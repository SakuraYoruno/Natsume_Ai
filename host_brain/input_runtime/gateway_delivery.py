"""Host input runtime responsibility extracted from the HTTP adapter."""

from collections.abc import Mapping
import logging
from typing import Any

from host_brain.input_runtime.audit import build_reply_preview as _build_reply_preview
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.reply_envelope import (
    can_send,
    coerce_reply_envelope,
    envelope_from_commit,
)
from host_brain.input_runtime.reply_performance import coerce_reply_performance
from host_brain.input_runtime.settings import get_input_settings
from host_brain.persona.response_form_contract import (
    is_media_response_form,
    is_pure_media_response_form,
    normalize_response_form,
)

_logger = logging.getLogger("Natsume.input_routes")

async def _deliver_gateway_reply(
    *,
    request_id: str,
    payload: dict[str, Any],
    decision: str,
    reason_code: str | None,
    narrative: str,
    # Phase XV P4：ReplyFormDecider 所需的上下文
    emotion_tag: str = "NEUTRAL",
    bond_stage: str = "FRIEND",
    user_msg_type: str = "text",
    response_form: str | None = None,
    response_form_reason: str | None = None,
    reply_envelope_payload: dict[str, Any] | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    normalized_decision = str(decision or "").strip().upper()
    normalized_reason = str(reason_code or "").strip().upper()
    reply_envelope = (
        coerce_reply_envelope(reply_envelope_payload)
        if reply_envelope_payload
        else envelope_from_commit(
            decision=decision,
            narrative=narrative,
            reason_code=reason_code,
            response_form=str(response_form or "text").strip() or "text",
            source="gateway_primary_reply",
            degraded=(normalized_decision in {"NO_REPLY", "SKIP_REPLY"} or not str(narrative or "").strip()),
            cacheable=False,
        )
    )
    if not can_send(reply_envelope) and normalized_decision == "APPROVE" and str(narrative or "").strip():
        repaired_envelope = envelope_from_commit(
            decision="APPROVE",
            narrative=narrative,
            reason_code=None,
            response_form="text",
            source="gateway_primary_reply_repaired",
            degraded=False,
            cacheable=False,
            raw_extra={
                "gate_repair": "invalid_reply_envelope_text_repair",
                "original_reply_envelope": reply_envelope.as_dict(),
                "original_response_form": str(response_form or "text").strip() or "text",
                "original_reason_code": str(reply_envelope.reason_code or normalized_reason or "").strip(),
            },
        )
        if can_send(repaired_envelope):
            reply_envelope = repaired_envelope
            response_form = "text"
            response_form_reason = (
                f"{str(response_form_reason or '').strip() or 'unknown'}|repaired_invalid_reply_envelope"
            )
    if not can_send(reply_envelope):
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": str(reply_envelope.reason_code or normalized_reason or "NO_REPLY").strip() or "NO_REPLY",
            "detail": "gateway final reply suppressed: no safe final reply",
            "latency_ms": 0,
            "reply_text_len": 0,
            "reply_preview": "",
            "reply_form": str(response_form or "text").strip() or "text",
            "reply_form_reason": str(response_form_reason or "suppressed_no_reply").strip() or "suppressed_no_reply",
            "reply_envelope": reply_envelope.as_dict(),
        }

    performance = coerce_reply_performance(reply_envelope.performance)
    emotion_tag = performance.emotion_tag or str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"

    reply_text = reply_envelope.text or str(narrative or "").strip()
    preview = _build_reply_preview(reply_text, limit=120)
    if not get_input_settings().gateway_auto_reply:
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": "GATEWAY_REPLY_DISABLED",
            "detail": "gateway reply skipped: HOST_INPUT_GATEWAY_AUTO_REPLY=false",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "auto_reply_disabled",
        }
    if not platform or not conversation_id:
        return {
            "success": False,
            "status": "FAILED",
            "reason_code": "GATEWAY_REPLY_MISSING_ROUTE",
            "detail": "gateway reply skipped: missing platform or conversation_id",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "missing_route",
        }

    from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415
    ReplyFormDecision = ports.reply_form_decision_type
    get_reply_form_decider = ports.get_reply_form_decider
    MsgType = ports.message_type
    get_multimodal_processor = ports.get_multimodal_processor

    envelope_raw_extra = (
        dict(reply_envelope.raw_extra or {})
        if hasattr(reply_envelope, "raw_extra")
        else dict((reply_envelope.as_dict() if hasattr(reply_envelope, "as_dict") else {}).get("raw_extra") or {})
    )
    envelope_media_ref = str(
        getattr(reply_envelope, "media_ref", "")
        or envelope_raw_extra.get("media_ref")
        or envelope_raw_extra.get("media_path")
        or envelope_raw_extra.get("sticker")
        or envelope_raw_extra.get("gif")
        or envelope_raw_extra.get("image")
        or ""
    ).strip()
    envelope_response_form = normalize_response_form(str(
        getattr(reply_envelope, "response_form", "")
        or response_form
        or "text"
    ))
    if is_pure_media_response_form(envelope_response_form) and envelope_media_ref:
        primary_msg_type = MsgType.IMAGE.value
        media_is_remote = envelope_media_ref.startswith(("http://", "https://", "data:"))
        primary_content = reply_text or (envelope_media_ref if media_is_remote else "")
        primary_media_path = None if media_is_remote else envelope_media_ref
        settings = get_input_settings()
        wait_result = settings.gateway_media_reply_wait_result
        timeout_ms = settings.gateway_reply_timeout_ms
        result = await post_gateway_reply(
            platform=platform,
            conversation_id=conversation_id,
            content=primary_content,
            msg_type=primary_msg_type,
            request_id=request_id,
            media_path=primary_media_path,
            raw_extra={
                **envelope_raw_extra,
                "message_kind": "final_reply",
                "reply_source": str(reply_envelope.source or "reaction_catalog").strip() or "reaction_catalog",
                "performance": performance.as_dict(),
                "reply_envelope": reply_envelope.as_dict(),
            },
            wait_result=wait_result,
            timeout_ms=timeout_ms,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
        preview = _build_reply_preview(
            reply_text or f"[{envelope_response_form}:{str(reply_envelope.reaction_id or '').strip() or 'media'}]",
            limit=120,
        )
        if not bool(getattr(result, "success", False)):
            return {
                "success": False,
                "status": str(getattr(result, "status", "FAILED")),
                "reason_code": str(getattr(result, "reason_code", "") or "") or "GATEWAY_MEDIA_REPLY_FAILED",
                "detail": str(getattr(result, "detail", "") or ""),
                "latency_ms": int(getattr(result, "latency_ms", 0)),
                "reply_text_len": len(reply_text),
                "reply_preview": preview,
                "reply_form": envelope_response_form,
                "reply_form_reason": str(response_form_reason or "social_reaction").strip() or "social_reaction",
                "reply_envelope": reply_envelope.as_dict(),
                "message_kind": "final_reply",
            }
        return {
            "success": True,
            "status": str(getattr(result, "status", "OK")),
            "reason_code": None,
            "detail": str(getattr(result, "detail", "") or ""),
            "latency_ms": int(getattr(result, "latency_ms", 0)),
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": envelope_response_form,
            "reply_form_reason": str(response_form_reason or "social_reaction").strip() or "social_reaction",
            "reply_envelope": reply_envelope.as_dict(),
            "message_kind": "final_reply",
        }
    if is_media_response_form(envelope_response_form) and not reply_text:
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": "MEDIA_REPLY_PAYLOAD_MISSING",
            "detail": "gateway media reply suppressed: structured media payload missing",
            "latency_ms": 0,
            "reply_text_len": 0,
            "reply_preview": "",
            "reply_form": envelope_response_form,
            "reply_form_reason": str(response_form_reason or "media_payload_missing").strip() or "media_payload_missing",
            "reply_envelope": reply_envelope.as_dict(),
            "message_kind": "no_reply",
        }

    mm_output = get_multimodal_processor().enhance_output(
        text=reply_text,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
        render_lang="zh",
    )
    explicit_sticker = str(
        envelope_raw_extra.get("sticker")
        or envelope_raw_extra.get("gif")
        or envelope_raw_extra.get("image")
        or envelope_media_ref
        or ""
    ).strip()
    suggested_sticker = explicit_sticker or str(getattr(mm_output, "suggested_sticker", "") or "").strip()
    def _normalize_form_for_platform(msg_type: MsgType, content: str) -> tuple[MsgType, str]:
        normalized_content = str(content or "").strip()
        return msg_type, normalized_content

    normalized_response_form = envelope_response_form
    planned_primary: MsgType | None = None
    planned_secondary: MsgType | None = None
    planned_reason = str(response_form_reason or "").strip()
    requested_secondary_response_form = str(
        envelope_raw_extra.get("secondary_response_form")
        or envelope_raw_extra.get("secondary_form")
        or ""
    ).strip().lower()
    if normalized_response_form == "voice":
        planned_primary = MsgType.TEXT
        planned_reason = planned_reason or "voice_delivery_text_fallback"
    elif normalized_response_form in {"sticker", "image", "gif"} and suggested_sticker:
        planned_primary = MsgType.IMAGE
    elif normalized_response_form in {"text_plus_sticker", "text_plus_image", "text_plus_gif"} and suggested_sticker:
        planned_primary = MsgType.TEXT
        planned_secondary = MsgType.IMAGE
    elif requested_secondary_response_form == "voice":
        planned_primary = MsgType.TEXT
        planned_reason = planned_reason or "voice_delivery_text_only"

    # ── Phase XV P4：ReplyFormDecider ──────────────────────────────────
    # 将用户消息类型字符串转换为 MsgType
    normalized_user_msg_type = str(user_msg_type or payload.get("msg_type") or "text").strip().lower()
    if normalized_user_msg_type in {"voice", "audio", "record", "sound"}:
        user_mtype = MsgType.VOICE
    else:
        try:
            user_mtype = MsgType(normalized_user_msg_type)
        except ValueError:
            user_mtype = MsgType.TEXT
    payload_raw_extra = dict(payload.get("raw_extra") or {}) if isinstance(payload.get("raw_extra"), dict) else {}
    user_text = str(
        payload.get("content")
        or payload.get("text")
        or payload.get("message")
        or payload_raw_extra.get("raw_message")
        or payload_raw_extra.get("message")
        or ""
    ).strip()
    user_id = str(
        payload.get("person_id")
        or payload.get("from_id")
        or payload_raw_extra.get("user_id")
        or conversation_id
        or "default"
    ).strip() or "default"

    try:
        user_mtype = MsgType(str(user_mtype.value or "text").strip().lower())
    except ValueError:
        user_mtype = MsgType.TEXT

    decider = get_reply_form_decider()
    form_decision = decider.decide(
        user_id=user_id,
        user_msg_type=user_mtype,
        narrative_zh=reply_text,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
        planned_primary=planned_primary,
        planned_secondary=planned_secondary,
        planned_reason=planned_reason,
        user_text=user_text,
        platform=platform,
    )
    if form_decision.primary == MsgType.VOICE:
        form_decision = ReplyFormDecision(
            MsgType.TEXT,
            form_decision.secondary,
            f"{form_decision.reason}|voice_delivery_text_fallback",
        )
    if form_decision.secondary == MsgType.VOICE:
        form_decision = ReplyFormDecision(
            form_decision.primary,
            None,
            f"{form_decision.reason}|voice_delivery_retired",
        )
    _logger.info(
        "gateway reply form request_id=%s normalized=%s planned_reason=%s primary=%s secondary=%s suggested_sticker=%s",
        request_id,
        normalized_response_form or "-",
        planned_reason or "-",
        getattr(form_decision.primary, "value", str(form_decision.primary)),
        getattr(form_decision.secondary, "value", "") or "-",
        suggested_sticker or "-",
    )

    async def _send_secondary_form(*, msg_type: MsgType, content: str) -> None:
        if not content:
            return
        effective_msg_type, effective_content = _normalize_form_for_platform(msg_type, content)
        secondary_request_id = f"{request_id}::secondary::{effective_msg_type.value}"
        secondary_timeout_ms = min(max(1000, int(timeout_ms)), 4000)
        try:
            _logger.info(
                "gateway secondary reply request_id=%s effective_request_id=%s msg_type=%s content=%s",
                request_id,
                secondary_request_id,
                effective_msg_type.value,
                effective_content[:40],
            )
            await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content=effective_content,
                msg_type=effective_msg_type.value,
                request_id=secondary_request_id,
                raw_extra={
                    "message_kind": "final_reply",
                    "reply_source": "gateway_secondary_form",
                    "performance": performance.as_dict(),
                    "reply_envelope": reply_envelope.as_dict(),
                },
                wait_result=True,
                timeout_ms=secondary_timeout_ms,
                action_preview_service=action_preview_service,
                action_preview_event_id=action_preview_event_id,
                action_preview_boundary=action_preview_boundary,
            )
        except Exception as exc:  # pragma: no cover - defensive async send
            _logger.debug("gateway secondary reply failed request_id=%s msg_type=%s err=%s", request_id, effective_msg_type.value, exc)

    # ── 主回复（文字/图片）─────────────────────────────────────────────
    if form_decision.primary == MsgType.IMAGE:
        primary_content = suggested_sticker or reply_text
    else:
        primary_content = reply_text
    primary_msg_type_enum, primary_content = _normalize_form_for_platform(form_decision.primary, primary_content)
    primary_msg_type = primary_msg_type_enum.value

    settings = get_input_settings()
    wait_result = settings.gateway_reply_wait_result
    text_reply_async = settings.gateway_text_reply_async
    if primary_msg_type == MsgType.TEXT.value and text_reply_async:
        wait_result = False
    timeout_ms = settings.gateway_reply_timeout_ms
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=primary_content,
        msg_type=primary_msg_type,
        request_id=request_id,
        raw_extra={
            "message_kind": "final_reply",
            "reply_source": "gateway_primary_reply",
            "performance": performance.as_dict(),
            "reply_envelope": reply_envelope.as_dict(),
        },
        wait_result=wait_result,
        timeout_ms=timeout_ms,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )
    if not bool(getattr(result, "success", False)):
        status = str(getattr(result, "status", "FAILED"))
        rc = str(getattr(result, "reason_code", "") or "") or "GATEWAY_REPLY_FAILED"
        detail = str(getattr(result, "detail", "") or "")
        return {
            "success": False,
            "status": status,
            "reason_code": rc,
            "detail": detail,
            "latency_ms": int(getattr(result, "latency_ms", 0)),
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": form_decision.primary.value,
            "reply_form_reason": form_decision.reason,
            "reply_envelope": reply_envelope.as_dict(),
        }

    if form_decision.secondary == MsgType.IMAGE:
        await _send_secondary_form(msg_type=MsgType.IMAGE, content=suggested_sticker)

    return {
        "success": True,
        "status": str(getattr(result, "status", "OK")),
        "reason_code": None,
        "detail": str(getattr(result, "detail", "") or ""),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "reply_text_len": len(reply_text),
        "reply_preview": preview,
        "reply_form": form_decision.primary.value,
        "reply_form_reason": form_decision.reason,
        "message_kind": "final_reply",
        "reply_envelope": reply_envelope.as_dict(),
    }
