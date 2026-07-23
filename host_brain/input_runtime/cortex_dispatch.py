"""Host input runtime responsibility extracted from the HTTP adapter."""

from .contracts_and_normalization import *  # noqa: F403

async def _run_cortex_orchestrator_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    intent_draft_override: dict[str, Any] | None = None,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    command_authority: dict[str, Any] | None = None,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    _evaluate_governor_with_timing = ports.evaluate_governor_with_timing
    get_intent_engine = ports.get_intent_engine
    build_chat_emotion_snapshot = ports.build_chat_emotion_snapshot
    build_chat_governance_snapshot = ports.build_chat_governance_snapshot
    resolve_chat_emotion_context = ports.resolve_chat_emotion_context
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
    ChatRequest = ports.chat_request_factory
    cortex_orchestrator_sse_generator = ports.cortex_orchestrator_sse_generator
    from host_brain.governor.route_token import (  # noqa: PLC0415
        RouteTokenDisposition,
        validate_governor_route_token,
    )

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    attachments = list(payload.get("attachments") or [])
    multimodal_observations = dict(payload.get("multimodal_observations") or {})
    message_text = str(text or "").strip()
    person_id = resolve_person_id(
        str(payload.get("person_id") or "").strip() or None,
        session_id=session_id,
        platform=reply_channel,
        from_id=str(payload.get("from_id") or "").strip() or None,
        conversation_id=reply_target,
    )
    response_form = str(payload.get("response_form") or payload.get("planned_response_form") or "").strip() or None
    resolved_render_profile = derive_render_profile_for_response_form(response_form)
    if resolved_render_profile == "ja_voice":
        render_target_lang = "ja"
    elif resolved_render_profile == "media_action":
        render_target_lang = "media"
    else:
        render_target_lang = "zh"

    request_body = ChatRequest(
        message=message_text,
        person_id=person_id,
        session_id=session_id,
        input_source=source,
        reply_channel=reply_channel,
        reply_target=reply_target,
        # Gateway 入口的语音/口型由 _deliver_gateway_reply 统一下发，避免 SurfaceReply 在 COMMIT 后重复补播。
        suppress_edge_tts=(source == "GATEWAY"),
        suppress_edge_vts=False,
        # 语言只由 response_form/render_profile 决定；Gateway/QQ/Web/API 只是传输来源。
        render_target_lang=render_target_lang,
        render_profile=resolved_render_profile,
        response_form=response_form,
        attachments=list(attachments or []),
        multimodal_observations=dict(multimodal_observations or {}),
        attachment_summary=str(payload.get("attachment_summary") or "").strip(),
    )
    emotion_context = resolve_chat_emotion_context(request_body=request_body)
    emotion_snapshot = build_chat_emotion_snapshot(
        emotion_context=emotion_context,
        source=f"input_routes.{str(source or '').lower()}",
        ts_ms=ts_ms,
    )
    intent_draft = dict(intent_draft_override or {})
    if not intent_draft:
        intent_draft = await _build_input_thalamus_intent_draft(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            person_id=person_id,
            ts_ms=ts_ms,
            ports=ports,
        )

    # A governed Host Input is owned by the authenticated ingress principal,
    # not by the conversational person projection.  The latter remains in the
    # request/person context, while the former must be frozen into the
    # Governor decision so its durable grant can later derive node grants for
    # the exact HostCommand principal.
    governed_authority: dict[str, Any] | None = None
    if governed_command_application_provider is not None:
        governed_authority = _require_governed_command_authority(
            command_authority,
        )
        intent_draft = dict(intent_draft or {})
        intent_draft["principal_id"] = governed_authority["principal_id"]

    def _result_from_commit(commit: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": str(commit.get("decision") or "").strip().upper() or "REJECT",
            "reason_code": str(commit.get("reason_code") or "").strip() or None,
            "narrative": str(commit.get("narrative") or "").strip(),
            "commit_event": commit,
            "intent_draft": dict(intent_draft or {}),
            "route_decision": {
                "route": str(intent_draft.get("route") or "chat").strip().lower() or "chat",
                "target_id": str(intent_draft.get("target_id") or "").strip(),
                "task": str(intent_draft.get("task") or "").strip(),
                "reason_code": str(
                    intent_draft.get("reason_code")
                    or intent_draft.get("route_reason")
                    or ""
                ).strip()
                or None,
                "source": str(intent_draft.get("route_source") or "").strip() or None,
                "confidence": _normalize_route_confidence(
                    intent_draft.get("route_confidence")
                    if intent_draft.get("route_confidence") is not None
                    else intent_draft.get("confidence")
                ),
                "override_reason": str(
                    intent_draft.get("route_override_reason") or ""
                ).strip()
                or None,
                "llm_route_used": bool(intent_draft.get("llm_route_used")),
            },
        }

    governance_snapshot = build_chat_governance_snapshot(
        emotion_context=emotion_context,
        ts_ms=ts_ms,
    )
    governor_result, governor_timing = await _evaluate_governor_with_timing(
        engine=get_intent_engine(),
        intent_draft=intent_draft,
        request_id=request_id,
        thread_id=session_id,
        governance_snapshot=governance_snapshot,
        inline=False,
    )
    governor_result = dict(governor_result or {})
    governor_result.setdefault(
        "queue_wait_ms",
        int(governor_timing.get("queue_wait_ms") or 0),
    )
    governor_result.setdefault(
        "execute_ms",
        int(governor_timing.get("execute_ms") or 0),
    )
    governor_decision = str(governor_result.get("decision") or "").strip().upper()
    governor_reason = str(governor_result.get("reason_code") or "").strip()
    if governor_decision != "APPROVE":
        terminal_decision = "PAUSE" if governor_decision == "PAUSE" else "REJECT"
        return _result_from_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": terminal_decision,
                "reason_code": governor_reason or "GOVERNOR_REJECTED",
                "request_id": request_id,
                "governor_result": governor_result,
            }
        )

    route_validation = validate_governor_route_token(governor_result)
    if route_validation.disposition == RouteTokenDisposition.PAUSE:
        return _result_from_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "PAUSE",
                "execution_state": "PAUSED_REVIEW",
                "reason_code": "GOV_AIRLOCK_REVIEW",
                "route_validation_reason_code": route_validation.reason_code,
                "committed_route_token": route_validation.token,
                "request_id": request_id,
                "governor_result": governor_result,
            }
        )
    if route_validation.disposition == RouteTokenDisposition.BLOCK:
        return _result_from_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "execution_state": "BLOCKED",
                "reason_code": route_validation.reason_code,
                "committed_route_token": route_validation.token or None,
                "request_id": request_id,
                "governor_result": governor_result,
            }
        )

    governed_command_application: Any | None = None
    if governed_command_application_provider is not None:
        governed_command_application = governed_command_application_provider()
        if inspect.isawaitable(governed_command_application):
            governed_command_application = await governed_command_application
        if governed_command_application is None:
            raise RuntimeError("governed_command_application_unavailable")

    commit_event: dict[str, Any] | None = None
    async for event in cortex_orchestrator_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=str(governor_result.get("intent_id") or request_id),
        metrics=_NoopMetrics(),
        governed_command_application=governed_command_application,
        command_authority=dict(governed_authority or {}),
    ):
        parsed = _parse_sse_data_event(event)
        if parsed and str(parsed.get("phase") or "").upper() == "COMMIT":
            commit_event = parsed

    if commit_event is None:
        raise RuntimeError("cortex_orchestrator_commit_missing")

    return _result_from_commit(commit_event)


