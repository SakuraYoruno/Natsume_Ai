"""Host input runtime responsibility extracted from the HTTP adapter."""

from .contracts_and_normalization import *  # noqa: F403
from .cortex_dispatch import _run_cortex_orchestrator_pipeline
from .gateway_delivery import _deliver_gateway_reply

async def _try_handle_gateway_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
    ts_ms: int,
    ports: InputRuntimeApplicationPorts,
) -> bool:
    del request_id, session_id, source, text, payload, ledger, ts_ms, ports
    return False
    if not _gateway_auto_reply_enabled():
        return False
    if str(source or "").strip().upper() != "GATEWAY":
        return False
    if not get_input_settings().gateway_progress_fastpath_enabled:
        return False
    if not _looks_like_progress_query_for_ack_skip(text):
        return False

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_TARGET_MISSING"},
            phase="PROGRESS_FASTPATH_SKIPPED",
            status="IGNORED",
            reason_code="PROGRESS_TARGET_MISSING",
            detail="gateway progress fastpath skipped: missing platform/conversation_id",
            ts_ms=ts_ms,
        )
        return False

    try:
        from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415
    except Exception:
        return False

    try:
        reply_text, reason, _summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
        payload=payload,
        exclude_request_id=request_id,
        ports=ports,
    )
    except Exception as exc:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_BUILD_FAILED"},
            phase="PROGRESS_FASTPATH_FAILED",
            status="FAILED",
            reason_code="PROGRESS_BUILD_FAILED",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            ts_ms=int(time.time() * 1000),
        )
        return False

    settings = get_input_settings()
    wait_result = settings.gateway_progress_fastpath_wait_result
    timeout_ms = settings.gateway_progress_fastpath_timeout_ms
    started_ms = int(time.time() * 1000)
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=reply_text,
        msg_type="text",
        request_id=request_id,
        raw_extra={"message_kind": "transport_ack", "reply_source": "progress_fastpath"},
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    success = bool(getattr(result, "success", False))
    finished_ms = int(time.time() * 1000)
    latency_ms = max(0, finished_ms - started_ms)
    payload_with_result = dict(payload or {})
    payload_with_result["progress_fastpath_status"] = str(getattr(result, "status", "UNKNOWN"))
    payload_with_result["progress_fastpath_latency_ms"] = int(getattr(result, "latency_ms", 0))
    payload_with_result["progress_fastpath_preview"] = _build_reply_preview(reply_text, limit=100)
    payload_with_result["progress_fastpath_reason"] = reason

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload_with_result,
        phase="PROGRESS_FASTPATH",
        status=("OK" if success else "DEGRADED"),
        reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_REPLY_FAILED")),
        detail=f"gateway progress fastpath status={getattr(result, 'status', 'UNKNOWN')} success={success}",
        latency_ms=latency_ms,
        ts_ms=finished_ms,
    )
    return success


