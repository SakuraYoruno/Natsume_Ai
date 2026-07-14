"""Host input runtime responsibility extracted from the HTTP adapter."""

from .contracts_and_normalization import *  # noqa: F403
from .cortex_dispatch import _maybe_realign_gateway_pipeline, _run_cortex_orchestrator_pipeline
from .gateway_delivery import _deliver_gateway_reply
from .multimodal_progress import (
    _maybe_schedule_gateway_immediate_ack,
    _maybe_schedule_gateway_route_started_voice_ack,
    _run_gateway_proactive_progress_push,
    _should_start_gateway_progress_push,
    _stop_progress_push_task,
)

async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    command_authority: dict[str, Any] | None = None,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    ledger = _get_ledger_safe(ports=ports)
    source_upper = str(source or "").upper().strip()
    if source_upper == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            ignored_ts = int(time.time() * 1000)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="IGNORED",
                status="IGNORED",
                reason_code=reject_reason,
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ignored_ts,
            )
            _logger.info(
                "input_routes: dispatch ignored request_id=%s source=%s reason=%s",
                request_id,
                source_upper,
                reject_reason,
            )
            return {
                "decision": "REJECT",
                "reason_code": reject_reason,
                "narrative": "",
                "commit_event": {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": reject_reason,
                    "request_id": request_id,
                },
                "route_decision": {},
            }

    dispatch_mode = "governed.awaited"
    gateway_route_preview: dict[str, Any] | None = None
    if source_upper == "GATEWAY":
        gateway_route_preview = _build_gateway_route_preview(text)

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )
    if source_upper == "GATEWAY":
        _maybe_schedule_gateway_immediate_ack(
            request_id=request_id,
            session_id=session_id,
            source=source_upper,
            text=text,
            payload=payload,
            intent_draft=gateway_route_preview,
            ledger=ledger,
        )

    return await _dispatch_to_graph_worker(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
        ledger=ledger,
        dispatch_mode=dispatch_mode,
        governed_command_application_provider=(
            governed_command_application_provider
        ),
        command_authority=dict(command_authority or {}),
        ports=ports,
    )


