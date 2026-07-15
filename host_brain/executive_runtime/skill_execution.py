"""Executive runtime responsibility extracted from the HTTP application adapter."""

from ._shared import *  # noqa: F403
from .ports import ExecutiveRuntimeApplicationPorts

def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _build_task_progress_sub_context(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    person_id: str,
) -> SimpleNamespace:
    projection = ports.get_task_status_projection()
    summary = projection.summarize_for_chat(
        person_id=person_id,
        session_id=session_id,
        exclude_request_id=request_id,
    )
    if not summary:
        return SimpleNamespace(
            success=False,
            result_text="当前没有可汇报的执行进度。",
            payload={},
        )

    status = _normalize_text(summary.get("status")) or "UNKNOWN"
    target_id = _normalize_text(summary.get("target_id")) or "task"
    progress = _normalize_text(summary.get("progress_message") or summary.get("detail"))
    elapsed = _format_elapsed(int(summary.get("elapsed_ms") or 0))
    result_text = f"当前任务 {target_id} 状态为 {status}，已运行 {elapsed}。"
    if progress:
        result_text += f" 当前进展：{progress}"
    result_value = _normalize_text(summary.get("result_text"))
    if result_value and status in {"COMPLETED", "FAILED"}:
        result_text += f" 结果摘要：{result_value}"
    return SimpleNamespace(
        success=True,
        result_text=result_text,
        payload=summary,
    )


def _extract_skill_args(intent_draft: dict[str, Any], request_body: Any, user_message: str) -> dict[str, Any]:
    if isinstance(intent_draft.get("args"), dict):
        return dict(intent_draft.get("args") or {})
    if isinstance(request_body, dict) and isinstance(request_body.get("args"), dict):
        return dict(request_body.get("args") or {})
    return {
        "query": user_message,
        "text": user_message,
        "user_message": user_message,
    }


