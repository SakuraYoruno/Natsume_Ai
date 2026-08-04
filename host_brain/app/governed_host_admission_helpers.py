"""Stateless admission helpers for the governed Host application facade."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import inspect
from typing import Any

from host_brain.app.governed_task_authority import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
)
from host_brain.app.governed_delegate_dispatch import (
    GovernedDelegateDispatchError,
)
from host_brain.skills.execution_policy_validation import (
    governed_skill_policy_error,
)


def resolve_direct_skill_execution_policy(
    intent_draft: Mapping[str, Any],
    *,
    skill_registry: Any,
) -> tuple[int, bool]:
    """Freeze canonical risk and Review policy before direct Skill admission."""

    require = getattr(skill_registry, "require", None)
    target = str(
        intent_draft.get("target_id") or intent_draft.get("skill_id") or ""
    ).strip()
    if not callable(require) or not target:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct Skill admission has no app-local SkillRegistry policy",
        )
    try:
        metadata = require(target)
        risk_level = int(getattr(metadata, "risk_level"))
    except Exception as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct Skill admission could not freeze canonical risk metadata",
        ) from exc
    policy_error = governed_skill_policy_error(target, metadata)
    if policy_error is not None:
        raise GovernedHostAdapterError(
            policy_error,
            "direct Skill execution policy is not eligible for governed Task admission",
        )
    if not 1 <= risk_level <= 5:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct Skill canonical risk level is invalid",
        )
    return risk_level, bool(
        getattr(metadata, "require_confirm", False) or risk_level >= 4
    )


async def review_continuation_result(
    operation: Any,
    *,
    transform: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Reduce an awaitable or event stream to one durable terminal result."""

    current = await operation if inspect.isawaitable(operation) else operation
    if isinstance(current, Mapping):
        return await transform(dict(current))
    if not hasattr(current, "__aiter__"):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Review continuation returned no durable Host result",
        )
    terminal: dict[str, Any] | None = None
    async for raw_event in current:
        if not isinstance(raw_event, Mapping):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "Review continuation emitted a non-mapping Host event",
            )
        event = await transform(dict(raw_event))
        if bool(event.get("done")):
            terminal = event
    if terminal is None:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Review continuation emitted no durable Host terminal result",
        )
    return terminal


def validate_delegate_target(
    intent_draft: Mapping[str, Any],
    *,
    validator: Callable[[str], Any] | None,
    immutable_policy_required: bool,
) -> dict[str, Any] | None:
    """Resolve a Delegate target without creating execution authority."""

    target_id = str(
        intent_draft.get("target_id") or intent_draft.get("agent_id") or ""
    ).strip()
    if not target_id:
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            "delegate route has no target_id",
        )
    if validator is None:
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            "delegate target registry is not composed",
        )
    try:
        result = validator(target_id)
    except GovernedDelegateDispatchError as exc:
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            exc.detail,
        ) from exc
    except Exception as exc:
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            "delegate target validation failed",
        ) from exc
    if inspect.isawaitable(result):
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            "delegate target validator must be synchronous before admission",
        )
    if not isinstance(result, Mapping):
        if not immutable_policy_required and str(result or "").strip() == target_id:
            return None
        raise GovernedHostAdapterError(
            "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
            "delegate target validator returned no immutable policy",
        )
    return dict(result)


__all__ = [
    "resolve_direct_skill_execution_policy",
    "review_continuation_result",
    "validate_delegate_target",
]
