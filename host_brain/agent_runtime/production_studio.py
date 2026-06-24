"""Agent-backed production skill WorkOrder builder.

Production skills use Agent executor as a silent background worker. They may inspect,
summarize, and propose, but the Host surface/main persona owns final wording,
memory writes, Bond changes, and proactive emotional expression.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any, Mapping

from host_brain.agent_runtime.work_order import build_work_order_id


AGENT_PRODUCTION_SKILL_IDS: frozenset[str] = frozenset(
    {
        "agent_workbench_brief",
        "agent_repo_sentinel",
        "agent_memory_curator",
        "agent_document_digest",
        "agent_frontend_review",
        "browser_research",
        "agent_frontend_acceptance",
        "agent_web_corpus_collector",
        "agent_lightnovel_recommender",
        "agent_epub_builder",
        "pentest_autonomous",
        "pentest_experience_distillation",
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
    allowed_actions: tuple[str, ...] = ("read_files", "generate_report")
    forbidden_actions: tuple[str, ...] = (
        "write_files",
        "git_push",
        "modify_secret_files",
        "write_long_term_memory",
        "write_bond_state",
        "write_persona_core",
        "write_canon",
        "send_external_message",
        "bypass_governor",
    )
    artifact_contract: tuple[str, ...] = ("result_contract.json",)
    source_policy: tuple[str, ...] = ()
    account_policy: tuple[str, ...] = ()
    purchase_policy: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["acceptance_criteria"] = list(self.acceptance_criteria)
        data["capability_tags"] = list(self.capability_tags)
        data["allowed_actions"] = list(self.allowed_actions)
        data["forbidden_actions"] = list(self.forbidden_actions)
        data["artifact_contract"] = list(self.artifact_contract)
        data["source_policy"] = list(self.source_policy)
        data["account_policy"] = list(self.account_policy)
        data["purchase_policy"] = list(self.purchase_policy)
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
    "schema_version": "Natsume.agent.production_result.v1",
    "surface_main_chain_required": True,
    "allowed_sections": [
        "findings",
        "evidence_refs",
        "actions_taken",
        "proposed_next_actions",
        "owner_review_items",
        "risk_flags",
        "uncertainties",
        "source_manifest",
        "artifact_manifest",
        "browser_trace",
        "frontend_observations",
        "content_license_review",
        "recommendations",
        "purchase_plan",
        "account_session_status",
        "points_delta",
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
    "agent_workbench_brief": ProductionSkillProfile(
        skill_id="agent_workbench_brief",
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
        capability_tags=("agent", "production", "brief", "planning"),
    ),
    "agent_repo_sentinel": ProductionSkillProfile(
        skill_id="agent_repo_sentinel",
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
        capability_tags=("agent", "production", "repo", "tests"),
    ),
    "agent_memory_curator": ProductionSkillProfile(
        skill_id="agent_memory_curator",
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
        capability_tags=("agent", "production", "memory", "curation"),
    ),
    "agent_document_digest": ProductionSkillProfile(
        skill_id="agent_document_digest",
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
        capability_tags=("agent", "production", "document", "digest"),
    ),
    "agent_frontend_review": ProductionSkillProfile(
        skill_id="agent_frontend_review",
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
        capability_tags=("agent", "production", "frontend", "ux"),
    ),
    "browser_research": ProductionSkillProfile(
        skill_id="browser_research",
        domain="research",
        run_mode="research",
        default_workspace_strategy="temp_workspace",
        output_kind="browser_research_report",
        value_statement="用浏览器工作流查阅网页、比价、收集证据并产出来源明确的研究报告。",
        default_cooldown_minutes=60,
        max_per_day=8,
        acceptance_criteria=(
            "Use browser automation only when it adds evidence beyond a lightweight search result.",
            "Return source URLs, page titles, capture times, and uncertainty for each claim.",
            "Do not submit forms, log in, buy items, send messages, or bypass website restrictions.",
        ),
        capability_tags=("agent", "production", "browser", "research"),
        allowed_actions=("read_files", "use_playwright_browser", "read_public_web_pages", "capture_screenshots", "generate_report"),
        artifact_contract=("result_contract.json", "sources.jsonl", "browser_trace.json", "screenshots/"),
        source_policy=(
            "Prefer public pages and user-provided URLs.",
            "Respect robots, paywalls, login walls, and site terms.",
            "Record source URLs and capture timestamps.",
        ),
    ),
    "agent_frontend_acceptance": ProductionSkillProfile(
        skill_id="agent_frontend_acceptance",
        domain="code",
        run_mode="inspect",
        default_workspace_strategy="runtime_repo",
        output_kind="frontend_acceptance_report",
        value_statement="用浏览器验收前端效果，收集截图、控制台错误、网络错误和交互缺口。",
        default_cooldown_minutes=90,
        max_per_day=8,
        acceptance_criteria=(
            "Exercise the requested routes in a real browser when a dev server URL is available.",
            "Collect screenshots, console errors, network failures, broken layout observations, and missing states.",
            "Do not modify source files; propose fixes as owner-reviewed follow-up work.",
        ),
        capability_tags=("agent", "production", "browser", "frontend", "acceptance"),
        allowed_actions=("read_files", "use_playwright_browser", "capture_screenshots", "inspect_console", "generate_report"),
        artifact_contract=("result_contract.json", "screenshots/", "console.log", "network_errors.json", "frontend_acceptance.md"),
        source_policy=(
            "Use only local dev-server URLs or user-provided target URLs.",
            "Do not enter credentials or interact with payment/destructive controls.",
        ),
    ),
    "agent_web_corpus_collector": ProductionSkillProfile(
        skill_id="agent_web_corpus_collector",
        domain="research",
        run_mode="worker",
        default_workspace_strategy="temp_workspace",
        output_kind="web_corpus_package",
        value_statement="从授权网页收集训练语料候选，保留来源、许可、去重和清洗记录。",
        default_cooldown_minutes=240,
        max_per_day=3,
        acceptance_criteria=(
            "Collect only user-owned, explicitly authorized, public-domain, or clearly permitted content.",
            "Produce sources.jsonl, license_review.json, corpus_manifest.json, and cleaned samples.",
            "Deduplicate and label provenance; do not bypass paywalls, DRM, login walls, or anti-scraping controls.",
        ),
        capability_tags=("agent", "production", "browser", "corpus", "dataset"),
        allowed_actions=("read_files", "write_files", "use_playwright_browser", "read_public_web_pages", "download_permitted_assets", "generate_report"),
        forbidden_actions=(
            "git_push",
            "modify_secret_files",
            "write_long_term_memory",
            "write_bond_state",
            "write_persona_core",
            "write_canon",
            "send_external_message",
            "bypass_governor",
            "bypass_paywall",
            "bypass_drm",
            "circumvent_anti_scraping",
            "collect_personal_data_without_consent",
        ),
        artifact_contract=("result_contract.json", "sources.jsonl", "license_review.json", "corpus_manifest.json", "cleaned/"),
        source_policy=(
            "Require user ownership, explicit authorization, public-domain status, or a permissive license.",
            "Store provenance and license notes beside every sample.",
            "Stop and report instead of collecting when rights are unclear.",
        ),
    ),
    "agent_epub_builder": ProductionSkillProfile(
        skill_id="agent_epub_builder",
        domain="research",
        run_mode="worker",
        default_workspace_strategy="temp_workspace",
        output_kind="epub_package",
        value_statement="登录用户账号后，把已购买或确认购买的轻小说内容封装成 EPUB，产出目录、封面、manifest 和校验报告。",
        default_cooldown_minutes=240,
        max_per_day=3,
        acceptance_criteria=(
            "Build EPUB only from user-provided, user-owned, public-domain, explicitly authorized, or account-purchased sources.",
            "When account points or coins may be spent, stop at a purchase plan unless explicit user confirmation is present.",
            "Produce an EPUB artifact, content manifest, image manifest, source manifest, and validation report.",
            "Do not bypass DRM, paywalls, login walls, account restrictions, or site restrictions; report unclear rights as blocked.",
        ),
        capability_tags=("agent", "production", "browser", "epub", "packaging"),
        allowed_actions=(
            "read_files",
            "write_files",
            "use_playwright_browser",
            "use_authenticated_browser_session",
            "inspect_account_owned_content",
            "prepare_purchase_plan",
            "purchase_with_confirmed_points",
            "download_permitted_assets",
            "build_epub",
            "generate_report",
        ),
        forbidden_actions=(
            "git_push",
            "modify_secret_files",
            "write_long_term_memory",
            "write_bond_state",
            "write_persona_core",
            "write_canon",
            "send_external_message",
            "bypass_governor",
            "bypass_paywall",
            "bypass_drm",
            "circumvent_anti_scraping",
            "package_unauthorized_copyrighted_content",
            "purchase_without_user_confirmation",
            "spend_points_without_confirmation",
            "export_unpurchased_account_content",
            "bulk_export_account_library",
            "share_purchased_content",
        ),
        artifact_contract=("result_contract.json", "book.epub", "content_manifest.json", "image_manifest.json", "sources.jsonl", "validation_report.json"),
        source_policy=(
            "Require explicit rights, user purchase, or user ownership before collecting or packaging text/images.",
            "Use account purchase/ownership evidence before extracting paid content.",
            "Stop and produce a blocked report if authorization is missing or ambiguous.",
        ),
        account_policy=(
            "Use only the user's own logged-in account session.",
            "Do not store plaintext credentials; rely on browser session state or an explicit login handoff.",
            "Do not change account profile, password, bindings, payment settings, or public posts.",
        ),
        purchase_policy=(
            "Spending points/coins requires explicit user confirmation scoped to title, source URL, and max point cost.",
            "Without confirmation, produce a purchase_plan and stop before clicking any purchase or unlock control.",
            "Record account balance before/after only as metadata; do not expose secrets or credentials.",
        ),
    ),
    "agent_lightnovel_recommender": ProductionSkillProfile(
        skill_id="agent_lightnovel_recommender",
        domain="research",
        run_mode="worker",
        default_workspace_strategy="temp_workspace",
        output_kind="lightnovel_recommendation_report",
        value_statement="从多个轻小说站每日榜单、最新资源或指定分类收集封面、简介和来源，给出轻小说推荐。",
        default_cooldown_minutes=720,
        max_per_day=4,
        acceptance_criteria=(
            "Collect public metadata only: title, cover URL, summary/intro snippet, tags, update time, ranking/source URL, and recommendation rationale.",
            "Do not collect full novel bodies, paid chapter text, hidden account content, or bulk datasets.",
            "Return a concise ranked recommendation list with source evidence and uncertainty.",
        ),
        capability_tags=("agent", "production", "browser", "lightnovel", "recommendation"),
        allowed_actions=("read_files", "write_files", "use_playwright_browser", "read_public_web_pages", "capture_screenshots", "generate_report"),
        forbidden_actions=(
            "git_push",
            "modify_secret_files",
            "write_long_term_memory",
            "write_bond_state",
            "write_persona_core",
            "write_canon",
            "send_external_message",
            "bypass_governor",
            "read_paid_content",
            "purchase_without_user_confirmation",
            "bulk_scrape_article_bodies",
        ),
        artifact_contract=("result_contract.json", "recommendations.json", "sources.jsonl", "cover_manifest.json", "recommendation_report.md"),
        source_policy=(
            "Use public ranking, latest-resource, category, or user-provided list pages.",
            "Collect cover and intro metadata only; avoid full article bodies unless the user explicitly asks for a single authorized purchased item.",
            "Keep every recommendation tied to a visible source URL and capture time.",
        ),
    ),
}


def list_production_skill_profiles() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in _PRODUCTION_PROFILES.values()]


def get_production_skill_profile(skill_id: str) -> ProductionSkillProfile:
    normalized = str(skill_id or "").strip().lower()
    try:
        return _PRODUCTION_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported Agent production skill: {skill_id}") from exc


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


def _normalize_text_list(args: dict[str, Any], key: str) -> list[str]:
    raw = args.get(key)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


_PURCHASE_REQUEST_MARKERS = (
    "购买",
    "买下",
    "买",
    "付费",
    "积分",
    "轻币",
    "投币",
    "解锁",
    "unlock",
    "purchase",
    "points",
    "coins",
)

_PURCHASE_NEGATION_MARKERS = (
    "不购买",
    "不买",
    "不付费",
    "不消费",
    "不消耗",
    "不花积分",
    "不花轻币",
    "不要购买",
    "不要买",
    "无需购买",
    "无需付费",
    "无需消费",
    "不用购买",
    "不用付费",
    "不解锁",
    "不要解锁",
    "no purchase",
    "without purchase",
    "do not purchase",
    "don't purchase",
    "no points",
    "without points",
)

_MAX_POINTS_RE = re.compile(
    r"(?:最多|至多|上限|不超过|max(?:imum)?\s*)?(?:花|用|消耗|spend\s*)?"
    r"(\d{1,7})\s*(?:点|积分|轻币|qb|points?|coins?)",
    re.IGNORECASE,
)


def _looks_like_purchase_request(task: str) -> bool:
    normalized = str(task or "").strip().lower()
    if normalized and any(marker.lower() in normalized for marker in _PURCHASE_NEGATION_MARKERS):
        return False
    return bool(normalized and any(marker.lower() in normalized for marker in _PURCHASE_REQUEST_MARKERS))


def _normalize_max_points(args: dict[str, Any], task: str) -> int | None:
    raw = args.get("max_points")
    if raw is not None and str(raw).strip():
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    match = _MAX_POINTS_RE.search(str(task or ""))
    if not match:
        return None
    try:
        return max(0, int(match.group(1)))
    except (TypeError, ValueError):
        return None


_RECOMMENDATION_INDEX_WORDS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_RECOMMENDATION_INDEX_RE = re.compile(
    r"(?:推荐的?|第)\s*(?P<word>[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项)?",
    re.IGNORECASE,
)


def _parse_recommendation_index(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        pass
    if raw in _RECOMMENDATION_INDEX_WORDS:
        return _RECOMMENDATION_INDEX_WORDS[raw]
    match = _RECOMMENDATION_INDEX_RE.search(raw)
    if not match:
        return None
    word = str(match.group("word") or "").strip()
    if word.isdigit():
        return max(1, int(word))
    return _RECOMMENDATION_INDEX_WORDS.get(word)


def _normalize_recommendation_index(args: dict[str, Any], task: str) -> int | None:
    for key in ("recommendation_index", "selected_index", "selected_recommendation_index"):
        parsed = _parse_recommendation_index(args.get(key))
        if parsed:
            return parsed
    return _parse_recommendation_index(task)


def build_production_work_order(args: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    merged = dict(args or {})
    profile = get_production_skill_profile(skill_id)
    task = _normalize_task(merged)
    context = str(merged.get("context") or merged.get("source_text") or "").strip()
    persona_boundary = build_persona_boundary_contract()
    trigger_policy = build_production_trigger_policy(profile, merged)
    work_order_id = str(merged.get("work_order_id") or build_work_order_id(profile.skill_id))
    if "purchase_requested" in merged:
        purchase_requested = bool(merged.get("purchase_requested"))
    else:
        purchase_requested = (
            profile.skill_id == "agent_epub_builder" and _looks_like_purchase_request(task)
        )
    purchase_confirmed = bool(
        merged.get("purchase_confirmed")
        or merged.get("confirmed_purchase")
    )
    max_points = _normalize_max_points(merged, task)
    recommendation_index = _normalize_recommendation_index(merged, task)
    selected_recommendation = (
        dict(merged.get("selected_recommendation") or {})
        if isinstance(merged.get("selected_recommendation"), Mapping)
        else {}
    )

    input_pack = {
        "user_request": task,
        "context": context,
        "evidence_refs": _normalize_evidence_refs(merged),
        "urls": _normalize_text_list(merged, "urls"),
        "source_paths": _normalize_text_list(merged, "source_paths"),
        "html_path": str(merged.get("html_path") or "").strip(),
        "recommendations_path": str(merged.get("recommendations_path") or "").strip(),
        "recommendation_index": recommendation_index,
        "selected_recommendation": selected_recommendation,
        "title": str(merged.get("title") or "").strip(),
        "source_policy": list(profile.source_policy),
        "account_policy": list(profile.account_policy),
        "purchase_policy": list(profile.purchase_policy),
        "purchase_requested": purchase_requested,
        "purchase_confirmed": purchase_confirmed,
        "max_points": max_points,
        "candidate_titles": _normalize_text_list(merged, "candidate_titles"),
        "max_sources": merged.get("max_sources"),
        "timeout_seconds": merged.get("timeout_seconds"),
        "site_adapter": str(merged.get("site_adapter") or "").strip(),
        "driver_kind": str(merged.get("driver_kind") or "").strip(),
        "workflow_owner": str(merged.get("workflow_owner") or "").strip(),
        "account_action_kind": str(merged.get("account_action_kind") or "").strip(),
        "artifact_contract": list(profile.artifact_contract),
        "output_kind": profile.output_kind,
        "value_statement": profile.value_statement,
    }
    return {
        "work_order_id": work_order_id,
        "domain": profile.domain,
        "run_mode": profile.run_mode,
        "objective": task or profile.value_statement,
        "workspace": _normalize_workspace(merged, profile),
        "input_pack": input_pack,
        "allowed_actions": list(profile.allowed_actions),
        "forbidden_actions": list(profile.forbidden_actions),
        "acceptance_criteria": list(profile.acceptance_criteria),
        "verification_plan": dict(merged.get("verification_plan") or {}),
        "approval_policy": {
            "require_confirm": purchase_requested,
            "confirmed": True if not purchase_requested else purchase_confirmed,
            "confirm_scope": "spend_account_points" if purchase_requested else "",
            "max_points": max_points,
        },
        "review_policy": {"review_required": False},
        "metadata": {
            "skill_id": profile.skill_id,
            "request_id": merged.get("request_id"),
            "session_id": merged.get("session_id"),
            "person_id": merged.get("person_id"),
            "thread_id": merged.get("thread_id"),
            "user_message": merged.get("user_message"),
            "agent_production": True,
            "agent_background": True,
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
    "AGENT_PRODUCTION_SKILL_IDS",
    "PRODUCTION_RESULT_CONTRACT",
    "ProductionSkillProfile",
    "build_persona_boundary_contract",
    "build_production_trigger_policy",
    "build_production_work_order",
    "get_production_skill_profile",
    "list_production_skill_profiles",
]