def _governed_node_grant_error(
    value: Mapping[str, Any] | None,
    *,
    execution_context: Mapping[str, Any],
    request_id: str,
    session_id: str,
    skill_id: str,
    intent_id: str,
) -> str | None:
    grant = dict(value) if isinstance(value, Mapping) else {}
    context = dict(execution_context or {})
    try:
        revision = int(context.get("graph_revision") or 0)
        grant_revision = int(grant.get("graph_revision") or 0)
        expires_at_ms = int(grant.get("expires_at_ms") or 0)
        fencing_token = int(context.get("fencing_token") or 0)
    except (TypeError, ValueError):
        return "GOVERNED_NODE_GRANT_INVALID"
    expected = {
        "grant_ref": str(context.get("node_grant_ref") or "").strip(),
        "parent_grant_ref": str(context.get("parent_grant_ref") or "").strip(),
        "request_id": str(request_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "principal_id": str(context.get("principal_id") or "").strip(),
        "graph_id": str(context.get("graph_id") or "").strip(),
        "root_work_order_id": str(context.get("work_order_id") or "").strip(),
        "node_id": str(context.get("node_id") or "").strip(),
        "route": "skill",
        "target_id": str(skill_id or "").strip(),
        "intent_id": str(intent_id or "").strip(),
        "action_kind": str(skill_id or "").strip(),
        "token_subject": str(skill_id or "").strip(),
    }
    actual = {key: str(grant.get(key) or "").strip() for key in expected}
    required_runtime = (
        "command_id",
        "attempt_id",
        "lease_id",
        "lease_owner_id",
        "reservation_id",
        "business_scope",
        "operation_fingerprint",
    )
    if (
        actual != expected
        or revision < 1
        or grant_revision != revision
        or expires_at_ms <= int(time.time() * 1000)
        or any(not str(context.get(key) or "").strip() for key in required_runtime)
        or fencing_token < 1
    ):
        return "GOVERNED_NODE_GRANT_INVALID"
    return None


def _merge_plan_node_args(
    *,
    base_intent_draft: dict[str, Any],
    node: SkillDagNode,
    task_text: str,
) -> dict[str, Any]:
    merged_args = dict(dict(base_intent_draft or {}).get("args") or {})
    node_args = dict(getattr(node, "args_payload", {}) or {})
    merged_args.update(node_args)
    normalized_target = _normalize_text(node.target_id, lower=True)
    if not _normalize_text(merged_args.get("query")):
        merged_args["query"] = task_text
    if not _normalize_text(merged_args.get("text")):
        merged_args["text"] = task_text
    if not _normalize_text(merged_args.get("user_message")):
        merged_args["user_message"] = task_text
    if normalized_target == "memory_store" and not _normalize_text(merged_args.get("content")):
        merged_args["content"] = task_text
    if is_agent_runtime_skill(normalized_target):
        if not _normalize_text(merged_args.get("task")):
            merged_args["task"] = _normalize_text(merged_args.get("problem_statement")) or task_text
        if not _normalize_text(merged_args.get("problem_statement")):
            merged_args["problem_statement"] = _normalize_text(merged_args.get("task")) or task_text
        if not _normalize_text(merged_args.get("run_mode"), lower=True):
            merged_args["run_mode"] = AGENT_RUN_MODE_BY_SKILL_ID.get(normalized_target, "worker")
        if (
            _normalize_text(merged_args.get("run_mode"), lower=True) != "inspect"
            and not _normalize_text(merged_args.get("workspace_strategy"), lower=True)
            and not isinstance(merged_args.get("workspace"), dict)
        ):
            merged_args["workspace_strategy"] = "runtime_repo"
    return merged_args


def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def _is_effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False

    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False

    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True


async def _dispatch_to_sub_agent(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    person_id: str,
    target_id: str,
    task_text: str,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> _ExecutionOutcome:
    started = time.time()
    dispatcher = ports.get_sub_agent_dispatcher()
    timeout_ms = get_orchestration_settings().subagent_dispatch_timeout_ms
    response = await dispatcher.dispatch(
        ports.sub_agent_request_factory(
            agent_id=target_id,
            task=task_text,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
            context=dict(context or {}),
            timeout_ms=timeout_ms,
        )
    )
    latency_ms = int((time.time() - started) * 1000)
    return _ExecutionOutcome(
        route_type="delegate",
        target_id=target_id,
        skill_family=str(dict(context or {}).get("skill_family") or "general"),
        success=bool(response.success),
        result_text=_normalize_text(response.result),
        error_text=_normalize_text(response.error),
        latency_ms=max(latency_ms, int(response.latency_ms or 0)),
        payload={
            "audit_refs": dict(response.audit_refs or {}),
            "agent_id": response.agent_id,
        },
    )


def _log_skill_exec_row(
    *,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
    ledger: Any,
    evidence_ref: str | None = None,
) -> None:
    if ledger is False:
        return
    try:
        ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": None,
                "turn_id": None,
                "intent_id": intent_id,
                "event_id": f"evt::{request_id}::{skill_name}",
                "skill_name": skill_name,
                "approval_token_ref": approval_token_ref,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": result_status,
                "result_code": result_code,
                "result_digest": result_digest[:4000] if result_digest else None,
                "evidence_ref": evidence_ref or _skill_receipt_ref(request_id, skill_name),
                "manifest_ref": None,
                "ts_ms": ts_ms,
            }
        )
    except Exception as exc:
        _logger.debug("[%s] skill exec audit skipped skill=%s: %s", request_id, skill_name, exc)


def _skill_receipt_ref(request_id: str, skill_name: str) -> str:
    digest = hashlib.sha256(
        f"{str(request_id or '').strip()}\n{str(skill_name or '').strip()}".encode("utf-8")
    ).hexdigest()
    return f"skillreceipt://sha256/{digest}"


