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
    ui_pusher: Callable[..., dict[str, Any]],
    life_snapshot_reader: Callable[[], Any] | None = None,
    llm_generator_override: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    last_interaction_ts_override: int | None = None,
    motive_state_override: Mapping[str, Any] | None = None,
    render_policy_override: Mapping[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
    runtime_context_application: Any | None = None,
    action_preview_service: Any | None = None,
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

    # Wrap the terminal UI effect before any personality handler is selected.
    # A handler therefore cannot retain an ungoverned pusher reference merely
    # because it returns before the proactive_talk branch below.
    preview_context = dict(execution_context or {}).get(
        "_action_preview_context"
    )
    raw_ui_pusher = ui_pusher

    def previewed_ui_pusher(message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(preview_context, Mapping) or action_preview_service is None:
            raise RuntimeError(
                "ACTION_PREVIEW_INVALID: personality UI effect has no app-local authority"
            )
        from host_brain.runtime.action_preview_authority import (  # noqa: PLC0415
            require_scheduler_derived_action,
        )

        concrete = dict(message or {})
        scope = str(
            concrete.get("delivery_id")
            or concrete.get("request_id")
            or system_intent.get("request_id")
            or "proactive.gateway"
        ).strip()
        request_id = str(concrete.get("request_id") or "").strip()
        if request_id and request_id != scope:
            raise RuntimeError(
                "ACTION_PREVIEW_DRIFT: proactive request_id differs from action scope"
            )
        concrete["request_id"] = scope
        receipt = require_scheduler_derived_action(
            service=action_preview_service,
            preview_context=preview_context,
            action_kind="proactive.ui_push",
            action_scope=scope,
            risk_level=3,
            action_args=concrete,
            guardian_capability_id="proactive_ui_push",
        )
        from host_brain.runtime.action_preview_proof import (  # noqa: PLC0415
            ActionPreviewExecutionProof,
        )

        proof = ActionPreviewExecutionProof(
            service=action_preview_service,
            event_id=str(receipt.get("event_id") or ""),
            boundary=dict(receipt.get("_terminal_execution_boundary") or {}),
        )
        result = dict(raw_ui_pusher(concrete, preview_proof=proof) or {})
        result["action_preview_receipt_ref"] = str(
            receipt.get("receipt_ref") or ""
        )
        result["action_preview_event_id"] = str(
            receipt.get("event_id") or ""
        )
        return result

    ui_pusher = previewed_ui_pusher

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

    try:
        frozen_runtime_context, scene_projection = _require_proactive_runtime_context(
            execution_context=execution_context,
            runtime_context_application=runtime_context_application,
        )
    except Exception as exc:
        return {
            "status": "REJECTED",
            "reason_code": str(
                getattr(exc, "reason_code", None)
                or "PROACTIVE_SCENE_CONTEXT_INVALID"
            ),
            "detail": str(exc)[:300],
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
        profile_provider = ports.get_user_emotion_profile
        if callable(profile_provider):
            profile = profile_provider()
            emotion_context["user_emotion_profile"] = profile.to_summary()
        try:
            timeline_summary = dict(ports.emotion_timeline.rebuild_summary(
                window_ms=6 * 3600 * 1000
            ) or {})
            if not _is_retired_legacy_emotion_timeline(timeline_summary):
                emotion_context["emotion_timeline_summary"] = timeline_summary
        except Exception:
            pass
        try:
            diary = dict(
                ports.emotion_timeline.build_daily_diary(
                    window_ms=24 * 3600 * 1000
                )
                or {}
            )
            if (
                not _is_retired_legacy_emotion_timeline(diary)
                and diary.get("diary_text")
            ):
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
            body_sense = limbic_agent.resolve_body_sense_state(
                person_id=offline_persona_id
            )
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
        # This compatibility projection is derived solely from the exact
        # SceneDecisionV2 admitted in the immutable WorkOrder.  Definition and
        # occurrence payloads cannot nominate the final Scene.
        "scene_awareness": _scene_awareness_from_projection(scene_projection),
        "scene_decision_v2": frozen_runtime_context["scene_decision_v2"],
        "scene_identity_v2": frozen_runtime_context["scene_identity_v2"],
        "inner_weather": proactive_inner_weather_label,
        "active_projects": proactive_active_projects,
        "proactive_readiness": proactive_readiness,
        "intention_hint": proactive_intention_hint,
        "pending_topics": list(recent_topics[:3]),
    }
    from host_brain.skills.impl.proactive_talk import execute_proactive_talk  # noqa: PLC0415

    result = dict(execute_proactive_talk(args, context) or {})
    result.setdefault("skill_name", skill_name)
    result["scene_identity_v2"] = dict(
        frozen_runtime_context["scene_identity_v2"]
    )
    result["scene_decision_digest"] = str(
        frozen_runtime_context["scene_identity_v2"].get("decision_digest") or ""
    )
    return result


class _ProactiveContextError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "PROACTIVE_SCENE_CONTEXT_INVALID")
        super().__init__(detail)


def _require_proactive_runtime_context(
    *,
    execution_context: Mapping[str, Any] | None,
    runtime_context_application: Any | None,
) -> tuple[dict[str, Any], Any]:
    """Verify Task Runtime authority and reopen the exact frozen Scene."""

    context = dict(execution_context or {})
    verified_grant = context.get("verified_node_grant")
    required_execution_fields = (
        "work_order_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "lease_id",
        "fencing_token",
    )
    if not isinstance(verified_grant, Mapping) or any(
        context.get(key) in (None, "") for key in required_execution_fields
    ):
        raise _ProactiveContextError(
            "PROACTIVE_NODE_AUTHORITY_REQUIRED",
            "proactive execution requires a verified governed Scheduler node",
        )
    if any(
        str(verified_grant.get(key) or "").strip()
        != str(context.get(context_key) or "").strip()
        for key, context_key in (
            ("graph_id", "graph_id"),
            ("graph_revision", "graph_revision"),
            ("node_id", "node_id"),
            ("grant_ref", "node_grant_ref"),
        )
    ):
        raise _ProactiveContextError(
            "PROACTIVE_NODE_AUTHORITY_DRIFT",
            "verified proactive node authority differs from execution lineage",
        )
    frozen_input = context.get("frozen_input_pack")
    if not isinstance(frozen_input, Mapping):
        raise _ProactiveContextError(
            "PROACTIVE_SCENE_CONTEXT_REQUIRED",
            "proactive WorkOrder lacks a frozen runtime context",
        )
    require_frozen = getattr(runtime_context_application, "require_frozen", None)
    if not callable(require_frozen):
        raise _ProactiveContextError(
            "PROACTIVE_SCENE_AUTHORITY_REQUIRED",
            "proactive Scene requires an app-local runtime context authority",
        )
    try:
        frozen = require_frozen(dict(frozen_input)).to_dict()
        from host_brain.runtime.scene_consumer_projection import (  # noqa: PLC0415
            project_scene_decision_v2,
        )

        projection = project_scene_decision_v2(
            frozen.get("scene_decision_v2"),
            expected_identity=frozen.get("scene_identity_v2"),
        )
    except Exception as exc:
        raise _ProactiveContextError(
            str(getattr(exc, "reason_code", None) or "PROACTIVE_SCENE_CONTEXT_INVALID"),
            "proactive frozen Scene is missing, tampered, or no longer canonical",
        ) from exc
    return frozen, projection


def _scene_awareness_from_projection(projection: Any) -> dict[str, Any]:
    decision = projection.decision
    scene_id = str(decision.scene_id or "").strip()
    activity = scene_id.removeprefix("scene.").replace("-", "_")
    scene_tags = [activity] if activity else []
    if "game" in activity and "gaming" not in scene_tags:
        scene_tags.append("gaming")
    allowed, _reason = projection.evaluate_proactive("proactive_talk")
    return {
        "scene_tags": scene_tags,
        "current_activity": activity or None,
        "app_name": None,
        "companion_suitable": bool(allowed),
        "source": "SceneDecisionV2",
        "confidence": 1.0,
        "has_screenshot": False,
        "scene_id": scene_id,
        "revision": int(decision.revision),
        "decision_digest": str(decision.decision_digest),
        "reason_codes": list(projection.reason_codes),
        "proactive_policy": str(projection.proactive_policy),
    }
