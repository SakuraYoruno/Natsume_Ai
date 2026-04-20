"""prompt_builder_node.py — 节点完整版 Prompt 构建（含所有上下文注入）。

拆分自 prompt_builder.py（P2-10）：
  - render_node_generation_prompt: 节点生成提示词（完整版，含 prompt_context）
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_prompt_context,
    get_chat_runtime_prompt_context_validation,
    get_chat_runtime_emotion_snapshot,
    get_chat_runtime_emotion_state,
    resolve_prompt_context_style_row,
    get_chat_runtime_render_target_lang,
    get_chat_runtime_retrieval,
    get_chat_runtime_skill_manifest,
    get_chat_runtime_user_message,
)
from host_brain.app.prompt_builder_utils import (
    CHAT_DIRECT_NODE_ID,
    CHAT_INTENT_SOURCE_NODE_ID,
    _build_character_dynamic_patch,
    _build_direct_response_priority,
    _build_node_role_instruction,
    _contains_any_token,
    _format_template_text,
    _get_prompt_evidence_limit,
    _load_character_profile,
    _load_template_mapping,
    _mapping_list,
    _mapping_section,
    _mapping_text,
    _set_injection_meta,
    _truncate_for_prompt,
    extract_gateway_narrative,
    extract_node_narrative_text,
    extract_node_runtime_payload,
    repair_utf8_latin1_mojibake,
)


def render_node_generation_prompt(*, node_id: str, runtime_payload: Mapping[str, Any]) -> str:
    """节点生成提示词（完整版，含 prompt_context 所有上下文注入）。"""
    user_message = _truncate_for_prompt(get_chat_runtime_user_message(runtime_payload), limit=1000)
    render_target_lang = get_chat_runtime_render_target_lang(runtime_payload)
    injection_templates = _load_template_mapping(
        "prompt_builder.injection_guard.templates",
        render_target_lang=render_target_lang,
    )

    # ── Ref #64：MemoryGuard 提示词注入检测 ──────────────────────────────
    _injection_meta: dict[str, Any] = {"mode": "SKIPPED"}
    try:
        from host_brain.app.memory.memory_guard import MemoryGuard
        _mg_inj = MemoryGuard()
        _inj_result = _mg_inj.check_prompt_injection(user_message)
        _inj_detected = bool(_inj_result.get("detected", False))
        _inj_risk = str(_inj_result.get("risk_level") or "LOW").upper()
        _inj_patterns = list(_inj_result.get("matched_patterns") or [])
        _injection_meta = {"mode": "ACTIVE", "detected": _inj_detected, "risk_level": _inj_risk, "matched_patterns": _inj_patterns}
        if _inj_detected and _inj_risk in ("HIGH", "CRITICAL"):
            _alert_text = _mapping_text(injection_templates, "security_alert_template", risk_level=_inj_risk, matched_patterns=_inj_patterns[:3])
            user_message = f"{user_message}\n\n{_alert_text}"
    except (ImportError, AttributeError):
        _injection_meta = {"mode": "SKIPPED_NOT_WIRED"}
    except Exception as _inj_exc:
        _injection_meta = {"mode": "DEGRADED", "error": str(_inj_exc)[:80]}
    _set_injection_meta(_injection_meta)

    prompt_context = get_chat_runtime_prompt_context(runtime_payload)
    prompt_context_validation = get_chat_runtime_prompt_context_validation(runtime_payload)
    retrieval = get_chat_runtime_retrieval(runtime_payload)
    retrieval_status = str(retrieval.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    retrieval_reason = str(retrieval.get("reason_code") or "").strip() or "NONE"
    _render_contract_templates = _load_template_mapping(
        "prompt_builder.render_contract.blocks",
        render_target_lang=render_target_lang,
    )
    _runtime_section_templates = _load_template_mapping(
        "prompt_builder.runtime_sections.templates",
        render_target_lang=render_target_lang,
    )
    _render_contract_empty_placeholder = _mapping_text(_render_contract_templates, "empty_placeholder")

    retrieval_slot_rows = prompt_context.get("retrieval")
    slot_rows = retrieval_slot_rows if isinstance(retrieval_slot_rows, list) else []
    rows = slot_rows if slot_rows else (retrieval.get("evidence_items") or [])

    evidence_lines: list[str] = []
    for row in rows[:6]:
        if not isinstance(row, Mapping):
            continue
        evidence_ref = _truncate_for_prompt(row.get("evidence_ref") or row.get("source_ref") or row.get("citation") or "unknown", limit=80)
        snippet = _truncate_for_prompt(row.get("content") or row.get("snippet"), limit=220)
        if not snippet:
            continue
        evidence_lines.append(f"- [{evidence_ref}] {snippet}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else _render_contract_empty_placeholder

    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled
        if _check_channel("RETRIEVAL"):
            evidence_block = pii_scrub_if_enabled(evidence_block, context="retrieval_evidence")
    except Exception:
        pass

    authoritative_rows = prompt_context.get("authoritative")
    world_rows = authoritative_rows if isinstance(authoritative_rows, list) else []
    world_lines: list[str] = []
    for row in world_rows[:3]:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("kind") or "").strip().lower() != "world_canon":
            continue
        citation = _truncate_for_prompt(row.get("citation") or row.get("doc_id") or "unknown", limit=96)
        content = _truncate_for_prompt(row.get("content"), limit=260)
        if not content:
            continue
        world_lines.append(f"- [{citation}] {content}")
    world_block = "\n".join(world_lines) if world_lines else _render_contract_empty_placeholder

    constraints_raw = prompt_context.get("constraints")
    constraints = dict(constraints_raw) if isinstance(constraints_raw, Mapping) else {}
    constraints_json = _truncate_for_prompt(json.dumps(constraints, ensure_ascii=False, sort_keys=True), limit=1200)
    _follow_up_hint = str(constraints.get("follow_up_hint") or "").strip()
    _bond_texture_raw = prompt_context.get("bond_texture")
    _bond_texture = dict(_bond_texture_raw) if isinstance(_bond_texture_raw, Mapping) else {}
    _bond_stage_for_prompt = str(_bond_texture.get("bond_stage") or (dict(prompt_context.get("bond_summary") or {}).get("stage")) or "").strip().upper()
    prompt_context_valid = bool(prompt_context_validation.get("valid", False))
    prompt_context_reason = str(prompt_context_validation.get("reason_code") or "NONE").strip() or "NONE"
    prompt_context_violation_count = int(prompt_context_validation.get("violation_count") or 0)

    core_payload = extract_node_runtime_payload(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_narrative = extract_node_narrative_text(runtime_payload, node_id=CHAT_INTENT_SOURCE_NODE_ID)
    core_meta = core_payload.get("meta")
    if isinstance(core_meta, dict) and core_meta.get("degraded"):
        secondary_narrative = extract_node_narrative_text(runtime_payload, node_id="core_reasoner_secondary")
        if secondary_narrative:
            core_narrative = secondary_narrative
            secondary_payload = extract_node_runtime_payload(runtime_payload, node_id="core_reasoner_secondary")
            if isinstance(secondary_payload.get("intent_draft"), dict) and secondary_payload["intent_draft"]:
                core_payload = secondary_payload

    core_intent_draft = core_payload.get("intent_draft")
    if not isinstance(core_intent_draft, dict):
        core_intent_draft = {}
    core_intent_json = json.dumps(core_intent_draft, ensure_ascii=False, sort_keys=True)
    planner_text = extract_node_narrative_text(runtime_payload, node_id="planner_constraints")
    verifier_text = extract_node_narrative_text(runtime_payload, node_id="safety_verifier")

    node_style = resolve_prompt_context_style_row(prompt_context, node_id=node_id)
    style_instruction = _truncate_for_prompt(node_style.get("style_instruction") or node_style.get("instruction"), limit=1200)
    style_block = f"【说话风格】\n{style_instruction}\n\n" if style_instruction else ""

    # ── 各类上下文块构建（bond_aware/predictive/planning/skill/multimodal）──
    bond_aware_style_block = ""
    predictive_state_block = ""
    planning_block = ""
    planning_execution_block = ""
    skill_dag_block = ""
    skill_execution_block = ""
    multimodal_input_block = ""

    try:
        bond_aware_raw = runtime_payload.get("bond_aware_style")
        bond_aware = dict(bond_aware_raw) if isinstance(bond_aware_raw, Mapping) else {}
        if bond_aware:
            bond_aware_style_block = (
                _mapping_text(_runtime_section_templates, "bond_aware_title")
                + _mapping_text(_runtime_section_templates, "bond_stage_label", value=_truncate_for_prompt(bond_aware.get("bond_stage"), limit=32) or "STRANGER")
                + _mapping_text(_runtime_section_templates, "intimacy_level_label", value=f"{float(bond_aware.get('intimacy_level') or 0.0):.2f}")
                + _mapping_text(_runtime_section_templates, "prompt_label", value=_truncate_for_prompt(bond_aware.get("prompt_injection"), limit=600) or "NONE")
                + _mapping_text(_runtime_section_templates, "forbidden_patterns_label", value=", ".join(list(bond_aware.get("forbidden_patterns") or [])[:5]) or "NONE")
                + "\n"
            )
        if not bond_aware_style_block:
            _bsi = str(runtime_payload.get("bond_style_instruction") or "").strip()
            if _bsi:
                bond_aware_style_block = _mapping_text(_runtime_section_templates, "bond_aware_title") + f"{_truncate_for_prompt(_bsi, limit=800)}\n\n"
    except Exception:
        pass

    _drift_correction_block = ""
    try:
        _dch = str(runtime_payload.get("drift_correction_hint") or "").strip()
        if _dch:
            _drift_correction_block = _mapping_text(_runtime_section_templates, "persona_drift_title") + f"{_truncate_for_prompt(_dch, limit=400)}\n\n"
    except Exception:
        pass

    try:
        predictive_raw = runtime_payload.get("predictive_state")
        predictive = dict(predictive_raw) if isinstance(predictive_raw, Mapping) else {}
        if predictive:
            predictive_state_block = (
                _mapping_text(_runtime_section_templates, "predictive_state_title")
                + _mapping_text(_runtime_section_templates, "period_label", value=_truncate_for_prompt(predictive.get("period_label"), limit=48) or "UNKNOWN")
                + _mapping_text(_runtime_section_templates, "dominant_emotion_label", value=_truncate_for_prompt(predictive.get("dominant_emotion"), limit=32) or "NONE")
                + _mapping_text(_runtime_section_templates, "confidence_label", value=f"{float(predictive.get('confidence') or 0.0):.2f}")
                + _mapping_text(_runtime_section_templates, "proactive_hint_label", value=_truncate_for_prompt(predictive.get("proactive_hint"), limit=220) or "NONE")
                + "\n"
            )
    except Exception:
        pass

    try:
        planning_raw = runtime_payload.get("planning_outline")
        planning = dict(planning_raw) if isinstance(planning_raw, Mapping) else {}
        if planning:
            planning_lines = [_truncate_for_prompt(step, limit=160) for step in list(planning.get("step_descriptions") or [])[:5] if str(step or "").strip()]
            if planning_lines:
                planning_block = (
                    _mapping_text(_runtime_section_templates, "planning_outline_title")
                    + _mapping_text(_runtime_section_templates, "total_steps_label", value=int(planning.get("total_steps") or len(planning_lines)))
                    + "\n".join(_mapping_text(_runtime_section_templates, "step_label", index=idx + 1, value=line).rstrip("\n") for idx, line in enumerate(planning_lines))
                    + "\n\n"
                )
    except Exception:
        pass

    try:
        planning_execution_raw = runtime_payload.get("planning_execution")
        planning_execution = dict(planning_execution_raw) if isinstance(planning_execution_raw, Mapping) else {}
        if planning_execution:
            execution_lines = [_truncate_for_prompt(f"{item.get('description')} => {item.get('status')}", limit=180) for item in list(planning_execution.get("step_results") or [])[:4] if isinstance(item, Mapping)]
            planning_execution_block = (
                _mapping_text(_runtime_section_templates, "planning_execution_title")
                + _mapping_text(_runtime_section_templates, "success_label", value=bool(planning_execution.get("success")))
                + _mapping_text(_runtime_section_templates, "executed_skills_label", value=", ".join(list(planning_execution.get("executed_skills") or [])[:6]) or "NONE")
                + ("\n".join(_mapping_text(_runtime_section_templates, "step_label", index=idx + 1, value=line).rstrip("\n") for idx, line in enumerate(execution_lines)) + "\n\n" if execution_lines else "\n")
            )
    except Exception:
        pass

    try:
        skill_dag_raw = runtime_payload.get("skill_dag")
        skill_dag = dict(skill_dag_raw) if isinstance(skill_dag_raw, Mapping) else {}
        if skill_dag:
            execution_layers = list(skill_dag.get("execution_order") or [])
            skill_dag_block = (
                _mapping_text(_runtime_section_templates, "skill_composition_title")
                + _mapping_text(_runtime_section_templates, "total_nodes_label", value=int(skill_dag.get("total_nodes") or 0))
                + _mapping_text(_runtime_section_templates, "execution_layers_label", value=len(execution_layers))
                + _mapping_text(_runtime_section_templates, "node_ids_label", value=", ".join(list(skill_dag.get("node_ids") or [])[:8]) or "NONE")
                + "\n"
            )
    except Exception:
        pass

    try:
        skill_execution_raw = runtime_payload.get("skill_execution")
        skill_execution = dict(skill_execution_raw) if isinstance(skill_execution_raw, Mapping) else {}
        if skill_execution:
            output_preview = dict(skill_execution.get("output_preview") or {})
            preview_lines = [f"- {key}: {_truncate_for_prompt(value, limit=180)}" for key, value in list(output_preview.items())[:4]]
            skill_execution_block = (
                _mapping_text(_runtime_section_templates, "skill_execution_results_title")
                + _mapping_text(_runtime_section_templates, "success_label", value=bool(skill_execution.get("success")))
                + _mapping_text(_runtime_section_templates, "completed_nodes_label", value=f"{int(skill_execution.get('completed_nodes') or 0)}/{int(skill_execution.get('total_nodes') or 0)}")
                + ("\n".join(preview_lines) + "\n\n" if preview_lines else "\n")
            )
    except Exception:
        pass

    try:
        multimodal_raw = runtime_payload.get("multimodal_input")
        multimodal = dict(multimodal_raw) if isinstance(multimodal_raw, Mapping) else {}
        if multimodal:
            multimodal_summary = _truncate_for_prompt(
                multimodal.get("summary_for_chat")
                or ((dict(multimodal.get("observations") or {})).get("summary_for_chat") if isinstance(multimodal.get("observations"), Mapping) else ""),
                limit=320,
            )
            multimodal_observations_block = ""
            multimodal_observations_raw = multimodal.get("observations")
            multimodal_observations = (
                dict(multimodal_observations_raw)
                if isinstance(multimodal_observations_raw, Mapping)
                else {}
            )
            if multimodal_observations:
                multimodal_observations_json = _truncate_for_prompt(
                    json.dumps(multimodal_observations, ensure_ascii=False, sort_keys=True),
                    limit=600,
                )
                multimodal_observations_block = _mapping_text(
                    _runtime_section_templates,
                    "multimodal_observations_label",
                    value=multimodal_observations_json,
                )
            multimodal_input_block = (
                _mapping_text(_runtime_section_templates, "multimodal_input_title")
                + _mapping_text(_runtime_section_templates, "primary_modality_label", value=_truncate_for_prompt(multimodal.get("primary_modality"), limit=24) or "text")
                + _mapping_text(_runtime_section_templates, "modalities_label", value=", ".join(list(multimodal.get("modalities") or [])[:5]) or "text")
                + _mapping_text(_runtime_section_templates, "emotion_signals_label", value=", ".join(list(multimodal.get("emotion_signals") or [])[:5]) or "NONE")
                + (
                    _mapping_text(
                        _runtime_section_templates,
                        "multimodal_summary_label",
                        value=multimodal_summary,
                    )
                    if multimodal_summary
                    else ""
                )
                + multimodal_observations_block
                + "\n"
            )
    except Exception:
        pass

    # ── 情感上下文 ──────────────────────────────────────────────────────
    from host_brain.app.emotion_context import normalize_chat_emotion_context
    emotion_context = get_chat_runtime_emotion_state(runtime_payload)
    emotion_snapshot = get_chat_runtime_emotion_snapshot(runtime_payload)
    snapshot_emotion_context_raw = emotion_snapshot.get("emotion_context")
    snapshot_emotion_context = dict(snapshot_emotion_context_raw) if isinstance(snapshot_emotion_context_raw, Mapping) else {}
    prompt_context_emotion_raw = prompt_context.get("emotion_pulse")
    prompt_context_emotion = dict(prompt_context_emotion_raw) if isinstance(prompt_context_emotion_raw, Mapping) else {}
    emotion_recall_text = _truncate_for_prompt(prompt_context.get("emotion_recall"), limit=320)
    merged_emotion_context = normalize_chat_emotion_context({
        "mood_state": snapshot_emotion_context.get("mood_state") or emotion_context.get("mood_state"),
        "emotion_tag": snapshot_emotion_context.get("emotion_tag") or emotion_context.get("emotion_tag"),
        "emotion_range_hint": snapshot_emotion_context.get("emotion_range_hint") or emotion_context.get("emotion_range_hint"),
        "emotion_intensity": snapshot_emotion_context.get("emotion_intensity") or emotion_context.get("emotion_intensity"),
        "emotion_ts_ms": emotion_snapshot.get("emotion_ts_ms") or snapshot_emotion_context.get("emotion_ts_ms") or emotion_context.get("emotion_ts_ms"),
    })
    pulse_style_hint = _truncate_for_prompt(prompt_context_emotion.get("style_hint"), limit=160)
    memory_emotion_hint_raw = prompt_context.get("memory_emotion_hint")
    memory_emotion_hint = dict(memory_emotion_hint_raw) if isinstance(memory_emotion_hint_raw, Mapping) else {}
    memory_recall_summary = _truncate_for_prompt(prompt_context.get("memory_recall_summary") or memory_emotion_hint.get("memory_recall_summary"), limit=360)
    memory_persona_evidence_raw = prompt_context.get("memory_persona_evidence")
    memory_persona_evidence = ([dict(item) for item in memory_persona_evidence_raw if isinstance(item, Mapping)] if isinstance(memory_persona_evidence_raw, list) else [])
    bond_texture_raw = prompt_context.get("bond_texture")
    bond_texture = dict(bond_texture_raw) if isinstance(bond_texture_raw, Mapping) else {}
    memory_emotion_hint_json = _truncate_for_prompt(json.dumps(memory_emotion_hint, ensure_ascii=False, sort_keys=True), limit=600)
    _bt_stage = str(bond_texture.get("bond_stage") or "").strip()
    _bt_trend = str(bond_texture.get("bond_trend") or "").strip()
    _bt_mood = str(bond_texture.get("mood_state") or "").strip()
    _bt_tone = str(bond_texture.get("tone_hint") or "").strip()
    _bt_texture_hint = str(bond_texture.get("texture_hint") or "").strip()
    _bt_rift_active = bool(bond_texture.get("rift_active", False))
    _bt_rift_hint = str(bond_texture.get("rift_hint") or "").strip()
    _emotion_reunion_type = str(runtime_payload.get("emotion_reunion_type") or "").strip()
    _reunion_guidance_templates = _load_template_mapping(
        "prompt_builder.reunion.guidance",
        render_target_lang=render_target_lang,
    )
    _emotion_reunion_guidance = _mapping_text(_reunion_guidance_templates, _emotion_reunion_type)

    # ── 离线状态 / 沉默状态 / Tribe 生命体征 / 身体感知 ──────────────────
    _offline_state_raw = runtime_payload.get("offline_state") or prompt_context.get("offline_state")
    _offline_state: dict = dict(_offline_state_raw) if isinstance(_offline_state_raw, Mapping) else {}
    _offline_hours = float(_offline_state.get("offline_hours") or 0.0)
    _offline_loneliness = float(_offline_state.get("loneliness_level") or 0.0)
    _offline_anticipation = float(_offline_state.get("anticipation_level") or 0.0)
    _offline_awakening_hint = str(_offline_state.get("awakening_hint") or "").strip()
    _offline_drift_summary = str(_offline_state.get("emotion_drift_summary") or "").strip()
    _offline_topics = list(_offline_state.get("anticipation_topics") or [])
    _offline_should_express = bool(_offline_state.get("should_express_waiting", False))
    _offline_active = _offline_hours >= 1.0 or bool(_offline_awakening_hint)

    _silence_state_raw = runtime_payload.get("silence_state") or prompt_context.get("silence_state")
    _silence_state: dict = dict(_silence_state_raw) if isinstance(_silence_state_raw, Mapping) else {}
    _silence_willingness = float(_silence_state.get("response_willingness") or 1.0)
    _silence_level = str(_silence_state.get("willingness_level") or "NORMAL").strip().upper()
    _silence_hint = str(_silence_state.get("silence_hint") or "").strip()
    _silence_topic_active = bool(_silence_state.get("topic_resistance_active", False))
    _silence_topic_hint = str(_silence_state.get("topic_resistance_hint") or "").strip()
    _silence_active = _silence_level != "NORMAL" or _silence_topic_active

    _tribe_vital_raw = runtime_payload.get("tribe_vital_signs") or prompt_context.get("tribe_vital_signs")
    _tribe_vital: dict = dict(_tribe_vital_raw) if isinstance(_tribe_vital_raw, Mapping) else {}
    _tv_gate_state = str(_tribe_vital.get("gate_state") or "open").strip().lower()
    _tv_stress_index = float(_tribe_vital.get("stress_index") or 0.0)
    _tv_energy_reserve = float(_tribe_vital.get("energy_reserve") or 0.6)
    _tv_homeostasis_ok = bool(_tribe_vital.get("homeostasis_ok", True))
    _tv_mood_state = str(_tribe_vital.get("mood_state") or "steady").strip().lower()
    _tv_bond_stage = str(_tribe_vital.get("bond_stage") or "stranger").strip().lower()
    _tv_voice_emotion_tag = str(_tribe_vital.get("voice_emotion_tag") or "").strip().upper()
    _tv_voice_emotion_intensity = _tribe_vital.get("voice_emotion_intensity")
    _tv_hedonic_tone = str(_tribe_vital.get("hedonic_tone") or "neutral").strip().lower()
    _tv_pain_level = float(_tribe_vital.get("pain_level") or 0.0)
    _tv_reward_level = float(_tribe_vital.get("reward_level") or 0.0)
    _tv_net_valence = float(_tribe_vital.get("net_valence") or 0.0)
    _tv_hedonic_active = (_tv_hedonic_tone in ("negative", "dysphoric", "suffering", "mixed") or _tv_pain_level >= 0.4 or _tv_reward_level >= 0.6)
    _tv_active = bool(_tribe_vital) and (_tv_gate_state != "open" or _tv_stress_index >= 0.4 or not _tv_homeostasis_ok or bool(_tv_voice_emotion_tag) or _tv_mood_state not in ("steady", "normal", "") or _tv_hedonic_active)

    _body_sense_raw = runtime_payload.get("body_sense") or prompt_context.get("body_sense")
    _body_sense: dict = dict(_body_sense_raw) if isinstance(_body_sense_raw, Mapping) else {}
    _bs_fatigue_level = str(_body_sense.get("fatigue_level") or "FRESH").strip().upper()
    _bs_drowsiness_level = str(_body_sense.get("drowsiness_level") or "ALERT").strip().upper()
    _bs_loneliness_level = str(_body_sense.get("loneliness_level") or "FINE").strip().upper()
    _bs_hint = str(_body_sense.get("body_sense_hint") or "").strip()
    _bs_active = bool(_body_sense.get("is_active", False)) or bool(_bs_hint)

    # ── ResponsePolicyLayer ──────────────────────────────────────────────
    _response_policy_raw = runtime_payload.get("response_policy") or prompt_context.get("response_policy")
    _response_policy: dict = dict(_response_policy_raw) if isinstance(_response_policy_raw, Mapping) else {}
    _rp_max_chars = int(_response_policy.get("max_chars") or 0)
    _rp_max_sentences = int(_response_policy.get("max_sentences") or 0)
    _rp_min_sentences = int(_response_policy.get("min_sentences") or 0)
    _rp_allow_followup = bool(_response_policy.get("allow_followup", True))
    _rp_allow_topic_shift = bool(_response_policy.get("allow_topic_shift", True))
    _rp_allow_proactive = bool(_response_policy.get("allow_proactive_after_reply", True))
    _rp_warmth_floor = float(_response_policy.get("warmth_floor") or 0.0)
    _rp_warmth_ceiling = float(_response_policy.get("warmth_ceiling") or 1.0)
    _rp_intimacy_ceiling = float(_response_policy.get("intimacy_ceiling") or 0.75)
    _rp_humor_ceiling = float(_response_policy.get("humor_ceiling") or 0.8)
    _rp_confession_guard = bool(_response_policy.get("confession_guard", False))
    _rp_withdrawal_guard = bool(_response_policy.get("withdrawal_guard", False))
    _rp_should_be_brief = bool(_response_policy.get("should_be_brief", False))
    _rp_should_be_withdrawn = bool(_response_policy.get("should_be_withdrawn", False))
    _rp_reasons = [str(item).strip() for item in (_response_policy.get("policy_reason_tags") or []) if str(item).strip()]
    _rp_active = bool(_response_policy)
    _rp_is_affection_turn = _contains_any_token(user_message, ("你喜欢我吗", "喜欢我吗", "我喜欢你", "喜欢你", "我爱你", "爱你", "好喜欢你", "想你", "抱抱", "亲亲", "陪陪我", "好き", "愛してる", "会いたい"))
    _response_policy_templates = _load_template_mapping(
        "prompt_builder.response_policy.directives",
        render_target_lang=render_target_lang,
    )
    _response_policy_bond_stage = _mapping_section(_response_policy_templates, "bond_stage")
    _rp_directives: list[str] = []
    if _rp_should_be_brief or (_rp_max_sentences and _rp_max_sentences <= 3):
        _rp_directives.append(_mapping_text(_response_policy_templates, "brief_with_limit", max_sentences=_rp_max_sentences) if _rp_max_sentences > 0 else _mapping_text(_response_policy_templates, "brief_generic"))
    if not _rp_allow_followup:
        _rp_directives.append(_mapping_text(_response_policy_templates, "no_followup"))
    if not _rp_allow_topic_shift:
        _rp_directives.append(_mapping_text(_response_policy_templates, "no_topic_shift"))
    if not _rp_allow_proactive:
        _rp_directives.append(_mapping_text(_response_policy_templates, "no_proactive"))
    if _rp_confession_guard:
        _rp_directives.append(_mapping_text(_response_policy_templates, "confession_guard"))
        if _rp_is_affection_turn:
            _rp_directives.append(_mapping_text(_response_policy_templates, "affection_turn_guard"))
    if _rp_should_be_withdrawn or _rp_withdrawal_guard:
        _rp_directives.append(_mapping_text(_response_policy_templates, "withdrawn"))
    _bond_stage_instruction = _mapping_text(_response_policy_bond_stage, _bt_stage.upper()) if _bt_stage else ""
    if _bt_stage and not _bond_stage_instruction:
        _bond_stage_instruction = str(_response_policy_bond_stage.get("__default") or "")

    # ── EmotionVector VAD / EmotionTimeline / 情感推理 / 用户画像 ────────
    emotion_vector_raw = runtime_payload.get("emotion_vector")
    emotion_vector = dict(emotion_vector_raw) if isinstance(emotion_vector_raw, Mapping) else {}
    try:
        _vad_valence = float(emotion_vector.get("valence") or 0.0)
        _vad_arousal = float(emotion_vector.get("arousal") or 0.0)
        _vad_dominance = float(emotion_vector.get("dominance") or 0.0)
    except (TypeError, ValueError):
        _vad_valence = _vad_arousal = _vad_dominance = 0.0
    _vad_active = not (_vad_valence == 0.0 and _vad_arousal == 0.0 and _vad_dominance == 0.0)

    emotion_timeline_raw = runtime_payload.get("emotion_timeline")
    emotion_timeline = dict(emotion_timeline_raw) if isinstance(emotion_timeline_raw, Mapping) else {}
    emotion_echo_raw = runtime_payload.get("emotion_echo")
    emotion_echo = dict(emotion_echo_raw) if isinstance(emotion_echo_raw, Mapping) else {}
    _et_dominant = str(emotion_timeline.get("dominant_emotion") or "NEUTRAL").strip() or "NEUTRAL"
    _et_trend = str(emotion_timeline.get("trend") or "stable").strip() or "stable"
    _et_entry_count = int(emotion_timeline.get("entry_count") or 0)
    _et_mode = str(emotion_timeline.get("mode") or "").strip()
    _echo_tag = str(emotion_echo.get("echo_emotion_tag") or "").strip() or "NONE"
    _echo_hint = _truncate_for_prompt(emotion_echo.get("response_hint"), limit=160) or "NONE"
    _echo_intensity = emotion_echo.get("echo_intensity", 0.0)
    _echo_bond_depth = str(emotion_echo.get("bond_depth") or "medium").strip()
    _inner_monologue = _truncate_for_prompt(runtime_payload.get("inner_monologue") or merged_emotion_context.get("inner_monologue") or prompt_context_emotion.get("inner_monologue") or "", limit=200)
    _et_active = _et_mode not in ("SKIPPED", "SKIPPED_NOT_WIRED", "DEGRADED") and _et_entry_count > 0

    _inferred_emotion_raw = runtime_payload.get("inferred_emotion")
    _inferred_emotion = dict(_inferred_emotion_raw) if isinstance(_inferred_emotion_raw, Mapping) else {}
    _ie_mode = str(_inferred_emotion.get("mode") or "").strip()
    _ie_user_emotion = str(_inferred_emotion.get("user_emotion") or "").strip()
    _ie_natsume_emotion = str(_inferred_emotion.get("natsume_emotion") or "").strip()
    _ie_reason = _truncate_for_prompt(_inferred_emotion.get("reason"), limit=120) or ""
    _ie_confidence = 0.0
    try:
        _ie_confidence = float(_inferred_emotion.get("confidence") or 0.0)
    except (TypeError, ValueError):
        pass
    _ie_active = _ie_mode == "ACTIVE" and _ie_confidence >= 0.4

    _user_profile_raw = runtime_payload.get("user_emotion_profile")
    if not isinstance(_user_profile_raw, dict):
        _user_profile_raw = merged_emotion_context.get("user_emotion_profile")
    _user_profile = dict(_user_profile_raw) if isinstance(_user_profile_raw, Mapping) else {}
    _up_status = str(_user_profile.get("status") or "").strip()
    _up_hint = _truncate_for_prompt(_user_profile.get("profile_hint"), limit=200) or ""
    _up_active = _up_status == "ACTIVE" and bool(_up_hint)

    # ── Bond 叙事层 / 自省脉冲 / 人格化表达策略 ─────────────────────────
    _bond_narrative_block = ""
    try:
        _bn_raw = runtime_payload.get("bond_narrative") or prompt_context.get("bond_narrative")
        _bn = dict(_bn_raw) if isinstance(_bn_raw, Mapping) else {}
        if _bn:
            _bn_memories = _bn.get("shared_memories") or []
            _bn_style = _truncate_for_prompt(_bn.get("narrative_style"), limit=100) or ""
            _bn_ms_count = int(_bn.get("milestone_count") or 0)
            _bn_parts: list[str] = ["BOND_NARRATIVE:"]
            if _bn_style:
                _bn_parts.append(f"- narrative_style: {_bn_style}")
            if _bn_ms_count > 0:
                _bn_parts.append(f"- milestone_count: {_bn_ms_count}")
            for idx, mem_text in enumerate(_bn_memories[:3]):
                _bn_parts.append(f"- shared_memory_{idx+1}: {_truncate_for_prompt(mem_text, limit=120) or ''}")
            if len(_bn_parts) > 1:
                _bond_narrative_block = "\n".join(_bn_parts) + "\n\n"
    except Exception:
        pass

    _self_reflection_block = ""
    try:
        _srp_raw = runtime_payload.get("self_reflection_pulse")
        _srp = dict(_srp_raw) if isinstance(_srp_raw, Mapping) else {}
        if _srp:
            _sr_parts: list[str] = ["SELF_REFLECTION_PULSE:"]
            for field_key, field_limit in [("self_awareness", 200), ("mood_awareness", 200), ("growth_note", 200), ("trigger_reason", 160), ("time_context", 80), ("contradiction_awareness", 200)]:
                val = _truncate_for_prompt(_srp.get(field_key), limit=field_limit) or ""
                if val:
                    _sr_parts.append(f"- {field_key}: {val}")
            # growth_awareness / growth_summary / narrative_guardrails（简化处理）
            _sr_ga_raw = _srp.get("growth_awareness")
            _sr_ga = dict(_sr_ga_raw) if isinstance(_sr_ga_raw, Mapping) else {}
            if _sr_ga:
                _ga_parts = ["- growth_awareness:"]
                for gk, gl in [("recent_change", 150), ("trait_felt", 150), ("milestone_felt", 100), ("stagnation_felt", 150), ("regression_felt", 150), ("desire", 100)]:
                    gv = _truncate_for_prompt(_sr_ga.get(gk), limit=gl) or ""
                    if gv:
                        _ga_parts.append(f"  {gk}: {gv}")
                if len(_ga_parts) > 1:
                    _sr_parts.extend(_ga_parts)
            _sr_ng_raw = _srp.get("narrative_guardrails")
            _sr_ng = dict(_sr_ng_raw) if isinstance(_sr_ng_raw, Mapping) else {}
            if _sr_ng:
                _ng_instruction = _truncate_for_prompt(_sr_ng.get("instruction"), limit=240) or ""
                _ng_max_sentences = _sr_ng.get("max_self_narrative_sentences")
                _ng_prompt_directives = [_truncate_for_prompt(item, limit=120) or "" for item in (_sr_ng.get("prompt_directives") or []) if _truncate_for_prompt(item, limit=120)]
                if _ng_instruction:
                    _self_reflection_templates = _load_template_mapping(
                        "prompt_builder.self_reflection.templates",
                        render_target_lang=render_target_lang,
                    )
                    _rp_directives.append(_mapping_text(_self_reflection_templates, "narrative_instruction_prefix", instruction=_ng_instruction))
                if _ng_max_sentences is not None:
                    _sr_parts.append(f"- max_self_narrative_sentences: {_ng_max_sentences}")
                if _ng_prompt_directives:
                    _sr_parts.append("- prompt_directives:")
                    for _ng_directive in _ng_prompt_directives:
                        _sr_parts.append(f"  - {_ng_directive}")
                        if _ng_directive not in _rp_directives:
                            _rp_directives.append(_ng_directive)
            if len(_sr_parts) > 1:
                _self_reflection_block = "\n".join(_sr_parts) + "\n\n"
    except Exception:
        pass

    _persona_expression_hint_block = ""
    _peh_tone = "gentle"
    _peh_style = "NONE"
    _peh_motive_guidance = ""
    try:
        _peh_raw = runtime_payload.get("persona_expression_hint") or prompt_context_emotion.get("persona_expression_hint")
        _peh = dict(_peh_raw) if isinstance(_peh_raw, Mapping) else {}
        if _peh:
            _peh_style = _truncate_for_prompt(_peh.get("style_hint"), limit=200) or "NONE"
            _peh_tone = str(_peh.get("tone") or "gentle").strip()
            _peh_echo_mod = float(_peh.get("echo_weight_modifier") or 0.0)
            _peh_follow_up = float(_peh.get("follow_up_tendency") or 0.0)
            _peh_motive_guidance = _truncate_for_prompt(_peh.get("motive_guidance"), limit=220) or ""
            _persona_expression_templates = _load_template_mapping(
                "prompt_builder.persona_expression.templates",
                render_target_lang=render_target_lang,
            )
            _peh_guidance = _mapping_text(_mapping_section(_persona_expression_templates, "tone_guidance"), _peh_tone) or _mapping_text(_persona_expression_templates, "default_guidance") or "保持真诚自然"
            _persona_expression_hint_block = (
                _mapping_text(_persona_expression_templates, "title")
                + _mapping_text(_persona_expression_templates, "style_hint_label", value=_peh_style)
                + _mapping_text(_persona_expression_templates, "tone_label", value=_peh_tone)
                + _mapping_text(_persona_expression_templates, "echo_weight_modifier_label", value=f"{_peh_echo_mod:+.3f}")
                + _mapping_text(_persona_expression_templates, "follow_up_tendency_label", value=f"{_peh_follow_up:.3f}")
                + (_mapping_text(_persona_expression_templates, "motive_guidance_label", value=_peh_motive_guidance) if _peh_motive_guidance else "")
                + _mapping_text(_persona_expression_templates, "guidance_label", value=_peh_guidance)
            )
    except Exception:
        pass

    # ── EMOTION_BINDING 数值字段转自然语言 ──────────────────────────────
    _emotion_binding_templates = _load_template_mapping(
        "prompt_builder.emotion_binding.texts",
        render_target_lang=render_target_lang,
    )
    _echo_intensity_templates = _mapping_section(_emotion_binding_templates, "echo_intensity")
    _vad_templates = _mapping_section(_emotion_binding_templates, "vad")

    def _echo_intensity_label(v: float) -> str:
        if v >= 0.7:
            return _mapping_text(_echo_intensity_templates, "high")
        elif v >= 0.4:
            return _mapping_text(_echo_intensity_templates, "mid")
        elif v >= 0.15:
            return _mapping_text(_echo_intensity_templates, "low")
        return _mapping_text(_echo_intensity_templates, "default")

    def _vad_to_label(valence: float, arousal: float, dominance: float) -> str:
        parts: list[str] = []
        if valence >= 0.4:
            parts.append(_mapping_text(_vad_templates, "valence_positive"))
        elif valence <= -0.4:
            parts.append(_mapping_text(_vad_templates, "valence_negative"))
        else:
            parts.append(_mapping_text(_vad_templates, "valence_neutral"))
        if arousal >= 0.5:
            parts.append(_mapping_text(_vad_templates, "arousal_high"))
        elif arousal <= -0.3:
            parts.append(_mapping_text(_vad_templates, "arousal_low"))
        if dominance >= 0.4:
            parts.append(_mapping_text(_vad_templates, "dominance_high"))
        elif dominance <= -0.4:
            parts.append(_mapping_text(_vad_templates, "dominance_low"))
        return "，".join(parts) if parts else _mapping_text(_vad_templates, "default")

    emotion_binding_block = (
        "EMOTION_BINDING:\n"
        f"- mood_state: {merged_emotion_context.get('mood_state')}\n"
        f"- emotion_tag: {merged_emotion_context.get('emotion_tag')}\n"
        f"- emotion_intensity: {merged_emotion_context.get('emotion_intensity')}\n"
        f"- emotion_range_hint: {_truncate_for_prompt(merged_emotion_context.get('emotion_range_hint'), limit=220) or 'NONE'}\n"
        f"- pulse_style_hint: {pulse_style_hint or 'NONE'}\n"
        f"- memory_emotion_hint_json: {memory_emotion_hint_json}\n"
        + (f"- emotion_reunion_type: {_emotion_reunion_type}\n- emotion_reunion_guidance: {_emotion_reunion_guidance}\n" if _emotion_reunion_type else "")
        + (f"- inner_monologue: {_inner_monologue}\n" if _inner_monologue else "")
        + (
            f"- bond_stage: {_bt_stage}\n- bond_trend: {_bt_trend}\n- bond_mood: {_bt_mood}\n"
            f"- bond_tone_hint: {_bt_tone or 'NONE'}\n- bond_stage_instruction: {_bond_stage_instruction}\n"
            + (f"- bond_texture_hint: {_bt_texture_hint}\n" if _bt_texture_hint else "")
            + (f"- bond_rift_hint: {_bt_rift_hint}\n" if _bt_rift_active and _bt_rift_hint else "")
            if _bt_stage else "- bond_stage: UNKNOWN\n- bond_stage_instruction: Be warm and genuine.\n"
        )
        + (
            f"- emotion_timeline_dominant: {_et_dominant}\n- emotion_timeline_trend: {_et_trend}\n"
            f"- emotion_echo_tag: {_echo_tag}\n- emotion_echo_hint: {_echo_hint}\n"
            f"- emotion_echo_intensity: {_echo_intensity_label(_echo_intensity)}\n- emotion_echo_bond_depth: {_echo_bond_depth}\n"
            if _et_active else "- emotion_timeline_dominant: NONE\n- emotion_timeline_trend: NONE\n"
        )
        + (f"- emotion_vad_quality: {_vad_to_label(_vad_valence, _vad_arousal, _vad_dominance)}\n" if _vad_active else "")
        + (f"- inferred_user_emotion: {_ie_user_emotion}\n- inferred_natsume_emotion: {_ie_natsume_emotion}\n- inferred_emotion_reason: {_ie_reason}\n" if _ie_active else "")
        + (f"- user_emotion_profile: {_up_hint}\n" if _up_active else "")
        + (
            f"- offline_hours: {_offline_hours:.1f}\n"
            f"- offline_loneliness: {'高' if _offline_loneliness >= 0.7 else '中' if _offline_loneliness >= 0.4 else '低'}\n"
            + (f"- offline_awakening_hint: {_offline_awakening_hint}\n" if _offline_awakening_hint else "")
            + (f"- offline_drift_summary: {_offline_drift_summary}\n" if _offline_drift_summary else "")
            + (f"- offline_anticipation_topics: {', '.join(_offline_topics)}\n" if _offline_topics else "")
            + ("- offline_should_express_waiting: true\n" if _offline_should_express else "")
            if _offline_active else ""
        )
        + (
            f"- response_willingness: {_silence_willingness:.2f}\n- willingness_level: {_silence_level}\n"
            + (f"- silence_hint: {_silence_hint}\n" if _silence_hint else "")
            + (f"- topic_resistance_hint: {_silence_topic_hint}\n" if _silence_topic_active and _silence_topic_hint else "")
            if _silence_active else ""
        )
        + (
            f"- tribe_gate_state: {_tv_gate_state}\n- tribe_stress_index: {_tv_stress_index:.2f}\n"
            f"- tribe_energy_reserve: {_tv_energy_reserve:.2f}\n- tribe_homeostasis_ok: {'true' if _tv_homeostasis_ok else 'false'}\n"
            f"- tribe_mood_state: {_tv_mood_state}\n- tribe_bond_stage: {_tv_bond_stage}\n"
            + (f"- tribe_voice_emotion_tag: {_tv_voice_emotion_tag}\n" + (f"- tribe_voice_emotion_intensity: {_tv_voice_emotion_intensity:.2f}\n" if _tv_voice_emotion_intensity is not None else "") if _tv_voice_emotion_tag else "")
            + (f"- tribe_hedonic_tone: {_tv_hedonic_tone}\n- tribe_pain_level: {_tv_pain_level:.2f}\n- tribe_reward_level: {_tv_reward_level:.2f}\n- tribe_net_valence: {_tv_net_valence:+.2f}\n" if _tv_hedonic_active else "")
            if _tv_active else ""
        )
        + (lambda _lh=runtime_payload.get("loneliness_hint") or prompt_context.get("loneliness_hint"): (f"- tribe_loneliness_level: {float((_lh or {}).get('loneliness_level') or 0.0):.2f}\n- tribe_loneliness_suggest: {str((_lh or {}).get('suggest') or '主动关怀，增加陪伴感表达')}\n" if isinstance(_lh, dict) and bool((_lh or {}).get("high_loneliness")) else ""))()
        + (lambda _ls=runtime_payload.get("language_style") or prompt_context.get("language_style"): (f"- language_style: {str((_ls or {}).get('style') or 'neutral')}\n- language_tone: {str((_ls or {}).get('tone') or 'balanced')}\n- language_register: {str((_ls or {}).get('register') or 'standard')}\n" + (f"- language_loneliness_hint: {str((_ls or {}).get('loneliness_hint'))}\n" if str((_ls or {}).get("loneliness_hint") or "").strip() else "") if isinstance(_ls, dict) and bool(_ls) else ""))()
        + (lambda _bta=runtime_payload.get("bond_tone_adjust") or prompt_context.get("bond_tone_adjust"): (f"- bond_tone_adjust: {str(_bta)}\n- bond_tone_directive: {'适当增加亲密感和温暖表达，关系正在升温' if str(_bta) == 'warmer' else '保持温和谨慎，关系面临退步风险，避免过于亲密或冒进的表达'}\n" if _bta and str(_bta).strip() in ("warmer", "cautious") else ""))()
        + (lambda _ic=runtime_payload.get("immune_caution") or prompt_context.get("immune_caution"): (f"- immune_caution_risk: {str((_ic or {}).get('risk_level') or 'medium')}\n- immune_caution_directive: {str((_ic or {}).get('suggest') or '当前会话存在内容风险，回复需更加谨慎')}\n" if isinstance(_ic, dict) and bool(_ic) else ""))()
        + (f"- fatigue_level: {_bs_fatigue_level}\n- drowsiness_level: {_bs_drowsiness_level}\n- loneliness_level: {_bs_loneliness_level}\n" + (f"- body_sense_hint: {_bs_hint}\n" if _bs_hint else "") if _bs_active else "")
        + (
            f"- response_policy_max_chars: {_rp_max_chars}\n- response_policy_max_sentences: {_rp_max_sentences}\n"
            f"- response_policy_min_sentences: {_rp_min_sentences}\n- response_policy_allow_followup: {'true' if _rp_allow_followup else 'false'}\n"
            f"- response_policy_allow_topic_shift: {'true' if _rp_allow_topic_shift else 'false'}\n"
            f"- response_policy_allow_proactive_after_reply: {'true' if _rp_allow_proactive else 'false'}\n"
            f"- response_policy_warmth_range: {_rp_warmth_floor:.2f}~{_rp_warmth_ceiling:.2f}\n"
            f"- response_policy_intimacy_ceiling: {_rp_intimacy_ceiling:.2f}\n- response_policy_humor_ceiling: {_rp_humor_ceiling:.2f}\n"
            f"- response_policy_confession_guard: {'true' if _rp_confession_guard else 'false'}\n"
            f"- response_policy_withdrawal_guard: {'true' if _rp_withdrawal_guard else 'false'}\n"
            f"- response_policy_should_be_brief: {'true' if _rp_should_be_brief else 'false'}\n"
            f"- response_policy_should_be_withdrawn: {'true' if _rp_should_be_withdrawn else 'false'}\n"
            + (f"- response_policy_directive: {' '.join(_rp_directives)}\n" if _rp_directives else "")
            + (f"- response_policy_reasons: {', '.join(_rp_reasons)}\n" if _rp_reasons else "")
            if _rp_active else ""
        )
        + "\n"
    )

    # ── 情感召回 / 记忆召回 / 记忆证据块 ────────────────────────────────
    emotion_recall_block = ""
    if emotion_recall_text:
        emotion_recall_block = _mapping_text(_runtime_section_templates, "emotion_recall_title") + _mapping_text(_runtime_section_templates, "emotion_recall_guidance") + f"- {emotion_recall_text}\n\n"

    memory_recall_persona_block = ""
    if memory_recall_summary:
        memory_recall_persona_block = _mapping_text(_runtime_section_templates, "memory_recall_persona_title") + _mapping_text(_runtime_section_templates, "memory_recall_persona_guidance") + f"- {memory_recall_summary}\n\n"

    memory_recall_evidence_block = ""
    if memory_persona_evidence:
        ev_lines: list[str] = []
        for item in memory_persona_evidence[:3]:
            tags: list[str] = []
            emotion_color_tag = str(item.get("emotion_color_tag") or "").strip().upper()
            if emotion_color_tag:
                tags.append(f"emotion={emotion_color_tag}")
            if bool(item.get("suppression_flag")):
                tags.append("suppressed")
            try:
                distortion_level = float(item.get("distortion_level"))
            except (TypeError, ValueError):
                distortion_level = 0.0
            if distortion_level > 0.0:
                tags.append(f"distortion={distortion_level:.2f}")
            content = _truncate_for_prompt(item.get("content"), limit=120) or "memory fragment"
            prefix = f"[{' | '.join(tags)}] " if tags else ""
            ev_lines.append(f"- {prefix}{content}")
        if ev_lines:
            memory_recall_evidence_block = _mapping_text(_runtime_section_templates, "memory_recall_evidence_title") + _mapping_text(_runtime_section_templates, "memory_recall_evidence_guidance") + "\n".join(ev_lines) + "\n\n"

    # ── episodic_vector 槽位注入 ─────────────────────────────────────────
    _episodic_vector_raw = prompt_context.get("episodic_vector") or runtime_payload.get("episodic_vector")
    _episodic_vector_text = ""
    if isinstance(_episodic_vector_raw, str):
        _episodic_vector_text = _truncate_for_prompt(_episodic_vector_raw, limit=600)
    elif isinstance(_episodic_vector_raw, list):
        _ev_lines: list[str] = []
        for _ev_item in _episodic_vector_raw[:_get_prompt_evidence_limit()]:
            if isinstance(_ev_item, str):
                _ev_lines.append(f"- {_truncate_for_prompt(_ev_item, limit=200)}")
            elif isinstance(_ev_item, dict):
                _ev_text = _truncate_for_prompt(_ev_item.get("text") or _ev_item.get("content") or "", limit=200)
                _ev_score_raw = _ev_item.get("score") or _ev_item.get("similarity")
                try:
                    _ev_score = float(_ev_score_raw) if _ev_score_raw is not None else None
                except (TypeError, ValueError):
                    _ev_score = None
                if _ev_text:
                    _ev_lines.append(f"- [{_ev_score:.2f}] {_ev_text}" if _ev_score is not None else f"- {_ev_text}")
        _episodic_vector_text = "\n".join(_ev_lines)

    episodic_vector_block = ""
    if _episodic_vector_text:
        episodic_vector_block = "EPISODIC_VECTOR_RECALL:\n- These are semantically similar past memories retrieved by vector search.\n- Use as soft context only; do not quote verbatim.\n" + f"{_episodic_vector_text}\n\n"
        try:
            from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled
            if _check_channel("RETRIEVAL"):
                episodic_vector_block = pii_scrub_if_enabled(episodic_vector_block, context="retrieval_episodic")
        except Exception:
            pass

    cn_style_boost_block = ""

    # ── 台词风格参考独立通道 ─────────────────────────────────────────────
    persona_examples_block = ""
    try:
        from host_brain.retrieval.emotion_utterance_index import get_utterance_index
        _emotion_tag_for_style = str(merged_emotion_context.get("emotion_tag") or emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
        _style_lang = "ja" if render_target_lang == "ja" else "cn"
        _utt_index = get_utterance_index()
        if _utt_index.is_built:
            _utt_result = _utt_index.retrieve_multi_scene(_emotion_tag_for_style, lang=_style_lang, primary_n=3, daily_n=2, max_chars=400)
            persona_examples_block = _utt_index.render_for_prompt(_utt_result, lang=_style_lang)
            if persona_examples_block:
                persona_examples_block += "\n"
    except Exception:
        pass

    if rows:
        _filtered_evidence_rows: list[Mapping[str, Any]] = [row for row in rows if isinstance(row, Mapping) and str(row.get("kind") or "").strip().lower() != "persona_utterance"]
        if _filtered_evidence_rows:
            evidence_lines = []
            for row in _filtered_evidence_rows[:6]:
                if not isinstance(row, Mapping):
                    continue
                evidence_ref = _truncate_for_prompt(row.get("evidence_ref") or row.get("source_ref") or row.get("citation") or "unknown", limit=80)
                snippet = _truncate_for_prompt(row.get("content") or row.get("snippet"), limit=220)
                if not snippet:
                    continue
                evidence_lines.append(f"- [{evidence_ref}] {snippet}")
            evidence_block = "\n".join(evidence_lines) if evidence_lines else _render_contract_empty_placeholder

    role_instruction = _build_node_role_instruction(node_id, render_target_lang=render_target_lang, skill_manifest=get_chat_runtime_skill_manifest(runtime_payload) or None)
    direct_response_block = ""
    if str(node_id or "").strip() == CHAT_DIRECT_NODE_ID:
        direct_response_block = _build_direct_response_priority(user_message=user_message, render_target_lang=render_target_lang, follow_up_hint=_follow_up_hint, bond_stage=_bond_stage_for_prompt)

    _render_contract_section_titles = _mapping_section(_render_contract_templates, "section_titles")
    _render_contract_labels = _mapping_section(_render_contract_templates, "labels")
    _render_contract_core_rules = _mapping_list(_render_contract_templates, "core_rules")

    # ── 常驻角色人设 ─────────────────────────────────────────────────────
    character_profile_block = ""
    _cp_text = _load_character_profile(render_target_lang)
    if _cp_text:
        _cp_emotion_tag = str(merged_emotion_context.get("emotion_tag") or emotion_context.get("emotion_tag") or "").strip().upper()
        _cp_is_holiday = bool(runtime_payload.get("is_holiday") or prompt_context.get("is_holiday"))
        _cp_holiday_name = str(runtime_payload.get("holiday_name") or prompt_context.get("holiday_name") or "").strip()
        _cp_patch = _build_character_dynamic_patch(
            emotion_tag=_cp_emotion_tag,
            is_holiday=_cp_is_holiday,
            holiday_name=_cp_holiday_name,
            render_target_lang=render_target_lang,
        )
        character_profile_title = str(_render_contract_section_titles.get("character_profile") or "【角色人设】")
        character_profile_block = f"{character_profile_title}\n{_cp_text}{_cp_patch}\n\n"

    _context_valid_label = _mapping_text(_render_contract_labels, "context_valid", value=prompt_context_valid)
    _context_reason_label = _mapping_text(_render_contract_labels, "context_reason", value=prompt_context_reason)
    _context_violation_count_label = _mapping_text(_render_contract_labels, "context_violation_count", value=prompt_context_violation_count)
    _node_label = _mapping_text(_render_contract_labels, "node", node_id=node_id)
    _output_language_label = _mapping_text(_render_contract_labels, "output_language", render_target_lang=render_target_lang)
    _retrieval_status_label = _mapping_text(_render_contract_labels, "retrieval_status", retrieval_status=retrieval_status)
    _retrieval_reason_label = _mapping_text(_render_contract_labels, "retrieval_reason", retrieval_reason=retrieval_reason)

    return (
        f"{character_profile_block}"
        f"{_mapping_text(_render_contract_templates, 'output_intro')}"
        f"{_mapping_text(_render_contract_templates, 'output_schema')}"
        f"{_mapping_text(_render_contract_templates, 'output_bilingual_hint')}"
        f"{_mapping_text(_render_contract_templates, 'core_rules_title')}"
        + "".join(f"{idx}) {rule}\n" for idx, rule in enumerate(_render_contract_core_rules, 1))
        + f"{_mapping_text(_render_contract_templates, 'node_role_binding_title')}"
        f"{role_instruction}\n\n"
        f"{direct_response_block}"
        f"{style_block}"
        f"{persona_examples_block}"
        f"{bond_aware_style_block}"
        f"{predictive_state_block}"
        f"{planning_block}"
        f"{planning_execution_block}"
        f"{skill_dag_block}"
        f"{skill_execution_block}"
        f"{multimodal_input_block}"
        f"{cn_style_boost_block}"
        f"{emotion_recall_block}"
        f"{memory_recall_persona_block}"
        f"{memory_recall_evidence_block}"
        f"{_persona_expression_hint_block}"
        f"{_self_reflection_block}"
        f"{_bond_narrative_block}"
        f"{_drift_correction_block}"
        f"{emotion_binding_block}"
        f"{str(_render_contract_section_titles.get('context_validation') or '【上下文校验】')}\n"
        f"{_context_valid_label}"
        f"{_context_reason_label}"
        f"{_context_violation_count_label}"
        f"{str(_render_contract_section_titles.get('worldview') or '【世界观设定】')}\n{world_block}\n\n"
        f"{str(_render_contract_section_titles.get('constraints') or '【约束条件】')}\n{constraints_json}\n\n"
        f"{_node_label}"
        f"{_output_language_label}"
        f"{str(_render_contract_section_titles.get('user_message') or '【用户消息】')}\n{user_message}\n\n"
        f"{_retrieval_status_label}"
        f"{_retrieval_reason_label}"
        f"{str(_render_contract_section_titles.get('retrieval_evidence') or '【检索证据】')}\n{evidence_block}\n\n"
        f"{episodic_vector_block}"
        f"{str(_render_contract_section_titles.get('core_narrative') or '【核心语义草稿】')}\n{core_narrative}\n\n"
        f"{str(_render_contract_section_titles.get('planner') or '【规划提示】')}\n{planner_text}\n\n"
        f"{str(_render_contract_section_titles.get('safety') or '【安全提示】')}\n{verifier_text}\n\n"
        f"{str(_render_contract_section_titles.get('intent_draft') or '【意图草稿】')}\n{core_intent_json}\n"
    )