async def _dispatch_to_graph_worker(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    dispatch_mode: str,
    run_in_thread: bool | None = None,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    command_authority: dict[str, Any] | None = None,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    """Run cortex orchestrator pipeline and optionally deliver Gateway reply."""

    del run_in_thread

    dispatch_ts = int(time.time() * 1000)
    source_upper = str(source or "").strip().upper()
    progress_push_stop_event: asyncio.Event | None = None
    progress_push_task: asyncio.Task | None = None
    thalamus_intent_draft: dict[str, Any] | None = None
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="DISPATCHED",
        status="DISPATCHED",
        detail=f"dispatch_mode={dispatch_mode} source={source}",
        ts_ms=dispatch_ts,
    )
    try:
        thalamus_intent_draft = await _build_input_thalamus_intent_draft(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
    except Exception as intent_exc:
        _logger.warning("[%s] thalamus intent draft build degraded before input dispatch: %s", request_id, intent_exc)
        thalamus_intent_draft = None

    if source_upper == "GATEWAY":
        try:
            _maybe_schedule_gateway_route_started_voice_ack(
                request_id=request_id,
                session_id=session_id,
                source=source_upper,
                payload=payload,
                route_type=str((thalamus_intent_draft or {}).get("route") or "").strip().lower(),
                target_id=str((thalamus_intent_draft or {}).get("target_id") or "").strip(),
                ledger=ledger,
            )
        except Exception as route_ack_exc:
            _logger.debug("[%s] gateway route-started ack intent draft failed: %s", request_id, route_ack_exc)
    if source_upper == "GATEWAY":
        should_push, push_reason = _should_start_gateway_progress_push(
            source=source_upper,
            text=text,
            payload=payload,
            intent_draft=thalamus_intent_draft,
        )
        if should_push:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="PROGRESS_PUSH_PENDING",
                status="ACCEPTED",
                detail="gateway proactive progress push scheduled",
                ts_ms=dispatch_ts,
            )
            progress_push_stop_event = asyncio.Event()
            progress_push_task = asyncio.create_task(
                _run_gateway_proactive_progress_push(
                    request_id=request_id,
                    session_id=session_id,
                    source=source,
                    text=text,
                    payload=payload,
                    ledger=ledger,
                    stop_event=progress_push_stop_event,
                    ports=ports,
                )
            )
            progress_push_task.add_done_callback(_log_progress_push_task_exception)
        else:
            if push_reason != "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY":
                _log_input_event(
                    ledger=ledger,
                    request_id=request_id,
                    session_id=session_id,
                    source=source,
                    input_payload={**dict(payload or {}), "progress_push_reason": push_reason},
                    phase="PROGRESS_PUSH_SKIPPED",
                    status="IGNORED",
                    reason_code=push_reason,
                    detail=f"gateway proactive progress push skipped: {push_reason}",
                    ts_ms=dispatch_ts,
                )
    if not await _ensure_gateway_multimodal_observed_for_dispatch(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
        ledger=ledger,
        ports=ports,
    ):
        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="DISPATCH_DEFERRED",
            status="ACCEPTED",
            reason_code="GATEWAY_MULTIMODAL_ASYNC_DEFERRED",
            detail="gateway multimodal dispatch deferred to async job",
            ts_ms=int(time.time() * 1000),
        )
        raise RuntimeError("gateway_multimodal_observation_incomplete")
    try:
        pipeline_result = await _run_cortex_orchestrator_pipeline(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
            intent_draft_override=thalamus_intent_draft,
            governed_command_application_provider=(
                governed_command_application_provider
            ),
            command_authority=dict(command_authority or {}),
            ports=ports,
        )
        deviation_meta: dict[str, Any] | None = None
        if source_upper == "GATEWAY":
            pipeline_result, deviation_meta = await _maybe_realign_gateway_pipeline(
                request_id=request_id,
                session_id=session_id,
                source=source,
                user_text=text,
                payload=payload,
                ts_ms=ts_ms,
                pipeline_result=pipeline_result,
                ledger=ledger,
                ports=ports,
            )
        decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
        reason_code = str(pipeline_result.get("reason_code") or "").strip() or None
        narrative = str(pipeline_result.get("narrative") or "").strip()
        route_decision = dict(pipeline_result.get("route_decision") or {})
        if isinstance(deviation_meta, dict) and bool(deviation_meta.get("retry_applied")):
            override_decision = str(deviation_meta.get("resolved_decision") or "").strip().upper()
            if override_decision:
                decision = override_decision
            if "resolved_reason_code" in deviation_meta:
                reason_code = str(deviation_meta.get("resolved_reason_code") or "").strip() or None
            override_narrative = str(deviation_meta.get("resolved_narrative") or "").strip()
            if override_narrative:
                narrative = override_narrative
            override_route_decision = deviation_meta.get("resolved_route_decision")
            if isinstance(override_route_decision, dict):
                route_decision = dict(override_route_decision)
        route_type = str(route_decision.get("route") or "chat").strip().lower() or "chat"
        route_target = str(route_decision.get("target_id") or "").strip()
        route_task = str(route_decision.get("task") or "").strip()
        route_source = str(route_decision.get("source") or "").strip() or None
        route_reason = str(route_decision.get("reason_code") or "").strip() or None
        route_override_reason = str(route_decision.get("override_reason") or "").strip() or None
        route_confidence = _normalize_route_confidence(route_decision.get("confidence"))
        route_llm_used = bool(route_decision.get("llm_route_used"))

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={
                **dict(payload or {}),
                "route_type": route_type,
                "route_target": route_target,
                "route_task": route_task,
                "route_source": route_source,
                "route_reason": route_reason,
                "route_confidence": route_confidence,
                "route_override_reason": route_override_reason,
                "llm_route_used": route_llm_used,
            },
            phase="ROUTE_DECIDED",
            status="OK",
            reason_code=route_reason,
            detail=(
                f"route={route_type} target={route_target or '-'} "
                f"source={route_source or '-'} llm_used={str(route_llm_used).lower()}"
            ),
            ts_ms=int(time.time() * 1000),
        )

        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        progress_push_stop_event = None
        progress_push_task = None

        gateway_delivery: dict[str, Any] | None = None
        if source == "GATEWAY":
            # Phase XV P4：从 pipeline_result 提取情感和 Bond 上下文
            commit_event = pipeline_result.get("commit_event") or {}
            audit_refs = commit_event.get("audit_refs") or {}
            _emotion_tag = str(audit_refs.get("emotion_tag") or "NEUTRAL").strip().upper()
            _tts_text_ja = str(audit_refs.get("tts_text_ja") or "").strip() or None
            _response_form = str(audit_refs.get("response_form") or "").strip() or None
            _response_form_reason = str(audit_refs.get("response_form_reason") or "").strip() or None
            # bond_stage 从 payload 的 bond_summary 取（若存在），否则用 FRIEND
            _bond_stage = str((payload.get("bond_summary") or {}).get("stage") or "FRIEND").strip().upper()
            _user_msg_type = str(payload.get("msg_type") or "text").strip().lower()

            try:
                gateway_delivery = await _deliver_gateway_reply(
                    request_id=request_id,
                    payload=payload,
                    decision=decision,
                    reason_code=reason_code,
                    narrative=narrative,
                    tts_text_ja=_tts_text_ja,
                    emotion_tag=_emotion_tag,
                    bond_stage=_bond_stage,
                    user_msg_type=_user_msg_type,
                    response_form=_response_form,
                    response_form_reason=_response_form_reason,
                    reply_envelope_payload=dict(audit_refs.get("reply_envelope") or commit_event.get("reply_envelope") or {}),
                    ports=ports,
                )
            except Exception as exc:
                gateway_delivery = {
                    "success": False,
                    "status": "FAILED",
                    "reason_code": "GATEWAY_REPLY_EXCEPTION",
                    "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
                    "latency_ms": 0,
                    "reply_text_len": 0,
                    "reply_preview": "",
                    "reply_form": "text",
                    "reply_form_reason": "exception",
                    "voice_sent": False,
                }

        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        completion_status = "OK" if decision == "APPROVE" else "DEGRADED"
        completion_reason_code = reason_code
        detail_parts = [
            f"cortex_orchestrator decision={decision}",
            f"mode={dispatch_mode}",
            f"latency_ms={latency_ms}",
        ]
        if reason_code:
            detail_parts.append(f"reason_code={reason_code}")
        detail_parts.append(f"route_type={route_type}")
        if route_target:
            detail_parts.append(f"route_target={route_target}")
        if route_source:
            detail_parts.append(f"route_source={route_source}")
        if route_reason:
            detail_parts.append(f"route_reason={route_reason}")
        if route_override_reason:
            detail_parts.append(f"route_override_reason={route_override_reason}")
        if route_confidence is not None:
            detail_parts.append(f"route_confidence={route_confidence:.3f}")
        detail_parts.append(f"route_llm_used={str(route_llm_used).lower()}")
        if isinstance(deviation_meta, dict):
            detail_parts.append(f"deviation_reason={deviation_meta.get('reason_code')}")
            detail_parts.append(f"deviation_retry_applied={bool(deviation_meta.get('retry_applied'))}")
            if deviation_meta.get("overlap_ratio") is not None:
                detail_parts.append(f"deviation_overlap={deviation_meta.get('overlap_ratio')}")
            if deviation_meta.get("retry_overlap_ratio") is not None:
                detail_parts.append(f"deviation_retry_overlap={deviation_meta.get('retry_overlap_ratio')}")
        if gateway_delivery is not None:
            gateway_success = bool(gateway_delivery.get("success", True))
            gateway_reason_code = str(gateway_delivery.get("reason_code") or "")
            gateway_detail = str(gateway_delivery.get("detail") or "")
            if not gateway_success:
                degrade_on_gateway_failure = (
                    get_input_settings().gateway_reply_failure_degrade_input
                )
                if degrade_on_gateway_failure:
                    completion_status = "DEGRADED"
                    if not completion_reason_code:
                        completion_reason_code = gateway_reason_code or "GATEWAY_REPLY_DELIVERY_FAILED"
                else:
                    detail_parts.append("gateway_failure_ignored=true")
            detail_parts.append(f"gateway_success={str(gateway_success).lower()}")
            detail_parts.append(f"gateway_status={gateway_delivery.get('status')}")
            detail_parts.append(f"gateway_latency_ms={gateway_delivery.get('latency_ms')}")
            detail_parts.append(f"reply_text_len={gateway_delivery.get('reply_text_len')}")
            detail_parts.append(f"reply_preview={gateway_delivery.get('reply_preview')}")
            detail_parts.append(f"reply_form={gateway_delivery.get('reply_form')}")
            detail_parts.append(f"reply_form_reason={gateway_delivery.get('reply_form_reason')}")
            detail_parts.append(f"gateway_voice_sent={str(bool(gateway_delivery.get('voice_sent'))).lower()}")
            if gateway_reason_code:
                detail_parts.append(f"gateway_reason_code={gateway_reason_code}")
            if gateway_detail:
                detail_parts.append(f"gateway_detail={_build_reply_preview(gateway_detail, limit=160)}")
            if gateway_delivery.get("voice_waited") is not None:
                detail_parts.append(
                    f"gateway_voice_waited={str(bool(gateway_delivery.get('voice_waited'))).lower()}"
                )
            if gateway_delivery.get("voice_success") is not None:
                detail_parts.append(
                    f"gateway_voice_success={str(bool(gateway_delivery.get('voice_success'))).lower()}"
                )
            if gateway_delivery.get("voice_status"):
                detail_parts.append(f"gateway_voice_status={gateway_delivery.get('voice_status')}")
            if gateway_delivery.get("voice_reason_code"):
                detail_parts.append(f"gateway_voice_reason_code={gateway_delivery.get('voice_reason_code')}")
            if gateway_delivery.get("voice_detail"):
                detail_parts.append(
                    f"gateway_voice_detail={_build_reply_preview(str(gateway_delivery.get('voice_detail')), limit=160)}"
                )
            if gateway_delivery.get("voice_latency_ms") is not None:
                detail_parts.append(f"gateway_voice_latency_ms={gateway_delivery.get('voice_latency_ms')}")
            if gateway_delivery.get("voice_started") is not None:
                detail_parts.append(
                    f"gateway_voice_started={str(bool(gateway_delivery.get('voice_started'))).lower()}"
                )
            if gateway_delivery.get("voice_vts_started") is not None:
                detail_parts.append(
                    f"gateway_voice_vts_started={str(bool(gateway_delivery.get('voice_vts_started'))).lower()}"
                )
            if gateway_delivery.get("voice_vts_expression_success") is not None:
                detail_parts.append(
                    "gateway_voice_vts_expression_success="
                    f"{str(bool(gateway_delivery.get('voice_vts_expression_success'))).lower()}"
                )

        execution_quality = {
            "degraded": False,
            "reason_code": None,
            "detail_tags": [],
            "skill_exec_count": 0,
        }
        if (
            get_input_settings().execution_quality_gate_enabled
            and isinstance(pipeline_result.get("commit_event"), dict)
        ):
            execution_quality = _analyze_request_execution_quality(
                ledger=ledger,
                request_id=request_id,
                decision=decision,
                route_type=route_type,
                route_target=route_target,
            )
            if bool(execution_quality.get("degraded")):
                completion_status = "DEGRADED"
                if not completion_reason_code:
                    completion_reason_code = str(
                        execution_quality.get("reason_code") or "EXECUTION_QUALITY_DEGRADED"
                    )
            detail_parts.append(f"exec_quality_degraded={str(bool(execution_quality.get('degraded'))).lower()}")
            if execution_quality.get("reason_code"):
                detail_parts.append(f"exec_quality_reason_code={execution_quality.get('reason_code')}")
            for tag in list(execution_quality.get("detail_tags") or ()):
                if str(tag or "").strip():
                    detail_parts.append(f"exec_quality_tag={tag}")

        completed_payload = dict(payload or {})
        completed_payload["decision"] = decision
        if completion_reason_code:
            completed_payload["reason_code"] = completion_reason_code
        completed_payload["route_type"] = route_type
        completed_payload["route_target"] = route_target
        completed_payload["route_task"] = route_task
        completed_payload["route_source"] = route_source
        completed_payload["route_reason"] = route_reason
        completed_payload["route_confidence"] = route_confidence
        completed_payload["route_override_reason"] = route_override_reason
        completed_payload["llm_route_used"] = route_llm_used
        completed_payload["exec_quality_degraded"] = bool(execution_quality.get("degraded"))
        completed_payload["exec_quality_reason_code"] = execution_quality.get("reason_code")
        completed_payload["exec_quality_tags"] = list(execution_quality.get("detail_tags") or [])
        completed_payload["exec_quality_skill_exec_count"] = int(execution_quality.get("skill_exec_count") or 0)
        if isinstance(deviation_meta, dict):
            completed_payload["deviation_reason"] = deviation_meta.get("reason_code")
            completed_payload["deviation_overlap_ratio"] = deviation_meta.get("overlap_ratio")
            completed_payload["deviation_retry_applied"] = bool(deviation_meta.get("retry_applied"))
            completed_payload["deviation_retry_overlap_ratio"] = deviation_meta.get("retry_overlap_ratio")
        if gateway_delivery is not None:
            completed_payload["gateway_success"] = bool(gateway_delivery.get("success", True))
            completed_payload["gateway_status"] = gateway_delivery.get("status")
            completed_payload["gateway_reason_code"] = gateway_delivery.get("reason_code")
            completed_payload["gateway_detail"] = gateway_delivery.get("detail")
            completed_payload["gateway_latency_ms"] = gateway_delivery.get("latency_ms")
            completed_payload["reply_text_len"] = gateway_delivery.get("reply_text_len")
            completed_payload["reply_preview"] = gateway_delivery.get("reply_preview")
            completed_payload["reply_form"] = gateway_delivery.get("reply_form")
            completed_payload["reply_form_reason"] = gateway_delivery.get("reply_form_reason")
            completed_payload["voice_sent"] = bool(gateway_delivery.get("voice_sent"))
            completed_payload["gateway_voice_waited"] = gateway_delivery.get("voice_waited")
            completed_payload["gateway_voice_success"] = gateway_delivery.get("voice_success")
            completed_payload["gateway_voice_status"] = gateway_delivery.get("voice_status")
            completed_payload["gateway_voice_reason_code"] = gateway_delivery.get("voice_reason_code")
            completed_payload["gateway_voice_detail"] = gateway_delivery.get("voice_detail")
            completed_payload["gateway_voice_latency_ms"] = gateway_delivery.get("voice_latency_ms")
            completed_payload["gateway_voice_started"] = gateway_delivery.get("voice_started")
            completed_payload["gateway_voice_vts_started"] = gateway_delivery.get("voice_vts_started")
            completed_payload["gateway_voice_vts_expression_success"] = gateway_delivery.get(
                "voice_vts_expression_success"
            )
            completed_payload["gateway_voice_event"] = gateway_delivery.get("voice_event")

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=completed_payload,
            phase="COMPLETED",
            status=completion_status,
            reason_code=completion_reason_code,
            detail=" ".join(detail_parts),
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: dispatch completed request_id=%s source=%s decision=%s latency_ms=%d mode=%s",
            request_id,
            source,
            decision,
            latency_ms,
            dispatch_mode,
        )
        return pipeline_result
    except Exception as exc:
        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        fail_ts = int(time.time() * 1000)
        latency_ms = fail_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="FAILED",
            status="FAILED",
            reason_code="INPUT_GRAPH_ERROR",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=latency_ms,
            ts_ms=fail_ts,
        )
        _logger.error("input_routes: dispatch failed request_id=%s: %s", request_id, exc)
        raise


def create_input_router(
    *,
    application_ports: InputRuntimeApplicationPorts,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
) -> APIRouter:
    """Create an input router whose governed application is closure-injected."""

    from .ingress import receive_input

    input_router = APIRouter(prefix="/v1/host", tags=["input"])

    @input_router.post("/input", response_model=InputResponse)
    async def receive_host_input(
        body: InputPayload,
        request: Request,
    ) -> InputResponse:
        return await receive_input(
            body,
            request,
            governed_command_application_provider=(
                governed_command_application_provider
            ),
            ports=application_ports,
        )

    return input_router
