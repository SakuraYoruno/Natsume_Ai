"""Digital-life runtime snapshot assembly."""

from ._shared import *  # noqa: F403
from .canonical_life_snapshot import (
    build_canonical_life_runtime_snapshot,
    canonical_life_reader_is_composed,
    resolve_runtime_relationship_subject_id,
)
from .ports import LifecycleRuntimeApplicationPorts


def build_digital_life_runtime_snapshot(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    current_topic: str = "",
) -> dict[str, Any]:
    now_ms = int(_time.time() * 1000)
    normalized_topic = str(current_topic or "").strip()
    persona_user_id, subject_status, subject_detail = (
        resolve_runtime_relationship_subject_id()
    )
    canonical_reader_composed = canonical_life_reader_is_composed(ports)
    if canonical_reader_composed or ports.require_canonical_life_snapshot:
        if not canonical_reader_composed:
            # The production app composition has declared the canonical
            # reader mandatory.  A missing provider is a degraded canonical
            # result, never permission to rebuild affect/relationship state
            # from the rollback-window assembler.
            ports.compatibility_usage_meter.record_legacy_runtime_snapshot_fallback_suppressed()
        return _attach_compatibility_usage(
            build_canonical_life_runtime_snapshot(
                ports=ports,
                current_topic=normalized_topic,
                now_ms=now_ms,
                relationship_subject_id=persona_user_id,
                subject_status=subject_status,
                subject_detail=subject_detail,
            ),
            ports=ports,
        )

    # A direct, isolated port graph may temporarily opt into the historic
    # projection while migration tests are retired.  Every such use is
    # explicitly visible; production composition cannot reach this branch.
    ports.compatibility_usage_meter.record_legacy_runtime_snapshot_read()
    slot_status: dict[str, str] = {}
    degraded_reasons: list[str] = []

    if subject_status == "live":
        slot_status["persona_user_id"] = "live"
    else:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="persona_user_id",
            status=subject_status,
            detail=subject_detail,
        )

    hard_state: dict[str, Any] = {}
    try:
        hard_state = _as_plain_dict(ports.get_hard_state())
        slot_status["hard_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="hard_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    # Environment settings are bootstrap input for the app-owned HardState,
    # never a second live runtime truth.  An unavailable/invalid owner keeps
    # the projection in maintenance mode instead of silently failing open.
    sys_mode = str(hard_state.get("sys_mode") or "maintenance").strip().lower()
    if sys_mode not in {
        "normal",
        "focus",
        "gaming",
        "sleep",
        "maintenance",
        "locked",
    }:
        sys_mode = "maintenance"
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="hard_state",
            status="degraded",
            detail="invalid sys_mode",
        )

    activity_monitor: dict[str, Any] = {}
    try:
        # Projection code is read-only.  It must never lazily start a second
        # ActivityMonitor (especially in the OBS process).
        monitor = ports.get_activity_monitor()
        if monitor is not None:
            if hasattr(monitor, "get_status"):
                activity_monitor = _as_plain_dict(monitor.get_status())
            else:
                activity_monitor = {
                    "running": bool(getattr(monitor, "_running", False)),
                    "gaming_check_interval_s": getattr(monitor, "_gaming_interval", None),
                    "idle_check_interval_s": getattr(monitor, "_idle_interval", None),
                }
            slot_status["activity_monitor"] = "live"
        else:
            slot_status["activity_monitor"] = "unavailable"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="activity_monitor",
            status="degraded",
            detail=str(exc)[:120],
        )

    emotion_context: dict[str, Any] = {
        "mood_state": str(hard_state.get("mood_state") or "steady").strip().lower() or "steady",
        "emotion_tag": "NEUTRAL",
        "emotion_intensity": 0.0,
    }
    timeline_summary: dict[str, Any] = {}
    timeline_diary: dict[str, Any] = {}
    try:
        profile_provider = ports.get_user_emotion_profile
        if callable(profile_provider):
            profile = profile_provider()
            profile_summary = _as_plain_dict(
                profile.to_summary() if hasattr(profile, "to_summary") else profile
            )
            if profile_summary:
                # A user-affect profile is an observation about the user, not
                # a self-affect source for Natsume.  Keep it separately so
                # OBS consumers cannot accidentally present it as her mood.
                emotion_context["user_emotion_profile"] = profile_summary

        timeline_summary = dict(ports.emotion_timeline.rebuild_summary(window_ms=6 * 3600 * 1000) or {})
        timeline_retired = _is_retired_legacy_emotion_timeline(
            timeline_summary
        )
        if timeline_summary and not timeline_retired:
            emotion_context["emotion_timeline_summary"] = timeline_summary
            dominant_emotion = str(timeline_summary.get("dominant_emotion") or "").strip().upper()
            if dominant_emotion:
                emotion_context["emotion_tag"] = dominant_emotion
            try:
                emotion_context["emotion_intensity"] = float(
                    timeline_summary.get("emotion_intensity")
                    or timeline_summary.get("avg_intensity")
                    or emotion_context.get("emotion_intensity")
                    or 0.0
                )
            except (TypeError, ValueError):
                pass

        timeline_diary = dict(ports.emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000) or {})
        timeline_retired = timeline_retired or _is_retired_legacy_emotion_timeline(
            timeline_diary
        )
        if timeline_diary and not timeline_retired:
            emotion_context["emotion_diary"] = {
                "dominant_emotion": timeline_diary.get("dominant_emotion"),
                "diary_text": str(timeline_diary.get("diary_text") or "")[:160],
            }

        if timeline_retired:
            # Retirement is an intentional production boundary, not a
            # runtime-health failure.  The status remains observable without
            # turning a healthy Host into a false degraded state.
            slot_status["emotion_context"] = "retired"
        else:
            slot_status["emotion_context"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="emotion_context",
            status="degraded",
            detail=str(exc)[:120],
        )

    endocrine_state: dict[str, Any] = {}
    try:
        endocrine_state = dict(
            ports.get_endocrine_state(
                emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                emotion_intensity=float(emotion_context.get("emotion_intensity") or 0.0),
            )
            or {}
        )
        emotion_context["endocrine_state"] = endocrine_state
        slot_status["endocrine_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="endocrine_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    immune_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

        immune_agent = get_immune_agent()
        immune_state = dict(immune_agent.export_runtime_state() or {})
        if normalized_topic:
            preview = immune_agent.inspect_text(
                text=normalized_topic,
                source="obs.runtime_snapshot.preview",
                metadata={"persona_user_id": persona_user_id},
                record=False,
            )
            immune_state["preview"] = {
                "decision": preview.get("decision"),
                "risk_level": preview.get("risk_level"),
                "score": preview.get("score"),
                "reason_codes": list(preview.get("reason_codes") or []),
            }
        slot_status["immune_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="immune_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    bond_state: dict[str, Any] = {}
    bond_summary: dict[str, Any] = {}
    bond_summary_human: dict[str, Any] = {}
    bond_state_obj: Any = None
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        social_snapshot = get_social_brain_agent().resolve_bond_snapshot(person_id=persona_user_id)
        bond_state_obj = social_snapshot.get("bond_state_obj")
        bond_summary = dict(social_snapshot.get("bond_summary") or {})
        bond_summary_human = _as_plain_dict(
            bond_state_obj.to_summary() if (bond_state_obj is not None and hasattr(bond_state_obj, "to_summary")) else {}
        )
        if bond_state_obj is not None:
            bond_state = {
                "user_id": persona_user_id,
                "current_stage": str(social_snapshot.get("bond_stage") or "FRIEND").strip() or "FRIEND",
                "trend": str(social_snapshot.get("bond_trend") or "").strip(),
                "trend_intensity": str(social_snapshot.get("bond_trend_intensity") or "").strip(),
                "capability_hint": str(social_snapshot.get("capability_hint") or "").strip(),
                "event_count": int(getattr(bond_state_obj, "event_count", 0) or 0),
                "last_event_ts_ms": int(getattr(bond_state_obj, "last_event_ts_ms", 0) or 0),
            }
            slot_status["bond_state"] = "live"
        else:
            slot_status["bond_state"] = "unavailable"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="bond_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    inner_world_state: dict[str, Any] = {}
    offline_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        limbic_agent = get_limbic_agent()
        inner_world_state = dict(limbic_agent.resolve_inner_world_state(person_id=persona_user_id) or {})
        offline_state = dict(limbic_agent.resolve_offline_state(person_id=persona_user_id) or {})
        slot_status["inner_world_state"] = "live"
        slot_status["offline_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="inner_world_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("offline_state", "degraded")

    silence_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        silence_state = _as_plain_dict(
            get_limbic_agent().resolve_silence_state(current_topic=normalized_topic)
        )
        slot_status["silence_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="silence_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    body_sense: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        body_sense = _as_plain_dict(
            get_limbic_agent().resolve_body_sense_state(
                person_id=persona_user_id
            )
        )
        slot_status["body_sense"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="body_sense",
            status="degraded",
            detail=str(exc)[:120],
        )

    self_model_snapshot: dict[str, Any] = {}
    self_reflection_pulse: dict[str, Any] = {}
    snapshot_llm_enabled = _runtime_snapshot_llm_enabled()
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        reflection_payload = get_prefrontal_agent().resolve_self_reflection_context(
            persona_user_id=persona_user_id,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state_obj=bond_state_obj,
            bond_summary=bond_summary,
            now_ms=now_ms,
            trigger_reason="runtime_snapshot",
            use_llm=snapshot_llm_enabled,
        )
        self_model_snapshot = _as_plain_dict(reflection_payload.get("self_model_snapshot"))
        self_reflection_pulse = _as_plain_dict(reflection_payload.get("self_reflection_pulse"))
        slot_status["self_model_snapshot"] = "live"
        slot_status["self_reflection_pulse"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="self_reflection_pulse",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("self_model_snapshot", "degraded")

    bond_narrative: dict[str, Any] = {}
    try:
        from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
        from host_brain.bond.milestone_tracker import MilestoneTracker  # noqa: PLC0415
        from host_brain.bond.shared_memory_index import SharedMemoryIndex  # noqa: PLC0415

        narrative_layer = BondNarrativeLayer(
            memory_index=SharedMemoryIndex(store=ports.get_episodic_store()),
            milestone_tracker=MilestoneTracker(),
            use_llm=snapshot_llm_enabled,
            model_attempt_service=(ports.get_model_attempt_service() if snapshot_llm_enabled else None),
        )
        narrative_result = narrative_layer.build_narrative(
            user_id=persona_user_id,
            bond_stage=str(bond_state.get("current_stage") or "FRIEND").upper(),
            now_ts_ms=now_ms,
        )
        bond_narrative = _as_plain_dict(
            narrative_result.to_prompt_slot() if hasattr(narrative_result, "to_prompt_slot") else narrative_result
        )
        slot_status["bond_narrative"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="bond_narrative",
            status="degraded",
            detail=str(exc)[:120],
        )

    shared_memory_count = 0
    for candidate in (
        bond_narrative.get("shared_memory_count"),
        self_model_snapshot.get("shared_memory_count"),
        bond_summary_human.get("shared_memory_count"),
    ):
        try:
            shared_memory_count = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    motive_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        motive_state = get_limbic_agent().compute_motive_state(
            silence_state=silence_state or None,
            body_sense=body_sense or None,
            offline_state=offline_state or None,
            bond_summary=bond_summary or None,
            self_reflection_pulse=self_reflection_pulse or None,
            emotion_context=emotion_context or None,
            pending_followup_topics=list(offline_state.get("anticipation_topics") or []),
            shared_memory_count=shared_memory_count,
            user_message=normalized_topic,
            runtime_flags={
                "source": "obs_runtime_snapshot",
                "sys_mode": sys_mode,
                "activity_monitor": activity_monitor,
            },
        )
        slot_status["motive_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="motive_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    render_policy: dict[str, Any] = {}
    try:
        from host_brain.persona.reply_plan import build_reply_plan_from_inputs  # noqa: PLC0415

        render_policy = dict(
            build_reply_plan_from_inputs(
                emotion_context=emotion_context or None,
                body_sense=body_sense or None,
                offline_state=offline_state or None,
                silence_state=silence_state or None,
                bond_summary=bond_summary or None,
                motive_state=motive_state or None,
            ).as_dict()
            or {}
        )
        slot_status["render_policy"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="render_policy",
            status="degraded",
            detail=str(exc)[:120],
        )

    somatic_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415

        somatic_state = dict(get_somatic_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="somatic_state",
            payload=somatic_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="somatic_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    peripheral_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415

        peripheral_state = dict(get_peripheral_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="peripheral_state",
            payload=peripheral_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="peripheral_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    circadian_state: dict[str, Any] = {}
    proprioception_state: dict[str, Any] = {}
    initiative_decision: dict[str, Any] = {}
    try:
        from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
        from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
        from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415

        circadian_state = dict(get_circadian_agent().export_runtime_state() or {})
        proprioception_state = dict(get_proprioception_agent().export_runtime_state() or {})
        initiative_decision = dict(get_initiative_governor_agent().get_last_decision() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="circadian_state",
            payload=circadian_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="proprioception_state",
            payload=proprioception_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="initiative_decision",
            payload=initiative_decision,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="circadian_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("proprioception_state", "degraded")
        slot_status.setdefault("initiative_decision", "degraded")

    default_mode_state: dict[str, Any] = {}
    mirror_neuron_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
        from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415

        default_mode_state = dict(get_default_mode_agent().export_runtime_state() or {})
        mirror_neuron_state = dict(get_mirror_neuron_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="default_mode_state",
            payload=default_mode_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="mirror_neuron_state",
            payload=mirror_neuron_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="default_mode_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("mirror_neuron_state", "degraded")

    memory_layers: dict[str, Any] = {}
    try:
        from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

        memory_layers = dict(get_hippocampus_agent().resolve_memory_layer_snapshot() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="memory_layers",
            payload=memory_layers,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="memory_layers",
            status="degraded",
            detail=str(exc)[:120],
        )

    language_dual_system: dict[str, Any] = {}
    try:
        from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

        language_agent = get_language_system_agent()
        language_row = language_agent.state_registry.get_state("language_system_agent") or {}
        language_state = dict(language_row.get("state") or {})
        language_dual_system = dict(language_state.get("last_dual_system") or {})
        if not language_dual_system and normalized_topic:
            bond_stage = str(
                bond_state.get("current_stage")
                or bond_summary.get("stage")
                or "friend"
            ).strip().lower() or "friend"
            emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            loneliness_level = _coerce_float(
                body_sense.get("loneliness"),
                _coerce_float(offline_state.get("loneliness_level"), 0.0),
            )
            language_dual_system = dict(
                language_agent.resolve_dual_system(
                    user_text=normalized_topic,
                    bond_stage=bond_stage,
                    emotion_tag=emotion_tag,
                    loneliness_level=loneliness_level,
                    high_loneliness_flag=loneliness_level >= 0.7,
                    response_goal="obs_runtime_snapshot",
                )
                or {}
            )
        if not language_dual_system:
            fallback_style = dict(language_state.get("last_language_style") or {})
            if fallback_style:
                language_dual_system = {"style": fallback_style}
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="language_dual_system",
            payload=language_dual_system,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="language_dual_system",
            status="degraded",
            detail=str(exc)[:120],
        )

    homeostasis_report: dict[str, Any] = {}
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

        homeostasis_report = dict(
            get_global_registry().get_homeostasis_report(now_ts_ms=now_ms)
            or {}
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="homeostasis_report",
            payload=homeostasis_report,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="homeostasis_report",
            status="degraded",
            detail=str(exc)[:120],
        )

    interoception_state: dict[str, Any] = {}
    motor_cortex_state: dict[str, Any] = {}
    cerebellum_state: dict[str, Any] = {}
    glymphatic_state: dict[str, Any] = {}
    vestibular_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
        from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
        from host_brain.tribe.cerebellum_agent import get_cerebellum_agent  # noqa: PLC0415
        from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
        from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415

        interoception_state = dict(get_interoception_agent().export_runtime_state() or {})
        motor_cortex_state = dict(get_motor_cortex_agent().export_runtime_state() or {})
        cerebellum_state = dict(get_cerebellum_agent().export_runtime_state() or {})
        glymphatic_state = dict(get_glymphatic_agent().export_runtime_state() or {})
        vestibular_state = dict(get_vestibular_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="interoception_state",
            payload=interoception_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="motor_cortex_state",
            payload=motor_cortex_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="cerebellum_state",
            payload=cerebellum_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="glymphatic_state",
            payload=glymphatic_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="vestibular_state",
            payload=vestibular_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="interoception_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("motor_cortex_state", "degraded")
        slot_status.setdefault("cerebellum_state", "degraded")
        slot_status.setdefault("glymphatic_state", "degraded")
        slot_status.setdefault("vestibular_state", "degraded")

    runtime_summary = _build_runtime_summary(
        current_topic=normalized_topic,
        sys_mode=sys_mode,
        activity_monitor=activity_monitor,
        bond_state=bond_state,
        bond_summary=bond_summary,
        offline_state=offline_state,
        silence_state=silence_state,
        body_sense=body_sense,
        self_reflection_pulse=self_reflection_pulse,
        bond_narrative=bond_narrative,
        motive_state=motive_state,
        render_policy=render_policy,
        slot_status=slot_status,
        degraded_reasons=degraded_reasons,
    )
    background_runtime = _build_background_runtime_snapshot(
        now_ms=now_ms,
        persona_user_id=persona_user_id,
        inner_world_state=inner_world_state,
    )

    return _attach_compatibility_usage(
        {
            "persona_user_id": persona_user_id,
            "current_topic": normalized_topic,
            "sys_mode": sys_mode,
            "hard_state": hard_state,
            "activity_monitor": activity_monitor,
            "emotion_context": emotion_context,
            "endocrine_state": endocrine_state,
            "immune_state": immune_state,
            "bond_state": bond_state,
            "bond_summary": bond_summary,
            "bond_summary_human": bond_summary_human,
            "inner_world_state": inner_world_state,
            "offline_state": offline_state,
            "silence_state": silence_state,
            "body_sense": body_sense,
            "self_model_snapshot": self_model_snapshot,
            "self_reflection_pulse": self_reflection_pulse,
            "bond_narrative": bond_narrative,
            "motive_state": motive_state,
            "render_policy": render_policy,
            "somatic_state": somatic_state,
            "peripheral_state": peripheral_state,
            "circadian_state": circadian_state,
            "proprioception_state": proprioception_state,
            "initiative_decision": initiative_decision,
            "default_mode_state": default_mode_state,
            "mirror_neuron_state": mirror_neuron_state,
            "memory_layers": memory_layers,
            "language_dual_system": language_dual_system,
            "homeostasis_report": homeostasis_report,
            "interoception_state": interoception_state,
            "motor_cortex_state": motor_cortex_state,
            "cerebellum_state": cerebellum_state,
            "glymphatic_state": glymphatic_state,
            "vestibular_state": vestibular_state,
            "runtime_summary": runtime_summary,
            "background_runtime": background_runtime,
            "slot_status": slot_status,
            "degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "timestamp": now_ms,
        },
        ports=ports,
    )


def _attach_compatibility_usage(
    snapshot: dict[str, Any],
    *,
    ports: LifecycleRuntimeApplicationPorts,
) -> dict[str, Any]:
    """Expose rollback-window use without exposing user-derived content."""

    snapshot["compatibility_projection_usage"] = (
        ports.compatibility_usage_meter.snapshot()
    )
    return snapshot
