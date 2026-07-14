"""Executive runtime responsibility extracted from the HTTP application adapter."""

from ._shared import *  # noqa: F403
from .ports import ExecutiveRuntimeApplicationPorts

async def stream_chat_route(
    *,
    route: str,
    request_id: str,
    route_reason: str,
    skill_family: str,
    runtime_capability_handoff_extra: dict[str, Any],
    record_dispatch_handoff_fn,
    surface_reply_sse_generator_fn,
    chat_input,
):
    if str(route or "").strip().lower() != "chat":
        return

    chat_route_started_ms = int(time.time() * 1000)
    _logger.info("[%s] ExecutiveRuntime -> chat (reason=%s)", request_id, route_reason)
    record_dispatch_handoff_fn(
        handoff_route="chat",
        handoff_target=None,
        handoff_reason=route_reason,
        extra={"skill_family": skill_family, **dict(runtime_capability_handoff_extra or {})},
    )
    first_event_logged = False
    first_render_logged = False
    async for event in surface_reply_sse_generator_fn(chat_input):
        if not first_event_logged:
            first_event_logged = True
            _logger.info(
                "[%s] ExecutiveRuntime chat_first_event_ms=%s",
                request_id,
                max(0, int(time.time() * 1000) - chat_route_started_ms),
            )
        if not first_render_logged and event.startswith("data: "):
            try:
                payload = json.loads(event[6:].strip())
            except Exception:
                payload = {}
            if str(payload.get("phase") or "").strip().upper() == "RENDER":
                first_render_logged = True
                _logger.info(
                    "[%s] ExecutiveRuntime chat_first_render_ms=%s",
                    request_id,
                    max(0, int(time.time() * 1000) - chat_route_started_ms),
                )
        yield event


def _action_arbiter_execution_enabled() -> bool:
    """Canary switch; release cutover enables this after focused validation."""
    return get_orchestration_settings().action_arbiter_enabled


