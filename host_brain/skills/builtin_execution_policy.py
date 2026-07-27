"""Fail-closed production policy for statically registered built-in Skills.

The registry deliberately does not infer write authority from risk, reversibility,
names, or arbitrary handler output.  Every declarative production Skill has one
exact frozen ``skill_id/tool/action`` descriptor below.  A new or drifted
descriptor therefore cannot silently become executable.
"""

from __future__ import annotations

from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)


_DESCRIPTORS: dict[str, tuple[str, str | None]] = {
    "file_read": ("file", "read"),
    "file_write": ("file", "write"),
    "file_append": ("file", "append"),
    "file_delete": ("file", "delete"),
    "file_list": ("file", "list"),
    "shell_run": ("shell", "run"),
    "shell_read_log": ("shell", "read_log"),
    "shell_run_test": ("shell", "run_test"),
    "notify_desktop": ("notification", "desktop"),
    "notify_tts_speak": ("notification", "tts_speak"),
    "notify_schedule_reminder": ("notification", "schedule_reminder"),
    "open_cursor": ("cursor", "open"),
    "run_pycharm_action": ("pycharm", None),
    "open_file_in_editor": ("open_file_in_editor", None),
    "agent_inspect": ("codex_runtime", None),
    "agent_worker": ("codex_runtime", None),
    "agent_self_heal": ("codex_runtime", None),
    "agent_self_evolve": ("codex_runtime", None),
    "agent_sql_audit": ("codex_runtime", None),
    "agent_translate_project": ("codex_runtime", None),
    "agent_skill_forge": ("codex_runtime", None),
    "agent_research": ("codex_runtime", None),
    "agent_organ_scan": ("codex_runtime", None),
    "codex_organ_scan": ("codex_runtime", None),
    "agent_persona_drift_audit": ("codex_runtime", None),
    "codex_persona_drift_audit": ("codex_runtime", None),
    "agent_dream_compile": ("codex_runtime", None),
    "codex_dream_compile": ("codex_runtime", None),
    "agent_workbench_brief": ("codex_production", "enqueue"),
    "agent_repo_sentinel": ("codex_production", "enqueue"),
    "agent_memory_curator": ("codex_production", "enqueue"),
    "agent_document_digest": ("codex_production", "enqueue"),
    "agent_frontend_review": ("codex_production", "enqueue"),
    "browser_research": ("codex_production", "enqueue"),
    "agent_frontend_acceptance": ("codex_production", "enqueue"),
    "agent_web_corpus_collector": ("codex_production", "enqueue"),
    "agent_lightnovel_recommender": ("codex_production", "enqueue"),
    "agent_epub_builder": ("codex_production", "enqueue"),
    "browser_navigate": ("browser", "navigate"),
    "browser_click": ("browser", "click"),
    "browser_type": ("browser", "type"),
    "browser_screenshot": ("browser", "screenshot"),
    "browser_execute_script": ("browser", "execute_script"),
    "browser_wait_for": ("browser", "wait_for"),
    "browser_scrape": ("browser", "scrape"),
    "browser_close": ("browser", "close"),
    "diary_writer": ("diary_writer", "generate"),
    "dream_generator": ("dream_generator", "generate"),
    "desktop_note": ("desktop_note", None),
    "focus_mode": ("focus_mode", None),
    "proactive_talk": ("proactive_talk", "send"),
    "desktop_screenshot": ("desktop", "screenshot"),
    "desktop_click": ("desktop", "click"),
    "desktop_type": ("desktop", "type"),
    "desktop_hotkey": ("desktop", "hotkey"),
    "clipboard_read": ("desktop", "clipboard_read"),
    "clipboard_write": ("desktop", "clipboard_write"),
    "memory_search": ("memory", "search"),
    "memory_store": ("memory", "store"),
    "memory_preference_extract": ("memory", "preference_extract"),
    "memory_consolidate": ("memory", "consolidate"),
    "memory_forget": ("memory", "forget"),
    "repo_health_scan": ("repo", "health_scan"),
    "test_failure_triage": ("repo", "test_failure_triage"),
    "frontend_route_audit": ("frontend", "route_audit"),
    "frontend_component_value_audit": ("frontend", "component_value_audit"),
    "emotion_update": ("emotion", "update"),
    "bond_score_update": ("emotion", "bond_update"),
    "emotion_get": ("emotion", "get"),
    "bond_score_get": ("emotion", "bond_get"),
    "scheduler_add": ("scheduler", "add"),
    "scheduler_cancel": ("scheduler", "cancel"),
    "scheduler_list": ("scheduler", "list"),
    "system_info": ("system", "info"),
    "process_list": ("system", "process_list"),
    "system_resource_snapshot": ("system", "resource_snapshot"),
    "process_resource_top": ("system", "process_resource_top"),
    "resource_pressure_analysis": ("system", "resource_pressure_analysis"),
    "process_kill": ("system", "process_kill"),
    "resource_release": ("system", "resource_release"),
    "resource_manager_status": ("system", "resource_manager_status"),
    "network_status": ("system", "network_status"),
    "open_app": ("system", "open_app"),
    "web_search": ("web", "search"),
    "http_request": ("web", "http_request"),
}

