"""Thin application port for registered, already-compiled model prompts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.model_runtime import ModelAttemptResult, model_attempt_result_trace
from host_brain.model_runtime.invocation import (
    complete_compiled_prompt,
    complete_compiled_prompt_sync,
)


@dataclass(frozen=True, slots=True)
class CompiledModelCompletion:
    result: ModelAttemptResult
    trace: Mapping[str, Any]

    @property
    def success(self) -> bool:
        return bool(self.result.success)

    @property
    def content(self) -> str:
        return str(self.result.content or "")


def complete_compiled_model_sync(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> CompiledModelCompletion:
    from host_brain.composition.process_services import get_model_attempt_service

    result = complete_compiled_prompt_sync(
        attempt_service=get_model_attempt_service(),
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=max(1, int(deadline_ms)),
        foreground=foreground,
        metadata={"caller": "compiled_model_attempt", **dict(metadata or {})},
    )
    return CompiledModelCompletion(result=result, trace=model_attempt_result_trace(result))


async def complete_compiled_model(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> CompiledModelCompletion:
    from host_brain.composition.process_services import get_model_attempt_service

    result = await complete_compiled_prompt(
        attempt_service=get_model_attempt_service(),
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=max(1, int(deadline_ms)),
        foreground=foreground,
        metadata={"caller": "compiled_model_attempt", **dict(metadata or {})},
    )
    return CompiledModelCompletion(result=result, trace=model_attempt_result_trace(result))


__all__ = [
    "CompiledModelCompletion",
    "complete_compiled_model",
    "complete_compiled_model_sync",
]