async def _executive_runtime_core_impl(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    person_id: str,
    sanitized_message: str,
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    del metrics

    declared_route = _normalize_text(dict(intent_draft or {}).get("route"), lower=True)
    if declared_route in {"chat", "skill", "delegate", "plan"}:
        raise PermissionError(
            "CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME"
        )

    skill_registry = ports.get_skill_registry()
    llm_route_used = bool(
        dict(intent_draft or {}).get("llm_route_used")
        or dict(intent_draft or {}).get("route_source") == "llm"
    )

    # ── Phase B：器官族状态并行激活注入路由决策 ──────────────────────────
    # B-1: LimbicAgent motive_state → 情绪驱动路由调整
    # B-2: ImmuneAgent 风险等级 → 高风险时强制 chat 快速回复
    # B-3: ProprioceptionAgent 认知负荷 → 高负荷时强制 chat 快速回复
    # B-4: PrefrontalAgent 漂移状态 → 漂移中强制 chat 路由
    # 四路并行读取，减少串行等待（真实神经系统是并行激活的）
    _organ_route_override: str | None = None
    _organ_route_reason: str | None = None
    _organ_route_meta: dict[str, Any] = {}

    def _fetch_immune_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
            return get_immune_agent().export_runtime_state()
        except Exception:
            return {}

    def _fetch_proprioception_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
            return get_proprioception_agent().export_runtime_state()
        except Exception:
            return {}

    def _fetch_limbic_motive() -> dict[str, Any]:
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
            _limbic = get_limbic_agent()
            if hasattr(_limbic, "compute_motive_state"):
                return dict(_limbic.compute_motive_state() or {})
        except Exception:
            pass
        return {}

    def _fetch_prefrontal_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            return get_prefrontal_agent().export_runtime_state()
        except Exception:
            return {}

    import asyncio as _asyncio  # noqa: PLC0415
    import concurrent.futures as _futures  # noqa: PLC0415

    _loop = _asyncio.get_event_loop()
    with _futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="organ_fetch") as _pool:
        _immune_fut = _loop.run_in_executor(_pool, _fetch_immune_state)
        _prop_fut = _loop.run_in_executor(_pool, _fetch_proprioception_state)
        _limbic_fut = _loop.run_in_executor(_pool, _fetch_limbic_motive)
        _pf_fut = _loop.run_in_executor(_pool, _fetch_prefrontal_state)
        _immune_state, _prop_state, _motive_state, _pf_state = await _asyncio.gather(
            _immune_fut, _prop_fut, _limbic_fut, _pf_fut,
            return_exceptions=True,
        )

    # 异常降级：gather 返回 Exception 时用空 dict 代替
    if isinstance(_immune_state, Exception):
        _immune_state = {}
    if isinstance(_prop_state, Exception):
        _prop_state = {}
    if isinstance(_motive_state, Exception):
        _motive_state = {}
    if isinstance(_pf_state, Exception):
        _pf_state = {}

    # B-2: ImmuneAgent 风险门控（最高优先级，先判断）
    _immune_risk = str(_immune_state.get("last_risk_level") or "none").strip().lower()
    _immune_decision = str(_immune_state.get("last_decision") or "ALLOW").strip().upper()
    _organ_route_meta["immune_risk_level"] = _immune_risk
    _organ_route_meta["immune_last_decision"] = _immune_decision
    if _immune_risk in {"critical", "high"} and _immune_decision in {"REPORT", "BLOCK"}:
        _organ_route_override = "chat"
        _organ_route_reason = f"immune_gate:risk={_immune_risk}:decision={_immune_decision}"

    # B-3: ProprioceptionAgent 认知负荷
    _cognitive_load = float(_prop_state.get("cognitive_load_index") or 0.0)
    _interrupt_freq = float(_prop_state.get("interrupt_frequency") or 0.0)
    _organ_route_meta["cognitive_load_index"] = round(_cognitive_load, 3)
    _organ_route_meta["interrupt_frequency"] = round(_interrupt_freq, 3)
    if _organ_route_override is None and _cognitive_load > 0.85:
        _organ_route_override = "chat"
        _organ_route_reason = f"proprioception:cognitive_load={_cognitive_load:.2f}"

    # B-1: LimbicAgent motive_state
    _organ_route_meta["motive_withdraw"] = round(float(_motive_state.get("withdraw", 0.0)), 3)
    _organ_route_meta["motive_care"] = round(float(_motive_state.get("care", 0.0)), 3)
    _organ_route_meta["motive_continuity"] = round(float(_motive_state.get("continuity", 0.0)), 3)
    if _organ_route_override is None:
        _withdraw = float(_motive_state.get("withdraw", 0.0))
        if _withdraw > 0.80:
            _organ_route_override = "chat"
            _organ_route_reason = f"limbic:withdraw={_withdraw:.2f}"

    # B-4: PrefrontalAgent 人格漂移状态
    _drift_alert = str(_pf_state.get("alert_level") or "NONE").strip().upper()
    _drift_score = float(_pf_state.get("drift_score") or 0.0)
    _organ_route_meta["prefrontal_drift_alert"] = _drift_alert
    _organ_route_meta["prefrontal_drift_score"] = round(_drift_score, 4)
    if _organ_route_override is None and _drift_alert in {"HIGH", "CRITICAL"}:
        _organ_route_override = "chat"
        _organ_route_reason = f"prefrontal:drift_alert={_drift_alert}:score={_drift_score:.3f}"
        _logger.info(
            "[%s] prefrontal drift override: alert=%s score=%.3f → force chat",
            request_id, _drift_alert, _drift_score,
        )

    # ── Phase D：PrefrontalAgent 意图规划（小模型，<500ms）────────────────
    # 基于器官族完整状态产出"我要做什么"的决策，结果注入 intent_draft 供 surface_reply 使用
    # 仅在 PREFRONTAL_PLAN_ENABLED=true 时启用（默认关，逐步灰度）
    _prefrontal_plan: dict[str, Any] = {}
    _core_settings = ports.get_core_settings()
    _prefrontal_plan_enabled = bool(_core_settings.prefrontal_plan_enabled)
    if _prefrontal_plan_enabled:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            _pf_agent = get_prefrontal_agent()
            # 提取 emotion 信息
            _emotion_tag = str((emotion_context or {}).get("emotion_tag") or "NEUTRAL")
            _valence = float((emotion_context or {}).get("valence") or 0.0)
            _arousal = float((emotion_context or {}).get("arousal") or 0.0)
            # 提取 bond 信息
            _bond_stage = str((bond_summary or {}).get("stage") or "FRIEND")
            _bond_intimacy = float((bond_summary or {}).get("intimacy_score") or 0.0)
            # 提取 recall 摘要
            _recall_summary = str((retrieval_summary or {}).get("summary") or "")
            _prefrontal_plan = await _pf_agent.plan_response(
                user_message=sanitized_message,
                motive_state=_motive_state,
                recall_summary=_recall_summary,
                bond_stage=_bond_stage,
                bond_intimacy=_bond_intimacy,
                immune_risk_level=_immune_risk,
                drift_alert_level=_drift_alert,
                emotion_tag=_emotion_tag,
                valence=_valence,
                arousal=_arousal,
                session_id=session_id,
                request_id=request_id,
                timeout_ms=500,
            )
            _organ_route_meta["prefrontal_plan_action"] = _prefrontal_plan.get("action")
            _organ_route_meta["prefrontal_plan_source"] = _prefrontal_plan.get("source")
            _organ_route_meta["prefrontal_plan_latency_ms"] = _prefrontal_plan.get("latency_ms")
            # plan_response 的 action 覆盖路由（仅当 organ_route_override 未设置时）
            _plan_action = str(_prefrontal_plan.get("action") or "").strip().lower()
            if _organ_route_override is None and _plan_action in {"skill", "delegate"}:
                _plan_target = str(_prefrontal_plan.get("target") or "").strip()
                if _plan_target:
                    _organ_route_override = _plan_action
                    _organ_route_reason = f"prefrontal_plan:{_plan_action}:{_plan_target}"
                    _logger.info(
                        "[%s] prefrontal_plan override: action=%s target=%s",
                        request_id, _plan_action, _plan_target,
                    )
            # 将 prefrontal_task 注入 intent_draft，供 surface_reply 作为语言化目标
            if isinstance(intent_draft, dict):
                intent_draft = dict(intent_draft)
            else:
                intent_draft = {}
            if _prefrontal_plan.get("task"):
                intent_draft["prefrontal_task"] = _prefrontal_plan.get("task")
            if _prefrontal_plan.get("tone_hint"):
                intent_draft["prefrontal_tone_hint"] = _prefrontal_plan.get("tone_hint")
        except Exception as _pf_plan_exc:
            _logger.debug("[%s] prefrontal plan_response 跳过: %s", request_id, _pf_plan_exc)

    route = _hard_rule_route(
        intent_draft=dict(intent_draft or {}),
        sanitized_message=sanitized_message,
        skill_registry=skill_registry,
        surface_task_control=dict(getattr(request_body, "surface_task_control", {}) or {}),
    )

    # 应用器官族路由覆盖（仅覆盖 skill/delegate/plan，不覆盖 recent_turn/task_progress）
    if _organ_route_override is not None and route.route_type not in {"recent_turn", "task_progress"}:
        _logger.info(
            "[%s] organ_route_override: %s → %s (reason=%s)",
            request_id, route.route_type, _organ_route_override, _organ_route_reason,
        )
        route = _RouteSelection(_organ_route_override, "", _organ_route_reason or "organ_override")

    # B-4: 器官族路由影响写入 trace_stage_meta（可观测性）
    if _organ_route_meta:
        _organ_route_meta["organ_route_override"] = _organ_route_override
        _organ_route_meta["organ_route_reason"] = _organ_route_reason
        if isinstance(intent_draft, dict):
            intent_draft = dict(intent_draft)
            intent_draft.setdefault("_organ_route_meta", _organ_route_meta)

    if _should_force_complex_plan(
        ports=ports,
        route=route,
        user_message=sanitized_message,
        intent_draft=dict(intent_draft or {}),
        llm_route_used=llm_route_used,
    ):
        route = _RouteSelection("plan", "skill_dag", "auto_plan:complex_task")

    _bio_ops_runtime_trace: dict[str, Any] = {}
    try:
        from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace  # noqa: PLC0415

        _bio_ops_runtime_trace = build_bio_ops_runtime_trace(
            message=sanitized_message,
            route_type=route.route_type,
            target_id=route.target_id,
            intent_draft=dict(intent_draft or {}),
            emotion_context=dict(emotion_context or {}),
            runtime_signals={
                "current_hour": int(time.localtime().tm_hour),
                "codex_queue_size": 0,
                "tts_status": "ok",
                "user_stress": str((emotion_context or {}).get("stress_level") or ""),
                "explicit_help_request": route.route_type in {"skill", "delegate", "plan"},
            },
        )
        if isinstance(intent_draft, dict):
            intent_draft = dict(intent_draft)
            intent_draft["_bio_ops_runtime_trace"] = _bio_ops_runtime_trace
    except Exception as _bio_ops_exc:
        _bio_ops_runtime_trace = {
            "source": "bio_ops.runtime_trace",
            "proposal_only": True,
            "status": "DEGRADED",
            "reason_code": "BIO_OPS_RUNTIME_TRACE_FAILED",
            "detail": str(_bio_ops_exc)[:200],
        }
    # This registry is intentionally confined to the legacy Executive
    # compatibility runtime. Canonical routes are admitted by Cortex directly
    # into the durable Governed Task Runtime and cannot reach this function.
    legacy_task_registry = ports.get_legacy_runtime_task_registry()
    runtime_task = legacy_task_registry.create_task(
        task_kind=route.route_type,
        target_id=route.target_id,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        source="legacy_executive_runtime",
        detail=route.reason_code,
        progress_message=f"route={route.route_type}",
        meta={
            "intent_id": intent_id,
            "render_target_lang": render_target_lang,
        },
    )

    runtime_capability_payload = None
    relational_reason = build_relational_chat_reason_code(sanitized_message)
    if route.route_type == "chat":
        simple_surface_technical_advice = _looks_like_simple_technical_advice(sanitized_message, intent_draft)
        simple_surface_task_split = _looks_like_simple_task_split_advice(sanitized_message, intent_draft)
        surface_main_chain_forced = str(route.reason_code or "").startswith("hard_rule:surface_")
        # Phase D: PREFRONTAL_PLAN_ENABLED 时，前额叶已规划，跳过 runtime_capability 合成
        _skip_capability_synthesis = (
            _prefrontal_plan_enabled
            and bool(_prefrontal_plan.get("action"))
        ) or surface_main_chain_forced or simple_surface_technical_advice or simple_surface_task_split
        if not _skip_capability_synthesis:
            runtime_capability_payload = _maybe_synthesize_runtime_capability(
                ports=ports,
                request_id=request_id,
                session_id=session_id,
                user_message=sanitized_message,
                route_type=route.route_type,
                target_id=route.target_id,
                reason_code=route.reason_code,
                llm_route_used=llm_route_used,
            )
        else:
            _logger.debug(
                "[%s] skip runtime_capability synthesis prefrontal_action=%s surface_main_chain_forced=%s simple_surface_technical_advice=%s simple_surface_task_split=%s",
                request_id, _prefrontal_plan.get("action"), surface_main_chain_forced, simple_surface_technical_advice, simple_surface_task_split,
            )
        if runtime_capability_payload is not None and not relational_reason:
            route = _RouteSelection(
                route_type=_normalize_text(runtime_capability_payload.get("executor_route"), lower=True) or "delegate",
                target_id=_normalize_text(runtime_capability_payload.get("executor_target")) or "intent_agent",
                reason_code="capability_gap:runtime_synthesized",
            )
            legacy_task_registry.update_task(
                runtime_task.task_id,
                task_kind=route.route_type,
                target_id=route.target_id,
                detail=route.reason_code,
                progress_message="runtime capability synthesized",
                meta={
                    **dict(runtime_task.meta or {}),
                    "runtime_capability_payload": runtime_capability_payload,
                },
            )
        elif relational_reason:
            route = _RouteSelection("chat", "", relational_reason)

    plan = None
    if route.route_type == "plan":
        plan = _maybe_build_multi_step_plan(
            request_id=request_id,
            sanitized_message=sanitized_message,
            intent_draft=dict(intent_draft or {}),
            skill_registry=skill_registry,
            parallel_window=int(_core_settings.skill_dag_parallel_window),
        )
        if plan is None:
            route = _RouteSelection("delegate", "intent_agent", "plan_fallback:intent_agent")

    yield ports.encode_sse(
        {
            "phase": "EXECUTIVE_ROUTE",
            "done": False,
            "request_id": request_id,
            "route_type": route.route_type,
            "target_id": route.target_id or None,
            "reason_code": route.reason_code,
            "runtime_task_id": runtime_task.task_id,
            "runtime_capability_id": dict(runtime_capability_payload or {}).get("runtime_capability", {}).get("runtime_capability_id"),
            "bio_ops_runtime_trace": _bio_ops_runtime_trace,
        }
    )

    if route.route_type == "chat":
        async for event in _stream_chat_with_context(
            ports=ports,
            request_body=request_body,
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta={
                "cortex_orchestrator_used": True,
                "executive_router_used": True,
                "skill_route_type": "chat",
                "skill_family": "social",
                "skill_route_reason": route.reason_code,
                "bio_ops_runtime_trace": _bio_ops_runtime_trace,
            },
            sub_agent_context=None,
            runtime_capability_payload=runtime_capability_payload,
            governor_result=governor_result,
        ):
            yield event
        legacy_task_registry.mark_completed(
            runtime_task.task_id,
            detail="chat route completed",
            result_text="chat stream completed",
            result_code="CHAT_COMPLETED",
        )
        return

    if plan is not None:
        legacy_task_registry.update_task(
            runtime_task.task_id,
            progress_message=f"executing plan {plan.plan_id}",
            meta={
                **dict(runtime_task.meta or {}),
                "plan": plan.as_dict(),
            },
        )
        plan_governor_result = dict(governor_result or {})
        plan_governor_result["node_grants"] = _build_http_plan_node_grants(
            ports=ports,
            plan=plan,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_draft=dict(intent_draft or {}),
            governor_result=dict(governor_result or {}),
            skill_registry=skill_registry,
        )
        plan_outcome, plan_meta = await _execute_skill_dag(
            ports=ports,
            plan=plan,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            governor_result=plan_governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
        plan_chat_outcome = _build_plan_chat_outcome(
            plan=plan,
            outcome=plan_outcome,
            plan_meta=plan_meta,
        )
        trace_stage_meta = {
            "cortex_orchestrator_used": True,
            "executive_router_used": True,
            "skill_route_type": "plan",
            "skill_family": "executive",
            "skill_route_reason": route.reason_code,
            "plan_id": plan.plan_id,
            "parallel_enabled": bool(plan_meta.get("parallel_enabled")),
            # Phase B-4: 器官族路由影响可观测性字段
            "organ_route_override": _organ_route_override,
            "organ_route_reason": _organ_route_reason,
            "organ_route_meta": _organ_route_meta if _organ_route_meta else None,
            "bio_ops_runtime_trace": _bio_ops_runtime_trace,
        }
        if plan_outcome.success:
            legacy_task_registry.mark_completed(
                runtime_task.task_id,
                detail="plan completed",
                result_text=plan_outcome.result_text,
                result_code="PLAN_COMPLETED",
            )
        else:
            legacy_task_registry.mark_failed(
                runtime_task.task_id,
                detail="plan failed",
                error=plan_outcome.error_text,
                result_code="PLAN_FAILED",
                result_text=plan_outcome.result_text,
            )
        async for event in _stream_chat_with_context(
            ports=ports,
            request_body=request_body,
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta=trace_stage_meta,
            sub_agent_context=_build_sub_agent_context(
                ports=ports,
                agent_id="plan_execution",
                task_text=sanitized_message,
                outcome=plan_chat_outcome,
            ),
            runtime_capability_payload=runtime_capability_payload,
            governor_result=governor_result,
        ):
            yield event
        return

    route_decision = build_skill_route_decision(
        route_type=route.route_type,
        target_id=route.target_id,
        reason_code=route.reason_code,
        skill_registry=skill_registry,
    )
    task_text = _normalize_text(dict(runtime_capability_payload or {}).get("task_text")) or sanitized_message
    record_executive_handoff(
        ports=ports,
        request_id=request_id,
        session_id=session_id,
        handoff_route=route.route_type,
        handoff_target=route.target_id or None,
        handoff_reason=route.reason_code,
        extra={
            "skill_family": route_decision.skill_family,
            "skill_id": route_decision.skill_id,
        },
    )

    if route.route_type == "skill":
        outcome = await asyncio.to_thread(
            _execute_skill,
            ports=ports,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id=route.target_id,
            task_text=task_text,
            user_message=sanitized_message,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
    else:
        outcome = await _dispatch_to_sub_agent(
            ports=ports,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            target_id=route.target_id or "intent_agent",
            task_text=task_text,
            user_message=sanitized_message,
            context={
                "skill_family": route_decision.skill_family or "general",
                "route_reason": route.reason_code,
            },
        )

    if runtime_capability_payload is not None:
        record_runtime_capability_execution(
            ports=ports,
            request_id=request_id,
            runtime_capability_payload=runtime_capability_payload,
            success=outcome.success,
            latency_ms=outcome.latency_ms,
            error_text=outcome.error_text or None,
            executor_route=route.route_type,
            executor_target=route.target_id or "",
            actor="executive_runtime",
        )

    if not outcome.success:
        recovery_plan = _build_failure_recovery_plan(
            route_type=route.route_type,
            target_id=route.target_id,
            skill_family=route_decision.skill_family or "general",
            payload=outcome.payload,
        )
        if recovery_plan:
            legacy_task_registry.mark_recovering(
                runtime_task.task_id,
                detail=recovery_plan["reason_code"],
                recovery_plan=recovery_plan,
            )
            if recovery_plan["route_type"] == "delegate":
                recovered = await _dispatch_to_sub_agent(
                    ports=ports,
                    request_id=request_id,
                    session_id=session_id,
                    person_id=person_id,
                    target_id=recovery_plan["target_id"],
                    task_text=task_text,
                    user_message=sanitized_message,
                    context={
                        "skill_family": route_decision.skill_family or "general",
                        "route_reason": recovery_plan["reason_code"],
                    },
                )
                legacy_task_registry.attach_recovery_result(
                    runtime_task.task_id,
                    recovery_status="SUCCESS" if recovered.success else "FAILED",
                    recovery_result_text=recovered.result_text,
                    recovery_error=recovered.error_text,
                )
                if recovered.success:
                    outcome = recovered

    if outcome.success:
        legacy_task_registry.mark_completed(
            runtime_task.task_id,
            detail=f"{route.route_type} completed",
            result_text=outcome.result_text,
            result_code="EXECUTED",
        )
    else:
        legacy_task_registry.mark_failed(
            runtime_task.task_id,
            detail=f"{route.route_type} failed",
            error=outcome.error_text,
            result_code="EXECUTION_FAILED",
            result_text=outcome.result_text,
        )

    trace_stage_meta = {
        "cortex_orchestrator_used": True,
        "executive_router_used": True,
        "skill_route_type": route.route_type,
        "skill_family": route_decision.skill_family or "general",
        "skill_id": route_decision.skill_id,
        "skill_route_reason": route.reason_code,
        # Phase B-4: 器官族路由影响可观测性字段
        "organ_route_override": _organ_route_override,
        "organ_route_reason": _organ_route_reason,
        "organ_route_meta": _organ_route_meta if _organ_route_meta else None,
        "bio_ops_runtime_trace": _bio_ops_runtime_trace,
    }
    async for event in _stream_chat_with_context(
        ports=ports,
        request_body=request_body,
        route_reason=route.reason_code,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=_build_sub_agent_context(
            ports=ports,
            agent_id=route.target_id or route.route_type,
            task_text=task_text,
            outcome=outcome,
        ),
        runtime_capability_payload=runtime_capability_payload,
        governor_result=governor_result,
    ):
        yield event
