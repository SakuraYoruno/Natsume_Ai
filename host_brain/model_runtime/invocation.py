"""Application port for invoking registered, already-compiled prompts.

Domain modules may still own prompt compilation while they are migrated, but
they must cross the model boundary through a registered prompt identity and
the single :class:`ModelAttemptService`.  This module deliberately exposes no
provider, model, credential, base-url, fallback, or cache arguments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.model_runtime.contracts import (
    ModelAttemptRequest,
    ModelAttemptResult,
    PromptRef,
    RetryEvaluator,
)


def _model_attempt_service():  # noqa: ANN202
    from host_brain.app.dependencies import get_model_attempt_service  # noqa: PLC0415

    return get_model_attempt_service()


def compiled_prompt_request(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelAttemptRequest:
    """Build the canonical request for a registered compiled-message prompt."""

    normalized_messages = tuple(dict(message) for message in messages)
    return ModelAttemptRequest(
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        prompt=PromptRef(prompt_id, variables={"messages": normalized_messages}),
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=dict(metadata or {}),
    )


async def complete_compiled_prompt(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelAttemptResult:
    """Invoke a compiled prompt through the sole asynchronous model authority."""

    request = compiled_prompt_request(
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=metadata,
    )
    return await _model_attempt_service().complete(request)


def complete_compiled_prompt_sync(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelAttemptResult:
    """Invoke a compiled prompt through the sole synchronous model authority."""

    request = compiled_prompt_request(
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=metadata,
    )
    return _model_attempt_service().complete_sync(request)


__all__ = [
    "compiled_prompt_request",
    "complete_compiled_prompt",
    "complete_compiled_prompt_sync",
]
