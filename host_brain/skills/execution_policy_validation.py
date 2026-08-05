"""Pure fail-closed validation for governed Skill execution metadata."""

from __future__ import annotations

from typing import Any

from host_brain.agent_runtime.governed_skill_work_order import (
    WRITE_AGENT_RUNTIME_SKILL_IDS,
)
from host_brain.agent_runtime.skill_ids import is_agent_runtime_skill
from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.core.errors import (
    GOVERNED_SKILL_ACCEPTANCE_EFFECT_REQUIRED,
)


def governed_skill_policy_error(
    skill_name: str,
    skill_meta: Any,
) -> str | None:
    """Return a stable rejection code, or ``None`` for an executable policy."""

    execution_policy = getattr(skill_meta, "execution_policy", None)
    budget_policy = getattr(skill_meta, "budget_policy", None)
    if not isinstance(execution_policy, SkillExecutionPolicy):
        return "GOVERNED_SKILL_EXECUTION_POLICY_INVALID"
    if not isinstance(budget_policy, SkillBudgetPolicy):
        return "GOVERNED_SKILL_BUDGET_POLICY_INVALID"
    if execution_policy is SkillExecutionPolicy.UNCLASSIFIED:
        return "GOVERNED_SKILL_EXECUTION_POLICY_INVALID"
    if (
        execution_policy
        is SkillExecutionPolicy.BLOCKED_PENDING_ACCEPTANCE_EFFECT
    ):
        return GOVERNED_SKILL_ACCEPTANCE_EFFECT_REQUIRED

    normalized = str(skill_name or "").strip().lower()
    write_agent = normalized in WRITE_AGENT_RUNTIME_SKILL_IDS
    if (
        execution_policy is SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
    ) != write_agent:
        return "GOVERNED_SKILL_EXECUTION_POLICY_INVALID"
    agent_runtime = is_agent_runtime_skill(normalized)
    proposal_only = (
        execution_policy
        is SkillExecutionPolicy.PRE_ACCEPTANCE_PROPOSAL_ONLY
    )
    proposal_capable = (agent_runtime and not write_agent) or normalized == "memory_store"
    if proposal_only != proposal_capable:
        return "GOVERNED_SKILL_EXECUTION_POLICY_INVALID"
    if (
        budget_policy is SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL
    ) != agent_runtime:
        return "GOVERNED_SKILL_BUDGET_POLICY_INVALID"
    if budget_policy is SkillBudgetPolicy.TASK_PRECHARGE_DECLARED_MODEL:
        if not bool(getattr(skill_meta, "model_budget_required", False)):
            return "GOVERNED_SKILL_BUDGET_POLICY_INVALID"
    elif bool(getattr(skill_meta, "model_budget_required", False)):
        return "GOVERNED_SKILL_BUDGET_POLICY_INVALID"
    return None


__all__ = ["governed_skill_policy_error"]