def _execute_skill(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
    skill_registry: Any | None = None,
    token_service_override: Any | None = None,
    ledger: Any | None = None,
    durable_authority_ledger: Any | None = None,
    capability_broker_override: Any | None = None,
    model_attempt_service_override: Any | None = None,
    production_authority_verifier: Any | None = None,
    governed_execution_context: Mapping[str, Any] | None = None,
    execution_args_override: Mapping[str, Any] | None = None,
    verified_node_grant: Mapping[str, Any] | None = None,
) -> _ExecutionOutcome:
    started = time.time()
    if skill_registry is None:
        skill_registry = ports.get_skill_registry()
    if ledger is None:
        ledger = ports.get_ledger()
    registry = skill_registry
    skill_meta = registry.require(skill_id)
    skill_receipt_ref = _skill_receipt_ref(request_id, skill_id)
    approval_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("approval_token_ref"))
    confirm_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("confirm_token_ref"))
    execution_intent_id = _normalize_text(intent_id) or f"intent::{request_id}::{skill_id}"
    governed_node_verified = False
    if governed_execution_context is not None:
        grant_error = _governed_node_grant_error(
            verified_node_grant,
            execution_context=governed_execution_context,
            request_id=request_id,
            session_id=session_id,
            skill_id=skill_id,
            intent_id=execution_intent_id,
        )
        if grant_error:
            raise PermissionError(grant_error)
        governed_node_verified = True
        if bool(skill_meta.require_confirm):
            raise PermissionError("GOVERNED_DURABLE_CONFIRM_REVIEW_REQUIRED")

    if _normalize_text(dict(governor_result or {}).get("decision"), lower=True) not in {"", "approve"}:
        error_text = _normalize_text(governor_result.get("reason_code")) or "GOVERNOR_REJECTED"
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="REJECTED",
            result_code=error_text,
            result_digest=error_text,
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=error_text,
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "receipt_ref": skill_receipt_ref,
            },
        )

    action_arbiter_decision: dict[str, Any] | None = None
    token_service = None
    if _action_arbiter_execution_enabled() and not governed_node_verified:
        envelope = dict(intent_draft or {})
        bound_intent_id = _normalize_text(envelope.get("id") or envelope.get("intent_id"))
        bound_session_id = _normalize_text(envelope.get("session_id"))
        try:
            bound_seq = int(envelope.get("seq"))
        except (TypeError, ValueError):
            bound_seq = -1
        authority_error = ""
        if not bound_intent_id or bound_intent_id != execution_intent_id:
            authority_error = "ARBITER_INTENT_ID_MISMATCH"
        elif not bound_session_id or bound_session_id != session_id:
            authority_error = "ARBITER_SESSION_ID_MISMATCH"
        elif bound_seq < 0:
            authority_error = "ARBITER_INTENT_SEQ_INVALID"

        if not authority_error and not governed_node_verified:
            from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL  # noqa: PLC0415

            if token_service_override is None:
                token_service_override = ports.get_token_service()
            token_service = token_service_override
            token_check = token_service.validate_bound_token(
                approval_token_ref or None,
                expected_type=TOKEN_TYPE_APPROVAL,
                intent_id=bound_intent_id,
                skill_name=skill_id,
                session_id=bound_session_id,
                seq=bound_seq,
                now_ts_ms=int(time.time() * 1000),
                consume=False,
            )
            if not token_check.valid:
                authority_error = str(token_check.reason_code or "TOKEN_INVALID")

        if not authority_error:
            from host_brain.governor.action_arbiter import arbitrate_action  # noqa: PLC0415
            from host_brain.persona.scene_policy import build_scene_decision  # noqa: PLC0415

            surface_control = dict(getattr(request_body, "surface_task_control", {}) or {})
            scene_awareness = envelope.get("scene_awareness")
            if not isinstance(scene_awareness, dict):
                envelope_args = envelope.get("args")
                scene_awareness = (
                    dict(envelope_args.get("scene_awareness") or {})
                    if isinstance(envelope_args, dict)
                    else {}
                )
            scene_decision = build_scene_decision(
                scene_awareness=scene_awareness,
                task_mode=str(surface_control.get("task_mode") or envelope.get("action") or ""),
                user_goal_ref=f"intent://{bound_intent_id}",
                now_ms=int(time.time() * 1000),
            )
            action_arbiter_decision = arbitrate_action(
                governor_decision=governor_result,
                proposed_action={
                    "action_kind": skill_id,
                    "scope": str(skill_meta.scope or ""),
                    "risk_level": int(skill_meta.risk_level),
                    "requires_token": True,
                    "is_proactive": False,
                },
                intent_envelope=envelope,
                scene_decision=scene_decision,
                user_intent={
                    "explicit": True,
                    "intent_id": bound_intent_id,
                    "action_kind": str(envelope.get("action") or ""),
                    "goal_ref": f"intent://{bound_intent_id}",
                },
                now_ms=int(time.time() * 1000),
            )
            try:
                from host_brain.persona.life_evolution_observer import (  # noqa: PLC0415
                    observe_action_arbiter_decision,
                    observe_scene_decision,
                )

                observation_ref = f"request://{request_id}"
                if scene_awareness:
                    observe_scene_decision(
                        scene_decision,
                        person_id=person_id,
                        source_kind="executive_runtime",
                        source_ref=observation_ref,
                    )
                observe_action_arbiter_decision(
                    action_arbiter_decision,
                    person_id=person_id,
                    source_kind="executive_runtime",
                    source_ref=observation_ref,
                )
            except Exception:
                pass
            if action_arbiter_decision.get("decision") == "BLOCK":
                authority_error = str(
                    (action_arbiter_decision.get("reason_codes") or ["ACTION_ARBITER_BLOCKED"])[0]
                )

        if authority_error:
            latency_ms = int((time.time() - started) * 1000)
            _log_skill_exec_row(
                request_id=request_id,
                intent_id=execution_intent_id,
                skill_name=skill_id,
                result_status="REJECTED",
                result_code=authority_error,
                result_digest=authority_error,
                approval_token_ref=approval_token_ref or None,
                ts_ms=int(time.time() * 1000),
                ledger=ledger,
                evidence_ref=skill_receipt_ref,
            )
            return _ExecutionOutcome(
                route_type="skill",
                target_id=skill_id,
                skill_family=str(skill_meta.family or "general"),
                success=False,
                result_text="",
                error_text=authority_error,
                latency_ms=latency_ms,
                payload={
                    "approval_token_ref": approval_token_ref or None,
                    "action_arbiter_decision": action_arbiter_decision,
                    "receipt_ref": skill_receipt_ref,
                },
            )

    args = (
        dict(execution_args_override)
        if execution_args_override is not None
        else _extract_skill_args(intent_draft, request_body, task_text)
    )
    if is_agent_runtime_skill(skill_id):
        if governed_execution_context is not None:
            # The admitted Host command owns the root WorkOrder identity.  A
            # governed skill must not mint a second WorkOrder inside its
            # normalizer, otherwise the sealed Task authority cannot bind the
            # actual executor payload to the durable command/graph.
            args["work_order_id"] = str(
                governed_execution_context.get("work_order_id") or ""
            ).strip()
        args.setdefault("approval_confirmed", True)
        args.setdefault("confirmed", True)
        if approval_token_ref:
            args.setdefault("approval_token_ref", approval_token_ref)
    exec_context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "mood_state": _normalize_text(dict(emotion_context or {}).get("mood_state")) or "NEUTRAL",
        "emotion_tag": _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL",
        "emotion_context": dict(emotion_context or {}),
        "skill_family": str(skill_meta.family or "general"),
    }
    if skill_id.startswith("hub_skill_"):
        exec_context.update(
            {
                "activate_hub_skill": ports.activate_hub_skill,
                "refresh_skill_registry": ports.refresh_skill_registry,
            }
        )
    if governed_execution_context is not None:
        exec_context["_governed_execution_context"] = {
            "execution_mode": "governed_task_runtime",
            **dict(governed_execution_context),
        }
        if capability_broker_override is not None:
            exec_context["capability_broker"] = capability_broker_override
        if model_attempt_service_override is not None:
            exec_context["model_attempt_service"] = model_attempt_service_override
        if callable(production_authority_verifier):
            exec_context["production_authority_verifier"] = production_authority_verifier
    if action_arbiter_decision is not None:
        exec_context["action_arbiter_decision"] = action_arbiter_decision
    if skill_id == "resource_manager_status":
        exec_context["resource_manager_provider"] = ports.get_resource_manager
    if skill_id.startswith("memory_"):
        exec_context.update(
            _build_memory_exec_context(
                ports=ports,
                request_id=request_id,
                session_id=session_id,
                intent_id=execution_intent_id,
                emotion_context=emotion_context,
            )
        )

    try:
        if token_service is not None:
            token_result = token_service.validate_execution_tokens(
                intent_id=bound_intent_id,
                skill_name=skill_id,
                session_id=bound_session_id,
                seq=bound_seq,
                approval_token_ref=approval_token_ref or None,
                confirm_token_ref=confirm_token_ref or None,
                require_confirm=bool(skill_meta.require_confirm),
                now_ts_ms=int(time.time() * 1000),
            )
            if not token_result.valid:
                raise PermissionError(str(token_result.reason_code or "TOKEN_INVALID"))
        handler_payload = skill_meta.handler(args, exec_context)
        if isinstance(handler_payload, dict):
            payload = dict(handler_payload)
            if governed_execution_context is not None:
                payload["_context"] = exec_context
            else:
                payload.setdefault("_context", exec_context)
        else:
            payload = handler_payload
        if not isinstance(payload, dict):
            raise PermissionError("SKILL_EXECUTOR_PAYLOAD_INVALID")
        if not str(payload.get("tool") or "").strip():
            # Pure/read-only handlers still cross the canonical capability
            # Broker; the private projection adapter only returns their
            # normalized result after authority admission and durable claim.
            payload = {
                "tool": "skill_result",
                "result": payload,
                "_context": exec_context,
            }
        governed_authority = (
            {
                "execution_mode": "governed_task_runtime",
                **dict(governed_execution_context),
            }
            if governed_execution_context is not None
            else None
        )
        try:
            authority_seq = int(intent_draft.get("seq") or 0)
        except (TypeError, ValueError):
            authority_seq = 0
        dispatch_authority = bind_validated_skill_authority(
            payload=payload,
            request_id=request_id,
            skill_id=skill_id,
            principal_id=person_id,
            # A Task-governed dispatch is bound to the admitted HostCommand
            # target.  Token-governed dispatches are instead bound to the
            # Governor scope (normally the SkillMeta scope, e.g. ``host``).
            target_id=(
                _normalize_text(
                    dict(governed_execution_context or {}).get("target_id"),
                    lower=True,
                )
                if governed_execution_context is not None
                else (
                    _normalize_text(intent_draft.get("target"), lower=True)
                    or _normalize_text(skill_meta.scope, lower=True)
                )
            ),
            intent_id=execution_intent_id,
            session_id=session_id,
            seq=authority_seq,
            approval_token_ref=approval_token_ref or None,
            governed_execution_context=governed_authority,
            # ``False`` is the worker-thread audit suppression sentinel, not a
            # ledger candidate.  Authority verification must still bind to
            # the process canonical ledger used by the governed coordinator.
            durable_ledger=(
                durable_authority_ledger
                if durable_authority_ledger is not None
                else (None if ledger is False else ledger)
            ),
            validated_by=(
                "governed_host_adapter.durable_grant"
                if governed_execution_context is not None
                else (
                    "executive_runtime.token_service"
                    if token_service is not None
                    else "executive_runtime.governor_output"
                )
            ),
        )
        raw_result = dispatch_executor_payload(
            payload,
            authority=dispatch_authority,
            capability_broker=(
                capability_broker_override
                if capability_broker_override is not None
                else ports.get_capability_broker()
            ),
        )
        result_text = _flatten_result_text(raw_result)
        result_mapping = _coerce_mapping(raw_result)
        raw_status = _normalize_text(result_mapping.get("status"), lower=True)
        raw_code = _normalize_text(result_mapping.get("reason_code") or result_mapping.get("result_code"))
        success = _is_effective_skill_success(
            raw_status=raw_status,
            raw_code=raw_code,
            raw_result=raw_result,
            result_text=result_text,
        )
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="SUCCESS" if success else "FAILED",
            result_code=raw_code or None,
            result_digest=(result_text or str(raw_result or "")),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=success,
            result_text=result_text,
            error_text="" if success else (raw_code or "SKILL_EXECUTION_NO_EFFECT"),
            latency_ms=latency_ms,
            payload={
                "result": raw_result,
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(skill_meta.fallback_policy or "chat_fallback"),
                "receipt_ref": skill_receipt_ref,
            },
        )
    except Exception as exc:
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="FAILED",
            result_code="SKILL_EXECUTION_ERROR",
            result_digest=str(exc),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=str(exc),
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(getattr(skill_meta, "fallback_policy", "chat_fallback") or "chat_fallback"),
                "receipt_ref": skill_receipt_ref,
            },
        )


