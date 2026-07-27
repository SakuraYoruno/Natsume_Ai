"""Stable execution and budget policies for governed Skills."""

from __future__ import annotations

from enum import Enum


class SkillExecutionPolicy(str, Enum):
    """Where one registered Skill is allowed to cross its effect boundary."""

    UNCLASSIFIED = "UNCLASSIFIED"
    INLINE_READ_ONLY = "INLINE_READ_ONLY"
    PRE_ACCEPTANCE_PROPOSAL_ONLY = "PRE_ACCEPTANCE_PROPOSAL_ONLY"
    POST_ACCEPTANCE_WORKSPACE = "POST_ACCEPTANCE_WORKSPACE"
    BLOCKED_PENDING_ACCEPTANCE_EFFECT = "BLOCKED_PENDING_ACCEPTANCE_EFFECT"


class SkillBudgetPolicy(str, Enum):
    """Canonical Task Budget evidence required by one Skill invocation."""

    TASK_PRECHARGE = "TASK_PRECHARGE"
    TASK_PRECHARGE_DECLARED_MODEL = "TASK_PRECHARGE_DECLARED_MODEL"
    TASK_PRECHARGE_AGENT_MODEL = "TASK_PRECHARGE_AGENT_MODEL"


__all__ = [
    "SkillBudgetPolicy",
    "SkillExecutionPolicy",
]