def _require_governed_command_authority(
    command_authority: dict[str, Any] | None,
) -> dict[str, str]:
    """Validate the exact Secure Ingress identity before Governor admission."""

    authority = {
        key: str(dict(command_authority or {}).get(key) or "").strip()
        for key in ("principal_id", "claim_ref", "audit_ref", "body_sha256")
    }
    missing = [key for key, value in authority.items() if not value]
    if missing:
        raise RuntimeError(
            "governed_command_authority_incomplete:" + ",".join(missing)
        )
    return authority


async def _run_cortex_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    return await _run_cortex_orchestrator_pipeline(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
        ports=ports,
    )


async def _maybe_realign_gateway_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    user_text: str,
    payload: dict[str, Any],
    ts_ms: int,
    pipeline_result: dict[str, Any],
    ledger: Any,
    ports: InputRuntimeApplicationPorts,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    commit_event = dict(pipeline_result.get("commit_event") or {})
    if isinstance(commit_event.get("execution_receipt"), dict):
        return pipeline_result, {
            "triggered": False,
            "reason_code": "DEVIATION_GOVERNED_RECEIPT_FINAL",
        }
    route_decision = dict(pipeline_result.get("route_decision") or {})
    route_type = str(route_decision.get("route") or "").strip().lower()
    route_target = str(route_decision.get("target_id") or "").strip().lower()
    if route_type in {"chat", "recent_turn"}:
        return pipeline_result, {
            "triggered": False,
            "reason_code": "DEVIATION_NON_EXECUTION_ROUTE",
            "route_type": route_type,
            "route_target": route_target,
        }
    if (route_type == "skill" and is_agent_runtime_skill(route_target)) or (
        route_type == "delegate" and route_target == "translate_agent"
    ):
        reason_code = (
            "DEVIATION_SKIPPED_codex_runtime"
            if is_agent_runtime_skill(route_target)
            else "DEVIATION_SKIPPED_TRANSLATE_AGENT"
        )
        return pipeline_result, {
            "triggered": False,
            "reason_code": reason_code,
            "route_type": route_type,
            "route_target": route_target,
        }

    decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
    narrative = str(pipeline_result.get("narrative") or "").strip()
    detection = _detect_gateway_execution_deviation(
        source=source,
        user_text=user_text,
        decision=decision,
        narrative=narrative,
    )
    if not bool(detection.get("triggered")):
        return pipeline_result, detection

    now_ms = int(time.time() * 1000)
    detection_payload = dict(payload or {})
    detection_payload["deviation_reason"] = detection.get("reason_code")
    detection_payload["deviation_overlap_ratio"] = detection.get("overlap_ratio")
    detection_payload["deviation_keyword_count"] = detection.get("keyword_count")
    detection_payload["deviation_hit_count"] = detection.get("hit_count")
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=detection_payload,
        phase="DEVIATION_DETECTED",
        status="DEGRADED",
        reason_code=str(detection.get("reason_code") or "DEVIATION_DETECTED"),
        detail=(
            f"overlap={detection.get('overlap_ratio')} "
            f"keywords={detection.get('keyword_count')} hits={detection.get('hit_count')}"
        ),
        ts_ms=now_ms,
    )

    notice_result = await _maybe_send_gateway_realign_notice(
        request_id=request_id,
        payload=payload,
        ports=ports,
    )
    detection["notice"] = notice_result

    if not get_input_settings().gateway_deviation_retry_enabled:
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_DISABLED"
        return pipeline_result, detection

    try:
        retry_message = _build_realign_user_message(
            original_user_text=user_text,
            first_narrative=narrative,
            render_target_lang=str(payload.get("render_target_lang") or "zh"),
        )
        retry_result = await _run_cortex_orchestrator_pipeline(
            request_id=f"{request_id}::realign",
            session_id=session_id,
            source=source,
            text=retry_message,
            payload=payload,
            ts_ms=int(time.time() * 1000),
            ports=ports,
        )
    except Exception as exc:
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_FAILED"
        detection["retry_error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
        return pipeline_result, detection

    retry_decision = str(retry_result.get("decision") or "REJECT").strip().upper()
    retry_narrative = str(retry_result.get("narrative") or "").strip()
    retry_overlap, retry_kw_count, retry_hit_count = _compute_keyword_overlap(
        source_text=user_text,
        target_text=retry_narrative,
    )
    min_overlap = float(
        detection.get("min_overlap")
        or get_input_settings().gateway_deviation_min_overlap
    )
    retry_ok = retry_decision == "APPROVE" and bool(retry_narrative) and retry_overlap >= min_overlap
    detection["retry_applied"] = retry_ok
    detection["retry_overlap_ratio"] = retry_overlap
    detection["retry_keyword_count"] = retry_kw_count
    detection["retry_hit_count"] = retry_hit_count

    if retry_ok:
        detection["resolved_decision"] = retry_decision
        detection["resolved_reason_code"] = str(retry_result.get("reason_code") or "").strip() or None
        detection["resolved_narrative"] = retry_narrative
        if isinstance(retry_result.get("route_decision"), dict):
            detection["resolved_route_decision"] = dict(retry_result.get("route_decision") or {})
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "deviation_retry_overlap_ratio": retry_overlap},
            phase="DEVIATION_RECOVERED",
            status="OK",
            reason_code="DEVIATION_RECOVERED",
            detail=f"retry decision={retry_decision} overlap={retry_overlap}",
            ts_ms=int(time.time() * 1000),
        )
        return retry_result, detection
    return pipeline_result, detection
