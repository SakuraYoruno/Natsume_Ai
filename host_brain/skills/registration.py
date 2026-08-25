"""Unified skill registration API (Phase 5).

Provides a single ``register_skill()`` entry-point that atomically writes
to both ``SkillRegistry`` (SkillMeta) and optionally ``ToolExecutorRegistry``
(ToolExecutorSpec).  This eliminates the dual-registration pattern where
callers had to remember to register to both registries separately.

Low-level ``registry.register()`` and ``executor_registry.register()`` are
still available for advanced use-cases; ``register_skill()`` is the
recommended path for new code.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry, AccessLevel
from host_brain.skills.executor_registry import ToolExecutorSpec, ToolExecutorRegistry


logger = logging.getLogger(__name__)


def register_skill(
    *,
    skill_name: str,
    clearance: str,
    risk_level: int,
    scope: str,
    handler: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]],
    skill_registry: SkillRegistry | None = None,
    executor_tool_name: str | None = None,
    executor_aliases: tuple[str, ...] = (),
    executor_handler: Callable[[dict[str, Any]], Any] | None = None,
    executor_registry: ToolExecutorRegistry | None = None,
    **meta_kwargs: Any,
) -> tuple[SkillMeta, ToolExecutorSpec | None]:
    """Atomic registration: write to SkillRegistry + optionally ToolExecutorRegistry.

    Args:
        skill_name: Unique skill identifier.
        clearance: Access clearance (LOW / MEDIUM / HIGH).
        risk_level: Risk level 1-5.
        scope: Execution scope (host / memory / system).
        handler: Skill handler callable ``(args, ctx) -> result``.
        skill_registry: Target SkillRegistry instance.
        executor_tool_name: When provided, a ``ToolExecutorSpec`` is also
            registered under this tool name.  ``None`` means SkillMeta-only
            registration (for noop / query / direct-handler skills).
        executor_aliases: Additional tool names the executor responds to.
        executor_handler: The executor callable ``(payload) -> Any``.
            When *None*, ``handler`` is used as the executor (common for
            skills that combine both roles).
        executor_registry: Target ToolExecutorRegistry instance.  Required
            when ``executor_tool_name`` is provided.
        **meta_kwargs: Extra fields forwarded to ``SkillMeta``:
            ``reversible``, ``require_confirm``, ``description``, ``family``,
            ``capability_tags``, ``parallelizable``, ``fallback_policy``,
            ``model_budget_required``, ``execution_policy``, ``budget_policy``,
            ``scheduler_allowed``, ``access_level`` and ``scheduler_profile``.

    Returns:
        A ``(skill_meta, executor_spec | None)`` tuple.  The second element
        is ``None`` when ``executor_tool_name`` was not provided.
    """
    # ── Resolve registries ────────────────────────────────────────────────
    _skill_reg = skill_registry
    if _skill_reg is None:
        raise RuntimeError(
            "No SkillRegistry available.  Pass skill_registry= before "
            "register_skill()."
        )

    # ── Build SkillMeta ──────────────────────────────────────────────────
    model_budget_required = bool(
        meta_kwargs.pop("model_budget_required", False)
    )
    budget_policy = meta_kwargs.pop("budget_policy", None) or (
        SkillBudgetPolicy.TASK_PRECHARGE_DECLARED_MODEL
        if model_budget_required
        else SkillBudgetPolicy.TASK_PRECHARGE
    )
    meta = SkillMeta(
        skill_name=skill_name,
        clearance=clearance,
        risk_level=risk_level,
        scope=scope,
        handler=handler,
        reversible=meta_kwargs.pop("reversible", True),
        require_confirm=meta_kwargs.pop("require_confirm", False),
        description=meta_kwargs.pop("description", ""),
        family=meta_kwargs.pop("family", "general"),
        capability_tags=meta_kwargs.pop("capability_tags", ()),
        parallelizable=meta_kwargs.pop("parallelizable", False),
        fallback_policy=meta_kwargs.pop("fallback_policy", "chat_fallback"),
        model_budget_required=model_budget_required,
        execution_policy=meta_kwargs.pop(
            "execution_policy",
            SkillExecutionPolicy.UNCLASSIFIED,
        ),
        budget_policy=budget_policy,
        scheduler_allowed=meta_kwargs.pop("scheduler_allowed", True),
        access_level=meta_kwargs.pop("access_level", AccessLevel.PUBLIC),
        scheduler_profile=meta_kwargs.pop("scheduler_profile", None),
    )
    if meta_kwargs:
        raise TypeError(
            f"register_skill() got unexpected keyword arguments: {list(meta_kwargs)}"
        )

    # ── Write SkillMeta ──────────────────────────────────────────────────
    _skill_reg.register(meta)
    logger.debug("SkillMeta registered: %s", skill_name)

    # ── Optionally build + write ToolExecutorSpec ─────────────────────────
    executor_spec: ToolExecutorSpec | None = None
    if executor_tool_name is not None:
        _exec_reg = executor_registry
        if _exec_reg is None:
            raise RuntimeError(
                "No ToolExecutorRegistry available.  Pass executor_registry= "
                "before register_skill() with executor_tool_name."
            )
        effective_executor = executor_handler if executor_handler is not None else handler
        executor_spec = ToolExecutorSpec(
            tool_name=executor_tool_name,
            handler=effective_executor,
            aliases=executor_aliases,
        )
        _exec_reg.register(executor_spec)
        logger.debug(
            "ToolExecutorSpec registered: tool=%s aliases=%s",
            executor_tool_name,
            executor_aliases,
        )

    return meta, executor_spec
