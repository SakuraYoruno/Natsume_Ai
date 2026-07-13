"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from ._shared import *  # noqa: F403
from .ports import LifecycleRuntimeApplicationPorts

def run_proactive_intent_dispatch(
    system_intent: dict[str, Any],
    *,
    ports: LifecycleRuntimeApplicationPorts,
    logger: Any,
    get_ledger: Callable[[], Any],
    retention_policy: Any,
    ui_pusher: Callable[[dict[str, Any]], dict[str, Any]],
    life_snapshot_reader: Callable[[], Any] | None = None,
    llm_generator_override: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    last_interaction_ts_override: int | None = None,
    motive_state_override: Mapping[str, Any] | None = None,
    render_policy_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    skill_name = str(system_intent.get("skill_name", "") or "").strip()
    args = dict(system_intent.get("args") or {})
    if skill_name == "audit_retention":
        try:
            from host_brain.audit.retention import build_retention_policy_from_env, run_retention_policy  # noqa: PLC0415
            from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415

            request_id = str(system_intent.get("request_id") or _new_id())
            now_value = int(_time.time() * 1000)
            policy = retention_policy if retention_policy is not None else build_retention_policy_from_env(now_ts_ms=now_value)
            result = run_retention_policy(
                ledger=get_ledger(),
                policy=policy,
                request_id=request_id,
                now_ts_ms=now_value,
            )
            payload = result.as_dict()
            payload["status"] = "OK" if result.success else "FAILED"
            return payload
        except Exception as retention_exc:
            logger.warning("audit_retention 调度执行失败: %s", retention_exc)
            return {
                "status": "FAILED",
                "reason_code": "AUDIT_RETENTION_FAILED",
                "detail": str(retention_exc),
            }

    if skill_name == "long_term_memory_distillation":
        return run_long_term_distillation_handler(args, {}, ports=ports)

    if skill_name == "life_memory_sleep_replay":
        return run_life_memory_sleep_replay_handler(args, {}, ports=ports)

    if skill_name == "evolution_cycle":
        return _run_evolution_cycle(system_intent=system_intent, logger=logger)

    if skill_name == "emotion_decay_tick":
        return _run_emotion_decay_tick(system_intent=system_intent, logger=logger)

    codex_production_result = _run_codex_production_scheduler_skill(
        system_intent=system_intent,
        logger=logger,
    )
    if codex_production_result is not None:
        return codex_production_result

    if skill_name != "proactive_talk":
        personality_result = _run_personality_scheduler_skill(
            ports=ports,
            system_intent=system_intent,
            logger=logger,
            get_ledger=get_ledger,
            ui_pusher=ui_pusher,
            llm_generator_override=llm_generator_override,
        )
        if personality_result is not None:
            return personality_result
        return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}

    authority_inputs = _scheduler_skill_authority_inputs(system_intent)
    if authority_inputs is None:
        return {
            "status": "REJECTED",
            "reason_code": "SKILL_AUTHORITY_MISSING",
            "skill_name": skill_name,
        }

    last_interaction_ts = (
        int(last_interaction_ts_override)
        if last_interaction_ts_override is not None
        else _load_last_interaction_ts(get_ledger=get_ledger)
    )

    bond_summary: dict[str, Any] = {}
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        bond_summary = get_social_brain_agent().resolve_scheduler_bond_summary(session_id="default")
    except Exception:
        pass

    recent_topics = _load_recent_topics(get_ledger=get_ledger)
    diary_snippet = _load_recent_diary_snippet()
    dream_snippet = _load_recent_dream_snippet(get_ledger=get_ledger)
    llm_generator = llm_generator_override or _build_llm_generator(
        ports=ports,
        logger=logger,
        recent_topics=recent_topics,
        diary_snippet=diary_snippet,
        dream_snippet=dream_snippet,
    )

    emotion_context: dict[str, Any] = {}
    try:
        emotion_context["emotion_tag"] = system_intent.get("emotion_tag", "NEUTRAL")
        emotion_context["mood_state"] = system_intent.get("mood_state", "steady")
        profile = ports.get_user_emotion_profile()
        emotion_context["user_emotion_profile"] = profile.to_summary()
        try:
            emotion_context["emotion_timeline_summary"] = ports.emotion_timeline.rebuild_summary(
                window_ms=6 * 3600 * 1000
            )
        except Exception:
            pass
        try:
            diary = ports.emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
            if diary.get("diary_text"):
                emotion_context["emotion_diary"] = {
                    "diary_text": str(diary["diary_text"])[:120],
                    "dominant_emotion": diary.get("dominant_emotion"),
                }
        except Exception:
            pass
    except Exception as emotion_exc:
        logger.debug("方向B: 情感上下文读取异常（忽略）: %s", emotion_exc)

    persona_user_id = ""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

        persona_user_id = str(get_canonical_bond_user_id() or "").strip()
    except Exception:
        persona_user_id = ""

    resolved_sys_mode = str(
        system_intent.get("sys_mode")
        or "normal"
    ).strip().lower() or "normal"

    motive_state: dict[str, Any] = dict(motive_state_override or {})
    if motive_state_override is None:
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            current_topic = recent_topics[0] if recent_topics else ""
            pending_topics = list(recent_topics[:3])
            motive_state = get_limbic_agent().compute_runtime_motive_state(
                persona_user_id=persona_user_id,
                current_topic=current_topic,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                pending_topics=pending_topics,
                runtime_flags={"source": "proactive_talk", "sys_mode": resolved_sys_mode},
            )
        except Exception as motive_exc:
            logger.debug("proactive motive_state compute failed（忽略）: %s", motive_exc)

    render_policy: dict[str, Any] = dict(render_policy_override or {})
    if render_policy_override is None:
        try:
            from host_brain.persona.reply_plan import build_reply_plan_from_inputs  # noqa: PLC0415
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            current_topic = recent_topics[0] if recent_topics else ""
            limbic_agent = get_limbic_agent()
            offline_persona_id = persona_user_id
            if not offline_persona_id:
                try:
                    from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415

                    offline_persona_id = get_canonical_persona_self_id()
                except Exception:
                    offline_persona_id = "natsume_ai"
            offline_state = limbic_agent.resolve_offline_state(person_id=offline_persona_id)
            silence_state = limbic_agent.resolve_silence_state(current_topic=current_topic)
            body_sense = limbic_agent.resolve_body_sense_state()
            render_policy = dict(
                build_reply_plan_from_inputs(
                    silence_state=silence_state,
                    body_sense=body_sense,
                    offline_state=offline_state,
                    bond_summary=bond_summary,
                    emotion_context=emotion_context,
                    motive_state=motive_state,
                ).as_dict()
                or {}
            )
        except Exception as policy_exc:
            logger.debug("proactive render_policy compute failed（忽略）: %s", policy_exc)

    # C2-2: 状态驱动主动——从 life-state 取项目/天气，算就绪度与意图开场。
    # life-state 无信号（新系统/loop 未跑）时 readiness 保持 None，execute 回退纯空闲门（向后兼容，绝不误 hold）。
    proactive_active_projects: list[Any] = []
    proactive_inner_weather_label = ""
    proactive_reach_out_bias = 0.0
    proactive_calibration: float | None = None
    try:
        if callable(life_snapshot_reader):
            from host_brain.digital_life.application_boundary import read_life_snapshot  # noqa: PLC0415

            _life = dict(
                read_life_snapshot(
                    persona_user_id or "default",
                    reader=life_snapshot_reader(),
                ).get("state")
                or {}
            )
            _weather = _as_plain_dict(_life.get("inner_weather"))
            proactive_inner_weather_label = str(_weather.get("label") or "")
            proactive_reach_out_bias = float(_weather.get("reach_out_bias") or 0.0)
            _growth = _as_plain_dict(_life.get("self_growth"))
            proactive_active_projects = list(_growth.get("active_projects") or _life.get("self_projects") or [])
            from host_brain.digital_life.policy import resolve_reach_out_calibration  # noqa: PLC0415

            proactive_calibration = resolve_reach_out_calibration(_life)
    except Exception as life_exc:
        logger.debug("proactive life-state fetch failed（忽略）: %s", life_exc)

    proactive_readiness: float | None = None
    proactive_intention_hint = ""
    if proactive_inner_weather_label or proactive_active_projects:
        try:
            from host_brain.persona.emotional_proactive import (  # noqa: PLC0415
                _project_nudge_opening,
                _project_nudge_pull,
                compute_proactive_readiness,
            )

            _now_ms = int(_time.time() * 1000)
            proactive_readiness = compute_proactive_readiness(
                reach_out_bias=proactive_reach_out_bias,
                active_projects=proactive_active_projects,
                anticipation=0.0,
                now_ms=_now_ms,
                calibration=proactive_calibration,
            )
            _nudge_pull, _nudge_project = _project_nudge_pull(proactive_active_projects, _now_ms)
            if _nudge_pull >= 0.5 and _nudge_project:
                proactive_intention_hint = _project_nudge_opening(_nudge_project)
        except Exception as readiness_exc:
            logger.debug("proactive readiness compute failed（忽略）: %s", readiness_exc)
            proactive_readiness = None

    context = {
        "sys_mode": resolved_sys_mode,
        "request_id": system_intent.get("request_id"),
        "intent_id": system_intent.get("intent_id"),
        "ui_pusher": ui_pusher,
        "last_interaction_ts": last_interaction_ts,
        "bond_summary": bond_summary,
        "llm_generator": llm_generator,
        "user_name": "主人",
        "emotion_context": emotion_context,
        "motive_state": motive_state,
        "render_policy": render_policy,
        "scene_awareness": system_intent.get("scene_awareness") or args.get("scene_awareness") or {},
        "inner_weather": proactive_inner_weather_label,
        "active_projects": proactive_active_projects,
        "proactive_readiness": proactive_readiness,
        "intention_hint": proactive_intention_hint,
        "pending_topics": list(recent_topics[:3]),
    }
    del context, authority_inputs
    return {
        "status": "REJECTED",
        "reason_code": "SKILL_AUTHORITY_MISSING",
        "skill_name": skill_name,
    }
