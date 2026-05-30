"""Codex runtime v2 skill provider."""

from __future__ import annotations

from typing import Any, Callable

from host_brain.bio_ops import build_proposal_only_policy
from host_brain.codex_runtime.production_studio import build_production_work_order, get_production_skill_profile
from host_brain.codex_runtime.studios import (
    build_code_work_order,
    build_finance_work_order,
    build_research_work_order,
    build_skill_forge_work_order,
    build_translation_work_order,
)
from host_brain.codex_runtime.work_order import build_work_order_id
from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


_LIGHTNOVEL_RECOMMENDER_INVOCATION_DEFAULTS = {
    "urls": [
        "https://www.lightnovel.fun/",
        "https://www.lightnovel.fun/cn/category/3/106?type=1",
        "https://book.sfacg.com/rank/",
        "https://www.17k.com/man/qingbook/",
    ],
    "site_adapter": "lightnovel.fun",
    "artifacts_root": "artifacts/codex_runs/lightnovel_recommender",
    "start_background": True,
    "driver_kind": "natsume_daily_recommendation",
    "workflow_owner": "natsume",
}


def _ctx_request_id(ctx: dict[str, Any] | None) -> str | None:
    return str((ctx or {}).get("request_id") or "").strip() or None


def _base_args(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(args or {})
    merged.setdefault("request_id", _ctx_request_id(ctx))
    for key in ("session_id", "person_id", "thread_id", "user_message"):
        value = str((ctx or {}).get(key) or "").strip()
        if value and not str(merged.get(key) or "").strip():
            merged[key] = value
    if not str(merged.get("task") or "").strip():
        for key in ("objective", "text", "user_message", "query"):
            value = str(merged.get(key) or (ctx or {}).get(key) or "").strip()
            if value:
                merged["task"] = value
                break
    return merged


def _payload_from_work_order(work_order: dict[str, Any]) -> dict[str, Any]:
    return {"work_order": work_order}


def _normalize_code(run_mode: str, skill_id: str, require_confirm: bool) -> Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]:
    def _normalize(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        merged = _base_args(args, ctx)
        return _payload_from_work_order(
            build_code_work_order(
                merged,
                run_mode=run_mode,
                skill_id=skill_id,
                require_confirm=require_confirm,
            )
        )

    return _normalize


def _normalize_finance(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return _payload_from_work_order(build_finance_work_order(_base_args(args, ctx)))


def _normalize_translation(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return _payload_from_work_order(build_translation_work_order(_base_args(args, ctx)))


def _normalize_skill_forge(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return _payload_from_work_order(build_skill_forge_work_order(_base_args(args, ctx)))


def _normalize_research(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return _payload_from_work_order(build_research_work_order(_base_args(args, ctx)))


def _normalize_production(skill_id: str) -> Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]:
    def _normalize(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        merged = _base_args(args, ctx)
        work_order = build_production_work_order(merged, skill_id=skill_id)
        payload = _payload_from_work_order(work_order)
        approval_policy = dict(work_order.get("approval_policy") or {})
        needs_confirmation = bool(approval_policy.get("require_confirm")) and not bool(approval_policy.get("confirmed"))
        payload["start_background"] = bool(merged.get("start_background", True)) and not needs_confirmation
        if needs_confirmation:
            payload["blocked_until_confirmation"] = True
        if "artifacts_root" in merged:
            payload["artifacts_root"] = merged.get("artifacts_root")
        if "runs_root" in merged:
            payload["runs_root"] = merged.get("runs_root")
        return payload

    return _normalize


def _normalize_bio_ops(capability: str, run_mode: str, skill_id: str) -> Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]:
    def _normalize(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        merged = _base_args(args, ctx)
        task = str(merged.get("task") or merged.get("objective") or "").strip()
        proposal_policy = build_proposal_only_policy()
        work_order = {
            "work_order_id": str(merged.get("work_order_id") or build_work_order_id(skill_id)),
            "domain": "bio_ops",
            "run_mode": run_mode,
            "objective": task,
            "workspace": dict(merged.get("workspace") or {"strategy": "runtime_repo"}),
            "input_pack": {
                "user_request": task,
                "context": str(merged.get("context") or ""),
                "bio_ops_capability": capability,
            },
            "allowed_actions": list(proposal_policy["allowed_actions"]),
            "forbidden_actions": list(proposal_policy["forbidden_actions"]),
            "acceptance_criteria": ["Produce proposal/report artifacts only."],
            "verification_plan": dict(merged.get("verification_plan") or {}),
            "approval_policy": {"require_confirm": False, "confirmed": True},
            "review_policy": {"review_required": False},
            "metadata": {
                "skill_id": skill_id,
                "request_id": merged.get("request_id"),
                "bio_ops_capability": capability,
                "bio_ops_policy": proposal_policy,
            },
        }
        return _payload_from_work_order(work_order)

    return _normalize


class CodexRuntimeSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="codex_inspect",
            method_name="codex_inspect",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            require_confirm=False,
            description="Run a read-only Codex WorkOrder inspection.",
            tool="codex_runtime",
            normalize_args=_normalize_code("inspect", "codex_inspect", False),
            family="code",
            capability_tags=("coding", "codex", "inspect"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_worker",
            method_name="codex_worker",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            description="Run a write-capable Codex WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_code("worker", "codex_worker", True),
            family="code",
            capability_tags=("coding", "codex", "worker"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_self_heal",
            method_name="codex_self_heal",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            description="Run a bounded Codex immune repair WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_code("self_heal", "codex_self_heal", True),
            family="code",
            capability_tags=("codex", "immune", "repair"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_self_evolve",
            method_name="codex_self_evolve",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            description="Run a Codex evolution experiment WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_code("self_evolve", "codex_self_evolve", True),
            family="code",
            capability_tags=("codex", "evolution"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_sql_audit",
            method_name="codex_sql_audit",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="Run a Codex SQL/finance audit WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_finance,
            family="code",
            capability_tags=("codex", "audit", "finance"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_translate_project",
            method_name="codex_translate_project",
            clearance="MEDIUM",
            risk_level=3,
            scope="host",
            reversible=True,
            description="Run a Codex translation project WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_translation,
            family="code",
            capability_tags=("codex", "translation"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_skill_forge",
            method_name="codex_skill_forge",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            description="Run a Codex SkillForge WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_skill_forge,
            family="code",
            capability_tags=("codex", "skill-forge"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_research",
            method_name="codex_research",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="Run a Codex research WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_research,
            family="code",
            capability_tags=("codex", "research"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_organ_scan",
            method_name="codex_organ_scan",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="Run a Codex OrganOps scan WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_bio_ops("organ_wiring_audit", "inspect", "codex_organ_scan"),
            family="code",
            capability_tags=("codex", "organ-ops"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_persona_drift_audit",
            method_name="codex_persona_drift_audit",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="Run a Codex persona drift audit WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_bio_ops("persona_drift_microscope", "inspect", "codex_persona_drift_audit"),
            family="code",
            capability_tags=("codex", "persona"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_dream_compile",
            method_name="codex_dream_compile",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="Run a Codex dream compile WorkOrder.",
            tool="codex_runtime",
            normalize_args=_normalize_bio_ops("dream_rehearsal", "inspect", "codex_dream_compile"),
            family="code",
            capability_tags=("codex", "dream"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_workbench_brief",
            method_name="codex_workbench_brief",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_workbench_brief").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_workbench_brief"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_workbench_brief").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_repo_sentinel",
            method_name="codex_repo_sentinel",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_repo_sentinel").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_repo_sentinel"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_repo_sentinel").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_memory_curator",
            method_name="codex_memory_curator",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_memory_curator").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_memory_curator"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_memory_curator").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_document_digest",
            method_name="codex_document_digest",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_document_digest").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_document_digest"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_document_digest").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_frontend_review",
            method_name="codex_frontend_review",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_frontend_review").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_frontend_review"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_frontend_review").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_browser_research",
            method_name="codex_browser_research",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_browser_research").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_browser_research"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_browser_research").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_frontend_acceptance",
            method_name="codex_frontend_acceptance",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_frontend_acceptance").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_frontend_acceptance"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_frontend_acceptance").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_web_corpus_collector",
            method_name="codex_web_corpus_collector",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_web_corpus_collector").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_web_corpus_collector"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_web_corpus_collector").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="codex_lightnovel_recommender",
            method_name="codex_lightnovel_recommender",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_lightnovel_recommender").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_lightnovel_recommender"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_lightnovel_recommender").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
            invocation_defaults=_LIGHTNOVEL_RECOMMENDER_INVOCATION_DEFAULTS,
            scheduler_profile={
                "auto_schedule": True,
                "task_id": "lightnovel_recommender_daily",
                "trigger_mode": "cron",
                "trigger_config": {"hour": 10, "minute": 30, "catch_up_window_minutes": 180},
                "trigger_env": {
                    "hour": {
                        "name": "HOST_LIGHTNOVEL_RECOMMENDER_HOUR",
                        "default": 10,
                        "low": 0,
                        "high": 23,
                    },
                    "minute": {
                        "name": "HOST_LIGHTNOVEL_RECOMMENDER_MINUTE",
                        "default": 30,
                        "low": 0,
                        "high": 59,
                    },
                    "catch_up_window_minutes": {
                        "name": "HOST_LIGHTNOVEL_RECOMMENDER_CATCH_UP_WINDOW_MINUTES",
                        "default": 180,
                        "low": 0,
                        "high": 720,
                    },
                },
                "priority": "LOW",
                "mode_allowlist": ["normal", "sleep"],
                "resource_class": "browser_heavy",
                "uses_llm": True,
                "uses_gpu": False,
                "args": {
                    "task": "从多个轻小说公开榜单、最新资源或指定分类收集公开元数据，生成轻小说推荐。",
                    **_LIGHTNOVEL_RECOMMENDER_INVOCATION_DEFAULTS,
                },
                "arg_env": {
                    "urls": {
                        "name": "HOST_LIGHTNOVEL_RECOMMENDER_URLS",
                        "default": list(_LIGHTNOVEL_RECOMMENDER_INVOCATION_DEFAULTS["urls"]),
                        "type": "csv",
                    },
                    "artifacts_root": {
                        "name": "HOST_LIGHTNOVEL_RECOMMENDER_ARTIFACTS_ROOT",
                        "default": _LIGHTNOVEL_RECOMMENDER_INVOCATION_DEFAULTS["artifacts_root"],
                    },
                },
                "advisory_policy": "rule",
                "description": "Natsume 日常推荐：每日从多个公开轻小说源生成推荐。",
            },
        ),
        DeclarativeSkillSpec(
            skill_name="codex_epub_builder",
            method_name="codex_epub_builder",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=False,
            description=get_production_skill_profile("codex_epub_builder").value_statement,
            tool="codex_production",
            action="enqueue",
            normalize_args=_normalize_production("codex_epub_builder"),
            family="code",
            capability_tags=tuple(get_production_skill_profile("codex_epub_builder").capability_tags),
            parallelizable=True,
            fallback_policy="strict_fail",
        ),
    )


def build_codex_runtime_skill_registry(provider: CodexRuntimeSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or CodexRuntimeSkillProvider())
    return registry


__all__ = ["CodexRuntimeSkillProvider", "build_codex_runtime_skill_registry"]