def _family_default_delegate(skill_family: str) -> str:
    normalized = _normalize_text(skill_family, lower=True)
    if normalized == "code":
        return "intent_agent"
    if normalized == "translation":
        return "translate_agent"
    if normalized in {"perception", "vision"}:
        return "vision_agent"
    if normalized == "system":
        return "repair_agent"
    return "intent_agent"


def _maybe_synthesize_runtime_capability(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    user_message: str,
    route_type: str,
    target_id: str,
    reason_code: str,
    llm_route_used: bool,
) -> dict[str, Any] | None:
    gap = detect_capability_gap(
        user_message=user_message,
        route_type=route_type,
        target_id=target_id,
        reason_code=reason_code,
        llm_route_used=llm_route_used,
    )
    if not gap.detected:
        return None

    forge_service = ports.get_capability_forge_service()
    override = forge_service.get_runtime_route_override(
        request_id=request_id,
        suggested_family=str(gap.suggested_family or "reasoning"),
        missing_capability_tags=list(gap.missing_capability_tags),
        reason_code=gap.reason_code,
    )
    runtime_spec = synthesize_runtime_capability(
        request_id=request_id,
        session_id=session_id,
        user_message=user_message,
        gap_decision=gap,
        registry=ports.get_runtime_capability_registry(),
    )

    override_target = _normalize_text(override.get("executor_target")) if isinstance(override, dict) else ""
    executor_route = runtime_spec.route_type
    executor_target = override_target or runtime_spec.target_id
    if override_target:
        executor_route = "delegate"

    ports.get_capability_gap_telemetry().record_detected(
        request_id=request_id,
        session_id=session_id,
        runtime_capability_id=runtime_spec.runtime_capability_id,
        reason_code=gap.reason_code,
        suggested_family=gap.suggested_family,
        missing_capability_tags=gap.missing_capability_tags,
        confidence=gap.confidence,
        executor_route=executor_route,
        executor_target=executor_target,
    )

    auto_plan_result: dict[str, Any] = {}
    try:
        auto_plan_result = forge_service.maybe_auto_plan_for_gap(
            request_id=request_id,
            session_id=session_id,
            reason_code=gap.reason_code,
            suggested_family=str(gap.suggested_family or "reasoning"),
            missing_capability_tags=list(gap.missing_capability_tags),
            proposer="executive_runtime",
        )
    except Exception as exc:
        _logger.debug("[%s] capability forge auto-plan skipped: %s", request_id, exc)
        auto_plan_result = {
            "triggered": False,
            "reason": "AUTO_PLAN_EXCEPTION",
        }

    capability_forge_payload = dict(override or {})
    if auto_plan_result:
        capability_forge_payload["auto_plan"] = dict(auto_plan_result)

    return {
        "capability_gap": gap.as_dict(),
        "runtime_capability": runtime_spec.as_dict(),
        "capability_forge": capability_forge_payload,
        "executor_route": executor_route,
        "executor_target": executor_target,
        "task_text": runtime_spec.task,
    }


def _build_failure_recovery_plan(
    *,
    route_type: str,
    target_id: str,
    skill_family: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    details = dict(payload or {})
    if route_type == "skill":
        fallback_policy = _normalize_text(details.get("fallback_policy"), lower=True) or "chat_fallback"
        if fallback_policy in {"same_family", "retry_once"}:
            return {
                "route_type": "delegate",
                "target_id": _family_default_delegate(skill_family),
                "reason_code": f"skill_recovery:{fallback_policy}",
            }
        return {
            "route_type": "chat",
            "target_id": "",
            "reason_code": "skill_recovery:chat_fallback",
        }
    if route_type == "delegate" and target_id != "intent_agent":
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "reason_code": "delegate_recovery:intent_agent",
        }
    return None
