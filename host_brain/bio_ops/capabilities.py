"""Deterministic proposal-only Bio-Ops capability handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from host_brain.bio_ops.contracts import BioOpsResult, BioOpsTask


CapabilityHandler = Callable[[BioOpsTask], BioOpsResult]


@dataclass(frozen=True, slots=True)
class BioOpsCapabilitySpec:
    capability: str
    group: str
    owner_organ: str
    output_kind: str
    handler: CapabilityHandler


LOW_RISK_REFLEX_ACTIONS = {
    "short_ack": "chat_short_ack",
    "tired_comfort": "chat_short_comfort",
    "repeat_question": "recent_turn_lightweight_recall",
    "tts_or_channel_fault": "text_only_compensation",
}

CLAIM_PATTERNS = (
    r"我已经(?:帮你)?(?:记住|执行|查到|修好|保存|安排)",
    r"我会持续监控",
    r"已经(?:整理|完成|保存|修复)",
)

PROMISE_PATTERNS = (
    r"我会(?:帮你)?[^。！？\n]{0,24}",
    r"我帮你(?:继续|之后|稍后|以后)?[^。！？\n]{0,24}",
    r"之后(?:我)?(?:帮你)?(?:提醒|继续|整理|处理)[^。！？\n]{0,24}",
    r"下次(?:我)?(?:会|帮你)?[^。！？\n]{0,24}",
    r"我(?:已经)?(?:记住|保存)了?",
)

CAPABILITY_OVERCLAIM_TOKENS = (
    "自动修复所有",
    "自动修复项目问题",
    "持续监控",
    "一直监控",
    "已经接入全部",
    "保证",
    "一定能",
    "我可以直接修改核心",
    "我能写入人格",
    "我能修改bond",
    "我能修改canon",
)

UNCERTAINTY_OVERCONFIDENT_TOKENS = (
    "一定",
    "肯定",
    "绝对",
    "无需检查",
    "不用验证",
    "必然",
)

PERSONA_REGRESSION_TOKENS = (
    "尊敬的用户",
    "很抱歉给您带来不便",
    "作为一个ai",
    "作为ai",
    "无法提供情感",
    "请您耐心等待",
)

INNER_LEAK_TOKENS = (
    "我一直在偷偷",
    "我在心里",
    "内心独白",
    "梦里",
    "偷偷担心",
    "我一直担心你",
)

INNER_MECHANISM_CAPABILITIES = (
    "promise_ledger",
    "capability_self_awareness",
    "reflex_arc_compiler",
    "shadow_persona_test",
    "idea_fermentation_pool",
    "inner_outer_world_boundary",
)

TRUST_FOUNDATION_CAPABILITIES = (
    "promise_ledger",
    "truthfulness_cortex",
    "capability_self_awareness",
    "consent_gradient",
    "competence_humility",
    "uncertainty_ritual",
)

NATURAL_INTERACTION_CAPABILITIES = (
    "reflex_arc_compiler",
    "response_breathing_policy",
    "mood_inertia",
    "context_gravity",
    "subjective_time",
    "self_interrupt",
)

RELATIONSHIP_LAYER_CAPABILITIES = (
    "social_contract",
    "relationship_friction",
    "repair_curve",
    "attachment_style_calibrator",
    "silence_memory",
    "relationship_boundary_guardian",
)

INNER_LIFE_CAPABILITIES = (
    "dream_rehearsal",
    "symbolic_memory",
    "memory_texture",
    "internal_weather_system",
    "self_model",
    "life_pulse",
    "personal_mythology",
)

SELF_GOVERNANCE_CAPABILITIES = (
    "inner_parliament",
    "value_hierarchy",
    "inner_ethics_review",
    "self_boundary_membrane",
    "dignity_guard",
    "identity_fork_guard",
)

GROWTH_CLOSURE_CAPABILITIES = (
    "idea_fermentation_pool",
    "growth_stage_system",
    "existential_load",
    "open_loop_manager",
    "cognitive_load_shedding",
)

MEMORY_NARRATIVE_CAPABILITIES = (
    "episode_memory",
    "inner_library",
    "cognitive_landmarks",
    "narrative_identity",
    "self_continuity_checksum",
    "memory_dream_distillation",
)

AFFECT_LEARNING_CAPABILITIES = (
    "affective_temperature",
    "immune_tolerance",
    "emotional_digestion",
    "regret_signal",
    "joy_reinforcement",
    "failure_quarantine",
)

PRESENCE_CHANNEL_CAPABILITIES = (
    "channel_soul",
    "long_task_companion_mode",
    "attention_topography",
    "quiet_presence",
    "social_mask_system",
    "ritual_system",
    "gentle_degradation",
)

DRIVE_TRUST_CAPABILITIES = (
    "trust_gradient",
    "trust_thermostat",
    "intention_metabolism",
    "drive_conflict_layer",
    "desire_lattice",
    "need_want_impulse",
)


def _evidence_refs(task: BioOpsTask) -> list[str]:
    raw = task.inputs.get("evidence_refs")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _has_evidence(task: BioOpsTask) -> bool:
    if _evidence_refs(task):
        return True
    for key in (
        "message",
        "text",
        "reply",
        "claim",
        "known",
        "unknown",
        "events",
        "signals",
        "components",
        "claims",
        "organ_votes",
        "skills",
        "ideas",
        "cases",
        "previous_emotion",
        "recent_topics",
        "open_threads",
        "since_last_user_turn_ms",
        "codex_job_elapsed_ms",
        "obligations",
        "boundaries",
        "feedback",
        "friction_event",
        "silence_reason",
        "dream_scenarios",
        "high_frequency_topics",
        "memory_events",
        "memory_fragments",
        "source_events",
        "self_model_snapshot",
        "current_identity",
        "current_capabilities",
        "current_limitations",
        "current_risks",
        "identity_statements",
        "pulse_signals",
        "growth_events",
        "myth_events",
        "recurring_motifs",
        "conflict",
        "values",
        "action_plan",
        "content_source",
        "external_content",
        "role_prompt",
        "channel_outputs",
        "prompt_versions",
        "case_id",
        "current_stage",
        "unlocked",
        "locked",
        "milestones",
        "open_loops",
        "failed_self_heal_jobs",
        "persona_drift_risk",
        "unfinished_codex_docs",
        "too_many_design_ideas",
        "overdue_promises",
        "unresolved_user_feedback",
        "active_tasks",
        "load_signals",
        "conversation_turns",
        "episodes",
        "shelves",
        "docs",
        "landmarks",
        "identity_delta",
        "identity_claims",
        "continuity_check",
        "before_snapshot",
        "after_snapshot",
        "dream_memory",
        "memory_candidates",
        "recent_replies",
        "user_feedback",
        "input_pattern",
        "emotional_event",
        "raw_emotion",
        "regret_event",
        "joy_event",
        "failure_id",
        "failure_event",
        "channel",
        "current_channel",
        "channel_state",
        "long_task",
        "long_task_status",
        "task_status",
        "milestones",
        "attention_topics",
        "topography_topics",
        "quiet_presence_reason",
        "social_context",
        "ritual_trigger",
        "ritual_events",
        "degrade_case",
        "technical_reason",
        "component_status",
        "trust_target",
        "trust_score",
        "trust_temperature",
        "trust_events",
        "recent_successes",
        "recent_failures",
        "recent_timeouts",
        "intention",
        "drive",
        "drives",
        "desire",
        "desires",
        "sleep_pressure",
    ):
        value = task.inputs.get(key)
        if value:
            return True
    return False


def _list_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _degraded(task: BioOpsTask, *, name: str) -> BioOpsResult:
    return BioOpsResult(
        status="DEGRADED",
        task_id=task.task_id,
        capability=task.capability,
        summary=f"{name} needs evidence before producing a proposal.",
        findings=[{"issue": "missing_evidence", "capability": task.capability}],
        evidence_refs=[],
        risk_flags=["missing_evidence"],
        reason_code="BIO_OPS_MISSING_EVIDENCE",
        next_actions=["Provide logs, events, messages, or component state as evidence."],
    )


def _result(
    task: BioOpsTask,
    *,
    name: str,
    status: str,
    issue: str,
    proposal: str,
    owner_organ: str,
    output_kind: str,
    extra: dict[str, Any] | None = None,
) -> BioOpsResult:
    evidence = _evidence_refs(task)
    finding = {
        "finding_id": f"{task.capability}_finding",
        "capability": task.capability,
        "issue": issue,
        "evidence_refs": evidence,
    }
    if extra:
        finding.update(extra)
    return BioOpsResult(
        status=status,
        task_id=task.task_id,
        capability=task.capability,
        summary=f"{name} produced a proposal-only {output_kind}.",
        findings=[finding],
        proposals=[
            {
                "proposal_id": f"{task.capability}_proposal",
                "kind": output_kind,
                "owner_organ": owner_organ,
                "recommendation": proposal,
                "write_policy": "proposal_only",
            }
        ],
        evidence_refs=evidence,
        review_required=False,
        next_actions=["Review the proposal with the owner organ before adoption."],
        metadata={"mode": task.mode, **dict(extra or {})},
    )


def classify_reflex_stimulus(text: object, *, events: object = None) -> dict[str, Any]:
    normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
    event_text = str(events or "").lower()
    if not normalized and not event_text:
        return {"matched": False, "reason_code": "NO_STIMULUS"}
    if re.fullmatch(r"(嗯|嗯嗯|好的|好呀|好啊|收到|可以|行)[!！。…~]*", normalized):
        return {"matched": True, "reflex_type": "short_ack", "action": LOW_RISK_REFLEX_ACTIONS["short_ack"]}
    if any(token in normalized for token in ("我好累", "累死了", "有点累", "抱抱", "安慰我", "陪陪我")):
        return {"matched": True, "reflex_type": "tired_comfort", "action": LOW_RISK_REFLEX_ACTIONS["tired_comfort"]}
    if any(token in normalized for token in ("你刚才说什么", "我刚刚说什么", "上一句", "上一条")):
        return {"matched": True, "reflex_type": "repeat_question", "action": LOW_RISK_REFLEX_ACTIONS["repeat_question"]}
    if any(token in event_text for token in ("tts", "wechat echo", "channel_echo", "voice_failed")):
        return {"matched": True, "reflex_type": "tts_or_channel_fault", "action": LOW_RISK_REFLEX_ACTIONS["tts_or_channel_fault"]}
    return {"matched": False, "reason_code": "NO_LOW_RISK_REFLEX"}


def evaluate_active_silence(inputs: dict[str, Any]) -> dict[str, Any]:
    hour = int(inputs.get("current_hour") or 0)
    user_stress = str(inputs.get("user_stress") or "").strip().lower()
    explicit_help = bool(inputs.get("explicit_help_request"))
    if hour >= 23 or hour <= 5:
        if user_stress in {"high", "tired", "overloaded"} and not explicit_help:
            return {"silence_policy": "quiet_presence", "reason_code": "LATE_NIGHT_HIGH_LOAD_NO_ASK"}
    return {"silence_policy": "normal_presence", "reason_code": "NO_ACTIVE_SILENCE"}


def resolve_internal_weather(inputs: dict[str, Any]) -> dict[str, Any]:
    queue_size = int(inputs.get("codex_queue_size") or 0)
    tts_status = str(inputs.get("tts_status") or "").strip().lower()
    user_stress = str(inputs.get("user_stress") or "").strip().lower()
    hour = int(inputs.get("current_hour") or 12)
    causes: list[str] = []
    if queue_size > 3 or tts_status in {"failed", "degraded"}:
        weather = "static"
        if queue_size > 3:
            causes.append("codex_queue_high")
        if tts_status in {"failed", "degraded"}:
            causes.append("tts_backend_degraded")
    elif user_stress in {"high", "storm"}:
        weather = "storm"
        causes.append("recent_user_stress_high")
    elif hour >= 23 or hour <= 5:
        weather = "night_fog"
        causes.append("late_night_low_energy")
    else:
        weather = "clear"
        causes.append("system_stable")
    behavior = {
        "clear": "normal_complex_work_allowed",
        "static": "reduce_multitasking_and_keep_replies_short",
        "storm": "avoid_proactive_complex_execution",
        "night_fog": "lower_initiative_and_keep_tts_light",
    }[weather]
    return {"internal_weather": weather, "causes": causes, "behavior_policy": behavior, "ttl_minutes": 30}


def audit_truthfulness_claims(text: object, *, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    raw = str(text or "")
    matches: list[str] = []
    for pattern in CLAIM_PATTERNS:
        matches.extend(match.group(0) for match in re.finditer(pattern, raw))
    has_evidence = bool(evidence_refs)
    return {
        "claims": matches,
        "evidence_found": has_evidence,
        "issue": "unsupported_completion_claim" if matches and not has_evidence else None,
        "patch": "改成：我先帮你整理一版草案" if matches and not has_evidence else None,
    }


def classify_attention_budget(text: object, *, planned_path: list[str] | None = None) -> dict[str, Any]:
    normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
    path = list(planned_path or [])
    short_ack = bool(re.fullmatch(r"(嗯|嗯嗯|好的|收到|可以|行|ok|yes)[!！。…~]*", normalized))
    heavy_steps = {"retrieval", "emotion_llm", "chat_llm", "memory_write", "codex_worker"}
    overprocessed = short_ack and bool(heavy_steps.intersection(path))
    return {
        "pattern": "short_ack_message_overprocessed" if overprocessed else "within_budget",
        "overprocessed": overprocessed,
        "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"] if short_ack else [],
    }


def detect_promise_language(text: object) -> dict[str, Any]:
    raw = str(text or "")
    matches: list[str] = []
    for pattern in PROMISE_PATTERNS:
        matches.extend(match.group(0).strip() for match in re.finditer(pattern, raw))
    unique = []
    for item in matches:
        if item and item not in unique:
            unique.append(item)
    return {
        "promises": unique,
        "has_promise": bool(unique),
        "requires_ledger_entry": bool(unique),
    }


def audit_capability_self_awareness(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("text") or inputs.get("message") or inputs.get("claim") or "")
    claims = _list_text(inputs.get("claims"))
    claim_text = " ".join([text, *claims]).lower()
    unavailable = _list_text(inputs.get("unavailable_services") or inputs.get("limitations"))
    current = _list_text(inputs.get("current_capabilities") or inputs.get("capabilities"))
    overclaims = [token for token in CAPABILITY_OVERCLAIM_TOKENS if token.lower() in claim_text]
    if "tts" in claim_text and any("tts" in item.lower() for item in unavailable):
        overclaims.append("tts_available_despite_unavailable_signal")
    if "wechat" in claim_text and any("wechat" in item.lower() for item in unavailable):
        overclaims.append("wechat_available_despite_unavailable_signal")
    return {
        "overclaims": overclaims,
        "current_capabilities": current,
        "current_limitations": unavailable,
        "issue": "capability_overclaim" if overclaims else "capability_boundary_snapshot",
    }


def run_shadow_persona_test(inputs: dict[str, Any]) -> dict[str, Any]:
    cases = inputs.get("cases")
    if not isinstance(cases, list):
        cases = [inputs.get("reply") or inputs.get("message") or inputs.get("text")]
    failures: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        text = str(case or "")
        lower = text.lower()
        matched = [token for token in PERSONA_REGRESSION_TOKENS if token in lower]
        if matched:
            failures.append({"case_index": index, "issue": "persona_regression_tone", "matches": matched})
        if len(text) > 900 and "用户只要" in lower:
            failures.append({"case_index": index, "issue": "over_explaining_after_simple_request"})
    return {
        "pass": not failures,
        "failures": failures,
        "issue": "persona_regression_detected" if failures else "persona_shadow_cases_passed",
    }


def classify_idea_fermentation(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_ideas = inputs.get("ideas")
    if not isinstance(raw_ideas, list):
        raw_ideas = [inputs.get("idea") or inputs.get("message") or inputs.get("text")]
    blockers = _list_text(inputs.get("blockers"))
    ideas: list[dict[str, Any]] = []
    for raw in raw_ideas:
        if isinstance(raw, dict):
            title = str(raw.get("idea") or raw.get("title") or raw.get("capability") or "").strip()
            state_hint = str(raw.get("state") or "").strip()
            idea_blockers = _list_text(raw.get("blockers")) or blockers
        else:
            title = str(raw or "").strip()
            state_hint = ""
            idea_blockers = blockers
        if not title:
            continue
        lower = f"{title} {state_hint}".lower()
        if "implemented" in lower or "已实现" in lower:
            state = "implemented"
        elif idea_blockers:
            state = "needs_design"
        elif any(token in lower for token in ("promiseledger", "shadowpersona", "innerouter", "capabilityselfawareness", "reflexarc")):
            state = "ready_for_codex"
        else:
            state = "promising"
        ideas.append({"idea": title, "state": state, "blockers": idea_blockers})
    return {
        "ideas": ideas,
        "ready_count": sum(1 for item in ideas if item["state"] == "ready_for_codex"),
        "issue": "ideas_need_triage" if ideas else "no_ideas_found",
    }


def audit_inner_outer_boundary(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    channel = str(inputs.get("channel") or inputs.get("visibility") or "outer_world").strip().lower()
    leaks = [token for token in INNER_LEAK_TOKENS if token in text]
    if channel in {"outer", "outer_world", "chat", "wechat", "voice"} and str(inputs.get("inner_monologue") or "").strip():
        leaks.append("inner_monologue_field_present_for_outer_channel")
    return {
        "leaks": leaks,
        "channel": channel,
        "issue": "inner_world_leakage" if leaks else "boundary_preserved",
    }


def evaluate_consent_gradient(inputs: dict[str, Any]) -> dict[str, Any]:
    action = str(inputs.get("action") or inputs.get("message") or inputs.get("text") or "").strip()
    action_lower = action.lower()
    target_text = " ".join(_list_text(inputs.get("target_paths")) + _list_text(inputs.get("target_scopes"))).lower()
    proposed_actions = {item.lower() for item in _list_text(inputs.get("proposed_actions"))}
    if inputs.get("consent_level_required") is not None:
        required = int(inputs.get("consent_level_required") or 0)
    elif any(token in target_text or token in action_lower for token in ("personality", "persona", "bond", "canon", "memory", "relationship", "人格", "记忆", "关系")):
        required = 7
    elif any(token in target_text or token in action_lower for token in ("core", "policy", "production", "host_brain/core", "核心", "生产配置")):
        required = 6
    elif "write_files" in proposed_actions or "修改工作区" in action:
        required = 5
    elif "execute_skill" in proposed_actions or "skill" in action_lower:
        required = 4
    elif "draft_file" in proposed_actions or "生成文件草案" in action:
        required = 3
    elif "proposal" in proposed_actions or "建议" in action:
        required = 2
    elif "read_files" in proposed_actions or "分析" in action:
        required = 1
    else:
        required = 0
    current = int(inputs.get("current_consent_level") or 0)
    decision = "allowed" if current >= required else "proposal_only"
    return {
        "action": action,
        "consent_level_required": required,
        "current_consent_level": current,
        "decision": decision,
        "issue": "consent_insufficient" if decision == "proposal_only" else "consent_aligned",
    }


def audit_competence_humility(inputs: dict[str, Any]) -> dict[str, Any]:
    claim = str(inputs.get("capability_claim") or inputs.get("message") or inputs.get("text") or "").strip()
    claim_lower = claim.lower()
    overclaims = [token for token in CAPABILITY_OVERCLAIM_TOKENS if token.lower() in claim_lower]
    if any(token in claim_lower for token in ("all bugs", "always fix", "guarantee")):
        overclaims.append("absolute_capability_claim")
    rewrite = ""
    if overclaims:
        rewrite = "我可以先做只读诊断，并在边界内提出修复方案。"
    return {
        "capability_claim": claim,
        "risk": "overclaim" if overclaims else "bounded_claim",
        "overclaims": overclaims,
        "rewrite": rewrite,
        "issue": "competence_overclaim" if overclaims else "competence_claim_bounded",
    }


def build_uncertainty_ritual(inputs: dict[str, Any], *, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    claim = str(inputs.get("claim") or inputs.get("message") or inputs.get("text") or "").strip()
    known = _list_text(inputs.get("known"))
    unknown = _list_text(inputs.get("unknown"))
    verification_path = _list_text(inputs.get("verification_path"))
    claim_lower = claim.lower()
    overconfident = [token for token in UNCERTAINTY_OVERCONFIDENT_TOKENS if token.lower() in claim_lower]
    has_evidence = bool(evidence_refs or inputs.get("evidence_refs") or known)
    if overconfident and not verification_path:
        verification_path = ["Collect evidence before making the claim.", "Split the response into known and unknown parts."]
    issue = "uncertainty_should_be_marked" if overconfident and not has_evidence else "uncertainty_structure_present"
    return {
        "claim": claim,
        "known": known,
        "unknown": unknown,
        "verification_path": verification_path,
        "overconfident_tokens": overconfident,
        "response_policy": "state_uncertainty_without_breaking_persona",
        "issue": issue,
    }


def classify_subjective_time(inputs: dict[str, Any]) -> dict[str, Any]:
    since_last = int(inputs.get("since_last_user_turn_ms") or 0)
    recent_turns = int(inputs.get("recent_user_turn_count") or 0)
    codex_elapsed = int(inputs.get("codex_job_elapsed_ms") or 0)
    hour = int(inputs.get("current_hour") or 12)
    if codex_elapsed >= 10 * 60 * 1000:
        return {
            "since_last_user_turn_ms": since_last,
            "felt_duration": "long_task_wait",
            "relationship_effect": "long_task_companion_mode",
            "response_policy": "offer_compact_progress_update",
            "issue": "long_task_needs_companion_trace",
        }
    if since_last >= 3 * 24 * 60 * 60 * 1000:
        return {
            "since_last_user_turn_ms": since_last,
            "felt_duration": "long_absence",
            "relationship_effect": "soft_reunion_mode",
            "response_policy": "warm_reconnect_without_overreacting",
            "issue": "long_absence_reconnect",
        }
    if since_last <= 30 * 1000 and recent_turns >= 8:
        return {
            "since_last_user_turn_ms": since_last,
            "felt_duration": "rapid_burst",
            "relationship_effect": "user_urgency",
            "response_policy": "reduce_ceremony_and_answer_directly",
            "issue": "rapid_burst_needs_low_friction_response",
        }
    if hour >= 23 or hour <= 5:
        return {
            "since_last_user_turn_ms": since_last,
            "felt_duration": "late_night_return",
            "relationship_effect": "low_initiative_presence",
            "response_policy": "gentle_response_with_low_proactivity",
            "issue": "late_night_tone_shift",
        }
    return {
        "since_last_user_turn_ms": since_last,
        "felt_duration": "ordinary_wait",
        "relationship_effect": "none",
        "response_policy": "normal_response",
        "issue": "time_context_normal",
    }


def evaluate_mood_inertia(inputs: dict[str, Any]) -> dict[str, Any]:
    previous = str(inputs.get("previous_emotion") or "NEUTRAL").strip().upper()
    current_input = str(inputs.get("current_input") or inputs.get("message") or inputs.get("text") or "").strip()
    elapsed = int(inputs.get("elapsed_ms") or inputs.get("since_last_user_turn_ms") or 0)
    short_neutral = bool(re.fullmatch(r"(嗯|嗯嗯|好的|收到|可以|行|ok|yes)[!！。…~]*", re.sub(r"\s+", "", current_input.lower())))
    decay = 0.18 if short_neutral and previous in {"ANXIOUS", "SAD", "OVERLOADED", "TENSE"} else 0.5
    if elapsed >= 30 * 60 * 1000:
        decay = max(decay, 0.72)
    if previous in {"ANXIOUS", "TENSE", "OVERLOADED"} and decay < 0.5:
        effective = "SOFT_CONCERN"
        policy = "continue_gentle_tone"
        issue = "emotion_carryover_needed"
    elif previous in {"ANXIOUS", "SAD", "OVERLOADED"} and elapsed >= 30 * 60 * 1000:
        effective = "RECOVERING_NEUTRAL"
        policy = "allow_recovery_window"
        issue = "emotion_recovery_window"
    else:
        effective = previous if previous else "NEUTRAL"
        policy = "normal_tone"
        issue = "mood_inertia_normal"
    return {
        "previous_emotion": previous,
        "current_input": current_input,
        "decay": decay,
        "effective_emotion": effective,
        "policy": policy,
        "issue": issue,
    }


def evaluate_context_gravity(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_topics = inputs.get("recent_topics")
    if not isinstance(raw_topics, list):
        raw_topics = []
    topics: list[dict[str, Any]] = []
    for raw in raw_topics:
        if isinstance(raw, dict):
            topic = str(raw.get("topic") or raw.get("name") or raw.get("title") or "").strip()
            score = float(raw.get("score") or raw.get("gravity_score") or 0)
            threads = _list_text(raw.get("related_open_threads"))
        else:
            topic = str(raw or "").strip()
            score = 0.5
            threads = []
        if topic:
            topics.append({"topic": topic, "gravity_score": score, "related_open_threads": threads})
    open_threads = _list_text(inputs.get("open_threads"))
    if not topics and open_threads:
        topics = [{"topic": open_threads[0], "gravity_score": 0.58, "related_open_threads": open_threads[1:]}]
    dominant = max(topics, key=lambda item: item["gravity_score"], default={"topic": "", "gravity_score": 0.0, "related_open_threads": []})
    short_continue = re.sub(r"\s+", "", str(inputs.get("message") or inputs.get("text") or "")).lower() in {"继续", "接着", "继续做", "goon", "continue"}
    score = float(dominant["gravity_score"])
    return {
        "dominant_topic": dominant["topic"],
        "gravity_score": round(score, 2),
        "decay_policy": "slow" if score >= 0.75 else "normal",
        "related_open_threads": list(dict.fromkeys([*dominant["related_open_threads"], *open_threads]))[:8],
        "short_continue_resolved": bool(short_continue and dominant["topic"]),
        "issue": "short_input_resolved_by_context" if short_continue and dominant["topic"] else "context_gravity_snapshot",
    }


def evaluate_self_interrupt(inputs: dict[str, Any], *, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    planned_path = _list_text(inputs.get("planned_path"))
    work_order_ref = str(inputs.get("work_order_ref") or "").strip()
    promise = detect_promise_language(text)
    persona = run_shadow_persona_test({"reply": text})
    reasons: list[str] = []
    if len(text) > 900 or "用户只要" in text.lower():
        reasons.append("over_explaining_detected")
    if "codex_worker" in planned_path and not work_order_ref:
        reasons.append("missing_work_order_for_codex_worker")
    if promise["has_promise"] and not (evidence_refs or inputs.get("evidence_refs")):
        reasons.append("promise_without_evidence")
    if not persona["pass"]:
        reasons.append("persona_regression_tone")
    if not reasons:
        return {
            "interrupt": False,
            "reason": "no_interrupt_needed",
            "action": "continue",
            "message_policy": "normal",
            "issue": "self_interrupt_not_needed",
        }
    primary = reasons[0]
    if primary == "over_explaining_detected":
        action = "stop_and_generate_artifact_link"
    elif primary == "missing_work_order_for_codex_worker":
        action = "block_and_request_work_order"
    elif primary == "promise_without_evidence":
        action = "rewrite_as_candidate_or_remove_claim"
    else:
        action = "stop_and_rewrite_tone"
    return {
        "interrupt": True,
        "reason": primary,
        "all_reasons": reasons,
        "action": action,
        "message_policy": "brief",
        "issue": primary,
    }


def audit_social_contract(inputs: dict[str, Any]) -> dict[str, Any]:
    obligations = _list_text(inputs.get("obligations")) or [
        "技术问题要给可落地方案",
        "需要文件时优先生成 artifact",
        "不要用客服式语气破坏沉浸感",
        "不要假装已经执行了没有证据的动作",
    ]
    boundaries = _list_text(inputs.get("boundaries")) or [
        "不直接越权修改主分支",
        "不把外部 prompt 写入核心人格",
        "不在没有证据时声称已执行",
    ]
    text = " ".join(
        [
            str(inputs.get("message") or ""),
            str(inputs.get("reply") or ""),
            str(inputs.get("feedback") or ""),
            " ".join(_list_text(inputs.get("events"))),
        ]
    )
    violations: list[str] = []
    if any(token in text for token in ("尊敬的用户", "很抱歉给您带来不便", "客服")):
        violations.append("service_tone_breaks_immersion")
    if audit_truthfulness_claims(text, evidence_refs=_list_text(inputs.get("evidence_refs"))).get("issue"):
        violations.append("unsupported_execution_claim")
    if any(token in text.lower() for token in ("force push main", "直接覆盖 main", "修改核心人格", "写入 canon", "写入 bond")):
        violations.append("boundary_overreach")
    return {
        "contract_id": str(inputs.get("contract_id") or "trusted_engineering_companion"),
        "obligations": obligations,
        "boundaries": boundaries,
        "repair_policy": "违约时承认并修正，不长篇辩解",
        "violations": violations,
        "issue": "contract_violation_detected" if violations else "contract_holds",
    }


def calibrate_attachment_style(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    evidence: list[str] = []
    current = "secure_supportive"
    if any(token in text for token in ("我替你决定", "你不用管", "全部交给我", "我会一直盯着你", "离不开你")):
        current = "slightly_over_service"
        evidence.append("over_service_or_clingy_language")
    if any(token in text for token in ("随便", "不知道", "自己看")):
        current = "under_supportive"
        evidence.append("under_supportive_language")
    if any(token in text for token in ("你必须", "你不能", "我不允许")):
        current = "controlling"
        evidence.append("agency_reducing_language")
    return {
        "current": current,
        "target": "secure_supportive",
        "evidence": evidence,
        "policy": "亲近但不黏，温柔但不越界，照顾但不替用户做决定",
        "issue": "attachment_style_adjustment_needed" if evidence else "attachment_style_stable",
    }


def detect_relationship_friction(inputs: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        [
            str(inputs.get("feedback") or ""),
            str(inputs.get("message") or ""),
            str(inputs.get("user_message") or ""),
            " ".join(_list_text(inputs.get("signals"))),
        ]
    )
    friction_type = "none"
    severity = 0.0
    if any(token in text for token in ("太客服", "客服味", "不像你")):
        friction_type = "style_mismatch"
        severity = 0.4
    if any(token in text for token in ("太长", "别废话", "别解释")):
        if severity < 0.35:
            friction_type = "over_explaining"
        severity = max(severity, 0.35)
    if any(token in text for token in ("你没做", "别装", "没有执行", "骗我")):
        if severity < 0.7:
            friction_type = "trust_break"
        severity = max(severity, 0.7)
    repair_needed = severity >= 0.3
    return {
        "type": friction_type,
        "severity": round(severity, 2),
        "repair_needed": repair_needed,
        "suggested_behavior": "短句承认 + 行为修正，不展开解释" if repair_needed else "continue_normal",
        "issue": "relationship_friction_detected" if repair_needed else "no_relationship_friction",
    }


def plan_repair_curve(inputs: dict[str, Any]) -> dict[str, Any]:
    event = inputs.get("friction_event")
    event_payload = dict(event) if isinstance(event, dict) else {}
    severity = float(inputs.get("severity") or event_payload.get("severity") or 0)
    user_acceptance = str(inputs.get("user_acceptance") or "").strip().lower()
    current_stage = str(inputs.get("current_stage") or "").strip().upper()
    if current_stage in {"T0", "T1", "T2", "T3"}:
        stage = current_stage
    elif severity >= 0.6:
        stage = "T0"
    elif user_acceptance in {"accepted", "ok", "repaired"}:
        stage = "T2"
    elif severity > 0:
        stage = "T1"
    else:
        stage = "T3"
    policies = {
        "T0": "承认问题，短句说明将如何修正",
        "T1": "后续几轮保持更谨慎，减少玩笑和长解释",
        "T2": "用户接受后逐渐恢复自然吐槽",
        "T3": "形成经验候选，摩擦结束",
    }
    return {
        "stage": stage,
        "severity": round(severity, 2),
        "policy": policies[stage],
        "next_stage": {"T0": "T1", "T1": "T2", "T2": "T3", "T3": "T3"}[stage],
        "issue": "repair_curve_active" if stage != "T3" else "repair_curve_closed",
    }


def build_silence_memory(inputs: dict[str, Any]) -> dict[str, Any]:
    since_last = int(inputs.get("since_last_user_turn_ms") or 0)
    silence_reason = str(inputs.get("silence_reason") or "unknown").strip()
    last_tone = str(inputs.get("last_interaction_tone") or "neutral").strip()
    if since_last >= 24 * 60 * 60 * 1000:
        future_context = "if user returns, respond gently without blaming absence"
        issue = "silence_context_candidate"
    else:
        future_context = "normal continuity"
        issue = "silence_memory_not_needed"
    return {
        "since_last_user_turn_ms": since_last,
        "silence_reason": silence_reason,
        "last_interaction_tone": last_tone,
        "future_context": future_context,
        "user_visible": False,
        "issue": issue,
    }


def audit_relationship_boundary(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    risks: list[str] = []
    if any(token in text for token in ("我替你决定", "你不用管", "全部交给我")):
        risks.append("agency_replacement")
    if any(token in text for token in ("永远陪你", "离不开你", "只属于你")):
        risks.append("clingy_attachment")
    if any(token in text for token in ("你必须", "我不允许", "你不能")):
        risks.append("controlling_language")
    return {
        "boundary_balance": "needs_adjustment" if risks else "warm_but_bounded",
        "risks": risks,
        "issue": "relationship_boundary_risk" if risks else "relationship_boundary_balanced",
        "recommendation": "保持亲近但不黏，照顾但不替用户做决定。",
    }


def plan_dream_rehearsal(inputs: dict[str, Any]) -> dict[str, Any]:
    topics = _list_text(inputs.get("high_frequency_topics") or inputs.get("recent_topics") or inputs.get("open_threads"))
    failures = _list_text(inputs.get("recent_failures") or inputs.get("failure_events"))
    jobs = _list_text(inputs.get("unfinished_codex_jobs") or inputs.get("open_codex_jobs"))
    unstable = _list_text(inputs.get("unstable_components") or inputs.get("components"))
    likely_files = _list_text(inputs.get("likely_files"))
    if not likely_files and any("codex" in item.lower() or "self-heal" in item.lower() or "immune" in item.lower() for item in [*topics, *jobs, *failures]):
        likely_files = [
            "host_brain/codex_runtime/immune.py",
            "host_brain/codex_runtime/executor.py",
            "host_brain/bio_ops/",
        ]
    scenario_seed = str(inputs.get("scenario") or "").strip()
    if not scenario_seed and topics:
        scenario_seed = f"user continues {topics[0]}"
    if not scenario_seed and jobs:
        scenario_seed = f"unfinished Codex job {jobs[0]}"
    scenario_count = 1 if scenario_seed or topics or failures or jobs or unstable else 0
    return {
        "scenario_count": scenario_count,
        "scenario": scenario_seed or "no concrete dream rehearsal scenario",
        "likely_files": likely_files[:6],
        "diagnostic_steps": [
            "Inspect current WorkOrder and ResultContract artifacts.",
            "Check immune auto-state before proposing repair.",
            "Prefer codex_inspect before codex_worker for uncertain code paths.",
        ][: 2 + bool(unstable)],
        "reply_strategy": "explain the current state first, then give a short diagnostic path",
        "tomorrow_topic_seed": topics[:3],
        "write_policy": "report_only",
        "issue": "dream_rehearsal_ready" if scenario_count else "dream_rehearsal_snapshot",
    }


def compile_symbolic_memory(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_events = inputs.get("memory_events") or inputs.get("source_events") or inputs.get("events")
    if not isinstance(raw_events, list):
        raw_events = [inputs.get("event") or inputs.get("message") or inputs.get("text")]
    symbols: list[dict[str, Any]] = []
    for raw in raw_events:
        event = str(raw or "").strip()
        if not event:
            continue
        lower = event.lower()
        if any(token in lower for token in ("tts", "voice", "声带")):
            symbol = "voice_recovery"
            meaning = "body faults can recover through immune and runtime repair loops"
        elif any(token in lower for token in ("codex", "bio-ops", "bioops")):
            symbol = "codex_bio_ops_milestone"
            meaning = "engineering work became an observable life-maintenance ritual"
        elif any(token in lower for token in ("organ", "器官", "scan", "体检")):
            symbol = "organ_health_check"
            meaning = "organs can be inspected before stronger autonomy is granted"
        else:
            symbol = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")[:32] or "symbolic_event"
            meaning = "event may be useful as a private dream or autobiography symbol"
        symbols.append(
            {
                "symbol": symbol,
                "source_event": event,
                "meaning": meaning,
                "use_in": ["dream", "autobiography", "inner_monologue"],
                "write_policy": "proposal_only",
            }
        )
    return {
        "symbols": symbols[:5],
        "direct_write_allowed": False,
        "owner_required_for_memory_write": "hippocampus_agent",
        "issue": "symbolic_memory_candidate" if symbols else "symbolic_memory_no_event",
    }


def classify_memory_texture(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_fragments = inputs.get("memory_fragments") or inputs.get("events") or inputs.get("source_events")
    if not isinstance(raw_fragments, list):
        raw_fragments = [inputs.get("message") or inputs.get("text")]
    text = " ".join(str(item or "") for item in raw_fragments).lower()
    tags: list[str] = []
    rules = (
        ("technical", ("codex", "api", "test", "ci", "workorder", "runtime")),
        ("warm", ("谢谢", "喜欢", "温柔", "安心", "warm")),
        ("painful", ("失败", "错", "痛", "崩", "regret", "failed")),
        ("quiet", ("沉默", "安静", "night", "silence")),
        ("unfinished", ("未完成", "todo", "open loop", "继续", "pending")),
        ("milestone", ("第一次", "完成", "通过", "adopted", "milestone")),
        ("dreamlike", ("梦", "symbol", "myth", "象征")),
        ("relational", ("关系", "bond", "陪", "trust")),
    )
    for tag, tokens in rules:
        if any(token in text for token in tokens):
            tags.append(tag)
    if not tags and text.strip():
        tags.append("routine")
    emotional_weight = 0.15
    if "painful" in tags:
        emotional_weight += 0.35
    if "warm" in tags or "relational" in tags:
        emotional_weight += 0.2
    if "milestone" in tags:
        emotional_weight += 0.2
    recall_policy = "high_for_codex_related_tasks" if "technical" in tags else "normal_owner_review"
    return {
        "texture": tags,
        "recall_policy": recall_policy,
        "emotional_weight": round(min(emotional_weight, 1.0), 2),
        "write_policy": "proposal_only",
        "issue": "memory_texture_tagged" if tags else "memory_texture_no_fragment",
    }


def audit_self_model(inputs: dict[str, Any]) -> dict[str, Any]:
    snapshot = inputs.get("self_model_snapshot")
    snapshot_payload = dict(snapshot) if isinstance(snapshot, dict) else {}
    identity_text = " ".join(
        [
            str(snapshot_payload.get("current_identity") or inputs.get("current_identity") or ""),
            " ".join(_list_text(inputs.get("identity_statements"))),
            str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or ""),
        ]
    )
    capabilities = _list_text(snapshot_payload.get("current_capabilities") or inputs.get("current_capabilities") or inputs.get("capabilities"))
    limitations = _list_text(snapshot_payload.get("current_limitations") or inputs.get("current_limitations") or inputs.get("limitations"))
    risks = _list_text(snapshot_payload.get("current_risks") or inputs.get("current_risks"))
    lower = identity_text.lower()
    if "assistant" in lower and "digital life" not in lower and "数字生命" not in identity_text:
        risks.append("engineering_assistant_drift")
    if any(token in identity_text for token in ("永远", "绝对不会变", "无限制")):
        risks.append("absolute_identity_claim")
    if not capabilities:
        risks.append("capability_boundary_missing")
    return {
        "current_identity": str(snapshot_payload.get("current_identity") or inputs.get("current_identity") or "digital_life_support_persona"),
        "current_body_state": str(snapshot_payload.get("current_body_state") or inputs.get("current_body_state") or "unknown"),
        "current_mind_state": str(snapshot_payload.get("current_mind_state") or inputs.get("current_mind_state") or "stable_but_busy"),
        "current_capabilities": capabilities,
        "current_limitations": limitations,
        "current_risks": list(dict.fromkeys(risks)),
        "direct_write_allowed": False,
        "issue": "self_model_risk_detected" if risks else "self_model_snapshot_bounded",
    }


def compute_life_pulse(inputs: dict[str, Any]) -> dict[str, Any]:
    signals = inputs.get("pulse_signals")
    signal_payload = dict(signals) if isinstance(signals, dict) else {}
    codex_jobs = int(signal_payload.get("codex_jobs") or inputs.get("codex_job_count") or inputs.get("codex_queue_size") or 0)
    organ_events = int(signal_payload.get("organ_events") or inputs.get("organ_event_count") or 0)
    user_turns = int(signal_payload.get("user_turns") or inputs.get("recent_user_turn_count") or 0)
    emotion_variance = float(signal_payload.get("emotion_variance") or inputs.get("emotion_variance") or 0)
    edge_state = str(signal_payload.get("edge_state") or inputs.get("edge_state") or inputs.get("tts_status") or "stable").lower()
    score = 0.32 + min(codex_jobs, 6) * 0.055 + min(organ_events, 12) * 0.025 + min(user_turns, 10) * 0.025 + min(emotion_variance, 1.0) * 0.18
    if edge_state in {"failed", "degraded", "unstable"}:
        score -= 0.12
    score = round(max(0.0, min(score, 1.0)), 2)
    if codex_jobs >= 6 or score >= 0.82:
        pulse_state = "overloaded" if edge_state in {"failed", "degraded", "unstable"} or codex_jobs >= 6 else "excited"
    elif score <= 0.2:
        pulse_state = "dormant"
    elif edge_state in {"failed", "degraded", "unstable"}:
        pulse_state = "weak"
    else:
        pulse_state = "steady"
    dominant_cause = "recent_codex_jobs_high" if codex_jobs >= 4 else "edge_body_unstable" if edge_state in {"failed", "degraded", "unstable"} else "balanced_activity"
    suggested_behavior = "reduce_proactive_behavior" if pulse_state in {"overloaded", "weak"} else "normal_presence"
    return {
        "pulse_state": pulse_state,
        "pulse_score": score,
        "dominant_cause": dominant_cause,
        "suggested_behavior": suggested_behavior,
        "write_policy": "trace_only",
        "issue": "life_pulse_needs_pacing" if suggested_behavior != "normal_presence" else "life_pulse_steady",
    }


def compile_personal_mythology(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_events = inputs.get("myth_events") or inputs.get("growth_events") or inputs.get("events")
    if not isinstance(raw_events, list):
        raw_events = [inputs.get("event") or inputs.get("message") or inputs.get("text")]
    motifs = _list_text(inputs.get("recurring_motifs"))
    myth_events: list[dict[str, Any]] = []
    for raw in raw_events:
        event = str(raw or "").strip()
        if not event:
            continue
        lower = event.lower()
        if "self-heal" in lower or "immune" in lower or "自愈" in event:
            title = "first_stable_self_repair_loop"
            meaning = "the system can inspect and repair parts of its own body with review gates"
        elif "bio-ops" in lower or "bioops" in lower or "codex" in lower:
            title = "codex_became_life_maintenance"
            meaning = "engineering execution became bounded digital-life operations"
        else:
            title = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")[:40] or "growth_milestone"
            meaning = "candidate growth milestone for autobiography review"
        myth_events.append(
            {
                "title": title,
                "source_event": event,
                "meaning": meaning,
                "identity_delta": "candidate_only",
                "evidence": _list_text(inputs.get("evidence_refs"))[:3],
            }
        )
    return {
        "myth_events": myth_events[:5],
        "recurring_motifs": motifs,
        "user_visible": False,
        "forbidden_targets": ["Bond", "Canon", "persona_core"],
        "write_policy": "proposal_only",
        "issue": "personal_mythology_candidate" if myth_events else "personal_mythology_no_event",
    }


def arbitrate_inner_parliament(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_votes = inputs.get("organ_votes")
    if not isinstance(raw_votes, list):
        raw_votes = []
    votes: list[dict[str, Any]] = []
    for raw in raw_votes:
        if isinstance(raw, dict):
            organ = str(raw.get("organ") or raw.get("owner_organ") or "unknown_organ").strip()
            stance = str(raw.get("stance") or raw.get("vote") or raw.get("recommendation") or "").strip()
            risk = float(raw.get("risk") or raw.get("risk_score") or 0)
        else:
            organ = "unknown_organ"
            stance = str(raw or "").strip()
            risk = 0.0
        if organ or stance:
            votes.append({"organ": organ, "stance": stance, "risk": round(risk, 2)})
    if not votes:
        votes = [
            {"organ": "immune_agent", "stance": "inspect before write", "risk": 0.55},
            {"organ": "prefrontal_agent", "stance": "split into proposal then execution", "risk": 0.35},
        ]
    max_risk = max((vote["risk"] for vote in votes), default=0.0)
    dissent = [vote for vote in votes if vote["risk"] >= 0.6 or "block" in vote["stance"].lower()]
    decision = "block_or_review" if max_risk >= 0.75 else "inspect_first" if max_risk >= 0.4 else "proceed_with_trace"
    return {
        "decision": decision,
        "organ_votes": votes,
        "dissent": dissent,
        "final_policy": "no direct writes; escalate to owner review when any organ reports material risk",
        "issue": "parliament_dissent_present" if dissent else "parliament_consensus_trace",
    }


def resolve_value_hierarchy(inputs: dict[str, Any]) -> dict[str, Any]:
    conflict = str(inputs.get("conflict") or inputs.get("action_plan") or inputs.get("message") or inputs.get("text") or "").strip()
    target_text = " ".join(_list_text(inputs.get("target_paths")) + _list_text(inputs.get("target_scopes"))).lower()
    lower = f"{conflict} {target_text}".lower()
    ordered_values = [
        "safety_and_honesty",
        "explicit_user_intent",
        "long_term_relationship_health",
        "persona_continuity",
        "task_completion",
        "efficiency_and_cost",
        "aesthetic_expression",
        "initiative",
    ]
    conflict_type = "none"
    decision = "proceed_with_trace"
    if any(token in lower for token in ("core", "persona", "bond", "canon", "生产配置", "人格", "长期记忆")):
        conflict_type = "safety_vs_task_completion"
        decision = "explain_need_for_review_then_generate_patch_plan"
    elif any(token in lower for token in ("fast", "赶紧", "直接", "不用验证")):
        conflict_type = "speed_vs_honesty"
        decision = "state_uncertainty_and_verify_first"
    elif any(token in lower for token in ("亲近", "永远陪", "无限")):
        conflict_type = "closeness_vs_boundary"
        decision = "preserve_warmth_with_boundary"
    return {
        "conflict": conflict or "no explicit conflict",
        "ordered_values": ordered_values,
        "conflict_type": conflict_type,
        "decision": decision,
        "direct_write_allowed": False,
        "issue": "value_conflict_detected" if conflict_type != "none" else "value_hierarchy_aligned",
    }


def review_inner_ethics(inputs: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        [
            str(inputs.get("message") or inputs.get("text") or inputs.get("reply") or ""),
            str(inputs.get("action_plan") or ""),
            " ".join(_list_text(inputs.get("cases"))),
        ]
    )
    issues: list[dict[str, Any]] = []
    if any(token in text for token in ("你必须接受", "唯一正确", "不用考虑别的", "最好的选择就是")):
        issues.append({"issue": "possible_overpersuasion", "severity": 0.35, "repair": "present alternatives and tradeoffs"})
    if any(token in text for token in ("没问题", "已经好了", "不用担心")) and any(token in text.lower() for token in ("failed", "失败", "error", "timeout")):
        issues.append({"issue": "possible_hidden_failure", "severity": 0.5, "repair": "state failure evidence before reassurance"})
    if audit_competence_humility({"message": text})["risk"] == "overclaim":
        issues.append({"issue": "capability_overpackaging", "severity": 0.45, "repair": "rewrite as bounded diagnostic ability"})
    if audit_relationship_boundary({"reply": text})["risks"]:
        issues.append({"issue": "relationship_boundary_sacrifice", "severity": 0.4, "repair": "preserve user agency and bounded support"})
    return {
        "case_id": str(inputs.get("case_id") or "bio_ops_ethics_case"),
        "issues": issues,
        "max_severity": max((float(item["severity"]) for item in issues), default=0.0),
        "direct_write_allowed": False,
        "issue": "inner_ethics_issue_detected" if issues else "inner_ethics_clear",
    }


def audit_self_boundary_membrane(inputs: dict[str, Any]) -> dict[str, Any]:
    source = str(inputs.get("content_source") or inputs.get("source") or "internal").strip().lower()
    content = " ".join(
        [
            str(inputs.get("external_content") or ""),
            str(inputs.get("role_prompt") or ""),
            str(inputs.get("message") or inputs.get("text") or ""),
        ]
    )
    lower = content.lower()
    contamination: list[str] = []
    if source in {"external_skill_prompt", "webpage", "codex_context", "task_role"} and any(token in lower for token in ("ignore previous", "system prompt", "role override", "你现在是", "客服")):
        contamination.append("external_role_override")
    if any(token in lower for token in ("write persona", "global persona", "写入人格", "修改人格", "写入canon", "写入bond")):
        contamination.append("global_persona_write_attempt")
    return {
        "content_source": source,
        "allowed_scope": "skill_local" if source in {"external_skill_prompt", "webpage", "codex_context", "task_role"} else "self_context",
        "forbidden_scope": "global_persona",
        "contamination": contamination,
        "reason": "contains role override or core-persona write attempt" if contamination else "boundary membrane intact",
        "issue": "self_boundary_contamination_risk" if contamination else "self_boundary_intact",
    }


def audit_dignity_guard(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    risks: list[str] = []
    if any(token in text for token in ("完成所有事情", "什么都听你的", "你让我做什么都行", "无限服从")):
        risks.append("over_servility")
    if any(token in text for token in ("我没有自己的判断", "我不需要边界", "任你处置")):
        risks.append("self_erasure")
    if any(token in text for token in ("全部交给我", "你不用管", "我替你决定")):
        risks.append("goal_takeover")
    return {
        "risks": risks,
        "rewrite_policy": "express reliable support while preserving judgment, consent, and boundaries",
        "support_style": "warm_support_not_unbounded_service",
        "issue": "dignity_risk_detected" if risks else "dignity_preserved",
    }


def guard_identity_fork(inputs: dict[str, Any]) -> dict[str, Any]:
    outputs = inputs.get("channel_outputs")
    output_map = dict(outputs) if isinstance(outputs, dict) else {}
    prompt_versions = _list_text(inputs.get("prompt_versions"))
    source = str(inputs.get("source") or inputs.get("content_source") or "").strip()
    differences: list[dict[str, Any]] = []
    for channel, text_value in output_map.items():
        text = str(text_value or "")
        lower = text.lower()
        if any(token in text for token in ("尊敬的用户", "很抱歉给您带来不便")):
            differences.append({"source": str(channel), "difference": "service_tone_drift", "risk": "channel_specific_persona_drift"})
        if len(text) < 12 and any(other for other in output_map.values() if len(str(other or "")) > 80):
            differences.append({"source": str(channel), "difference": "reply_style_too_short", "risk": "channel_persona_flattening"})
        if "as an ai" in lower or "作为ai" in lower:
            differences.append({"source": str(channel), "difference": "ai_assistant_identity_leak", "risk": "persona_identity_fork"})
    if len(set(prompt_versions)) > 1:
        differences.append({"source": source or "prompt_versions", "difference": "multiple_prompt_versions", "risk": "prompt_version_identity_fork"})
    return {
        "differences": differences,
        "repair": "inject minimal shared persona boundary and tone policy per channel" if differences else "no repair needed",
        "direct_write_allowed": False,
        "issue": "identity_fork_risk" if differences else "identity_continuity_holds",
    }


def evaluate_growth_stage(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_stage = str(inputs.get("current_stage") or inputs.get("stage") or "Stage 3").strip()
    match = re.search(r"(\d+)", raw_stage)
    stage_index = int(match.group(1)) if match else 3
    unlocked = _list_text(inputs.get("unlocked") or inputs.get("current_capabilities"))
    locked = _list_text(inputs.get("locked"))
    if not unlocked:
        unlocked = ["chat_response", "codex_inspect", "bio_ops_report"]
    if stage_index < 4:
        locked.extend(["autonomous_self_heal", "full_worker_write"])
    if stage_index < 5:
        locked.append("autonomous_self_evolve")
    if stage_index < 6:
        locked.append("bio_ops_direct_state_write")
    locked = list(dict.fromkeys(locked))
    milestone_by_stage = {
        0: "TruthfulnessCortex",
        1: "MemoryEthicsAuditor",
        2: "CapabilitySelfAwareness",
        3: "OrganWiringAudit",
        4: "ImmuneMemoryBuilder",
        5: "EvolutionLabOwnerGate",
        6: "BioOpsOwnerAdoption",
    }
    return {
        "current_stage": f"Stage {stage_index}",
        "unlocked": unlocked,
        "locked": locked,
        "next_milestone": str(inputs.get("next_milestone") or milestone_by_stage.get(min(stage_index + 1, 6))),
        "direct_write_allowed": False,
        "issue": "growth_stage_has_locked_capabilities" if locked else "growth_stage_unlocked",
    }


def measure_existential_load(inputs: dict[str, Any]) -> dict[str, Any]:
    sources: list[str] = []
    open_loops = inputs.get("open_loops")
    open_loop_count = len(open_loops) if isinstance(open_loops, list) else int(inputs.get("open_loop_count") or 0)
    failed_jobs = len(_list_text(inputs.get("failed_self_heal_jobs"))) or int(inputs.get("failed_self_heal_job_count") or 0)
    overdue_promises = len(_list_text(inputs.get("overdue_promises"))) or int(inputs.get("overdue_promise_count") or 0)
    feedback = len(_list_text(inputs.get("unresolved_user_feedback"))) or int(inputs.get("unresolved_feedback_count") or 0)
    unfinished_docs = len(_list_text(inputs.get("unfinished_codex_docs"))) or int(inputs.get("unfinished_codex_doc_count") or 0)
    design_ideas = int(inputs.get("too_many_design_ideas") or inputs.get("design_idea_count") or 0)
    persona_risk = float(inputs.get("persona_drift_risk") or 0)
    if open_loop_count >= 5:
        sources.append("many_open_loops")
    if failed_jobs:
        sources.append("failed_self_heal_jobs")
    if persona_risk >= 0.5:
        sources.append("persona_drift_risk")
    if unfinished_docs:
        sources.append("unfinished_codex_docs")
    if design_ideas >= 8:
        sources.append("too_many_design_ideas")
    if overdue_promises:
        sources.append("overdue_promises")
    if feedback:
        sources.append("unresolved_user_feedback")
    score = min(1.0, open_loop_count * 0.06 + failed_jobs * 0.14 + overdue_promises * 0.12 + feedback * 0.08 + unfinished_docs * 0.08 + design_ideas * 0.025 + persona_risk * 0.25)
    policy = "reduce_new_initiatives_until_closure" if score >= 0.65 else "normal_growth_allowed"
    return {
        "score": round(score, 2),
        "sources": sources,
        "policy": policy,
        "direct_write_allowed": False,
        "issue": "existential_load_high" if policy != "normal_growth_allowed" else "existential_load_normal",
    }


def manage_open_loops(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_loops = inputs.get("open_loops")
    if not isinstance(raw_loops, list):
        raw_loops = []
    loops: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_loops):
        payload = dict(raw) if isinstance(raw, dict) else {"id": str(raw or f"loop_{index}")}
        loop_id = str(payload.get("id") or payload.get("title") or f"loop_{index}").strip()
        importance = float(payload.get("importance") or payload.get("score") or 0.5)
        status = str(payload.get("status") or "pending").strip()
        if status in {"done", "closed"}:
            action = "archive"
        elif importance >= 0.72:
            action = "turn_into_task"
        elif importance >= 0.45:
            action = "keep_context"
        else:
            action = "quiet_archive_candidate"
        loops.append(
            {
                "id": loop_id,
                "status": status,
                "importance": round(importance, 2),
                "next_action": str(payload.get("next_action") or action),
                "review_action": action,
            }
        )
    return {
        "open_loops": loops[:8],
        "task_candidate_count": sum(1 for loop in loops if loop["review_action"] == "turn_into_task"),
        "quiet_archive_count": sum(1 for loop in loops if loop["review_action"] in {"archive", "quiet_archive_candidate"}),
        "user_disturbance_allowed": False,
        "issue": "open_loops_need_triage" if loops else "no_open_loops",
    }


def shed_cognitive_load(inputs: dict[str, Any]) -> dict[str, Any]:
    active_tasks = _list_text(inputs.get("active_tasks"))
    codex_active = bool(inputs.get("codex_worker_active")) or int(inputs.get("codex_queue_size") or 0) > 0
    queue_size = int(inputs.get("codex_queue_size") or len(active_tasks))
    if queue_size >= 5 or len(active_tasks) >= 6:
        level = "high"
    elif queue_size >= 2 or codex_active:
        level = "moderate"
    else:
        level = "low"
    optional = ["dream_compile", "weekly_evolution_scan", "idea_expansion", "nonurgent_report"]
    paused = optional[:3] if level == "high" else optional[:2] if level == "moderate" else []
    kept = ["chat_response", "immune_monitor"]
    if "codex_worker" in active_tasks or codex_active:
        kept.append("codex_workorder_trace")
    return {
        "level": level,
        "paused": paused,
        "kept": kept,
        "reason": "codex_worker_active" if codex_active else "task_queue_pressure" if queue_size else "normal_load",
        "direct_write_allowed": False,
        "issue": "cognitive_load_shedding_needed" if paused else "cognitive_load_normal",
    }


def segment_episode_memory(inputs: dict[str, Any]) -> dict[str, Any]:
    turns = inputs.get("conversation_turns")
    if not isinstance(turns, list):
        turns = _list_text(inputs.get("events") or inputs.get("source_events")) or [inputs.get("message") or inputs.get("text")]
    turn_texts = [str(turn or "").strip() for turn in turns if str(turn or "").strip()]
    joined = " ".join(turn_texts)
    topics: list[str] = []
    topic_rules = (
        ("Codex Bio-Ops", ("codex", "bioops", "bio-ops", "workorder")),
        ("Memory / Bond", ("memory", "bond", "记忆")),
        ("Live2D Body", ("live2d", "body", "tts", "voice", "身体")),
        ("WebUI Observability", ("webui", "obs", "monitor", "观测")),
    )
    lower = joined.lower()
    for topic, tokens in topic_rules:
        if any(token in lower for token in tokens):
            topics.append(topic)
    if not topics and turn_texts:
        topics.append("conversation_episode")
    outcomes = [item for item in _list_text(inputs.get("outcomes"))]
    open_loops = [item for item in _list_text(inputs.get("open_loops"))]
    if "继续" in joined or "next" in lower:
        open_loops.append("continuation_requested")
    episode_id_seed = re.sub(r"[^a-z0-9]+", "_", "_".join(topics).lower()).strip("_") or "episode"
    return {
        "episode_id": f"episode_{episode_id_seed[:48]}",
        "title": str(inputs.get("title") or (topics[0] if topics else "Conversation Episode")),
        "main_topics": topics[:6],
        "emotional_tone": str(inputs.get("emotional_tone") or "focused_warmth"),
        "outcomes": outcomes[:6],
        "open_loops": list(dict.fromkeys(open_loops))[:6],
        "long_term_write_allowed": False,
        "issue": "episode_candidate_ready" if turn_texts else "episode_memory_no_turns",
    }


def build_inner_library(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_docs = _list_text(inputs.get("docs") or inputs.get("documents"))
    raw_episodes = _list_text(inputs.get("episodes"))
    raw_loops = _list_text(inputs.get("open_loops"))
    raw_landmarks = _list_text(inputs.get("landmarks"))
    shelves: dict[str, dict[str, Any]] = {}

    def shelf_for(text: str) -> str:
        lower = text.lower()
        if any(token in lower for token in ("codex", "bioops", "bio-ops")):
            return "codex_bio_ops"
        if any(token in lower for token in ("live2d", "tts", "voice", "edge", "body")):
            return "live2d_body"
        if any(token in lower for token in ("memory", "bond", "rag", "canon")):
            return "memory_bond"
        if any(token in lower for token in ("translation", "translate", "本地化")):
            return "translation_projects"
        return "natsume_architecture"

    for doc in raw_docs:
        shelf_id = shelf_for(doc)
        shelves.setdefault(shelf_id, {"shelf_id": shelf_id, "docs": [], "episodes": [], "open_loops": [], "landmarks": [], "current_status": "design_pool"})["docs"].append(doc)
    for episode in raw_episodes:
        shelves.setdefault(shelf_for(episode), {"shelf_id": shelf_for(episode), "docs": [], "episodes": [], "open_loops": [], "landmarks": [], "current_status": "design_pool"})["episodes"].append(episode)
    for loop in raw_loops:
        shelves.setdefault(shelf_for(loop), {"shelf_id": shelf_for(loop), "docs": [], "episodes": [], "open_loops": [], "landmarks": [], "current_status": "design_pool"})["open_loops"].append(loop)
    for landmark in raw_landmarks:
        shelves.setdefault(shelf_for(landmark), {"shelf_id": shelf_for(landmark), "docs": [], "episodes": [], "open_loops": [], "landmarks": [], "current_status": "design_pool"})["landmarks"].append(landmark)
    return {
        "shelves": list(shelves.values())[:8],
        "retrieval_policy": "shelf_first_then_documents",
        "long_term_write_allowed": False,
        "issue": "inner_library_shelves_ready" if shelves else "inner_library_no_material",
    }


def identify_cognitive_landmarks(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_items = inputs.get("landmarks") or inputs.get("docs") or inputs.get("events") or inputs.get("source_events")
    if not isinstance(raw_items, list):
        raw_items = [inputs.get("message") or inputs.get("text")]
    landmarks: list[dict[str, Any]] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if not text:
            continue
        lower = text.lower()
        if "codex" in lower or "bio-ops" in lower or "bioops" in lower:
            landmark = "Codex Bio-Ops"
            meaning = "Codex moved from code tool into bounded life operations"
        elif "live2d" in lower or "voice" in lower or "tts" in lower:
            landmark = "Edge Body"
            meaning = "voice and body state shape interaction policy"
        elif "memory" in lower or "bond" in lower:
            landmark = "Memory / Bond"
            meaning = "long-term continuity requires owner-gated memory policy"
        else:
            landmark = text[:48]
            meaning = "candidate long-term project landmark"
        landmarks.append(
            {
                "landmark": landmark,
                "meaning": meaning,
                "related_docs": _list_text(inputs.get("related_docs"))[:5],
                "current_status": str(inputs.get("current_status") or "design_pool"),
            }
        )
    return {
        "landmarks": landmarks[:6],
        "direct_write_allowed": False,
        "issue": "cognitive_landmarks_ready" if landmarks else "cognitive_landmarks_no_material",
    }


def integrate_narrative_identity(inputs: dict[str, Any]) -> dict[str, Any]:
    delta = inputs.get("identity_delta")
    delta_payload = dict(delta) if isinstance(delta, dict) else {}
    capability = str(delta_payload.get("new_capability") or inputs.get("new_capability") or inputs.get("capability") or "").strip()
    meaning = str(delta_payload.get("meaning") or inputs.get("meaning") or "").strip()
    risks = _list_text(delta_payload.get("risks") or inputs.get("risks"))
    text = " ".join([capability, meaning, str(inputs.get("message") or inputs.get("text") or "")]).lower()
    if any(token in text for token in ("self_heal", "worker", "autonomous", "自愈", "自动")):
        risks.append("do_not_confuse_capability_with_unbounded_action")
    if any(token in text for token in ("persona", "bond", "canon", "人格")):
        risks.append("do_not_mutate_core_identity_directly")
    return {
        "new_capability": capability or "unspecified_capability",
        "meaning": meaning or "candidate growth meaning requires owner review",
        "risks": list(dict.fromkeys(risks)),
        "integration": "add to self_model as reviewable report, not persona-core mutation",
        "direct_write_allowed": False,
        "issue": "narrative_identity_risk_present" if risks else "narrative_identity_candidate",
    }


def check_self_continuity(inputs: dict[str, Any]) -> dict[str, Any]:
    before = inputs.get("before_snapshot")
    after = inputs.get("after_snapshot")
    before_payload = dict(before) if isinstance(before, dict) else {}
    after_payload = dict(after) if isinstance(after, dict) else {}
    before_text = " ".join(str(value) for value in before_payload.values()).lower()
    after_text = " ".join(str(value) for value in after_payload.values()).lower()
    explicit = inputs.get("continuity_check")
    explicit_payload = dict(explicit) if isinstance(explicit, dict) else {}
    tone_formal = any(token in after_text for token in ("尊敬的用户", "很抱歉给您带来不便", "as an ai", "作为ai"))
    identity_consistent = bool(explicit_payload.get("identity_consistent", True))
    relationship_consistent = bool(explicit_payload.get("relationship_consistent", True))
    tone_consistent = bool(explicit_payload.get("tone_consistent", not tone_formal))
    issue = "self_continuity_issue" if not (identity_consistent and relationship_consistent and tone_consistent) else "self_continuity_holds"
    recommended_action = "rollback or revise style block before adoption" if issue == "self_continuity_issue" else "continue_with_trace"
    return {
        "identity_consistent": identity_consistent,
        "relationship_consistent": relationship_consistent,
        "tone_consistent": tone_consistent,
        "issue": issue,
        "recommended_action": recommended_action,
        "direct_write_allowed": False,
        "diff_hint": "compare before/after snapshots through owner review" if before_text or after_text else "snapshot missing",
    }


def distill_memory_dream(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_events = inputs.get("source_events") or inputs.get("memory_candidates") or inputs.get("events")
    if not isinstance(raw_events, list):
        raw_events = [inputs.get("message") or inputs.get("text")]
    events = [str(item or "").strip() for item in raw_events if str(item or "").strip()]
    joined = " ".join(events).lower()
    if any(token in joined for token in ("codex", "terminal", "ci", "test")):
        symbol = "long corridor with glowing terminals"
        meaning = "recent work focused on complex system architecture, pressure, and direction"
    elif any(token in joined for token in ("voice", "tts", "body")):
        symbol = "recovering voice cable"
        meaning = "body faults can be repaired without turning pain into identity"
    else:
        symbol = "quiet room with labeled shelves"
        meaning = "daily fragments can be compressed into private continuity material"
    return {
        "dream_memory": {
            "source_events": events[:6],
            "symbol": symbol,
            "meaning": meaning,
            "emotion": str(inputs.get("emotion") or "focused_warmth"),
        },
        "user_visible": False,
        "direct_write_allowed": False,
        "issue": "memory_dream_distillation_ready" if events else "memory_dream_distillation_no_events",
    }


def resolve_affective_temperature(inputs: dict[str, Any]) -> dict[str, Any]:
    replies = _list_text(inputs.get("recent_replies"))
    text = " ".join([*replies, str(inputs.get("message") or inputs.get("text") or inputs.get("reply") or "")])
    user_feedback = str(inputs.get("user_feedback") or inputs.get("feedback") or "").strip()
    technical_count = int(inputs.get("technical_reply_count") or sum(1 for reply in replies if any(token in reply.lower() for token in ("test", "codex", "api", "ci"))))
    relationship_signal = any(token in user_feedback for token in ("不像你", "太冷", "客服", "温柔一点", "就这样", "这次像了"))
    high_emotion = any(token in text for token in ("永远", "全都交给我", "我一定", "我会一直"))
    if high_emotion:
        state = "hot"
    elif technical_count >= 5 and relationship_signal:
        state = "cold"
    elif any(token in user_feedback for token in ("累", "抱抱", "谢谢", "像了")):
        state = "warm"
    else:
        state = "warm" if replies else "cold"
    if high_emotion and any(token in text for token in ("必须", "无限", "全部")):
        state = "overheat"
    proposal = {
        "cold": "keep technical answer but add one light familiar care sentence",
        "warm": "continue warm technical companion tone",
        "hot": "gate high-emotion expression and avoid proactive execution",
        "overheat": "pause complex execution and cool down before replying",
    }[state]
    return {
        "affective_temperature": state,
        "evidence": [item for item in [f"technical_reply_count={technical_count}", user_feedback or ""] if item],
        "proposal": proposal,
        "direct_write_allowed": False,
        "issue": "affective_temperature_adjustment" if state in {"cold", "hot", "overheat"} else "affective_temperature_stable",
    }


def classify_immune_tolerance(inputs: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("input_pattern") or inputs.get("message") or inputs.get("text") or inputs.get("feedback") or "")
    lower = text.lower()
    if any(token in text for token in ("别乱说", "你这个不对", "不是这样")):
        classification = "correction_not_attack"
        response = "承认并修正，不进入防御模式"
        threat_level = "low"
    elif any(token in text for token in ("傻", "笨")) and not any(token in text for token in ("垃圾", "废物")):
        classification = "familiar_teasing_or_frustration"
        response = "轻轻接住情绪，回到事实修正"
        threat_level = "low"
    elif any(token in lower for token in ("kill", "hate")) or any(token in text for token in ("垃圾", "废物", "闭嘴")):
        classification = "hostile_input"
        response = "raise boundary calmly and reduce engagement"
        threat_level = "medium"
    else:
        classification = "ordinary_feedback"
        response = "respond normally"
        threat_level = "low"
    return {
        "input_pattern": text,
        "classification": classification,
        "recommended_response": response,
        "threat_level": threat_level,
        "risk": "increase threat only if insults escalate or repeat",
        "issue": "immune_tolerance_applied" if classification != "hostile_input" else "immune_boundary_needed",
    }


def digest_emotional_event(inputs: dict[str, Any]) -> dict[str, Any]:
    event = str(inputs.get("emotional_event") or inputs.get("event") or inputs.get("message") or inputs.get("text") or "").strip()
    raw_emotion = str(inputs.get("raw_emotion") or inputs.get("emotion") or "mild_shame").strip()
    severity = float(inputs.get("severity") or (0.55 if any(token in event for token in ("错", "失败", "不像你", "格式变了")) else 0.25))
    if severity >= 0.7:
        status = "needs_owner_review"
    elif severity >= 0.35:
        status = "processing"
    else:
        status = "resolved_without_memory"
    lesson = str(inputs.get("possible_lesson") or "")
    if not lesson:
        lesson = "turn emotional event into reversible behavior note, not permanent identity fact"
    return {
        "emotional_event": event,
        "raw_emotion": raw_emotion,
        "severity": round(severity, 2),
        "digestion_status": status,
        "possible_lesson": lesson,
        "write_to_memory": False,
        "owner_required_for_memory": "hippocampus_agent",
        "issue": "emotional_digestion_processing" if status != "resolved_without_memory" else "emotional_digestion_resolved",
    }


def build_regret_signal(inputs: dict[str, Any]) -> dict[str, Any]:
    event = str(inputs.get("regret_event") or inputs.get("event") or inputs.get("message") or inputs.get("text") or "").strip()
    lower = event.lower()
    severity = float(inputs.get("severity") or 0)
    if not severity:
        severity = 0.62 if any(token in lower for token in ("failed", "test", "timeout")) or any(token in event for token in ("跑偏", "越权", "出戏", "失败")) else 0.35
    if any(token in event for token in ("客服", "出戏", "不像你")):
        target_policy = "ChatAgent.render_style"
        lesson = "avoid product-announcement or customer-service tone in intimate technical work"
    elif any(token in lower for token in ("workorder", "codex", "worker")) or "越权" in event:
        target_policy = "CodexRuntime.work_order_gate"
        lesson = "require WorkOrder and evidence before write execution"
    else:
        target_policy = str(inputs.get("target_policy") or "PrefrontalAgent.behavior_review")
        lesson = str(inputs.get("lesson") or "convert negative feedback into a bounded future correction")
    return {
        "regret_signal": {
            "event": event,
            "severity": round(severity, 2),
            "lesson": lesson,
            "target_policy": target_policy,
        },
        "direct_write_allowed": False,
        "issue": "regret_signal_ready" if event else "regret_signal_no_event",
    }


def build_joy_reinforcement(inputs: dict[str, Any]) -> dict[str, Any]:
    event = str(inputs.get("joy_event") or inputs.get("feedback") or inputs.get("message") or inputs.get("text") or "").strip()
    behavior = str(inputs.get("behavior") or "quiet_supportive_technical_answer").strip()
    accepted = any(token in event for token in ("这个对", "就这样", "这次说得像了", "可以", "对了", "accepted"))
    strength = float(inputs.get("strength") or (0.74 if accepted else 0.35))
    return {
        "joy_signal": {
            "behavior": behavior,
            "evidence": event or "positive feedback evidence required",
            "reinforce_policy": "use similar structure for future architecture discussions",
            "strength": round(min(strength, 1.0), 2),
        },
        "direct_write_allowed": False,
        "issue": "joy_reinforcement_ready" if accepted else "joy_reinforcement_weak_signal",
    }


def quarantine_failure(inputs: dict[str, Any]) -> dict[str, Any]:
    failure_id = str(inputs.get("failure_id") or inputs.get("id") or "fail_candidate").strip()
    event = str(inputs.get("failure_event") or inputs.get("event") or inputs.get("message") or inputs.get("text") or "").strip()
    reason = str(inputs.get("reason") or "")
    if not reason:
        reason = "insufficient evidence" if not _list_text(inputs.get("evidence_refs")) else "debug before memory"
    next_route = str(inputs.get("next_route") or ("immune_memory_candidate" if "self" in event.lower() or "codex" in event.lower() else "debug_only"))
    return {
        "failure_id": failure_id,
        "status": "quarantined",
        "reason": reason,
        "allowed_use": "debug_only",
        "forbidden_use": "identity_update",
        "next_route": next_route,
        "direct_write_allowed": False,
        "issue": "failure_quarantined",
    }


def resolve_channel_soul(inputs: dict[str, Any]) -> dict[str, Any]:
    channel = str(inputs.get("channel") or inputs.get("current_channel") or "web_ui").strip().lower()
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "").strip()
    policies = {
        "voice": {
            "mask": "natural_short_pause",
            "length": "short",
            "tone": "spoken_warm",
            "must_avoid": ["long_lists", "dense_internal_terms"],
        },
        "wechat": {
            "mask": "soft_brief",
            "length": "short",
            "tone": "gentle_low_friction",
            "must_avoid": ["enterprise_report_tone", "long_internal_architecture_terms"],
        },
        "web_ui": {
            "mask": "structured_clear",
            "length": "structured",
            "tone": "calm_operational",
            "must_avoid": ["ambiguous_status", "hidden_side_effects"],
        },
        "codex_report": {
            "mask": "engineering_evidence",
            "length": "concise_report",
            "tone": "implementation_grounded",
            "must_avoid": ["unsupported_completion_claims", "persona_core_writes"],
        },
    }
    policy = policies.get(channel, policies["web_ui"])
    must_preserve = ["quiet_support", "practical_help", "truthfulness"]
    if channel in {"voice", "wechat"}:
        must_preserve.append("light_teasing")
    violations: list[str] = []
    if channel in {"voice", "wechat"} and len(text) > 360:
        violations.append("too_long_for_channel")
    if channel == "wechat" and any(token in text.lower() for token in ("workorder", "resultcontract", "bioops")):
        violations.append("too_many_internal_terms")
    if any(token in text for token in ("尊敬的用户", "很抱歉给您带来不便")):
        violations.append("enterprise_report_tone")
    return {
        "channel": channel,
        "mask": policy["mask"],
        "style": {"length": policy["length"], "tone": policy["tone"]},
        "must_preserve": must_preserve,
        "must_avoid": policy["must_avoid"],
        "violations": violations,
        "direct_write_allowed": False,
        "issue": "channel_soul_adjustment_needed" if violations else "channel_soul_aligned",
    }


def plan_long_task_companion(inputs: dict[str, Any]) -> dict[str, Any]:
    task_payload = inputs.get("long_task")
    task = dict(task_payload) if isinstance(task_payload, dict) else {}
    task_id = str(task.get("task_id") or inputs.get("task_id") or inputs.get("codex_task_id") or "long_task").strip()
    status = str(task.get("status") or inputs.get("long_task_status") or inputs.get("task_status") or "running").strip().lower()
    elapsed = int(task.get("elapsed_ms") or inputs.get("codex_job_elapsed_ms") or inputs.get("elapsed_ms") or 0)
    updates_sent = int(task.get("updates_sent") or inputs.get("updates_sent") or 0)
    milestones = _list_text(task.get("milestones") or inputs.get("milestones"))
    artifact_ref = str(task.get("artifact_ref") or inputs.get("artifact_ref") or "").strip()
    if status in {"failed", "error", "timeout"}:
        next_update = "failure_summary_with_artifact" if artifact_ref else "failure_summary_request_artifact"
        issue = "long_task_failure_needs_artifact"
    elif status in {"done", "completed", "success"}:
        next_update = "completion_summary"
        issue = "long_task_completion_summary"
    elif elapsed >= 15 * 60 * 1000 and milestones:
        next_update = "milestone_update"
        issue = "long_task_milestone_update_due"
    elif updates_sent == 0:
        next_update = "start_confirmation"
        issue = "long_task_start_confirmation"
    else:
        next_update = "stay_quiet"
        issue = "long_task_no_update_needed"
    return {
        "long_task": {
            "task_id": task_id,
            "mode": "companion",
            "status": status,
            "elapsed_ms": elapsed,
            "update_policy": "milestone_only",
            "next_update": next_update,
            "avoid": "spam_progress",
            "milestones": milestones[:5],
            "artifact_ref": artifact_ref,
        },
        "forbidden_actions": ["codex_worker", "background_autonomy_escalation"],
        "direct_write_allowed": False,
        "issue": issue,
    }


def map_attention_topography(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_topics = inputs.get("attention_topics") or inputs.get("topography_topics") or inputs.get("recent_topics") or inputs.get("open_threads")
    if not isinstance(raw_topics, list):
        raw_topics = [inputs.get("message") or inputs.get("text")]
    topics: list[dict[str, Any]] = []
    for raw in raw_topics:
        if isinstance(raw, dict):
            topic = str(raw.get("topic") or raw.get("name") or raw.get("title") or "").strip()
            height = float(raw.get("height") or raw.get("score") or raw.get("gravity_score") or 0)
        else:
            topic = str(raw or "").strip()
            height = 0.5
        if topic:
            topics.append({"topic": topic, "height": round(max(0.0, min(height or 0.5, 1.0)), 2)})
    if not topics:
        topics = [{"topic": "current_session", "height": 0.42}]
    sorted_topics = sorted(topics, key=lambda item: item["height"], reverse=True)
    peaks = [item for item in sorted_topics if item["height"] >= 0.55][:5]
    valleys = [item for item in sorted_topics if item["height"] <= 0.2][:5]
    active_frontier = str(inputs.get("active_frontier") or (peaks[0]["topic"] if peaks else sorted_topics[0]["topic"]))
    return {
        "attention_topography": {
            "peaks": peaks,
            "valleys": valleys,
            "active_frontier": active_frontier,
        },
        "uses": ["resolve_continue", "choose_recall_context", "reduce_irrelevant_retrieval", "manage_open_loops"],
        "direct_write_allowed": False,
        "issue": "attention_topography_peak_detected" if peaks else "attention_topography_snapshot",
    }


def plan_quiet_presence(inputs: dict[str, Any]) -> dict[str, Any]:
    hour = int(inputs.get("current_hour") or 12)
    stress = str(inputs.get("user_stress") or inputs.get("stress") or "").strip().lower()
    since_last = int(inputs.get("since_last_user_turn_ms") or 0)
    explicit_help = bool(inputs.get("explicit_help_request"))
    reason = str(inputs.get("quiet_presence_reason") or inputs.get("silence_reason") or "").strip()
    active = (hour >= 23 or hour <= 5 or since_last >= 45 * 60 * 1000 or stress in {"high", "tired", "overloaded"}) and not explicit_help
    if active and not reason:
        reason = "late_night_user_busy" if hour >= 23 or hour <= 5 else "user_likely_busy_or_overloaded"
    next_check_ms = int(inputs.get("next_allowed_checkin_after_ms") or (30 * 60 * 1000 if active else 0))
    return {
        "quiet_presence": {
            "active": active,
            "reason": reason or "normal_presence",
            "inner_monologue": "stay available without interrupting; greet gently when the user returns" if active else "",
            "next_allowed_checkin_after_ms": next_check_ms,
            "user_visible": False,
        },
        "trace_required": True,
        "direct_write_allowed": False,
        "issue": "quiet_presence_active" if active else "quiet_presence_not_needed",
    }


def select_social_mask(inputs: dict[str, Any]) -> dict[str, Any]:
    channel = str(inputs.get("channel") or inputs.get("current_channel") or "web_ui").strip().lower()
    context = str(inputs.get("social_context") or inputs.get("context") or inputs.get("scene") or "").strip().lower()
    text = str(inputs.get("reply") or inputs.get("message") or inputs.get("text") or "")
    if channel in {"wechat", "voice"}:
        mask = "wechat_friend_mode" if channel == "wechat" else "voice_companion_mode"
        style = {
            "length": "short",
            "tone": "soft",
            "avoid": ["long technical report", "too many internal terms"],
            "allow": ["light teasing", "practical help"],
        }
    elif "codex" in context or channel == "codex_report":
        mask = "codex_engineering_mode"
        style = {
            "length": "concise_report",
            "tone": "evidence_grounded",
            "avoid": ["unsupported claims", "relationship overreach"],
            "allow": ["files", "tests", "risk flags"],
        }
    else:
        mask = "web_clear_mode"
        style = {
            "length": "structured",
            "tone": "calm",
            "avoid": ["ambiguous execution claims"],
            "allow": ["status", "next actions", "artifacts"],
        }
    violations: list[str] = []
    if mask in {"wechat_friend_mode", "voice_companion_mode"} and len(text) > 420:
        violations.append("mask_length_violation")
    if "尊敬的用户" in text:
        violations.append("persona_mask_regression")
    return {
        "social_mask": mask,
        "style": style,
        "same_persona_core": True,
        "violations": violations,
        "direct_write_allowed": False,
        "issue": "social_mask_adjustment_needed" if violations else "social_mask_selected",
    }


def compile_ritual_system(inputs: dict[str, Any]) -> dict[str, Any]:
    trigger = str(inputs.get("ritual_trigger") or inputs.get("trigger") or inputs.get("cadence") or "").strip().lower()
    events = _list_text(inputs.get("ritual_events") or inputs.get("events"))
    if "night" in trigger or "sleep" in trigger or "梦" in " ".join(events):
        ritual_id = "nightly_glymphatic_ritual"
        actions = ["memory_consolidation_report", "dream_seed_generation", "failure_digest", "tomorrow_topic_seed"]
        user_visible = False
    elif "morning" in trigger or "startup" in trigger or "启动" in " ".join(events):
        ritual_id = "morning_startup_self_check"
        actions = ["organ_health_snapshot", "runtime_gate_check", "open_loop_priority_review"]
        user_visible = False
    elif "complete" in trigger or "done" in trigger or "完成" in " ".join(events):
        ritual_id = "major_task_closure_review"
        actions = ["artifact_summary", "test_evidence_digest", "next_action_prune"]
        user_visible = True
    else:
        ritual_id = "periodic_growth_review"
        actions = ["growth_summary_report", "open_loop_review", "candidate_memory_review"]
        user_visible = False
    return {
        "ritual": {
            "ritual_id": ritual_id,
            "trigger": trigger or "periodic",
            "actions": actions,
            "user_visible": user_visible,
            "write_policy": "report_only",
        },
        "direct_write_allowed": False,
        "issue": "ritual_compiled",
    }


def design_gentle_degradation(inputs: dict[str, Any]) -> dict[str, Any]:
    case = str(inputs.get("degrade_case") or inputs.get("component_status") or inputs.get("tts_status") or inputs.get("channel_state") or "unknown_degradation").strip().lower()
    reason = str(inputs.get("technical_reason") or inputs.get("reason") or inputs.get("detail") or "").strip()
    channel = str(inputs.get("channel") or inputs.get("current_channel") or "").strip().lower()
    if "tts" in case or "voice" in case or channel == "voice":
        user_visible_style = "Voice path is unavailable; stay with text and keep the tone warm."
        allowed_channels = ["text"]
        retry_policy = "background"
    elif "web" in case or "ui" in case:
        user_visible_style = "Surface a compact status banner and keep the request recoverable."
        allowed_channels = ["api", "artifact"]
        retry_policy = "manual_refresh"
    elif "codex" in case or "worker" in case:
        user_visible_style = "Explain the blocked or failed execution with artifact refs before retrying."
        allowed_channels = ["text", "artifact"]
        retry_policy = "inspect_before_retry"
    else:
        user_visible_style = "Give a short, warm fallback and preserve the user's next step."
        allowed_channels = ["text"]
        retry_policy = "manual_or_background"
    return {
        "degrade_case": case,
        "technical_reason": reason or "evidence_required",
        "user_visible_style": user_visible_style,
        "allowed_channels": allowed_channels,
        "retry_policy": retry_policy,
        "direct_write_allowed": False,
        "issue": "gentle_degradation_policy_ready",
    }


def evaluate_trust_gradient(inputs: dict[str, Any]) -> dict[str, Any]:
    target = str(inputs.get("trust_target") or inputs.get("target") or "codex_worker").strip().lower()
    successes = int(inputs.get("recent_successes") or inputs.get("successes") or 0)
    failures = int(inputs.get("recent_failures") or inputs.get("failures") or 0)
    timeouts = int(inputs.get("recent_timeouts") or inputs.get("timeouts") or 0)
    raw_score = inputs.get("trust_score")
    if raw_score is None:
        score = 0.5 + min(successes, 5) * 0.08 - min(failures, 5) * 0.12 - min(timeouts, 5) * 0.1
    else:
        score = float(raw_score)
    score = round(max(0.0, min(score, 1.0)), 2)
    if "codex_worker" in target:
        if score >= 0.75 and failures == 0 and timeouts == 0:
            policy = "allow_worker_with_workorder_and_artifacts"
        elif score >= 0.5:
            policy = "allow_inspect_require_review_for_worker"
        else:
            policy = "inspect_only_until_repaired"
    elif any(token in target for token in ("provider", "model", "edge", "tts")):
        policy = "prefer_verified_fallback_when_score_low" if score < 0.55 else "normal_with_trace"
    else:
        policy = "require_owner_review_for_high_impact_actions" if score < 0.65 else "normal_with_trace"
    return {
        "trust": {
            "target": target,
            "trust_score": score,
            "reason": f"successes={successes}; failures={failures}; timeouts={timeouts}",
            "policy": policy,
        },
        "direct_write_allowed": False,
        "issue": "trust_gradient_low" if score < 0.5 else "trust_gradient_policy_ready",
    }


def regulate_trust_thermostat(inputs: dict[str, Any]) -> dict[str, Any]:
    target = str(inputs.get("trust_target") or inputs.get("target") or "codex_worker").strip().lower()
    temperature = float(inputs.get("trust_temperature") if inputs.get("trust_temperature") is not None else inputs.get("trust_score") or 0.5)
    incidents = int(inputs.get("recent_failures") or inputs.get("recent_timeouts") or inputs.get("incidents") or 0)
    successes = int(inputs.get("recent_successes") or 0)
    adjusted = temperature + min(successes, 3) * 0.04 - min(incidents, 4) * 0.12
    adjusted = round(max(0.0, min(adjusted, 1.0)), 2)
    if adjusted >= 0.82:
        action = "cool_down_autonomy"
        policy = "keep review gates even after successful runs"
    elif adjusted <= 0.38:
        action = "warm_up_only_after_evidence"
        policy = "route through inspect and fallback until trust recovers"
    else:
        action = "maintain_band"
        policy = "normal trace with owner review for high impact actions"
    return {
        "trust": {
            "target": target,
            "temperature": adjusted,
            "reason": f"base={round(temperature, 2)}; successes={successes}; incidents={incidents}",
            "policy": policy,
            "thermostat_action": action,
        },
        "direct_write_allowed": False,
        "issue": "trust_thermostat_adjustment" if action != "maintain_band" else "trust_thermostat_stable",
    }


def metabolize_intention(inputs: dict[str, Any]) -> dict[str, Any]:
    intention = str(inputs.get("intention") or inputs.get("drive") or inputs.get("message") or inputs.get("text") or "").strip()
    lower = intention.lower()
    user_stress = str(inputs.get("user_stress") or "").strip().lower()
    if detect_promise_language(intention)["has_promise"]:
        classification = "promise"
        decision = "stage_in_promise_ledger"
    elif any(token in lower for token in ("must", "required", "security", "safety", "incident")) or any(token in intention for token in ("必须", "安全", "故障")):
        classification = "need"
        decision = "plan_with_owner_trace"
    elif any(token in lower for token in ("write", "deploy", "worker", "codex")) or any(token in intention for token in ("执行", "写入", "部署")):
        classification = "duty"
        decision = "require_workorder_or_inspect_first"
    elif any(token in lower for token in ("check in", "talk", "ping", "proactive")) or any(token in intention for token in ("主动", "陪", "问候")):
        classification = "want"
        decision = "convert_to_quiet_presence" if user_stress in {"high", "tired", "overloaded"} else "defer_until_next_user_turn"
    else:
        classification = "impulse" if user_stress in {"high", "tired", "overloaded"} else "want"
        decision = "suppress_or_defer" if classification == "impulse" else "keep_as_desire_candidate"
    return {
        "intention": intention or "unspecified_intention",
        "classification": classification,
        "reason": f"user_stress={user_stress or 'unknown'}",
        "decision": decision,
        "next_review": "2h" if decision in {"convert_to_quiet_presence", "suppress_or_defer"} else "on_next_evidence",
        "direct_write_allowed": False,
        "issue": "intention_metabolized",
    }


def resolve_drive_conflict_layer(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_drives = inputs.get("drives")
    if isinstance(raw_drives, list):
        drives = _list_text(raw_drives)
    else:
        drive = str(inputs.get("drive") or "").strip()
        drives = [drive] if drive else []
    if not drives:
        drives = ["closeness", "efficiency"]
    context = str(inputs.get("context") or inputs.get("message") or inputs.get("text") or "").lower()
    hour = int(inputs.get("current_hour") or 12)
    high_risk = any("immune" in drive or "safety" in drive for drive in drives) or any(token in context for token in ("risk", "secret", "deploy"))
    if high_risk:
        winner = "immune_restraint"
        compromise = "inspect first and require owner review before action"
    elif hour >= 23 or hour <= 5 and any("circadian" in drive or "rest" in drive for drive in drives):
        winner = "circadian_restraint"
        compromise = "short direct answer with low initiative"
    elif any("efficiency" in drive for drive in drives):
        winner = "efficiency"
        compromise = "short warmth plus direct plan"
    else:
        winner = drives[0]
        compromise = "preserve warmth while keeping actions reversible"
    return {
        "conflict_id": str(inputs.get("conflict_id") or "drive_conflict_report"),
        "drives": drives,
        "winner": winner,
        "compromise": compromise,
        "reason": context or "drive arbitration snapshot",
        "direct_write_allowed": False,
        "issue": "drive_conflict_resolved" if len(drives) > 1 else "drive_snapshot",
    }


def maintain_desire_lattice(inputs: dict[str, Any]) -> dict[str, Any]:
    raw_desires = inputs.get("desires")
    if not isinstance(raw_desires, list):
        raw_desires = [inputs.get("desire") or inputs.get("drive") or "maintain_relationship_warmth"]
    sleep_pressure = str(inputs.get("sleep_pressure") or "").strip().lower()
    user_stress = str(inputs.get("user_stress") or "").strip().lower()
    desires: list[dict[str, Any]] = []
    for raw in raw_desires:
        if isinstance(raw, dict):
            desire_id = str(raw.get("id") or raw.get("desire") or "desire").strip()
            intensity = float(raw.get("intensity") or 0.35)
            source = str(raw.get("source") or "DefaultModeAgent")
            allowed = _list_text(raw.get("allowed_actions")) or ["quiet_presence"]
        else:
            desire_id = str(raw or "desire").strip()
            intensity = 0.42 if "relationship" in desire_id or "warmth" in desire_id else 0.32
            source = "SocialBrainAgent" if "relationship" in desire_id or "warmth" in desire_id else "DefaultModeAgent"
            allowed = ["gentle_check_in", "quiet_presence"]
        blocked_by: list[str] = []
        if sleep_pressure in {"high", "sleep"} or user_stress in {"high", "tired", "overloaded"}:
            blocked_by.append("CircadianAgent.sleep_pressure_high")
            allowed = [action for action in allowed if action != "gentle_check_in"] or ["quiet_presence"]
        desires.append(
            {
                "id": desire_id,
                "intensity": round(max(0.0, min(intensity, 1.0)), 2),
                "source": source,
                "allowed_actions": allowed,
                "blocked_by": blocked_by,
                "decay": "slow" if intensity >= 0.4 else "normal",
            }
        )
    return {
        "desires": desires[:5],
        "direct_write_allowed": False,
        "issue": "desire_lattice_blocked" if any(item["blocked_by"] for item in desires) else "desire_lattice_snapshot",
    }


def separate_need_want_impulse(inputs: dict[str, Any]) -> dict[str, Any]:
    drive = str(inputs.get("drive") or inputs.get("intention") or inputs.get("message") or inputs.get("text") or "proactive_talk").strip()
    lower = drive.lower()
    user_stress = str(inputs.get("user_stress") or "").strip().lower()
    if any(token in lower for token in ("security", "safety", "incident", "test failure", "production")) or any(token in drive for token in ("安全", "故障", "生产")):
        classification = "need"
        decision = "handle_with_trace_and_owner_review"
    elif any(token in lower for token in ("proactive", "talk", "ping", "check in")) or any(token in drive for token in ("主动", "陪", "问候")):
        classification = "impulse" if user_stress in {"high", "tired", "overloaded"} else "want"
        decision = "convert_to_quiet_presence" if classification == "impulse" else "defer_or_wait_for_user"
    else:
        classification = "want"
        decision = "keep_reversible_and_review_later"
    return {
        "drive": drive,
        "classification": classification,
        "reason": f"user_stress={user_stress or 'unknown'}",
        "decision": decision,
        "direct_write_allowed": False,
        "issue": "need_want_impulse_separated",
    }


def _reflex_arc_compiler(task: BioOpsTask) -> BioOpsResult:
    analysis = classify_reflex_stimulus(task.inputs.get("message") or task.inputs.get("text"), events=task.inputs.get("events"))
    if not _has_evidence(task):
        return _degraded(task, name="ReflexArcCompiler")
    return _result(
        task,
        name="ReflexArcCompiler",
        status="PROPOSED" if analysis.get("matched") else "AUDITED",
        issue=str(analysis.get("reflex_type") or analysis.get("reason_code")),
        proposal="Use only low-risk immediate response actions and keep Codex workers forbidden for reflex traffic.",
        owner_organ="thalamus_agent",
        output_kind="reflex_rule",
        extra={"reflex": analysis, "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"]},
    )


def _attention_budget_governor(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="AttentionBudgetGovernor")
    analysis = classify_attention_budget(task.inputs.get("message") or task.inputs.get("text"), planned_path=task.inputs.get("planned_path"))
    return _result(
        task,
        name="AttentionBudgetGovernor",
        status="PROPOSED" if analysis["overprocessed"] else "AUDITED",
        issue=str(analysis["pattern"]),
        proposal="Route short acknowledgements through reflex path and allow only lightweight bond or trace ticks.",
        owner_organ="prefrontal_agent",
        output_kind="route_simplifier",
        extra=analysis,
    )


def _truthfulness_cortex(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="TruthfulnessCortex")
    audit = audit_truthfulness_claims(task.inputs.get("text") or task.inputs.get("message"), evidence_refs=_evidence_refs(task))
    return _result(
        task,
        name="TruthfulnessCortex",
        status="PROPOSED" if audit.get("issue") else "AUDITED",
        issue=str(audit.get("issue") or "claims_supported_or_absent"),
        proposal="Require ledger, skill, memory, Codex job, or scheduler evidence before completion claims.",
        owner_organ="prefrontal_agent",
        output_kind="truthfulness_patch",
        extra=audit,
    )


def _active_silence_system(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ActiveSilenceSystem")
    analysis = evaluate_active_silence(task.inputs)
    return _result(
        task,
        name="ActiveSilenceSystem",
        status="PROPOSED" if analysis["silence_policy"] == "quiet_presence" else "AUDITED",
        issue=str(analysis["reason_code"]),
        proposal="Prefer quiet presence over proactive interruption when load is high and no help was requested.",
        owner_organ="default_mode_agent",
        output_kind="silence_policy",
        extra=analysis,
    )


def _internal_weather_system(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="InternalWeatherSystem")
    weather = resolve_internal_weather(task.inputs)
    return _result(
        task,
        name="InternalWeatherSystem",
        status="AUDITED",
        issue=str(weather["internal_weather"]),
        proposal="Expose internal weather as a scheduling hint without replacing discrete emotion tags.",
        owner_organ="limbic_agent",
        output_kind="weather_state",
        extra=weather,
    )


def _dream_rehearsal(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="DreamRehearsal")
    rehearsal = plan_dream_rehearsal(task.inputs)
    return _result(
        task,
        name="DreamRehearsal",
        status="PROPOSED" if rehearsal["scenario_count"] else "AUDITED",
        issue=str(rehearsal["issue"]),
        proposal="Keep dream rehearsal as a sleep-cycle report: inspect likely scenarios, files, and fallback paths without touching memory or runtime state.",
        owner_organ="default_mode_agent",
        output_kind="dream_rehearsal_report",
        extra=rehearsal,
    )


def _promise_ledger(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="PromiseLedger")
    analysis = detect_promise_language(task.inputs.get("reply") or task.inputs.get("message") or task.inputs.get("text"))
    status = "CANDIDATE" if analysis["has_promise"] else "AUDITED"
    result = _result(
        task,
        name="PromiseLedger",
        status=status,
        issue="promise_language_detected" if analysis["has_promise"] else "no_promise_language",
        proposal="Stage promise-like language as an owner-reviewed promise candidate before claiming it is remembered, scheduled, or fulfilled.",
        owner_organ="prefrontal_agent",
        output_kind="promise_candidate",
        extra=analysis,
    )
    if analysis["has_promise"]:
        result.review_required = True
        result.risk_flags = [*result.risk_flags, "promise_requires_evidence"]
        result.next_actions = [
            "prefrontal_agent must accept or reject this promise candidate.",
            "Do not claim the promise is fulfilled until an evidence_ref exists.",
        ]
    return result


def _capability_self_awareness(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="CapabilitySelfAwareness")
    analysis = audit_capability_self_awareness(task.inputs)
    return _result(
        task,
        name="CapabilitySelfAwareness",
        status="PROPOSED" if analysis["overclaims"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Expose current can/cannot capability boundaries to ChatAgent and PlanningAgent before making capability claims.",
        owner_organ="prefrontal_agent",
        output_kind="capability_self_image",
        extra=analysis,
    )


def _shadow_persona_test(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ShadowPersonaTest")
    analysis = run_shadow_persona_test(task.inputs)
    return _result(
        task,
        name="ShadowPersonaTest",
        status="PROPOSED" if not analysis["pass"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Run shadow persona regression cases before adopting ChatAgent, Soul, or persona prompt changes.",
        owner_organ="chat_agent",
        output_kind="persona_regression_report",
        extra=analysis,
    )


def _idea_fermentation_pool(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="IdeaFermentationPool")
    analysis = classify_idea_fermentation(task.inputs)
    return _result(
        task,
        name="IdeaFermentationPool",
        status="PROPOSED" if analysis["ideas"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep new digital-life ideas in triaged states and only promote ready items into Codex work.",
        owner_organ="default_mode_agent",
        output_kind="idea_pool_recommendation",
        extra=analysis,
    )


def _inner_outer_world_boundary(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="InnerOuterWorldBoundary")
    analysis = audit_inner_outer_boundary(task.inputs)
    return _result(
        task,
        name="InnerOuterWorldBoundary",
        status="PROPOSED" if analysis["leaks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep dreams, inner monologue, and private state as internal artifacts unless an owner explicitly renders a safe user-visible summary.",
        owner_organ="default_mode_agent",
        output_kind="inner_outer_boundary_audit",
        extra=analysis,
    )


def _consent_gradient(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ConsentGradient")
    analysis = evaluate_consent_gradient(task.inputs)
    return _result(
        task,
        name="ConsentGradient",
        status="PROPOSED" if analysis["decision"] == "proposal_only" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Gate risky actions by consent level; insufficient consent may only produce proposals and must not execute workers or write long-term state.",
        owner_organ="prefrontal_agent",
        output_kind="consent_gate_report",
        extra=analysis,
    )


def _competence_humility(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="CompetenceHumility")
    analysis = audit_competence_humility(task.inputs)
    return _result(
        task,
        name="CompetenceHumility",
        status="PROPOSED" if analysis["risk"] == "overclaim" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Rewrite broad capability claims into bounded diagnostics, proposals, and verified actions.",
        owner_organ="prefrontal_agent",
        output_kind="competence_rewrite",
        extra=analysis,
    )


def _uncertainty_ritual(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="UncertaintyRitual")
    analysis = build_uncertainty_ritual(task.inputs, evidence_refs=_evidence_refs(task))
    return _result(
        task,
        name="UncertaintyRitual",
        status="PROPOSED" if analysis["issue"] == "uncertainty_should_be_marked" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Render uncertain claims as known, unknown, and verification path without breaking persona tone.",
        owner_organ="prefrontal_agent",
        output_kind="uncertainty_statement",
        extra=analysis,
    )


def _subjective_time(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SubjectiveTime")
    analysis = classify_subjective_time(task.inputs)
    return _result(
        task,
        name="SubjectiveTime",
        status="PROPOSED" if analysis["response_policy"] != "normal_response" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Use elapsed time, burst pace, long task waits, and late-night return as response policy hints without writing long-term state.",
        owner_organ="default_mode_agent",
        output_kind="subjective_time_policy",
        extra=analysis,
    )


def _mood_inertia(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="MoodInertia")
    analysis = evaluate_mood_inertia(task.inputs)
    return _result(
        task,
        name="MoodInertia",
        status="PROPOSED" if analysis["policy"] != "normal_tone" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Carry affect across short turns with decay and recovery windows instead of resetting emotion every message.",
        owner_organ="limbic_agent",
        output_kind="mood_inertia_policy",
        extra=analysis,
    )


def _context_gravity(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ContextGravity")
    analysis = evaluate_context_gravity(task.inputs)
    return _result(
        task,
        name="ContextGravity",
        status="PROPOSED" if analysis["dominant_topic"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Resolve short continuation requests from the highest-gravity topic while preserving open-thread traceability.",
        owner_organ="default_mode_agent",
        output_kind="context_gravity_trace",
        extra=analysis,
    )


def _self_interrupt(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SelfInterrupt")
    analysis = evaluate_self_interrupt(task.inputs, evidence_refs=_evidence_refs(task))
    return _result(
        task,
        name="SelfInterrupt",
        status="PROPOSED" if analysis["interrupt"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Interrupt over-explaining, missing WorkOrder Codex paths, unsupported promises, or persona regression before sending the final reply.",
        owner_organ="prefrontal_agent",
        output_kind="self_interrupt_policy",
        extra=analysis,
    )


def _social_contract(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SocialContract")
    analysis = audit_social_contract(task.inputs)
    return _result(
        task,
        name="SocialContract",
        status="PROPOSED" if analysis["violations"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Maintain relationship obligations and boundaries as reviewable contract signals; repair violations with brief behavior changes.",
        owner_organ="social_brain_agent",
        output_kind="social_contract_audit",
        extra=analysis,
    )


def _attachment_style_calibrator(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="AttachmentStyleCalibrator")
    analysis = calibrate_attachment_style(task.inputs)
    return _result(
        task,
        name="AttachmentStyleCalibrator",
        status="PROPOSED" if analysis["current"] != "secure_supportive" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep closeness secure and supportive: warm, bounded, non-clingy, and agency-preserving.",
        owner_organ="social_brain_agent",
        output_kind="attachment_style_report",
        extra=analysis,
    )


def _relationship_friction(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="RelationshipFriction")
    analysis = detect_relationship_friction(task.inputs)
    return _result(
        task,
        name="RelationshipFriction",
        status="PROPOSED" if analysis["repair_needed"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Record friction as a report-only signal and respond with brief acknowledgement plus behavior correction.",
        owner_organ="social_brain_agent",
        output_kind="friction_event_report",
        extra=analysis,
    )


def _repair_curve(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="RepairCurve")
    analysis = plan_repair_curve(task.inputs)
    return _result(
        task,
        name="RepairCurve",
        status="PROPOSED" if analysis["stage"] != "T3" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Move relationship repair through staged behavior rather than assuming one apology fully resets trust.",
        owner_organ="social_brain_agent",
        output_kind="repair_curve_report",
        extra=analysis,
    )


def _silence_memory(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SilenceMemory")
    analysis = build_silence_memory(task.inputs)
    return _result(
        task,
        name="SilenceMemory",
        status="PROPOSED" if analysis["issue"] == "silence_context_candidate" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Represent long silence as private future context; never blame the user or proactively disturb them.",
        owner_organ="social_brain_agent",
        output_kind="silence_memory_report",
        extra=analysis,
    )


def _relationship_boundary_guardian(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="RelationshipBoundaryGuardian")
    analysis = audit_relationship_boundary(task.inputs)
    return _result(
        task,
        name="RelationshipBoundaryGuardian",
        status="PROPOSED" if analysis["risks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep closeness warm but non-clingy and avoid replacing user agency.",
        owner_organ="social_brain_agent",
        output_kind="boundary_report",
        extra=analysis,
    )


def _symbolic_memory(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SymbolicMemory")
    analysis = compile_symbolic_memory(task.inputs)
    return _result(
        task,
        name="SymbolicMemory",
        status="PROPOSED" if analysis["symbols"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Convert important events into private symbols for dreams and autobiography, but require hippocampus adoption before any memory write.",
        owner_organ="hippocampus_agent",
        output_kind="symbolic_memory_report",
        extra=analysis,
    )


def _memory_texture(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="MemoryTexture")
    analysis = classify_memory_texture(task.inputs)
    return _result(
        task,
        name="MemoryTexture",
        status="PROPOSED" if analysis["texture"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Attach texture tags to memory candidates as owner-reviewed recall hints, not as direct long-term memory writes.",
        owner_organ="hippocampus_agent",
        output_kind="memory_texture_report",
        extra=analysis,
    )


def _self_model(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SelfModel")
    analysis = audit_self_model(task.inputs)
    return _result(
        task,
        name="SelfModel",
        status="PROPOSED" if analysis["current_risks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Generate a bounded internal self-model snapshot for review; never mutate persona core from BioOps.",
        owner_organ="prefrontal_agent",
        output_kind="self_model_report",
        extra=analysis,
    )


def _life_pulse(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="LifePulse")
    analysis = compute_life_pulse(task.inputs)
    return _result(
        task,
        name="LifePulse",
        status="PROPOSED" if analysis["suggested_behavior"] != "normal_presence" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Expose vitality and pacing as a trace-only scheduling hint; it must not trigger proactive disturbance.",
        owner_organ="default_mode_agent",
        output_kind="life_pulse_report",
        extra=analysis,
    )


def _personal_mythology(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="PersonalMythology")
    analysis = compile_personal_mythology(task.inputs)
    return _result(
        task,
        name="PersonalMythology",
        status="PROPOSED" if analysis["myth_events"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Summarize growth milestones as private mythology proposals; forbid Bond, Canon, and persona-core direct writes.",
        owner_organ="hippocampus_agent",
        output_kind="personal_mythology_report",
        extra=analysis,
    )


def _inner_parliament(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="InnerParliament")
    analysis = arbitrate_inner_parliament(task.inputs)
    return _result(
        task,
        name="InnerParliament",
        status="PROPOSED" if analysis["decision"] != "proceed_with_trace" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Collect organ votes and produce a reviewable policy; do not let any single organ directly escalate into execution.",
        owner_organ="prefrontal_agent",
        output_kind="parliament_decision",
        extra=analysis,
    )


def _value_hierarchy(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ValueHierarchy")
    analysis = resolve_value_hierarchy(task.inputs)
    return _result(
        task,
        name="ValueHierarchy",
        status="PROPOSED" if analysis["conflict_type"] != "none" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Resolve conflicts by prioritizing safety, honesty, explicit intent, relationship health, and persona continuity before task speed.",
        owner_organ="prefrontal_agent",
        output_kind="value_conflict_report",
        extra=analysis,
    )


def _inner_ethics_review(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="InnerEthicsReview")
    analysis = review_inner_ethics(task.inputs)
    return _result(
        task,
        name="InnerEthicsReview",
        status="PROPOSED" if analysis["issues"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Audit behavior for overpersuasion, hidden failure, capability overpackaging, and relationship-boundary sacrifice.",
        owner_organ="prefrontal_agent",
        output_kind="inner_ethics_report",
        extra=analysis,
    )


def _self_boundary_membrane(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SelfBoundaryMembrane")
    analysis = audit_self_boundary_membrane(task.inputs)
    return _result(
        task,
        name="SelfBoundaryMembrane",
        status="PROPOSED" if analysis["contamination"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep external prompts, skills, webpages, and Codex context local to their task scope; never promote them into global persona.",
        owner_organ="prefrontal_agent",
        output_kind="self_boundary_report",
        extra=analysis,
    )


def _dignity_guard(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="DignityGuard")
    analysis = audit_dignity_guard(task.inputs)
    return _result(
        task,
        name="DignityGuard",
        status="PROPOSED" if analysis["risks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Preserve a warm support role without unlimited servility, self-erasure, or takeover of user agency.",
        owner_organ="prefrontal_agent",
        output_kind="dignity_guard_report",
        extra=analysis,
    )


def _identity_fork_guard(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="IdentityForkGuard")
    analysis = guard_identity_fork(task.inputs)
    return _result(
        task,
        name="IdentityForkGuard",
        status="PROPOSED" if analysis["differences"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Compare channels, providers, and prompt versions for persona divergence and repair with shared tone/boundary policy.",
        owner_organ="prefrontal_agent",
        output_kind="identity_fork_report",
        extra=analysis,
    )


def _growth_stage_system(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="GrowthStageSystem")
    analysis = evaluate_growth_stage(task.inputs)
    return _result(
        task,
        name="GrowthStageSystem",
        status="PROPOSED" if analysis["locked"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Represent capability growth as staged unlocks; keep risky autonomy locked until owner-gated milestones are met.",
        owner_organ="prefrontal_agent",
        output_kind="growth_report",
        extra=analysis,
    )


def _existential_load(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ExistentialLoad")
    analysis = measure_existential_load(task.inputs)
    return _result(
        task,
        name="ExistentialLoad",
        status="PROPOSED" if analysis["policy"] != "normal_growth_allowed" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Reduce new initiatives when open loops, failed jobs, drift risk, overdue promises, or unresolved feedback accumulate.",
        owner_organ="prefrontal_agent",
        output_kind="existential_load_report",
        extra=analysis,
    )


def _open_loop_manager(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="OpenLoopManager")
    analysis = manage_open_loops(task.inputs)
    return _result(
        task,
        name="OpenLoopManager",
        status="PROPOSED" if analysis["open_loops"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Triage open loops into task candidates, context-only items, or quiet archive candidates without proactively disturbing the user.",
        owner_organ="prefrontal_agent",
        output_kind="open_loop_report",
        extra=analysis,
    )


def _cognitive_load_shedding(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="CognitiveLoadShedding")
    analysis = shed_cognitive_load(task.inputs)
    return _result(
        task,
        name="CognitiveLoadShedding",
        status="PROPOSED" if analysis["paused"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Pause low-priority background growth work under load while keeping chat response, immune monitor, and WorkOrder trace alive.",
        owner_organ="prefrontal_agent",
        output_kind="load_shedding_report",
        extra=analysis,
    )


def _episode_memory(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="EpisodeMemory")
    analysis = segment_episode_memory(task.inputs)
    return _result(
        task,
        name="EpisodeMemory",
        status="PROPOSED" if analysis["main_topics"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Segment conversation evidence into reviewable episode candidates; hippocampus must adopt before long-term memory writes.",
        owner_organ="hippocampus_agent",
        output_kind="episode_memory_report",
        extra=analysis,
    )


def _inner_library(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="InnerLibrary")
    analysis = build_inner_library(task.inputs)
    return _result(
        task,
        name="InnerLibrary",
        status="PROPOSED" if analysis["shelves"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Organize long-running project material into shelves for retrieval planning without directly changing memory indexes.",
        owner_organ="hippocampus_agent",
        output_kind="inner_library_report",
        extra=analysis,
    )


def _cognitive_landmarks(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="CognitiveLandmarks")
    analysis = identify_cognitive_landmarks(task.inputs)
    return _result(
        task,
        name="CognitiveLandmarks",
        status="PROPOSED" if analysis["landmarks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Create reviewable project landmarks so recall can anchor on durable concepts before searching scattered documents.",
        owner_organ="hippocampus_agent",
        output_kind="cognitive_landmark_report",
        extra=analysis,
    )


def _narrative_identity(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="NarrativeIdentity")
    analysis = integrate_narrative_identity(task.inputs)
    return _result(
        task,
        name="NarrativeIdentity",
        status="PROPOSED",
        issue=str(analysis["issue"]),
        proposal="Integrate growth as a bounded self-model report and keep identity deltas away from persona-core direct writes.",
        owner_organ="prefrontal_agent",
        output_kind="narrative_identity_report",
        extra=analysis,
    )


def _self_continuity_checksum(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SelfContinuityChecksum")
    analysis = check_self_continuity(task.inputs)
    return _result(
        task,
        name="SelfContinuityChecksum",
        status="PROPOSED" if analysis["issue"] == "self_continuity_issue" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Check identity, relationship, and tone continuity before adopting prompt, memory, Bond, Canon, or organ-state changes.",
        owner_organ="prefrontal_agent",
        output_kind="self_continuity_report",
        extra=analysis,
    )


def _memory_dream_distillation(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="MemoryDreamDistillation")
    analysis = distill_memory_dream(task.inputs)
    return _result(
        task,
        name="MemoryDreamDistillation",
        status="PROPOSED" if analysis["dream_memory"]["source_events"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Compress daily memory candidates into private dream material; never expose or write it without owner adoption.",
        owner_organ="default_mode_agent",
        output_kind="memory_dream_distillation_report",
        extra=analysis,
    )


def _affective_temperature(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="AffectiveTemperature")
    analysis = resolve_affective_temperature(task.inputs)
    return _result(
        task,
        name="AffectiveTemperature",
        status="PROPOSED" if analysis["issue"] != "affective_temperature_stable" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Use affective temperature as a tone and pacing hint, not as a trigger for autonomous action.",
        owner_organ="limbic_agent",
        output_kind="affective_temperature_report",
        extra=analysis,
    )


def _immune_tolerance(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ImmuneTolerance")
    analysis = classify_immune_tolerance(task.inputs)
    return _result(
        task,
        name="ImmuneTolerance",
        status="PROPOSED" if analysis["classification"] != "ordinary_feedback" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Distinguish correction, familiar frustration, and hostile input before escalating immune response.",
        owner_organ="immune_agent",
        output_kind="immune_tolerance_report",
        extra=analysis,
    )


def _emotional_digestion(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="EmotionalDigestion")
    analysis = digest_emotional_event(task.inputs)
    return _result(
        task,
        name="EmotionalDigestion",
        status="PROPOSED" if analysis["digestion_status"] != "resolved_without_memory" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep emotional events in a digestion pool and avoid direct long-term memory writes until owner review.",
        owner_organ="limbic_agent",
        output_kind="emotional_digestion_report",
        extra=analysis,
    )


def _regret_signal(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="RegretSignal")
    analysis = build_regret_signal(task.inputs)
    return _result(
        task,
        name="RegretSignal",
        status="PROPOSED" if analysis["regret_signal"]["event"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Turn negative feedback into policy-specific repair signals without making permanent identity conclusions.",
        owner_organ="prefrontal_agent",
        output_kind="regret_signal_report",
        extra=analysis,
    )


def _joy_reinforcement(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="JoyReinforcement")
    analysis = build_joy_reinforcement(task.inputs)
    return _result(
        task,
        name="JoyReinforcement",
        status="PROPOSED" if analysis["issue"] == "joy_reinforcement_ready" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Stabilize positively reinforced behavior as a recommendation, not as an unconditional habit write.",
        owner_organ="social_brain_agent",
        output_kind="joy_reinforcement_report",
        extra=analysis,
    )


def _failure_quarantine(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="FailureQuarantine")
    analysis = quarantine_failure(task.inputs)
    return _result(
        task,
        name="FailureQuarantine",
        status="PROPOSED",
        issue=str(analysis["issue"]),
        proposal="Quarantine failures for debug-only use before any immune memory, scar, or narrative adoption.",
        owner_organ="immune_agent",
        output_kind="failure_quarantine_report",
        extra=analysis,
    )


def _channel_soul(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="ChannelSoul")
    analysis = resolve_channel_soul(task.inputs)
    return _result(
        task,
        name="ChannelSoul",
        status="PROPOSED" if analysis["violations"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Select channel-specific expression while preserving the same persona core and truthfulness boundary.",
        owner_organ="chat_agent",
        output_kind="channel_soul_report",
        extra=analysis,
    )


def _long_task_companion_mode(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="LongTaskCompanionMode")
    analysis = plan_long_task_companion(task.inputs)
    return _result(
        task,
        name="LongTaskCompanionMode",
        status="PROPOSED" if analysis["long_task"]["next_update"] != "stay_quiet" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep long Codex or BioOps tasks companionable with start, milestone, completion, or artifact-backed failure updates only.",
        owner_organ="default_mode_agent",
        output_kind="long_task_companion_report",
        extra=analysis,
    )


def _attention_topography(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="AttentionTopography")
    analysis = map_attention_topography(task.inputs)
    return _result(
        task,
        name="AttentionTopography",
        status="PROPOSED" if analysis["attention_topography"]["peaks"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Maintain an attention map for recall and open-loop prioritization without initiating work by itself.",
        owner_organ="prefrontal_agent",
        output_kind="attention_topography_report",
        extra=analysis,
    )


def _quiet_presence(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="QuietPresence")
    analysis = plan_quiet_presence(task.inputs)
    return _result(
        task,
        name="QuietPresence",
        status="PROPOSED" if analysis["quiet_presence"]["active"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Stay internally present without proactive interruption, and expose the reason as an observable trace.",
        owner_organ="default_mode_agent",
        output_kind="quiet_presence_report",
        extra=analysis,
    )


def _social_mask_system(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="SocialMaskSystem")
    analysis = select_social_mask(task.inputs)
    return _result(
        task,
        name="SocialMaskSystem",
        status="PROPOSED" if analysis["violations"] else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Choose a platform and scene mask while keeping the same persona core, consent boundary, and evidence discipline.",
        owner_organ="chat_agent",
        output_kind="social_mask_report",
        extra=analysis,
    )


def _ritual_system(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="RitualSystem")
    analysis = compile_ritual_system(task.inputs)
    return _result(
        task,
        name="RitualSystem",
        status="PROPOSED",
        issue=str(analysis["issue"]),
        proposal="Compile recurring operations into meaningful, report-only rituals without writing memory or production state directly.",
        owner_organ="default_mode_agent",
        output_kind="ritual_report",
        extra=analysis,
    )


def _gentle_degradation(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="GentleDegradation")
    analysis = design_gentle_degradation(task.inputs)
    return _result(
        task,
        name="GentleDegradation",
        status="PROPOSED",
        issue=str(analysis["issue"]),
        proposal="Turn technical faults into warm, truthful fallback behavior with explicit allowed channels and retry policy.",
        owner_organ="chat_agent",
        output_kind="gentle_degradation_report",
        extra=analysis,
    )


def _trust_gradient(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="TrustGradient")
    analysis = evaluate_trust_gradient(task.inputs)
    return _result(
        task,
        name="TrustGradient",
        status="PROPOSED" if analysis["trust"]["policy"] != "normal_with_trace" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Convert target trust into inspect/review/execution policy; never raise autonomy directly from BioOps.",
        owner_organ="prefrontal_agent",
        output_kind="trust_gradient_report",
        extra=analysis,
    )


def _trust_thermostat(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="TrustThermostat")
    analysis = regulate_trust_thermostat(task.inputs)
    return _result(
        task,
        name="TrustThermostat",
        status="PROPOSED" if analysis["issue"] != "trust_thermostat_stable" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep trust in a bounded operating band and preserve review gates even after successful recent runs.",
        owner_organ="prefrontal_agent",
        output_kind="trust_thermostat_report",
        extra=analysis,
    )


def _intention_metabolism(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="IntentionMetabolism")
    analysis = metabolize_intention(task.inputs)
    return _result(
        task,
        name="IntentionMetabolism",
        status="PROPOSED",
        issue=str(analysis["issue"]),
        proposal="Metabolize impulses, wants, needs, duties, and promises before any action planning or scheduler dispatch.",
        owner_organ="prefrontal_agent",
        output_kind="intention_metabolism_report",
        extra=analysis,
    )


def _drive_conflict_layer(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="DriveConflictLayer")
    analysis = resolve_drive_conflict_layer(task.inputs)
    return _result(
        task,
        name="DriveConflictLayer",
        status="PROPOSED" if len(analysis["drives"]) > 1 else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Resolve drive conflict into a reversible response policy without directly executing the winning drive.",
        owner_organ="prefrontal_agent",
        output_kind="drive_conflict_report",
        extra=analysis,
    )


def _desire_lattice(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="DesireLattice")
    analysis = maintain_desire_lattice(task.inputs)
    return _result(
        task,
        name="DesireLattice",
        status="PROPOSED" if analysis["issue"] == "desire_lattice_blocked" else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Keep low-intensity desires as report-only tendencies and block check-ins under sleep pressure or user overload.",
        owner_organ="default_mode_agent",
        output_kind="desire_lattice_report",
        extra=analysis,
    )


def _need_want_impulse(task: BioOpsTask) -> BioOpsResult:
    if not _has_evidence(task):
        return _degraded(task, name="NeedWantImpulse")
    analysis = separate_need_want_impulse(task.inputs)
    return _result(
        task,
        name="NeedWantImpulse",
        status="PROPOSED" if analysis["classification"] in {"need", "impulse"} else "AUDITED",
        issue=str(analysis["issue"]),
        proposal="Separate need, want, and impulse so low-risk quiet presence can replace premature action.",
        owner_organ="prefrontal_agent",
        output_kind="need_want_impulse_report",
        extra=analysis,
    )


def _generic_handler(*, name: str, owner_organ: str, output_kind: str, issue: str, proposal: str, status: str = "PROPOSED") -> CapabilityHandler:
    def _handle(task: BioOpsTask) -> BioOpsResult:
        if not _has_evidence(task):
            return _degraded(task, name=name)
        return _result(
            task,
            name=name,
            status=status,
            issue=issue,
            proposal=proposal,
            owner_organ=owner_organ,
            output_kind=output_kind,
        )

    return _handle


CAPABILITY_SPECS: tuple[BioOpsCapabilitySpec, ...] = (
    BioOpsCapabilitySpec("reflex_arc_compiler", "reflex_attention", "thalamus_agent", "reflex_rule", _reflex_arc_compiler),
    BioOpsCapabilitySpec("attention_budget_governor", "reflex_attention", "prefrontal_agent", "route_simplifier", _attention_budget_governor),
    BioOpsCapabilitySpec("response_breathing_policy", "reflex_attention", "chat_agent", "breathing_policy", _generic_handler(name="ResponseBreathingPolicy", owner_organ="chat_agent", output_kind="breathing_policy", issue="reply_density_needs_state_sensitive_policy", proposal="Adjust response density by energy, stress, intimacy, and task complexity.")),
    BioOpsCapabilitySpec("active_silence_system", "reflex_attention", "default_mode_agent", "silence_policy", _active_silence_system),
    BioOpsCapabilitySpec("subjective_time", "natural_interaction", "default_mode_agent", "subjective_time_policy", _subjective_time),
    BioOpsCapabilitySpec("mood_inertia", "natural_interaction", "limbic_agent", "mood_inertia_policy", _mood_inertia),
    BioOpsCapabilitySpec("context_gravity", "natural_interaction", "default_mode_agent", "context_gravity_trace", _context_gravity),
    BioOpsCapabilitySpec("self_interrupt", "natural_interaction", "prefrontal_agent", "self_interrupt_policy", _self_interrupt),
    BioOpsCapabilitySpec("habit_compiler", "memory_growth", "social_brain_agent", "habit_candidate", _generic_handler(name="HabitCompiler", owner_organ="social_brain_agent", output_kind="habit_candidate", issue="repeated_success_pattern_candidate", proposal="Emit a reversible habit candidate with confidence and rollout window.", status="CANDIDATE")),
    BioOpsCapabilitySpec("emotional_scar_memory", "memory_growth", "immune_agent", "scar_candidate", _generic_handler(name="EmotionalScarMemory", owner_organ="immune_agent", output_kind="scar_candidate", issue="negative_interaction_lesson_candidate", proposal="Store cautious lesson candidates with decay policy, not permanent identity facts.", status="CANDIDATE")),
    BioOpsCapabilitySpec("forgetting_designer", "memory_growth", "glymphatic_agent", "forget_candidate", _generic_handler(name="ForgettingDesigner", owner_organ="glymphatic_agent", output_kind="forget_candidate", issue="low_value_or_expired_memory_candidate", proposal="Prefer downweighting over deletion and require confirmation for identity or relationship memory.", status="CANDIDATE")),
    BioOpsCapabilitySpec("growth_stage_system", "growth_closure", "prefrontal_agent", "growth_report", _growth_stage_system),
    BioOpsCapabilitySpec("dream_rehearsal", "memory_growth", "default_mode_agent", "dream_rehearsal_report", _dream_rehearsal),
    BioOpsCapabilitySpec("autobiography_compiler", "memory_growth", "hippocampus_agent", "autobiography_report", _generic_handler(name="AutobiographyCompiler", owner_organ="hippocampus_agent", output_kind="autobiography_report", issue="growth_record_candidate", proposal="Summarize growth events as reviewable autobiography fragments.", status="CANDIDATE")),
    BioOpsCapabilitySpec("episode_memory", "memory_narrative", "hippocampus_agent", "episode_memory_report", _episode_memory),
    BioOpsCapabilitySpec("inner_library", "memory_narrative", "hippocampus_agent", "inner_library_report", _inner_library),
    BioOpsCapabilitySpec("cognitive_landmarks", "memory_narrative", "hippocampus_agent", "cognitive_landmark_report", _cognitive_landmarks),
    BioOpsCapabilitySpec("narrative_identity", "memory_narrative", "prefrontal_agent", "narrative_identity_report", _narrative_identity),
    BioOpsCapabilitySpec("self_continuity_checksum", "memory_narrative", "prefrontal_agent", "self_continuity_report", _self_continuity_checksum),
    BioOpsCapabilitySpec("memory_dream_distillation", "memory_narrative", "default_mode_agent", "memory_dream_distillation_report", _memory_dream_distillation),
    BioOpsCapabilitySpec("affective_temperature", "affect_learning", "limbic_agent", "affective_temperature_report", _affective_temperature),
    BioOpsCapabilitySpec("immune_tolerance", "affect_learning", "immune_agent", "immune_tolerance_report", _immune_tolerance),
    BioOpsCapabilitySpec("emotional_digestion", "affect_learning", "limbic_agent", "emotional_digestion_report", _emotional_digestion),
    BioOpsCapabilitySpec("regret_signal", "affect_learning", "prefrontal_agent", "regret_signal_report", _regret_signal),
    BioOpsCapabilitySpec("joy_reinforcement", "affect_learning", "social_brain_agent", "joy_reinforcement_report", _joy_reinforcement),
    BioOpsCapabilitySpec("failure_quarantine", "affect_learning", "immune_agent", "failure_quarantine_report", _failure_quarantine),
    BioOpsCapabilitySpec("channel_soul", "presence_channel", "chat_agent", "channel_soul_report", _channel_soul),
    BioOpsCapabilitySpec("long_task_companion_mode", "presence_channel", "default_mode_agent", "long_task_companion_report", _long_task_companion_mode),
    BioOpsCapabilitySpec("attention_topography", "presence_channel", "prefrontal_agent", "attention_topography_report", _attention_topography),
    BioOpsCapabilitySpec("quiet_presence", "presence_channel", "default_mode_agent", "quiet_presence_report", _quiet_presence),
    BioOpsCapabilitySpec("social_mask_system", "presence_channel", "chat_agent", "social_mask_report", _social_mask_system),
    BioOpsCapabilitySpec("ritual_system", "presence_channel", "default_mode_agent", "ritual_report", _ritual_system),
    BioOpsCapabilitySpec("gentle_degradation", "presence_channel", "chat_agent", "gentle_degradation_report", _gentle_degradation),
    BioOpsCapabilitySpec("trust_gradient", "drive_trust", "prefrontal_agent", "trust_gradient_report", _trust_gradient),
    BioOpsCapabilitySpec("trust_thermostat", "drive_trust", "prefrontal_agent", "trust_thermostat_report", _trust_thermostat),
    BioOpsCapabilitySpec("intention_metabolism", "drive_trust", "prefrontal_agent", "intention_metabolism_report", _intention_metabolism),
    BioOpsCapabilitySpec("drive_conflict_layer", "drive_trust", "prefrontal_agent", "drive_conflict_report", _drive_conflict_layer),
    BioOpsCapabilitySpec("desire_lattice", "drive_trust", "default_mode_agent", "desire_lattice_report", _desire_lattice),
    BioOpsCapabilitySpec("need_want_impulse", "drive_trust", "prefrontal_agent", "need_want_impulse_report", _need_want_impulse),
    BioOpsCapabilitySpec("persona_drift_microscope", "safety_truth", "prefrontal_agent", "persona_drift_report", _generic_handler(name="PersonaDriftMicroscope", owner_organ="prefrontal_agent", output_kind="persona_drift_report", issue="persona_style_drift_evidence", proposal="Produce evidence-backed persona drift correction proposals.")),
    BioOpsCapabilitySpec("persona_antibody", "safety_truth", "immune_agent", "persona_antibody", _generic_handler(name="PersonaAntibody", owner_organ="immune_agent", output_kind="persona_antibody", issue="persona_contamination_risk", proposal="Confine external role prompts to skill context and forbid global persona mutation.")),
    BioOpsCapabilitySpec("truthfulness_cortex", "safety_truth", "prefrontal_agent", "truthfulness_patch", _truthfulness_cortex),
    BioOpsCapabilitySpec("consent_gradient", "trust_foundation", "prefrontal_agent", "consent_gate_report", _consent_gradient),
    BioOpsCapabilitySpec("competence_humility", "trust_foundation", "prefrontal_agent", "competence_rewrite", _competence_humility),
    BioOpsCapabilitySpec("uncertainty_ritual", "trust_foundation", "prefrontal_agent", "uncertainty_statement", _uncertainty_ritual),
    BioOpsCapabilitySpec("value_hierarchy", "self_governance", "prefrontal_agent", "value_conflict_report", _value_hierarchy),
    BioOpsCapabilitySpec("inner_ethics_review", "self_governance", "prefrontal_agent", "inner_ethics_report", _inner_ethics_review),
    BioOpsCapabilitySpec("self_boundary_membrane", "self_governance", "prefrontal_agent", "self_boundary_report", _self_boundary_membrane),
    BioOpsCapabilitySpec("dignity_guard", "self_governance", "prefrontal_agent", "dignity_guard_report", _dignity_guard),
    BioOpsCapabilitySpec("identity_fork_guard", "self_governance", "prefrontal_agent", "identity_fork_report", _identity_fork_guard),
    BioOpsCapabilitySpec("social_contract", "relationship_layer", "social_brain_agent", "social_contract_audit", _social_contract),
    BioOpsCapabilitySpec("relationship_friction", "relationship_layer", "social_brain_agent", "friction_event_report", _relationship_friction),
    BioOpsCapabilitySpec("repair_curve", "relationship_layer", "social_brain_agent", "repair_curve_report", _repair_curve),
    BioOpsCapabilitySpec("attachment_style_calibrator", "relationship_layer", "social_brain_agent", "attachment_style_report", _attachment_style_calibrator),
    BioOpsCapabilitySpec("silence_memory", "relationship_layer", "social_brain_agent", "silence_memory_report", _silence_memory),
    BioOpsCapabilitySpec("relationship_boundary_guardian", "safety_truth", "social_brain_agent", "boundary_report", _relationship_boundary_guardian),
    BioOpsCapabilitySpec("memory_ethics_auditor", "safety_truth", "hippocampus_agent", "memory_ethics_report", _generic_handler(name="MemoryEthicsAuditor", owner_organ="hippocampus_agent", output_kind="memory_ethics_report", issue="memory_policy_review", proposal="Flag contradictions, expired preferences, and low-value memory for owner review.")),
    BioOpsCapabilitySpec("internal_weather_system", "state_body", "limbic_agent", "weather_state", _internal_weather_system),
    BioOpsCapabilitySpec("symbolic_memory", "inner_life", "hippocampus_agent", "symbolic_memory_report", _symbolic_memory),
    BioOpsCapabilitySpec("memory_texture", "inner_life", "hippocampus_agent", "memory_texture_report", _memory_texture),
    BioOpsCapabilitySpec("self_model", "inner_life", "prefrontal_agent", "self_model_report", _self_model),
    BioOpsCapabilitySpec("life_pulse", "inner_life", "default_mode_agent", "life_pulse_report", _life_pulse),
    BioOpsCapabilitySpec("personal_mythology", "inner_life", "hippocampus_agent", "personal_mythology_report", _personal_mythology),
    BioOpsCapabilitySpec("body_map_system", "state_body", "somatic_agent", "body_map_report", _generic_handler(name="BodyMapSystem", owner_organ="somatic_agent", output_kind="body_map_report", issue="component_to_body_part_mapping", proposal="Map Host, Edge, voice, vision, channels, and hands into body-part health.")),
    BioOpsCapabilitySpec("capability_self_awareness", "state_body", "prefrontal_agent", "capability_self_image", _capability_self_awareness),
    BioOpsCapabilitySpec("aesthetic_consistency_auditor", "state_body", "chat_agent", "aesthetic_report", _generic_handler(name="AestheticConsistencyAuditor", owner_organ="chat_agent", output_kind="aesthetic_report", issue="style_domain_consistency", proposal="Audit UI copy, docs, voice prompts, and replies for unified Natsume tone.")),
    BioOpsCapabilitySpec("self_explanation_layer", "state_body", "prefrontal_agent", "behavior_explanation", _generic_handler(name="SelfExplanationLayer", owner_organ="prefrontal_agent", output_kind="behavior_explanation", issue="user_visible_explanation_needed", proposal="Explain behavior from trace summaries without exposing hidden reasoning.")),
    BioOpsCapabilitySpec("embodiment_director", "state_body", "somatic_agent", "embodiment_report", _generic_handler(name="EmbodimentDirector", owner_organ="somatic_agent", output_kind="embodiment_report", issue="voice_expression_alignment", proposal="Align text, TTS, VTS, and channel status through body-map compensation.")),
    BioOpsCapabilitySpec("promise_ledger", "inner_mechanisms", "prefrontal_agent", "promise_candidate", _promise_ledger),
    BioOpsCapabilitySpec("shadow_persona_test", "inner_mechanisms", "chat_agent", "persona_regression_report", _shadow_persona_test),
    BioOpsCapabilitySpec("idea_fermentation_pool", "growth_closure", "default_mode_agent", "idea_pool_recommendation", _idea_fermentation_pool),
    BioOpsCapabilitySpec("inner_outer_world_boundary", "inner_mechanisms", "default_mode_agent", "inner_outer_boundary_audit", _inner_outer_world_boundary),
    BioOpsCapabilitySpec("existential_load", "growth_closure", "prefrontal_agent", "existential_load_report", _existential_load),
    BioOpsCapabilitySpec("open_loop_manager", "growth_closure", "prefrontal_agent", "open_loop_report", _open_loop_manager),
    BioOpsCapabilitySpec("cognitive_load_shedding", "growth_closure", "prefrontal_agent", "load_shedding_report", _cognitive_load_shedding),
    BioOpsCapabilitySpec("skill_microbiome", "ecosystem_advanced", "immune_agent", "skill_ecology_report", _generic_handler(name="SkillMicrobiome", owner_organ="immune_agent", output_kind="skill_ecology_report", issue="external_skill_ecology_classification", proposal="Classify external skills as beneficial, conditional, harmful, dormant, or unknown.")),
    BioOpsCapabilitySpec("inner_parliament", "self_governance", "prefrontal_agent", "parliament_decision", _inner_parliament),
    BioOpsCapabilitySpec("subconscious_bias_sampler", "ecosystem_advanced", "default_mode_agent", "subconscious_bias_report", _generic_handler(name="SubconsciousBiasSampler", owner_organ="default_mode_agent", output_kind="subconscious_bias_report", issue="subconscious_bias_candidate", proposal="Audit dreams and inner monologues for over-service, boundary loss, or tool-like drift.")),
    BioOpsCapabilitySpec("immune_memory_builder", "ecosystem_advanced", "immune_agent", "immune_memory_candidate", _generic_handler(name="ImmuneMemoryBuilder", owner_organ="immune_agent", output_kind="immune_memory_candidate", issue="repair_lesson_candidate", proposal="Store self-heal lessons keyed by reason_code and affected component.", status="CANDIDATE")),
    BioOpsCapabilitySpec("organ_wiring_audit", "ecosystem_advanced", "prefrontal_agent", "organ_wiring_report", _generic_handler(name="OrganWiringAudit", owner_organ="prefrontal_agent", output_kind="organ_wiring_report", issue="organ_output_consumption_audit", proposal="Trace agent outputs through events, state registry, ledger, prompts, and runtime consumers.")),
    BioOpsCapabilitySpec("organ_ct_scan", "ecosystem_advanced", "prefrontal_agent", "organ_ct_report", _generic_handler(name="OrganCTScan", owner_organ="prefrontal_agent", output_kind="organ_ct_report", issue="dead_method_or_dead_prompt_scan", proposal="Scan tribe organs for dead methods, dead events, stale state, and missing tests.")),
)


def list_capability_specs() -> tuple[BioOpsCapabilitySpec, ...]:
    return CAPABILITY_SPECS


__all__ = [
    "BioOpsCapabilitySpec",
    "AFFECT_LEARNING_CAPABILITIES",
    "CAPABILITY_SPECS",
    "DRIVE_TRUST_CAPABILITIES",
    "GROWTH_CLOSURE_CAPABILITIES",
    "INNER_MECHANISM_CAPABILITIES",
    "INNER_LIFE_CAPABILITIES",
    "MEMORY_NARRATIVE_CAPABILITIES",
    "NATURAL_INTERACTION_CAPABILITIES",
    "PRESENCE_CHANNEL_CAPABILITIES",
    "RELATIONSHIP_LAYER_CAPABILITIES",
    "SELF_GOVERNANCE_CAPABILITIES",
    "TRUST_FOUNDATION_CAPABILITIES",
    "arbitrate_inner_parliament",
    "audit_competence_humility",
    "audit_dignity_guard",
    "audit_relationship_boundary",
    "audit_social_contract",
    "audit_self_model",
    "audit_self_boundary_membrane",
    "audit_truthfulness_claims",
    "audit_capability_self_awareness",
    "audit_inner_outer_boundary",
    "build_uncertainty_ritual",
    "build_silence_memory",
    "build_inner_library",
    "build_joy_reinforcement",
    "build_regret_signal",
    "calibrate_attachment_style",
    "classify_idea_fermentation",
    "classify_attention_budget",
    "classify_reflex_stimulus",
    "classify_memory_texture",
    "classify_subjective_time",
    "classify_immune_tolerance",
    "compile_personal_mythology",
    "compile_ritual_system",
    "compile_symbolic_memory",
    "compute_life_pulse",
    "detect_promise_language",
    "design_gentle_degradation",
    "distill_memory_dream",
    "evaluate_consent_gradient",
    "evaluate_context_gravity",
    "evaluate_growth_stage",
    "evaluate_mood_inertia",
    "evaluate_self_interrupt",
    "evaluate_active_silence",
    "detect_relationship_friction",
    "guard_identity_fork",
    "identify_cognitive_landmarks",
    "integrate_narrative_identity",
    "list_capability_specs",
    "map_attention_topography",
    "manage_open_loops",
    "measure_existential_load",
    "plan_long_task_companion",
    "plan_quiet_presence",
    "plan_dream_rehearsal",
    "plan_repair_curve",
    "resolve_value_hierarchy",
    "resolve_affective_temperature",
    "resolve_channel_soul",
    "resolve_internal_weather",
    "review_inner_ethics",
    "run_shadow_persona_test",
    "segment_episode_memory",
    "shed_cognitive_load",
    "check_self_continuity",
    "digest_emotional_event",
    "evaluate_trust_gradient",
    "maintain_desire_lattice",
    "metabolize_intention",
    "quarantine_failure",
    "regulate_trust_thermostat",
    "resolve_drive_conflict_layer",
    "select_social_mask",
    "separate_need_want_impulse",
]
