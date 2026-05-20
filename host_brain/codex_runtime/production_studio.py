"""Codex-backed production skill WorkOrder builder.

Production skills use Codex as a silent background worker. They may inspect,
summarize, and propose, but the Host surface/main persona owns final wording,
memory writes, Bond changes, and proactive emotional expression.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from host_brain.codex_runtime.work_order import build_work_order_id


CODEX_PRODUCTION_SKILL_IDS: frozenset[str] = frozenset(
    {
        "codex_workbench_brief",
        "codex_repo_sentinel",
        "codex_memory_curator",
        "codex_document_digest",
        "codex_frontend_review",
    }
)


@dataclass(frozen=True, slots=True)
class ProductionSkillProfile:
    skill_id: str
    domain: str
    run_mode: str
    default_workspace_strategy: str
    output_kind: str
    value_statement: str
    default_cooldown_minutes: int
    max_per_day: int
    acceptance_criteria: tuple[str, ...]
    capability_tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["acceptance_criteria"] = list(self.acceptance_criteria)
        data["capability_tags"] = list(self.capability_tags)
        return data


PERSONA_BOUNDARY_CONTRACT: dict[str, Any] = {
    "worker_role": "silent_background_engineer",
    "surface_owner": "host_main_persona",
    "final_user_expression_owner": "surface_main_chain",
    "must": [
        "Return structured findings, evidence, proposed actions, and uncertainty only.",
        "Keep output impersonal and suitable for the Host persona to reinterpret.",
        "Use owner-review proposals for memory, Bond, Canon, or personality-affecting changes.",
    ],
    "must_not": [
        "Do not speak as Natsume, Blue, or the independent persona.",
        "Do not write long-term memory, Bond state, Canon, persona core, or emotional state directly.",
        "Do not send proactive messages or external communications directly.",
        "Do not bypass Governor, approval, or owner-review gates.",
    ],
}


PRODUCTION_RESULT_CONTRACT: dict[str, Any] = {
    "schema_version": "Natsume.codex.production_result.v1",
    "surface_main_chain_required": True,
    "allowed_sections": [
        "findings",
        "evidence_refs",
        "actions_taken",
        "proposed_next_actions",
        "owner_review_items",
        "risk_flags",
        "uncertainties",
    ],
    "forbidden_sections": [
        "persona_final_reply",
        "memory_write",
        "bond_write",
        "canon_write",
        "external_message_send",
    ],
}


_PRODUCTION_PROFILES: dict[str, ProductionSkillProfile] = {
    "codex_workbench_brief": ProductionSkillProfile(
        skill_id="codex_workbench_brief",
        domain="research",
        run_mode="research",
        default_workspace_strategy="temp_workspace",
        output_kind="workbench_brief",
        value_statement="把任务、上下文和系统线索整理成可执行工作简报。",
        default_cooldown_minutes=60,
        max_per_day=8,
        acceptance_criteria=(
            "Produce a concise priority brief with decisions, blockers, and next actions.",
            "Separate confirmed facts from assumptions.",
            "Do not create final persona speech; leave expression to the Host main chain.",
        ),
        capability_tags=("codex", "production", "brief", "planning"),
    ),
    "codex_repo_sentinel": ProductionSkillProfile(
        skill_id="codex_repo_sentinel",
        domain="code",
        run_mode="inspect",
        default_workspace_strategy="runtime_repo",
        output_kind="repo_sentinel_report",
        value_statement="后台巡检仓库健康、测试风险、提交前阻塞点。",
        default_cooldown_minutes=120,
        max_per_day=6,
        acceptance_criteria=(
            "Inspect repository state without modifying files.",
            "Report actionable risks, likely files, and focused verification commands.",
            "Avoid speculative rewrites; propose owner-reviewed follow-up WorkOrders for changes.",
        ),
        capability_tags=("codex", "production", "repo", "tests"),
    ),
    "codex_memory_curator": ProductionSkillProfile(
        skill_id="codex_memory_curator",
        domain="memory",
        run_mode="inspect",
        default_workspace_strategy="temp_workspace",
        output_kind="memory_candidate_report",
        value_statement="把会话和事件整理成长期记忆候选，不直接写记忆。",
        default_cooldown_minutes=180,
        max_per_day=4,
        acceptance_criteria=(
            "Extract memory candidates as review items only.",
            "Mark privacy, confidence, emotional weight, and expiry hints.",
            "Do not write long-term memory or Bond state directly.",
        ),
        capability_tags=("codex", "production", "memory", "curation"),
    ),
    "codex_document_digest": ProductionSkillProfile(
        skill_id="codex_document_digest",
        domain="research",
        run_mode="research",
        default_workspace_strategy="temp_workspace",
        output_kind="document_digest",
        value_statement="消化文档、网页摘录和资料，产出决策/行动摘要。",
        default_cooldown_minutes=60,
        max_per_day=12,
        acceptance_criteria=(
            "Summarize decisions, requirements, open questions, and action items.",
            "Preserve source references and uncertainty.",
            "Avoid unsupported claims when sources are incomplete.",
        ),
        capability_tags=("codex", "production", "document", "digest"),
    ),
    "codex_frontend_review": ProductionSkillProfile(
        skill_id="codex_frontend_review",
        domain="code",
        run_mode="inspect",
        default_workspace_strategy="runtime_repo",
        output_kind="frontend_review_report",
        value_statement="后台审查前端页面、组件价值、布局和缺失交互。",
        default_cooldown_minutes=120,
        max_per_day=6,
        acceptance_criteria=(
            "Inspect routes/components without modifying files.",
            "Identify low-value UI, duplicated cards, missing states, and layout risks.",
            "Return prioritized improvements with file and route evidence.",
        ),
        capability_tags=("codex", "production", "frontend", "ux"),
    ),
}


def list_production_skill_profiles() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in _PRODUCTION_PROFILES.values()]


def get_production_skill_profile(skill_id: str) -> ProductionSkillProfile:
    normalized = str(skill_id or "").strip().lower()
    try:
        return _PRODUCTION_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex production skill: {skill_id}") from exc


def build_persona_boundary_contract() -> dict[str, Any]:
    return {
        "schema_version": "Natsume.persona_boundary.v1",
        **PERSONA_BOUNDARY_CONTRACT,
    }


def build_production_trigger_policy(
    profile: ProductionSkillProfile,
    args: dict[str, Any],
) -> dict[str, Any]:
    cooldown = args.get("cooldown_minutes")
    max_per_day = args.get("max_per_day")
    try:
        cooldown_minutes = max(5, min(1440, int(cooldown or profile.default_cooldown_minutes)))
    except (TypeError, ValueError):
        cooldown_minutes = profile.default_cooldown_minutes
    try:
        per_day = max(1, min(48, int(max_per_day or profile.max_per_day)))
    except (TypeError, ValueError):
        per_day = profile.max_per_day
    return {
        "mode": "async_background",
        "manual_trigger_allowed": True,
        "event_trigger_allowed": True,
        "default_cooldown_minutes": cooldown_minutes,
        "max_per_day": per_day,
        "main_chain_blocking_allowed": False,
        "emotional_interrupt_allowed": False,
        "quiet_surface_until_result_reviewed": True,
    }


def _normalize_task(args: dict[str, Any]) -> str:
    return str(
        args.get("task")
        or args.get("objective")
        or args.get("query")
        or args.get("text")
        or args.get("user_message")
        or ""
    ).strip()


def _normalize_workspace(args: dict[str, Any], profile: ProductionSkillProfile) -> dict[str, Any]:
    raw_workspace = args.get("workspace")
    if isinstance(raw_workspace, dict):
        return dict(raw_workspace)
    raw_path = str(args.get("workspace_path") or args.get("path") or args.get("project_path") or "").strip()
    if raw_path:
        return {"path": raw_path}
    strategy = str(args.get("workspace_strategy") or profile.default_workspace_strategy).strip() or profile.default_workspace_strategy
    return {"strategy": strategy}


def _normalize_evidence_refs(args: dict[str, Any]) -> list[str]:
    raw = args.get("evidence_refs")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def build_production_work_order(args: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    merged = dict(args or {})
    profile = get_production_skill_profile(skill_id)
    task = _normalize_task(merged)
    context = str(merged.get("context") or merged.get("source_text") or "").strip()
    persona_boundary = build_persona_boundary_contract()
    trigger_policy = build_production_trigger_policy(profile, merged)
    work_order_id = str(merged.get("work_order_id") or build_work_order_id(profile.skill_id))

    return {
        "work_order_id": work_order_id,
        "domain": profile.domain,
        "run_mode": profile.run_mode,
        "objective": task or profile.value_statement,
        "workspace": _normalize_workspace(merged, profile),
        "input_pack": {
            "user_request": task,
            "context": context,
            "evidence_refs": _normalize_evidence_refs(merged),
            "output_kind": profile.output_kind,
            "value_statement": profile.value_statement,
        },
        "allowed_actions": ["read_files", "generate_report"],
        "forbidden_actions": [
            "write_files",
            "git_push",
            "modify_secret_files",
            "write_long_term_memory",
            "write_bond_state",
            "write_persona_core",
            "write_canon",
            "send_external_message",
            "bypass_governor",
        ],
        "acceptance_criteria": list(profile.acceptance_criteria),
        "verification_plan": dict(merged.get("verification_plan") or {}),
        "approval_policy": {"require_confirm": False, "confirmed": True},
        "review_policy": {"review_required": False},
        "metadata": {
            "skill_id": profile.skill_id,
            "request_id": merged.get("request_id"),
            "codex_production": True,
            "codex_background": True,
            "surface_main_chain_required": True,
            "persona_boundary": persona_boundary,
            "result_contract": dict(PRODUCTION_RESULT_CONTRACT),
            "trigger_policy": trigger_policy,
            "output_kind": profile.output_kind,
            "capability_tags": list(profile.capability_tags),
            "timeout_seconds": merged.get("timeout_seconds") or 900,
        },
    }


__all__ = [
    "CODEX_PRODUCTION_SKILL_IDS",
    "PRODUCTION_RESULT_CONTRACT",
    "ProductionSkillProfile",
    "build_persona_boundary_contract",
    "build_production_trigger_policy",
    "build_production_work_order",
    "get_production_skill_profile",
    "list_production_skill_profiles",
]