_POST_ACCEPTANCE_WORKSPACE = frozenset(
    {"agent_worker", "agent_self_heal", "agent_self_evolve"}
)

_PRE_ACCEPTANCE_PROPOSAL_ONLY = frozenset(
    {
        "agent_document_digest",
        "agent_frontend_acceptance",
        "agent_frontend_review",
        "agent_inspect",
        "agent_memory_curator",
        "agent_organ_scan",
        "agent_persona_drift_audit",
        "agent_repo_sentinel",
        "agent_research",
        "agent_sql_audit",
        "agent_workbench_brief",
        "codex_organ_scan",
        "codex_persona_drift_audit",
    }
)

# These capabilities remain visible in the catalog but are not executable as
# Skill nodes until a typed post-Acceptance effect handler exists.  Their
# positive path belongs in a Tool/Effect node, not in a pre-Acceptance worker.
_BLOCKED_PENDING_ACCEPTANCE_EFFECT = frozenset(
    {
        "agent_dream_compile",
        "agent_epub_builder",
        "agent_lightnovel_recommender",
        "agent_skill_forge",
        "agent_translate_project",
        "agent_web_corpus_collector",
        "browser_click",
        "browser_close",
        "browser_execute_script",
        "browser_navigate",
        "browser_research",
        "browser_scrape",
        "browser_screenshot",
        "browser_type",
        "browser_wait_for",
        "bond_score_update",
        "clipboard_read",
        "clipboard_write",
        "codex_dream_compile",
        "desktop_click",
        "desktop_hotkey",
        "desktop_note",
        "desktop_screenshot",
        "desktop_type",
        "diary_writer",
        "dream_generator",
        "emotion_update",
        "file_append",
        "file_delete",
        "file_write",
        "focus_mode",
        "http_request",
        "memory_forget",
        "memory_store",
        "notify_desktop",
        "notify_schedule_reminder",
        "notify_tts_speak",
        "open_app",
        "open_cursor",
        "open_file_in_editor",
        "process_kill",
        "proactive_talk",
        "repo_health_scan",
        "resource_release",
        "run_pycharm_action",
        "scheduler_add",
        "scheduler_cancel",
        "shell_run",
        "shell_run_test",
    }
)

_INLINE_READ_ONLY = (
    frozenset(_DESCRIPTORS)
    - _POST_ACCEPTANCE_WORKSPACE
    - _PRE_ACCEPTANCE_PROPOSAL_ONLY
    - _BLOCKED_PENDING_ACCEPTANCE_EFFECT
)


def builtin_skill_policies(
    *,
    skill_id: str,
    tool: str,
    action: str | None,
) -> tuple[SkillExecutionPolicy, SkillBudgetPolicy]:
    """Resolve one exact frozen built-in descriptor or fail closed."""

    normalized_id = str(skill_id or "").strip().lower()
    normalized_descriptor = (
        str(tool or "").strip().lower(),
        str(action).strip().lower() if action is not None else None,
    )
    expected = _DESCRIPTORS.get(normalized_id)
    if expected is None:
        raise ValueError("BUILTIN_SKILL_EXECUTION_POLICY_MISSING")
    if normalized_descriptor != expected:
        raise ValueError("BUILTIN_SKILL_EXECUTION_DESCRIPTOR_DRIFT")
    if normalized_id in _POST_ACCEPTANCE_WORKSPACE:
        execution = SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
    elif normalized_id in _PRE_ACCEPTANCE_PROPOSAL_ONLY:
        execution = SkillExecutionPolicy.PRE_ACCEPTANCE_PROPOSAL_ONLY
    elif normalized_id in _BLOCKED_PENDING_ACCEPTANCE_EFFECT:
        execution = SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT
    elif normalized_id in _INLINE_READ_ONLY:
        execution = SkillExecutionPolicy.INLINE_READ_ONLY
    else:  # pragma: no cover - set partition is asserted by contract tests.
        raise ValueError("BUILTIN_SKILL_EXECUTION_POLICY_MISSING")
    budget = (
        SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL
        if expected[0] in {"codex_runtime", "codex_production"}
        else SkillBudgetPolicy.TASK_PRECHARGE
    )
    return execution, budget


def builtin_skill_policy_inventory() -> dict[str, dict[str, str | None]]:
    """Return a deterministic, authority-free policy projection."""

    result: dict[str, dict[str, str | None]] = {}
    for skill_id, (tool, action) in sorted(_DESCRIPTORS.items()):
        execution, budget = builtin_skill_policies(
            skill_id=skill_id,
            tool=tool,
            action=action,
        )
        result[skill_id] = {
            "tool": tool,
            "action": action,
            "execution_policy": execution.value,
            "budget_policy": budget.value,
        }
    return result


__all__ = [
    "builtin_skill_policies",
    "builtin_skill_policy_inventory",
]
