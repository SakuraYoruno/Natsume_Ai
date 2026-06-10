"""Canonical public skill ids for Agent runtime v2."""

from __future__ import annotations

from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS


AGENT_RUNTIME_SKILL_IDS: frozenset[str] = frozenset(
    {
        "agent_inspect",
        "agent_worker",
        "agent_self_heal",
        "agent_self_evolve",
        "agent_sql_audit",
        "agent_translate_project",
        "agent_skill_forge",
        "agent_research",
        "agent_organ_scan",
        "agent_persona_drift_audit",
        "agent_dream_compile",
    }
) | AGENT_PRODUCTION_SKILL_IDS

AGENT_RUN_MODE_BY_SKILL_ID: dict[str, str] = {
    "agent_inspect": "inspect",
    "agent_worker": "worker",
    "agent_self_heal": "self_heal",
    "agent_self_evolve": "self_evolve",
    "agent_sql_audit": "audit",
    "agent_translate_project": "translate",
    "agent_research": "research",
    "agent_workbench_brief": "research",
    "agent_repo_sentinel": "inspect",
    "agent_memory_curator": "inspect",
    "agent_document_digest": "research",
    "agent_frontend_review": "inspect",
    "agent_browser_research": "research",
    "agent_frontend_acceptance": "inspect",
    "agent_web_corpus_collector": "worker",
    "agent_lightnovel_recommender": "worker",
    "agent_epub_builder": "worker",
}


def is_agent_runtime_skill(skill_id: object) -> bool:
    return str(skill_id or "").strip().lower() in AGENT_RUNTIME_SKILL_IDS


__all__ = [
    "AGENT_RUN_MODE_BY_SKILL_ID",
    "AGENT_RUNTIME_SKILL_IDS",
    "is_agent_runtime_skill",
]
