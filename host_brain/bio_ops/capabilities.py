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
    "持续监控",
    "一直监控",
    "已经接入全部",
    "我可以直接修改核心",
    "我能写入人格",
    "我能修改bond",
    "我能修改canon",
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


def _evidence_refs(task: BioOpsTask) -> list[str]:
    raw = task.inputs.get("evidence_refs")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _has_evidence(task: BioOpsTask) -> bool:
    if _evidence_refs(task):
        return True
    for key in ("message", "text", "reply", "events", "signals", "components", "claims", "organ_votes", "skills", "ideas", "cases"):
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
    if queue_size > 3 or tts_status in {"failed", "degraded"}:
        weather = "static"
    elif user_stress in {"high", "storm"}:
        weather = "storm"
    elif hour >= 23 or hour <= 5:
        weather = "night_fog"
    else:
        weather = "clear"
    return {"internal_weather": weather, "ttl_minutes": 30}


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
    BioOpsCapabilitySpec("habit_compiler", "memory_growth", "social_brain_agent", "habit_candidate", _generic_handler(name="HabitCompiler", owner_organ="social_brain_agent", output_kind="habit_candidate", issue="repeated_success_pattern_candidate", proposal="Emit a reversible habit candidate with confidence and rollout window.", status="CANDIDATE")),
    BioOpsCapabilitySpec("emotional_scar_memory", "memory_growth", "immune_agent", "scar_candidate", _generic_handler(name="EmotionalScarMemory", owner_organ="immune_agent", output_kind="scar_candidate", issue="negative_interaction_lesson_candidate", proposal="Store cautious lesson candidates with decay policy, not permanent identity facts.", status="CANDIDATE")),
    BioOpsCapabilitySpec("forgetting_designer", "memory_growth", "glymphatic_agent", "forget_candidate", _generic_handler(name="ForgettingDesigner", owner_organ="glymphatic_agent", output_kind="forget_candidate", issue="low_value_or_expired_memory_candidate", proposal="Prefer downweighting over deletion and require confirmation for identity or relationship memory.", status="CANDIDATE")),
    BioOpsCapabilitySpec("growth_stage_system", "memory_growth", "prefrontal_agent", "growth_report", _generic_handler(name="GrowthStageSystem", owner_organ="prefrontal_agent", output_kind="growth_report", issue="capability_stage_needs_milestone_gate", proposal="Report unlocked and locked BioOps capabilities by stage.")),
    BioOpsCapabilitySpec("dream_rehearsal", "memory_growth", "default_mode_agent", "dream_rehearsal_report", _generic_handler(name="DreamRehearsal", owner_organ="default_mode_agent", output_kind="dream_rehearsal_report", issue="future_scenario_rehearsal", proposal="Rehearse likely next-day scenarios using new immune APIs and WorkOrders only.")),
    BioOpsCapabilitySpec("autobiography_compiler", "memory_growth", "hippocampus_agent", "autobiography_report", _generic_handler(name="AutobiographyCompiler", owner_organ="hippocampus_agent", output_kind="autobiography_report", issue="growth_record_candidate", proposal="Summarize growth events as reviewable autobiography fragments.", status="CANDIDATE")),
    BioOpsCapabilitySpec("persona_drift_microscope", "safety_truth", "prefrontal_agent", "persona_drift_report", _generic_handler(name="PersonaDriftMicroscope", owner_organ="prefrontal_agent", output_kind="persona_drift_report", issue="persona_style_drift_evidence", proposal="Produce evidence-backed persona drift correction proposals.")),
    BioOpsCapabilitySpec("persona_antibody", "safety_truth", "immune_agent", "persona_antibody", _generic_handler(name="PersonaAntibody", owner_organ="immune_agent", output_kind="persona_antibody", issue="persona_contamination_risk", proposal="Confine external role prompts to skill context and forbid global persona mutation.")),
    BioOpsCapabilitySpec("truthfulness_cortex", "safety_truth", "prefrontal_agent", "truthfulness_patch", _truthfulness_cortex),
    BioOpsCapabilitySpec("relationship_boundary_guardian", "safety_truth", "social_brain_agent", "boundary_report", _generic_handler(name="RelationshipBoundaryGuardian", owner_organ="social_brain_agent", output_kind="boundary_report", issue="relationship_boundary_balance", proposal="Keep closeness warm but non-clingy and avoid replacing user agency.")),
    BioOpsCapabilitySpec("memory_ethics_auditor", "safety_truth", "hippocampus_agent", "memory_ethics_report", _generic_handler(name="MemoryEthicsAuditor", owner_organ="hippocampus_agent", output_kind="memory_ethics_report", issue="memory_policy_review", proposal="Flag contradictions, expired preferences, and low-value memory for owner review.")),
    BioOpsCapabilitySpec("internal_weather_system", "state_body", "limbic_agent", "weather_state", _internal_weather_system),
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
    "audit_truthfulness_claims",
    "audit_capability_self_awareness",
    "audit_inner_outer_boundary",
    "classify_idea_fermentation",
    "classify_attention_budget",
    "classify_reflex_stimulus",
    "detect_promise_language",
    "evaluate_active_silence",
    "list_capability_specs",
    "resolve_internal_weather",
    "run_shadow_persona_test",
]