def _log_multimodal_job_task_exception(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - defensive callback
        _logger.error("input_routes: multimodal async job task failed: %s", exc)


def _schedule_gateway_multimodal_async_job(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    create_multimodal_job = ports.create_multimodal_job

    attachments = list(payload.get("attachments") or [])
    job = create_multimodal_job(
        request_id=request_id,
        conversation_id=str(payload.get("conversation_id") or "").strip(),
        platform=str(payload.get("platform") or "").strip(),
        job_type="gateway_multimodal_async",
        payload={
            "attachment_count": len(attachments),
            "attachment_types": [str(item.get("type") or "") for item in attachments],
        },
    )

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload={**dict(payload or {}), "multimodal_job": job},
        phase="MULTIMODAL_ASYNC_ACCEPTED",
        status="ACCEPTED",
        detail=f"gateway multimodal async accepted job_id={job['job_id']}",
        ts_ms=ts_ms,
    )

    async def _runner() -> None:
        await _run_gateway_multimodal_async_job(
            job_id=str(job["job_id"]),
            request_id=request_id,
            session_id=session_id,
            text=text,
            payload=dict(payload or {}),
            ledger=ledger,
            ports=ports,
        )

    asyncio.create_task(_runner()).add_done_callback(_log_multimodal_job_task_exception)
    return job


async def _run_gateway_multimodal_async_job(
    *,
    job_id: str,
    request_id: str,
    session_id: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
    ports: InputRuntimeApplicationPorts,
) -> None:
    from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415

    get_multimodal_observer = ports.get_multimodal_observer
    update_multimodal_job = ports.update_multimodal_job

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    attachments = list(payload.get("attachments") or [])
    ack_text = ""
    if _gateway_auto_reply_enabled() and platform and conversation_id and ack_text:
        try:
            await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content=ack_text,
                msg_type="text",
                request_id=request_id,
                raw_extra={"message_kind": "transport_ack", "reply_source": "multimodal_async_ack"},
                wait_result=True,
                timeout_ms=6000,
            )
        except Exception as exc:
            _logger.debug("gateway multimodal async ack degraded request_id=%s err=%s", request_id, exc)

    update_multimodal_job(job_id, status="RUNNING")
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source="GATEWAY",
        input_payload={**dict(payload or {}), "multimodal_job_id": job_id},
        phase="MULTIMODAL_ASYNC_RUNNING",
        status="RUNNING",
        detail=f"gateway multimodal async running job_id={job_id}",
        ts_ms=int(time.time() * 1000),
    )

    try:
        observer_result = await asyncio.to_thread(
            get_multimodal_observer().observe,
            request_id=request_id,
            platform=platform,
            conversation_id=conversation_id,
            user_text=text,
            attachments=attachments,
            render_target_lang="zh",
            processing_mode_hint="background",
        )
        payload["multimodal_observations"] = dict(observer_result.get("observations") or {})
        payload["attachment_summary"] = str(observer_result.get("summary_for_chat") or "").strip()
        payload["attachment_processing_mode"] = "async"
        payload["multimodal_confidence"] = float(observer_result.get("confidence") or 0.0)
        payload["multimodal_followup_required"] = bool(observer_result.get("followup_required"))
        payload["multimodal_artifacts"] = dict(observer_result.get("artifacts") or {})
        _remember_recent_gateway_multimodal(session_id=session_id, payload=payload)
        update_multimodal_job(
            job_id,
            status="OBSERVED",
            result={
                "summary_for_chat": payload["attachment_summary"],
                "observed_types": list((payload["multimodal_observations"] or {}).get("observed_types") or []),
            },
        )

        pipeline_result = await _run_cortex_orchestrator_pipeline(
            request_id=request_id,
            session_id=session_id,
            source="GATEWAY",
            text=text,
            payload=payload,
            ts_ms=int(time.time() * 1000),
            ports=ports,
        )
        commit_event = dict(pipeline_result.get("commit_event") or {})
        audit_refs = dict(commit_event.get("audit_refs") or {})
        _response_form = str(audit_refs.get("response_form") or "").strip() or None
        _response_form_reason = str(audit_refs.get("response_form_reason") or "").strip() or None
        gateway_delivery = await _deliver_gateway_reply(
            request_id=request_id,
            payload=payload,
            decision=str(pipeline_result.get("decision") or "REJECT"),
            reason_code=str(pipeline_result.get("reason_code") or "").strip() or None,
            narrative=str(pipeline_result.get("narrative") or "").strip(),
            tts_text_ja=str(audit_refs.get("tts_text_ja") or "").strip() or None,
            emotion_tag=str(audit_refs.get("emotion_tag") or "NEUTRAL").strip().upper(),
            bond_stage=str((payload.get("bond_summary") or {}).get("stage") or "FRIEND").strip().upper(),
            user_msg_type=str(payload.get("msg_type") or "text").strip().lower(),
            response_form=_response_form,
            response_form_reason=_response_form_reason,
            reply_envelope_payload=dict(audit_refs.get("reply_envelope") or commit_event.get("reply_envelope") or {}),
            ports=ports,
        )
        update_multimodal_job(job_id, status="COMPLETED", result={"gateway_delivery": gateway_delivery})
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source="GATEWAY",
            input_payload={**dict(payload or {}), "multimodal_job_id": job_id},
            phase="MULTIMODAL_ASYNC_COMPLETED",
            status=("OK" if gateway_delivery.get("success") else "DEGRADED"),
            reason_code=str(gateway_delivery.get("reason_code") or "").strip() or None,
            detail=f"gateway multimodal async completed job_id={job_id}",
            ts_ms=int(time.time() * 1000),
        )
    except Exception as exc:
        update_multimodal_job(job_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source="GATEWAY",
            input_payload={**dict(payload or {}), "multimodal_job_id": job_id},
            phase="MULTIMODAL_ASYNC_FAILED",
            status="FAILED",
            reason_code="GATEWAY_MULTIMODAL_ASYNC_FAILED",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            ts_ms=int(time.time() * 1000),
        )
        if platform and conversation_id:
            templates = _load_gateway_multimodal_runtime_texts_localized(render_target_lang="zh")
            failure_text = str(templates.get("async_processing_failed") or "")
            if failure_text:
                try:
                    await post_gateway_reply(
                        platform=platform,
                        conversation_id=conversation_id,
                        content=failure_text,
                        msg_type="text",
                        request_id=request_id,
                        wait_result=True,
                        timeout_ms=6000,
                    )
                except Exception:
                    pass


