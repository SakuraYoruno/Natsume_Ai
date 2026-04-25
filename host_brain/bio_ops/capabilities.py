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
    BioOpsCapabilitySpec("growth_stage_system", "memory_growth", "prefrontal_agent", "growth_report", _generic_handler(name="GrowthStageSystem", owner_organ="prefrontal_agent", output_kind="growth_report", issue="capability_stage_needs_milestone_gate", proposal="Report unlocked and locked BioOps capabilities by stage.")),
    BioOpsCapabilitySpec("dream_rehearsal", "memory_growth", "default_mode_agent", "dream_rehearsal_report", _dream_rehearsal),
    BioOpsCapabilitySpec("autobiography_compiler", "memory_growth", "hippocampus_agent", "autobiography_report", _generic_handler(name="AutobiographyCompiler", owner_organ="hippocampus_agent", output_kind="autobiography_report", issue="growth_record_candidate", proposal="Summarize growth events as reviewable autobiography fragments.", status="CANDIDATE")),
    BioOpsCapabilitySpec("persona_drift_microscope", "safety_truth", "prefrontal_agent", "persona_drift_report", _generic_handler(name="PersonaDriftMicroscope", owner_organ="prefrontal_agent", output_kind="persona_drift_report", issue="persona_style_drift_evidence", proposal="Produce evidence-backed persona drift correction proposals.")),
    BioOpsCapabilitySpec("persona_antibody", "safety_truth", "immune_agent", "persona_antibody", _generic_handler(name="PersonaAntibody", owner_organ="immune_agent", output_kind="persona_antibody", issue="persona_contamination_risk", proposal="Confine external role prompts to skill context and forbid global persona mutation.")),
    BioOpsCapabilitySpec("truthfulness_cortex", "safety_truth", "prefrontal_agent", "truthfulness_patch", _truthfulness_cortex),
    BioOpsCapabilitySpec("consent_gradient", "trust_foundation", "prefrontal_agent", "consent_gate_report", _consent_gradient),
    BioOpsCapabilitySpec("competence_humility", "trust_foundation", "prefrontal_agent", "competence_rewrite", _competence_humility),
    BioOpsCapabilitySpec("uncertainty_ritual", "trust_foundation", "prefrontal_agent", "uncertainty_statement", _uncertainty_ritual),
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
    BioOpsCapabilitySpec("idea_fermentation_pool", "inner_mechanisms", "default_mode_agent", "idea_pool_recommendation", _idea_fermentation_pool),
    BioOpsCapabilitySpec("inner_outer_world_boundary", "inner_mechanisms", "default_mode_agent", "inner_outer_boundary_audit", _inner_outer_world_boundary),
    BioOpsCapabilitySpec("skill_microbiome", "ecosystem_advanced", "immune_agent", "skill_ecology_report", _generic_handler(name="SkillMicrobiome", owner_organ="immune_agent", output_kind="skill_ecology_report", issue="external_skill_ecology_classification", proposal="Classify external skills as beneficial, conditional, harmful, dormant, or unknown.")),
    BioOpsCapabilitySpec("inner_parliament", "ecosystem_advanced", "prefrontal_agent", "parliament_decision", _generic_handler(name="InnerParliament", owner_organ="prefrontal_agent", output_kind="parliament_decision", issue="multi_organ_decision_needed", proposal="Collect organ votes and produce inspect-first policy for complex or risky actions.")),
    BioOpsCapabilitySpec("subconscious_bias_sampler", "ecosystem_advanced", "default_mode_agent", "subconscious_bias_report", _generic_handler(name="SubconsciousBiasSampler", owner_organ="default_mode_agent", output_kind="subconscious_bias_report", issue="subconscious_bias_candidate", proposal="Audit dreams and inner monologues for over-service, boundary loss, or tool-like drift.")),
    BioOpsCapabilitySpec("immune_memory_builder", "ecosystem_advanced", "immune_agent", "immune_memory_candidate", _generic_handler(name="ImmuneMemoryBuilder", owner_organ="immune_agent", output_kind="immune_memory_candidate", issue="repair_lesson_candidate", proposal="Store self-heal lessons keyed by reason_code and affected component.", status="CANDIDATE")),
    BioOpsCapabilitySpec("organ_wiring_audit", "ecosystem_advanced", "prefrontal_agent", "organ_wiring_report", _generic_handler(name="OrganWiringAudit", owner_organ="prefrontal_agent", output_kind="organ_wiring_report", issue="organ_output_consumption_audit", proposal="Trace agent outputs through events, state registry, ledger, prompts, and runtime consumers.")),
    BioOpsCapabilitySpec("organ_ct_scan", "ecosystem_advanced", "prefrontal_agent", "organ_ct_report", _generic_handler(name="OrganCTScan", owner_organ="prefrontal_agent", output_kind="organ_ct_report", issue="dead_method_or_dead_prompt_scan", proposal="Scan tribe organs for dead methods, dead events, stale state, and missing tests.")),
)


def list_capability_specs() -> tuple[BioOpsCapabilitySpec, ...]:
    return CAPABILITY_SPECS


__all__ = [
    "BioOpsCapabilitySpec",
    "CAPABILITY_SPECS",
    "INNER_MECHANISM_CAPABILITIES",
    "INNER_LIFE_CAPABILITIES",
    "NATURAL_INTERACTION_CAPABILITIES",
    "RELATIONSHIP_LAYER_CAPABILITIES",
    "TRUST_FOUNDATION_CAPABILITIES",
    "audit_competence_humility",
    "audit_relationship_boundary",
    "audit_social_contract",
    "audit_self_model",
    "audit_truthfulness_claims",
    "audit_capability_self_awareness",
    "audit_inner_outer_boundary",
    "build_uncertainty_ritual",
    "build_silence_memory",
    "calibrate_attachment_style",
    "classify_idea_fermentation",
    "classify_attention_budget",
    "classify_reflex_stimulus",
    "classify_memory_texture",
    "classify_subjective_time",
    "compile_personal_mythology",
    "compile_symbolic_memory",
    "compute_life_pulse",
    "detect_promise_language",
    "evaluate_consent_gradient",
    "evaluate_context_gravity",
    "evaluate_mood_inertia",
    "evaluate_self_interrupt",
    "evaluate_active_silence",
    "detect_relationship_friction",
    "list_capability_specs",
    "plan_dream_rehearsal",
    "plan_repair_curve",
    "resolve_internal_weather",
    "run_shadow_persona_test",
]
