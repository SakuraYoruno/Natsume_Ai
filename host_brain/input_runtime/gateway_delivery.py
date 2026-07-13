"""Host input runtime responsibility extracted from the HTTP adapter."""

from .contracts_and_normalization import *  # noqa: F403

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
    # Gateway 双轨语言约定：
    # - 文字回复：中文
    # - 语音回复：默认中文，遇到显式日文内容再路由日语
    # tts_text_ja 是旧字段名；新的语音链路优先使用中文 narrative。
    tts_text_ja: str | None = None,
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
            "voice_sent": False,
            "voice_waited": False,
            "voice_success": None,
            "voice_status": None,
            "voice_reason_code": None,
            "voice_detail": None,
            "voice_latency_ms": None,
            "voice_started": None,
            "voice_vts_started": None,
            "voice_vts_expression_success": None,
            "voice_event": None,
        }

    performance = coerce_reply_performance(reply_envelope.performance)
    emotion_tag = performance.emotion_tag or str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    emotion_intensity = performance.emotion_intensity

    reply_text = reply_envelope.text or _build_gateway_reply_text(
        decision=decision,
        narrative=narrative,
        reason_code=reason_code,
        render_target_lang="zh",
    )
    voice_text = _build_gateway_voice_text(
        reply_text=reply_text,
        tts_text_ja=tts_text_ja,
        render_target_lang="zh",
    )
    preview = _build_reply_preview(reply_text, limit=120)
    voice_options = _resolve_gateway_voice_reply_options(payload)
    voice_result: dict[str, Any] = {
        "voice_sent": False,
        "voice_waited": False,
        "voice_success": None,
        "voice_status": None,
        "voice_reason_code": None,
        "voice_detail": None,
        "voice_latency_ms": None,
        "voice_started": None,
        "voice_vts_started": None,
        "voice_vts_expression_success": None,
        "voice_audio_path": "",
        "voice_gateway_sent": False,
        "voice_gateway_status": None,
        "voice_gateway_detail": None,
        "voice_gateway_latency_ms": None,
        "voice_event": None,
    }
    if not _gateway_auto_reply_enabled():
        if voice_options["force_dual_voice_reply"] and platform and conversation_id and reply_text:
            voice_result["voice_sent"] = True
            if voice_options["wait_voice_reply_result"]:
                voice_result["voice_waited"] = True
                voice_result.update(
                    await _send_gateway_voice_reply(
                        platform=platform,
                        conversation_id=conversation_id,
                        tts_text=voice_text,
                        request_id=request_id,
                        emotion_tag=emotion_tag,
                        emotion_intensity=emotion_intensity,
                        send_gateway_voice_message=bool(voice_options["send_gateway_voice_message"]),
                    )
                )
            else:
                voice_result["voice_waited"] = True
                voice_result.update(
                    await _send_gateway_voice_reply(
                        platform=platform,
                        conversation_id=conversation_id,
                        tts_text=voice_text,
                        request_id=request_id,
                        emotion_tag=emotion_tag,
                        emotion_intensity=emotion_intensity,
                        send_gateway_voice_message=bool(voice_options["send_gateway_voice_message"]),
                    )
                )
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": "GATEWAY_REPLY_DISABLED",
            "detail": (
                "gateway text reply skipped: HOST_INPUT_GATEWAY_AUTO_REPLY=false"
                if voice_result["voice_sent"]
                else "gateway reply skipped: HOST_INPUT_GATEWAY_AUTO_REPLY=false"
            ),
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "auto_reply_disabled",
            **voice_result,
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
            "voice_sent": False,
        }

    performance_result = await ports.trigger_reply_performance(
        request_id=request_id,
        reply_envelope=reply_envelope,
        enable_vts=True,
    )
    _logger.info(
        "gateway reply performance completed request_id=%s invoked=%s success=%s emotion=%s",
        request_id,
        performance_result.get("invoked"),
        performance_result.get("success"),
        performance_result.get("emotion_tag"),
    )

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
                "voice_sent": False,
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
            "voice_sent": False,
            "voice_waited": False,
            "voice_success": None,
            "voice_status": None,
            "voice_reason_code": None,
            "voice_detail": None,
            "voice_latency_ms": None,
            "voice_started": None,
            "voice_vts_started": None,
            "voice_vts_expression_success": None,
            "voice_event": None,
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
            "voice_sent": False,
            "voice_waited": False,
            "voice_success": None,
            "voice_status": None,
            "voice_reason_code": None,
            "voice_detail": None,
            "voice_latency_ms": None,
            "voice_started": None,
            "voice_vts_started": None,
            "voice_vts_expression_success": None,
            "voice_event": None,
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
    normalized_platform = str(platform or "").strip().lower()

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
        planned_secondary = MsgType.VOICE
        planned_reason = planned_reason or "planned_voice"
    elif normalized_response_form in {"sticker", "image", "gif"} and suggested_sticker:
        planned_primary = MsgType.IMAGE
    elif normalized_response_form in {"text_plus_sticker", "text_plus_image", "text_plus_gif"} and suggested_sticker:
        planned_primary = MsgType.TEXT
        planned_secondary = MsgType.IMAGE
    elif requested_secondary_response_form == "voice":
        planned_primary = MsgType.TEXT
        planned_secondary = MsgType.VOICE
        planned_reason = planned_reason or "planned_secondary_voice"

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

    if voice_options["voice_selection_mode"] == "off" and planned_secondary == MsgType.VOICE:
        planned_primary = MsgType.TEXT
        planned_secondary = None
        planned_reason = "voice_selection_off_text"

    try:
        user_mtype = MsgType(str(user_mtype.value or "text").strip().lower())
    except ValueError:
        user_mtype = MsgType.TEXT

    decider = get_reply_form_decider()
    if voice_options["force_dual_voice_reply"]:
        form_decision = ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "force_dual_voice_reply")
    elif voice_options["voice_selection_mode"] == "planned" and planned_primary is None:
        form_decision = ReplyFormDecision(MsgType.TEXT, None, "planned_voice_only_text")
    else:
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
    _logger.info(
        "gateway reply form request_id=%s normalized=%s planned_reason=%s voice_mode=%s primary=%s secondary=%s suggested_sticker=%s",
        request_id,
        normalized_response_form or "-",
        planned_reason or "-",
        voice_options["voice_selection_mode"],
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
            )
        except Exception as exc:  # pragma: no cover - defensive async send
            _logger.debug("gateway secondary reply failed request_id=%s msg_type=%s err=%s", request_id, effective_msg_type.value, exc)

    async def _trigger_secondary_reply() -> dict[str, Any]:
        nonlocal voice_result
        if form_decision.secondary is None:
            return dict(voice_result)
        if form_decision.secondary == MsgType.IMAGE:
            await _send_secondary_form(msg_type=MsgType.IMAGE, content=suggested_sticker)
            return dict(voice_result)
        if not voice_text or form_decision.secondary != MsgType.VOICE:
            return dict(voice_result)

        voice_result["voice_sent"] = True
        voice_result["voice_waited"] = True
        voice_result.update(
            await _send_gateway_voice_reply(
                platform=platform,
                conversation_id=conversation_id,
                tts_text=voice_text,
                request_id=request_id,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
                send_gateway_voice_message=bool(voice_options["send_gateway_voice_message"]),
            )
        )
        return dict(voice_result)

    # ── 主回复（文字/语音）─────────────────────────────────────────────
    collapse_secondary_into_primary = False
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
    )
    if not bool(getattr(result, "success", False)):
        status = str(getattr(result, "status", "FAILED"))
        rc = str(getattr(result, "reason_code", "") or "") or "GATEWAY_REPLY_FAILED"
        detail = str(getattr(result, "detail", "") or "")
        if (
            form_decision.secondary == MsgType.VOICE
            and voice_options["allow_voice_reply_on_primary_failure"]
        ):
            voice_result = await _trigger_secondary_reply()
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
            **voice_result,
        }

    if form_decision.is_dual and not collapse_secondary_into_primary:
        voice_result = await _trigger_secondary_reply()

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
        **voice_result,
    }


