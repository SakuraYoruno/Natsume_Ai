"""Canonical public skill ids for Codex runtime v2."""

from __future__ import annotations


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
)

CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID: dict[str, str] = {
    "codex_inspect": "inspect",
    "codex_worker": "worker",
    "codex_self_heal": "self_heal",
    "codex_self_evolve": "self_evolve",
    "codex_sql_audit": "audit",
    "codex_translate_project": "translate",
    "codex_research": "research",
}


def is_codex_runtime_skill(skill_id: object) -> bool:
    return str(skill_id or "").strip().lower() in CODEX_RUNTIME_SKILL_IDS


__all__ = [
    "CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID",
    "CODEX_RUNTIME_SKILL_IDS",
    "is_codex_runtime_skill",
]
