"""将已准入 Host 输入交给 Cortex：规范化感知线索并收敛治理执行结果。

本层可为路由与情绪融合准备受限上下文，但不把 Edge 遥测提升为用户自述，也不自行
授予命令权限；最终准入、持久化与副作用收据仍由治理命令应用和 Task Runtime 负责。
"""

from collections.abc import Mapping
import math
from typing import Any

from host_brain.agent_runtime.skill_ids import is_agent_runtime_skill
from host_brain.core.emotion_constants import (
    VALID_EMOTION_TAGS,
    normalize_emotion_tag,
)

from .contracts_and_normalization import *  # noqa: F403


_ASR_UNTRUSTED_EMOTION_SOURCES = frozenset(
    {
        "",
        "asr_text",
        "asr_text_bootstrap",
        "asr_text_unavailable",
        "inferred",
        "runtime_unknown",
        "unknown",
    }
)
_ASR_ACOUSTIC_MODALITIES = frozenset(
    {"acoustic", "audio", "voice", "voice_acoustic"}
)
_AFFECT_OBSERVATION_KEYS = frozenset(
    {"user_affect_observations", "affect_observations"}
)


def _finite_unit(value: Any) -> float | None:
    """Return one finite unit scalar, or reject an untrusted edge value."""

    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved):
        return None
    return max(0.0, min(1.0, resolved))


def _edge_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _trusted_asr_acoustic_observation(
    *,
    payload: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any] | None:
    """Translate one attested acoustic signal into a user observation.

    An Edge ASR event is transport telemetry, not a user self-report.  The
    former keyword-only ASR inference therefore must not be copied into
    ``ChatRequest.emotion_tag``: Fusion interprets that field as an explicit
    user observation.  Only an available, non-degraded acoustic model gets a
    typed, short-lived ``ACOUSTIC`` observation.  Its raw source material is
    intentionally absent from the returned mapping.
    """

    source = str(payload.get("emotion_source") or "").strip().lower()
    modality = str(payload.get("emotion_modality") or "").strip().lower()
    if (
        source in _ASR_UNTRUSTED_EMOTION_SOURCES
        or modality not in _ASR_ACOUSTIC_MODALITIES
        or not _edge_flag(payload.get("acoustic_emotion_available"))
        or _edge_flag(payload.get("emotion_inference_degraded"))
    ):
        return None

    raw_tag = str(payload.get("emotion_tag") or "").strip()
    tag = normalize_emotion_tag(raw_tag)
    if not raw_tag or tag not in VALID_EMOTION_TAGS:
        return None
    intensity = _finite_unit(payload.get("emotion_intensity"))
    confidence = _finite_unit(payload.get("emotion_confidence"))
    if intensity is None or confidence is None or confidence <= 0.0:
        return None

    observed_at_ms = max(0, int(ts_ms))
    # DL-02 fixes the acoustic TTL at five seconds.  The journal reducer and
    # Fusion service enforce the same bound again; passing both timestamps
    # prevents a delayed bridge job from making an old microphone result fresh.
    return {
        "modality": "ACOUSTIC",
        "emotion_distribution": {tag: max(0.05, intensity)},
        "confidence": confidence,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": observed_at_ms + 5_000,
        "provider": f"edge-acoustic:{source[:64]}",
    }