async def _send_gateway_voice_reply(
    *,
    platform: str,
    conversation_id: str,
    tts_text: str,
    request_id: str,
    emotion_tag: str = "NEUTRAL",
    emotion_intensity: float | None = None,
    send_gateway_voice_message: bool = False,
) -> dict[str, Any]:
    """合成语音并驱动 Edge 播放/VTS；平台语音消息仅显式开关发送。"""
    import logging as _logging  # noqa: PLC0415
    _vlogger = _logging.getLogger("Natsume.gateway_voice")
    try:
        from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415
        from host_brain.channels.embodiment import submit_voice_command  # noqa: PLC0415
        from host_brain.channels.tts_context_router import TtsContextRouter  # noqa: PLC0415
        _tts_text_normalized = str(tts_text or "").strip()
        if not _tts_text_normalized:
            return {
                "voice_success": False,
                "voice_status": "SKIPPED",
                "voice_reason_code": "GATEWAY_VOICE_TEXT_EMPTY",
                "voice_detail": "gateway voice reply skipped: empty text",
                "voice_latency_ms": 0,
                "voice_started": False,
                "voice_vts_started": False,
                "voice_vts_expression_success": False,
                "voice_audio_path": "",
                "voice_gateway_sent": False,
                "voice_gateway_status": None,
                "voice_gateway_detail": None,
                "voice_gateway_latency_ms": None,
                "voice_event": {},
            }

        tts_params = TtsContextRouter().route(
            text=_tts_text_normalized,
            emotion_tag=emotion_tag,
        )
        effective_emotion_tag = _resolve_gateway_voice_emotion_tag(emotion_tag)
        result = await submit_voice_command(
            action="speak",
            payload={
                "text": _tts_text_normalized,
                "emotion_tag": effective_emotion_tag,
                "emotion_intensity": emotion_intensity,
                "lang_hint": str(tts_params.lang_hint or "zh"),
                "export_audio": bool(send_gateway_voice_message),
            },
            request_id=request_id,
            idempotency_key=f"{request_id}:gateway-voice",
        )
        voice_result = _extract_gateway_voice_result(result)
        audio_path = str(voice_result.get("voice_audio_path") or "").strip()
        if not send_gateway_voice_message:
            voice_result["voice_gateway_detail"] = "Gateway voice message disabled; Edge playback/VTS only"
        elif audio_path and platform and conversation_id:
            gateway_timeout_ms = get_input_settings().gateway_voice_reply_timeout_ms
            gateway_result = await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content="",
                msg_type="voice",
                media_path=audio_path,
                request_id=f"{request_id}::voice_record",
                raw_extra={
                    "message_kind": "voice_reply",
                    "reply_source": "edge_tts_export",
                    "tts_request_id": request_id,
                    "emotion_tag": str(effective_emotion_tag or "").strip().upper(),
                },
                wait_result=True,
                timeout_ms=gateway_timeout_ms,
            )
            voice_result["voice_gateway_sent"] = bool(getattr(gateway_result, "success", False))
            voice_result["voice_gateway_status"] = str(getattr(gateway_result, "status", "") or "")
            voice_result["voice_gateway_detail"] = str(getattr(gateway_result, "detail", "") or "")
            voice_result["voice_gateway_latency_ms"] = int(getattr(gateway_result, "latency_ms", 0) or 0)
        else:
            voice_result["voice_gateway_detail"] = "TTS audio export path missing; Gateway voice message not sent"
        _vlogger.debug(
            "gateway_voice completed platform=%s conversation=%s success=%s started=%s vts_started=%s platform_voice=%s len=%d",
            platform,
            conversation_id,
            voice_result["voice_success"],
            voice_result["voice_started"],
            voice_result["voice_vts_started"],
            voice_result.get("voice_gateway_sent"),
            len(_tts_text_normalized),
        )
        return voice_result
    except Exception as exc:
        _vlogger.debug("gateway_voice send failed (non-critical): %s", exc)
        return {
            "voice_success": False,
            "voice_status": "FAILED",
            "voice_reason_code": "GATEWAY_VOICE_EXCEPTION",
            "voice_detail": f"{type(exc).__name__}: {str(exc)[:180]}",
            "voice_latency_ms": 0,
            "voice_started": False,
            "voice_vts_started": False,
            "voice_vts_expression_success": False,
            "voice_audio_path": "",
            "voice_gateway_sent": False,
            "voice_gateway_status": None,
            "voice_gateway_detail": None,
            "voice_gateway_latency_ms": None,
            "voice_event": {},
        }