def _should_start_gateway_progress_push(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
    intent_draft: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Proactive progress push is retired; progress queries use the explicit fastpath."""
    return False, "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY"


async def _run_gateway_proactive_progress_push(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str | None = None,
    payload: dict[str, Any],
    ledger: Any,
    stop_event: asyncio.Event,
    ports: InputRuntimeApplicationPorts,
) -> dict[str, Any]:
    from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415

    _ = (source, text)

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return

    settings = get_input_settings()
    warmup_ms = settings.gateway_progress_push_warmup_ms
    interval_ms = settings.gateway_progress_push_interval_ms
    max_updates = settings.gateway_progress_push_max_updates
    wait_result = settings.gateway_progress_push_wait_result
    timeout_ms = settings.gateway_progress_push_timeout_ms

    if warmup_ms > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(warmup_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            pass

    sent_count = 0
    last_signature = ""
    while not stop_event.is_set() and sent_count < max_updates:
        reply_text, reason, summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
            payload=payload,
            exclude_request_id=None,
            ports=ports,
        )
        summary_dict = dict(summary or {})
        status = str(summary_dict.get("status") or "NONE").strip().upper()
        target_id = str(summary_dict.get("target_id") or "").strip()
        progress_message = str(summary_dict.get("progress_message") or summary_dict.get("detail") or "").strip()
        signature = f"{status}|{target_id}|{progress_message}"

        if reason == "PROGRESS_SUMMARY_FOUND" and status in {"RUNNING", "RECOVERING"} and signature != last_signature:
            started_ms = int(time.time() * 1000)
            result = await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content=reply_text,
                msg_type="text",
                request_id=request_id,
                wait_result=wait_result,
                timeout_ms=timeout_ms,
            )
            success = bool(getattr(result, "success", False))
            finished_ms = int(time.time() * 1000)
            latency_ms = max(0, finished_ms - started_ms)
            push_payload = dict(payload or {})
            push_payload["progress_push_status"] = str(getattr(result, "status", "UNKNOWN"))
            push_payload["progress_push_reason"] = reason
            push_payload["progress_push_preview"] = _build_reply_preview(reply_text, limit=100)
            push_payload["progress_push_updates"] = sent_count + 1
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=push_payload,
                phase="PROGRESS_PUSHED",
                status=("OK" if success else "DEGRADED"),
                reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_PUSH_REPLY_FAILED")),
                detail=f"gateway progress push status={getattr(result, 'status', 'UNKNOWN')} success={success}",
                latency_ms=latency_ms,
                ts_ms=finished_ms,
            )
            sent_count += 1
            last_signature = signature
        elif reason == "PROGRESS_SUMMARY_FOUND" and status in {"COMPLETED", "FAILED"}:
            return

        if sent_count >= max_updates:
            return

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            continue


async def _stop_progress_push_task(
    *,
    stop_event: asyncio.Event | None,
    task: asyncio.Task | None,
) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
    except asyncio.TimeoutError:
        task.cancel()
    except Exception:
        return


def _maybe_schedule_gateway_immediate_ack(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    intent_draft: dict[str, Any] | None = None,
    ledger: Any,
) -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source=source,
        text=text,
        payload=payload,
        intent_draft=intent_draft,
    )
    now_ms = int(time.time() * 1000)
    if not should_send:
        if reason not in {
            "ACK_RETIRED_REPLY_ENVELOPE_ONLY",
            "ACK_ROUTE_UNRESOLVED",
            "ACK_NON_EXECUTION_ROUTE",
            "ACK_PROGRESS_QUERY",
            "ACK_DIRECT_OPEN_APP",
        }:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload={**dict(payload or {}), "immediate_ack_reason": reason},
                phase="ACK_SKIPPED",
                status="IGNORED",
                reason_code=reason,
                detail=f"gateway immediate ack skipped: {reason}",
                ts_ms=now_ms,
            )
        return

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACK_PENDING",
        status="ACCEPTED",
        detail="gateway immediate ack scheduled",
        ts_ms=now_ms,
    )

    async def _runner() -> None:
        start_ms = int(time.time() * 1000)
        try:
            ack_result = await _deliver_gateway_immediate_ack(
                request_id=request_id,
                payload=payload,
                text=text,
            )
        except Exception as exc:
            finish_ms = int(time.time() * 1000)
            latency_ms = max(0, finish_ms - start_ms)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="ACK_FAILED",
                status="FAILED",
                reason_code="ACK_DELIVERY_EXCEPTION",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                latency_ms=latency_ms,
                ts_ms=finish_ms,
            )
            return

        finish_ms = int(time.time() * 1000)
        latency_ms = max(0, finish_ms - start_ms)
        ack_payload = dict(payload or {})
        ack_payload["immediate_ack_status"] = ack_result.get("status")
        ack_payload["immediate_ack_latency_ms"] = ack_result.get("latency_ms")
        ack_payload["immediate_ack_preview"] = ack_result.get("ack_preview")
        ack_payload["immediate_ack_reason"] = ack_result.get("reason_code")
        ack_success = bool(ack_result.get("success"))
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=ack_payload,
            phase=("ACKED" if ack_success else "ACK_FAILED"),
            status=("OK" if ack_success else "FAILED"),
            reason_code=(str(ack_result.get("reason_code") or "") or None),
            detail=f"gateway immediate ack status={ack_result.get('status')} success={ack_success}",
            latency_ms=latency_ms,
            ts_ms=finish_ms,
        )

    asyncio.create_task(_runner()).add_done_callback(_log_gateway_ack_task_exception)
