"""Host input runtime responsibility extracted from the HTTP adapter."""

from .contracts_and_normalization import *  # noqa: F403
from .worker_dispatch import _dispatch_to_graph

async def receive_input(
    body: InputPayload,
    request: Request,
    *,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    ports: InputRuntimeApplicationPorts,
) -> InputResponse:
    pre_auth_request_id = new_request_id()
    request_id = pre_auth_request_id
    ts_ms = int(time.time() * 1000)
    raw_source = str(body.source or "").upper().strip()
    source, source_alias_platform = _normalize_input_source(raw_source)
    gateway_ingress: _HostGatewayIngressOutcome | None = None
    _logger.info("input_routes: received edge input source=%s request_id=%s", raw_source or source, request_id)

    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR input missing required fields: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()
        if body.is_final is not True:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail="ASR non-final segment ignored",
                ts_ms=ts_ms,
            )
    elif source == "GATEWAY":
        initial_payload = body.model_dump(exclude_none=True)
        gateway_attachments = _normalize_gateway_attachments(initial_payload)
        has_gateway_content = bool(str(body.content or "").strip())
        gateway_platform = str(body.platform or source_alias_platform or "").strip()
        if ((not has_gateway_content and not gateway_attachments) or not body.conversation_id or not gateway_platform):
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="GATEWAY input missing required fields: content|attachments, conversation_id, platform",
                ts_ms=ts_ms,
            )
        if _is_invalid_gateway_conversation_id(body.conversation_id):
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail=f"invalid gateway conversation_id: {body.conversation_id}",
                ts_ms=ts_ms,
            )
        text = str(body.content or "").strip()
        session_id = str(body.conversation_id).strip()
    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"unsupported source: {source}, only ASR/GATEWAY are accepted",
            ts_ms=ts_ms,
        )

    # Authentication is selected by the /v1/host/input transport surface,
    # never by the caller-controlled source field.  This closes the former
    # GATEWAY -> ASR relabel bypass while preserving structural validation
    # before the durable idempotency claim.
    gateway_ingress = _evaluate_host_gateway_ingress(
        request=request,
        raw_body=await _read_input_raw_body(body, request),
        received_at_ms=ts_ms,
    )
    if gateway_ingress.allowed or gateway_ingress.duplicate:
        authoritative_request_id = str(gateway_ingress.request_id or "").strip()
        if not authoritative_request_id:
            raise HTTPException(
                status_code=503,
                detail="host secure ingress omitted its authoritative request id",
            )
        request_id = authoritative_request_id
        _logger.info(
            "input_routes: secure ingress bound request_id=%s pre_auth_correlation=%s",
            request_id,
            pre_auth_request_id,
        )
    if gateway_ingress.duplicate:
        existing = str(gateway_ingress.existing_result_ref or "")
        if existing.startswith("host-input-result://"):
            if existing.rsplit("/", 1)[-1] in {"ACCEPTED", "COMPLETED"}:
                return InputResponse(
                    success=True,
                    request_id=request_id,
                    source=source,
                    status="IGNORED",
                    detail=f"duplicate host input delivery: {gateway_ingress.reason_code}",
                    ts_ms=ts_ms,
                    **_host_input_receipt_fields(
                        gateway_ingress,
                        receipt_ref=existing,
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail="host input previously completed without acceptance",
            )
        # A blank result means the first delivery has not yet reached atomic
        # admission; a non-legacy result is a HostCommand id.  Both re-enter the
        # governed application, whose repository owns exact replay semantics.
    if not gateway_ingress.allowed and not gateway_ingress.duplicate:
        raise HTTPException(
            status_code=_gateway_ingress_rejection_http_status(gateway_ingress.reason_code),
            detail=f"host secure ingress rejected: {gateway_ingress.reason_code}",
        )

    payload = body.model_dump(exclude_none=True)
    payload["secure_ingress"] = {
        "claim_ref": gateway_ingress.claim_ref,
        "audit_ref": gateway_ingress.audit_ref,
        "principal_id": gateway_ingress.principal_id,
        "idempotency_key": gateway_ingress.idempotency_key,
        "body_sha256": gateway_ingress.body_sha256,
    }
    if source == "GATEWAY":
        payload["source"] = "GATEWAY"
        if source_alias_platform and not str(payload.get("platform") or "").strip():
            payload["platform"] = source_alias_platform
        gateway_attachments = _normalize_gateway_attachments(payload)
        if gateway_attachments:
            payload["attachments"] = gateway_attachments
            placeholder_text = _build_gateway_multimodal_placeholder_text(
                content=text,
                attachments=gateway_attachments,
            )
            if placeholder_text and placeholder_text != text:
                payload["raw_content"] = str(body.content or "")
                payload["content"] = placeholder_text
                payload["content_is_multimodal_placeholder"] = True
                text = placeholder_text
    if source == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            _logger.info(
                "input_routes: ASR ignored by noise gate request_id=%s reason=%s",
                request_id,
                reject_reason,
            )
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ts_ms,
            )

    immune_result: dict[str, Any] = {}
    try:
        immune_result = _evaluate_immune_gate_for_input(
            text=text,
            source=source,
            session_id=session_id,
            ports=ports,
            person_id=str(body.person_id or "").strip() or None,
        )
    except Exception as exc:
        if str(exc) == "HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED":
            _logger.error(
                "input_routes: immune gate authority unavailable request_id=%s",
                request_id,
            )
            immune_result = {
                "decision": "BLOCK",
                "reason_codes": ["IMMUNE_GATE_UNAVAILABLE"],
            }
        else:
            _logger.warning("input_routes: immune gate evaluate degraded request_id=%s: %s", request_id, exc)
            immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    payload["immune_gate"] = {
        "decision": immune_decision,
        "risk_level": immune_result.get("risk_level"),
        "score": immune_result.get("score"),
        "reason_codes": list(immune_result.get("reason_codes") or []),
    }
    if immune_decision == "BLOCK":
        immune_unavailable = "IMMUNE_GATE_UNAVAILABLE" in set(
            str(item) for item in immune_result.get("reason_codes") or ()
        )
        reason_code = (
            "IMMUNE_GATE_UNAVAILABLE"
            if immune_unavailable
            else "IMMUNE_BLOCKED_HIGH_RISK"
        )
        detail = (
            "immune gate authority unavailable"
            if immune_unavailable
            else "immune gate blocked high-risk input"
        )
        _log_input_event(
            ledger=_get_ledger_safe(ports=ports),
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="REJECTED",
            status="REJECTED",
            reason_code=reason_code,
            detail=detail,
            ts_ms=ts_ms,
        )
        result_persisted = _persist_gateway_ingress_result(
            gateway_ingress,
            request_id=request_id,
            status="REJECTED",
            at_ms=ts_ms,
        )
        if not result_persisted:
            raise HTTPException(
                status_code=503,
                detail="gateway durable rejection result unavailable",
            )
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=detail,
            ts_ms=ts_ms,
        )
    if immune_decision == "REPORT":
        _logger.warning(
            "input_routes: immune gate report request_id=%s source=%s reasons=%s",
            request_id,
            source,
            immune_result.get("reason_codes"),
        )

    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415

        payload["person_id"] = resolve_person_id(
            str(body.person_id or "").strip() or None,
            session_id=session_id,
            platform=str(body.platform or "").strip() or None,
            from_id=str(body.from_id or "").strip() or None,
            conversation_id=str(body.conversation_id or "").strip() or None,
        )
        ports.notify_user_interaction()
    except Exception:
        pass

    try:
        life_snapshot = read_life_snapshot(
            str(payload.get("person_id") or "").strip() or "default",
            reader=ports.get_life_snapshot_reader(),
        )
        snapshot = project_inner_life_snapshot(life_snapshot)
        payload["inner_life_snapshot"] = {
            "being": dict(snapshot.get("being") or {}),
            "drive": dict(snapshot.get("drive") or {}),
            "attention": dict(snapshot.get("attention") or {}),
            "preoccupation": dict(snapshot.get("preoccupation") or {}),
            "expression_bias": dict(snapshot.get("expression_bias") or {}),
        }
        payload["life_snapshot_ref"] = {
            "person_id": life_snapshot["person_id"],
            "version": life_snapshot["version"],
            "state_digest": life_snapshot["state_digest"],
        }
    except Exception as life_exc:
        _logger.debug(
            "input_routes: stable inner life snapshot degraded request_id=%s: %s",
            request_id,
            life_exc,
        )

    feedback_record = None
    try:
        feedback_record = _record_feedback_for_latest_persona_reply(
            session_id=session_id,
            person_id=str(payload.get("person_id") or "").strip() or None,
            user_message=text,
            ledger=_get_ledger_safe(ports=ports),
            ts_ms=ts_ms,
        )
        if feedback_record:
            proposal_status = "NOT_CONFIGURED"
            proposal_id = ""
            if callable(ports.get_evolution_activation_service):
                workspace_id, _owner_id = (
                    ports.require_evolution_scope()
                )
                proposal_result = dict(
                    ports.require_evolution_activation_service()
                    .stage_expression_feedback(
                        workspace_id=workspace_id,
                        subject_id=(
                            str(payload.get("person_id") or "").strip()
                            or session_id
                        ),
                        target_request_id=str(
                            feedback_record.get(
                                "target_request_id"
                            )
                            or ""
                        ).strip(),
                        target_text_hash=str(
                            feedback_record.get("target_text_hash")
                            or ""
                        ).strip(),
                        feedback_score=float(
                            feedback_record.get("feedback_score")
                            or 0.0
                        ),
                        feedback_tags=tuple(
                            str(item)
                            for item in list(
                                feedback_record.get(
                                    "feedback_tags"
                                )
                                or []
                            )
                        ),
                        feedback_source=str(
                            feedback_record.get(
                                "feedback_source"
                            )
                            or "user_explicit"
                        ),
                    )
                )
                proposal_status = str(
                    proposal_result.get("status") or ""
                ).strip()
                proposal_id = str(
                    dict(proposal_result.get("proposal") or {}).get(
                        "proposal_id"
                    )
                    or ""
                ).strip()
            _log_input_event(
                ledger=_get_ledger_safe(ports=ports),
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="INPUT_FEEDBACK_PROPOSAL_STAGED",
                status=(
                    "STAGED"
                    if proposal_status == "STAGED"
                    else "LOGGED"
                ),
                reason_code=(
                    "EXPLICIT_RESPONSE_FEEDBACK_PROPOSAL_ONLY"
                    if proposal_status == "STAGED"
                    else "EXPLICIT_RESPONSE_FEEDBACK_COMPAT_LOG_ONLY"
                ),
                detail=(
                    "response feedback normalized for latest persona reply "
                    f"target_request_id={str(feedback_record.get('target_request_id') or '').strip()} "
                    f"score={float(feedback_record.get('feedback_score') or 0.0):.2f} "
                    f"proposal_status={proposal_status} "
                    f"proposal_id={proposal_id}"
                ),
                ts_ms=ts_ms,
            )
    except Exception as feedback_exc:
        _log_input_event(
            ledger=_get_ledger_safe(ports=ports),
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="INPUT_FEEDBACK_PROPOSAL_FAILED",
            status="FAILED",
            reason_code="EXPLICIT_RESPONSE_FEEDBACK_PERSIST_FAILED",
            detail=(
                f"{type(feedback_exc).__name__}:"
                f"{str(feedback_exc)[:512]}"
            ),
            ts_ms=ts_ms,
        )
        _logger.error(
            "input_routes: response feedback proposal failed "
            "request_id=%s: %s",
            request_id,
            feedback_exc,
        )

    if source == "GATEWAY" and not list(payload.get("attachments") or []):
        _maybe_restore_recent_gateway_multimodal(session_id=session_id, text=text, payload=payload)

    try:
        pipeline_result = dict(
            await _dispatch_to_graph(
                request_id=request_id,
                session_id=session_id,
                source=source,
                text=text,
                payload=payload,
                ts_ms=ts_ms,
                governed_command_application_provider=(
                    governed_command_application_provider
                ),
                command_authority={
                    "principal_id": gateway_ingress.principal_id,
                    "claim_ref": gateway_ingress.claim_ref,
                    "audit_ref": gateway_ingress.audit_ref,
                    "body_sha256": gateway_ingress.body_sha256,
                },
                ports=ports,
            )
            or {}
        )
        commit_event = dict(pipeline_result.get("commit_event") or {})
        execution_receipt = dict(commit_event.get("execution_receipt") or {})
        command_id = str(execution_receipt.get("command_id") or "").strip()
        decision = str(
            pipeline_result.get("decision")
            or ("APPROVE" if governed_command_application_provider is None else "REJECT")
        ).strip().upper()
        reason_code = str(pipeline_result.get("reason_code") or "").strip() or None
        completed_ms = int(time.time() * 1000)
        if command_id:
            return InputResponse(
                success=(decision == "APPROVE"),
                request_id=request_id,
                source=source,
                status="COMPLETED" if decision == "APPROVE" else "REJECTED",
                detail=(
                    "governed command completed"
                    if decision == "APPROVE"
                    else f"governed command rejected: {reason_code or 'REJECTED'}"
                ),
                ts_ms=completed_ms,
                **_host_input_receipt_fields(
                    gateway_ingress,
                    receipt_ref=command_id,
                ),
            )

        if decision != "APPROVE":
            terminal_status = "PAUSED" if decision == "PAUSE" else "REJECTED"
            if not _persist_gateway_ingress_result(
                gateway_ingress,
                request_id=request_id,
                status=terminal_status,
                at_ms=completed_ms,
            ):
                raise HTTPException(
                    status_code=503,
                    detail="gateway durable rejection result unavailable",
                )
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status=terminal_status,
                detail=f"input not admitted: {reason_code or decision}",
                ts_ms=completed_ms,
                **_host_input_receipt_fields(
                    gateway_ingress,
                    receipt_ref=_host_input_result_ref(
                        request_id=request_id,
                        status=terminal_status,
                    ),
                ),
            )

        route_decision = dict(pipeline_result.get("route_decision") or {})
        route_type = str(route_decision.get("route") or "").strip().lower()
        route_target = str(route_decision.get("target_id") or "").strip()
        direct_skill_cutover = route_type == "skill" and bool(route_target)
        if (
            governed_command_application_provider is not None
            and direct_skill_cutover
        ):
            raise HTTPException(
                status_code=503,
                detail="direct skill command completed without an execution receipt",
            )

        # Chat/delegate paths not yet owned by Task Runtime still complete in
        # the awaited legacy pipeline.  Direct-skill cutover never falls back.
        legacy_status = (
            "COMPLETED"
            if governed_command_application_provider is not None
            else "ACCEPTED"
        )
        if not _persist_gateway_ingress_result(
            gateway_ingress,
            request_id=request_id,
            status=legacy_status,
            at_ms=int(time.time() * 1000),
        ):
            raise HTTPException(
                status_code=503,
                detail="gateway durable acceptance result unavailable",
            )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status=legacy_status,
            detail=(
                "input completed by awaited legacy pipeline"
                if legacy_status == "COMPLETED"
                else "input accepted for processing"
            ),
            ts_ms=ts_ms,
            **_host_input_receipt_fields(
                gateway_ingress,
                receipt_ref=_host_input_result_ref(
                    request_id=request_id,
                    status=legacy_status,
                ),
            ),
        )
    except Exception as exc:
        _logger.error("input_routes: dispatch failed source=%s request_id=%s: %s", source, request_id, exc)
        if governed_command_application_provider is None:
            _persist_gateway_ingress_result(
                gateway_ingress,
                request_id=request_id,
                status="ERROR",
                at_ms=int(time.time() * 1000),
            )
        if isinstance(exc, HTTPException):
            raise
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"dispatch failed: {exc}",
            ts_ms=ts_ms,
        )
