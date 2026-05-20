"""Canonical public skill ids for Codex runtime v2."""

from __future__ import annotations

from host_brain.codex_runtime.production_studio import CODEX_PRODUCTION_SKILL_IDS


CODEX_RUNTIME_SKILL_IDS: frozenset[str] = frozenset(
    {
        "codex_inspect",
        "codex_worker",
        "codex_self_heal",
        "codex_self_evolve",
        "codex_sql_audit",
        "codex_translate_project",
        "codex_skill_forge",
        "codex_research",
        "codex_organ_scan",
        "codex_persona_drift_audit",
        "codex_dream_compile",
    }
) | CODEX_PRODUCTION_SKILL_IDS

CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID: dict[str, str] = {
    "codex_inspect": "inspect",
    "codex_worker": "worker",
    "codex_self_heal": "self_heal",
    "codex_self_evolve": "self_evolve",
    "codex_sql_audit": "audit",
    "codex_translate_project": "translate",
    "codex_research": "research",
    "codex_workbench_brief": "research",
    "codex_repo_sentinel": "inspect",
    "codex_memory_curator": "inspect",
    "codex_document_digest": "research",
    "codex_frontend_review": "inspect",
}


def is_codex_runtime_skill(skill_id: object) -> bool:
    return str(skill_id or "").strip().lower() in CODEX_RUNTIME_SKILL_IDS


__all__ = [
    "CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID",
    "CODEX_RUNTIME_SKILL_IDS",
    "is_codex_runtime_skill",
]