def _prepare_input_affect_handoff(
    *,
    source: str,
    payload: Mapping[str, Any],
    ts_ms: int,
) -> tuple[str | None, Any, dict[str, Any], dict[str, Any] | None]:
    """Return request affect fields without upgrading Edge telemetry to self-report.

    Non-ASR ingress retains the public ``ChatRequest`` compatibility fields.
    ASR ingress is stricter: arbitrary Edge ``emotion_tag``, ``emotion_context``
    and supplied affect lists are untrusted.  A valid acoustic attestation is
    reintroduced only as one bounded multimodal user observation.
    """

    multimodal_raw = payload.get("multimodal_observations")
    multimodal = (
        dict(multimodal_raw) if isinstance(multimodal_raw, Mapping) else {}
    )
    if str(source or "").strip().upper() != "ASR":
        emotion_context_raw = payload.get("emotion_context")
        return (
            str(payload.get("emotion_tag") or "").strip() or None,
            payload.get("emotion_intensity"),
            multimodal,
            (
                dict(emotion_context_raw)
                if isinstance(emotion_context_raw, Mapping)
                else None
            ),
        )

    for key in _AFFECT_OBSERVATION_KEYS:
        multimodal.pop(key, None)
    observation = _trusted_asr_acoustic_observation(
        payload=payload,
        ts_ms=ts_ms,
    )
    if observation is not None:
        multimodal["user_affect_observations"] = [observation]
    # A sensor result is neither a self-report nor a trusted internal hand-off.
    # Keeping no separate emotion_context here closes both spoofing paths.
    return None, None, multimodal, None

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
    from host_brain.runtime.conversation_scope import (  # noqa: PLC0415
        GatewayConversationScopeError,
        SecureGatewayConversationScopeV1,
    )
    ChatRequest = ports.chat_request_factory
    cortex_orchestrator_sse_generator = ports.cortex_orchestrator_sse_generator
    from host_brain.governor.route_token import (  # noqa: PLC0415
        RouteTokenDisposition,
        validate_governor_route_token,
    )

    # Verify the Secure Ingress envelope *before* any affect or relationship
    # consumer can open a scope.  The old ordering admitted user observations
    # under a legacy default identity before checking the governed principal.
    governed_authority: dict[str, Any] | None = None
    if governed_command_application_provider is not None:
        governed_authority = _require_governed_command_authority(
            command_authority,
        )

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    attachments = list(payload.get("attachments") or [])
    (
        request_emotion_tag,
        request_emotion_intensity,
        multimodal_observations,
        request_emotion_context,
    ) = _prepare_input_affect_handoff(
        source=source,
        payload=payload,
        ts_ms=ts_ms,
    )
    message_text = str(text or "").strip()
    source_upper = str(source or "").strip().upper()
    workspace_id: str | None
    gateway_scope_required = bool(
        governed_command_application_provider is not None
        or command_authority
        or payload.get("secure_gateway_conversation_scope_v1") is not None
    )
    if source_upper == "GATEWAY" and gateway_scope_required:
        try:
            raw_scope = payload.get("secure_gateway_conversation_scope_v1")
            if not isinstance(raw_scope, dict):
                raise GatewayConversationScopeError(
                    "GATEWAY_CONVERSATION_SCOPE_PAYLOAD_REQUIRED"
                )
            gateway_scope = SecureGatewayConversationScopeV1.from_dict(raw_scope)
            gateway_scope.assert_bound_to_authority(
                governed_authority or command_authority
            )
        except GatewayConversationScopeError as exc:
            # This occurs before AffectFusion/Appraisal.  A signed external
            # sender must never fall back to the single-user Bond default.
            raise RuntimeError(str(exc) or "GATEWAY_CONVERSATION_SCOPE_INVALID") from exc
        if session_id != gateway_scope.session_id:
            raise RuntimeError("GATEWAY_CONVERSATION_SCOPE_SESSION_DRIFT")
        person_id = gateway_scope.relationship_subject_id
        workspace_id = gateway_scope.workspace_id
    else:
        # The only production Gateway entrypoint is ``receive_input``, which
        # always supplies the signed scope above.  This branch preserves old
        # isolated/offline unit adapters that call this internal helper
        # directly without a Secure Ingress envelope; it is never a substitute
        # for the authenticated transport boundary.
        person_id = resolve_person_id(
            str(payload.get("person_id") or "").strip() or None,
            session_id=session_id,
            platform=reply_channel,
            from_id=str(payload.get("from_id") or "").strip() or None,
            conversation_id=reply_target,
        )
        workspace_id = str(payload.get("workspace_id") or "").strip() or None
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
        workspace_id=workspace_id,
        person_id=person_id,
        session_id=session_id,
        emotion_tag=request_emotion_tag,
        emotion_intensity=request_emotion_intensity,
        emotion_context=request_emotion_context,
        input_source=source,
        reply_channel=reply_channel,
        reply_target=reply_target,
        # Only the ungoverned compatibility delivery keeps its old Gateway
        # suppression to avoid duplicating ``_deliver_gateway_reply``. A
        # governed Chat preserves the frozen ExpressionPlan through Task
        # Acceptance, where the Host-owned outbox admits its Voice/Avatar pair;
        # transport source alone is not a suppression authority.
        suppress_edge_tts=(
            source_upper == "GATEWAY"
            and governed_command_application_provider is None
        ),
        suppress_edge_vts=False,
        # 语言只由 response_form/render_profile 决定；Gateway/QQ/Web/API 只是传输来源。
        render_target_lang=render_target_lang,
        render_profile=resolved_render_profile,
        response_form=response_form,
        attachments=list(attachments or []),
        multimodal_observations=dict(multimodal_observations or {}),
        attachment_summary=str(payload.get("attachment_summary") or "").strip(),
    )
    emotion_context = resolve_chat_emotion_context(
        request_body=request_body,
        request_id=request_id,
    )
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
    # request/person context, while the former is already verified above and
    # is now frozen into the Governor decision for exact node grant derivation.
    if governed_authority is not None:
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
) -> dict[str, Any]:
    """Validate the exact Secure Ingress identity before Governor admission."""

    raw_authority = dict(command_authority or {})
    authority: dict[str, Any] = {
        key: str(raw_authority.get(key) or "").strip()
        for key in (
            "principal_id",
            "tenant_id",
            "claim_ref",
            "audit_ref",
            "body_sha256",
        )
    }
    missing = [key for key, value in authority.items() if not value]
    if missing:
        raise RuntimeError(
            "governed_command_authority_incomplete:" + ",".join(missing)
        )
    raw_scope = raw_authority.get("secure_gateway_conversation_scope_v1")
    if raw_scope is not None:
        from host_brain.runtime.conversation_scope import (  # noqa: PLC0415
            GatewayConversationScopeError,
            SecureGatewayConversationScopeV1,
        )

        try:
            if not isinstance(raw_scope, dict):
                raise GatewayConversationScopeError(
                    "GATEWAY_CONVERSATION_SCOPE_AUTHORITY_INVALID"
                )
            scope = SecureGatewayConversationScopeV1.from_dict(raw_scope)
            scope.assert_bound_to_authority(authority)
        except GatewayConversationScopeError as exc:
            raise RuntimeError(str(exc) or "GATEWAY_CONVERSATION_SCOPE_INVALID") from exc
        authority["secure_gateway_conversation_scope_v1"] = scope.to_dict()
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
