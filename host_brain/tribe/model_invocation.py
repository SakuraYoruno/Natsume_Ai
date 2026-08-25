"""Canonical, centrally governed model invocation for Tribe agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.core.ids import new_request_id
from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.model_runtime.invocation import complete_compiled_prompt
from host_brain.model_runtime.json_parser import extract_json_object
from host_brain.model_runtime.live_prompts import TRIBE_PROMPT_IDS
from host_brain.model_runtime.model_attempt_service import ModelAttemptService
from host_brain.model_runtime.contracts import ModelAttemptResult
from host_brain.model_runtime.trace import model_attempt_result_trace


@dataclass(frozen=True, slots=True)
class TribeModelSemantic:
    semantic_id: str
    prompt_id: str
    cognitive_tier: str


@dataclass(frozen=True, slots=True)
class TribeModelCompletion:
    result: ModelAttemptResult
    trace: Mapping[str, Any]

    @property
    def success(self) -> bool:
        return bool(self.result.success)

    @property
    def content(self) -> str:
        return str(self.result.content or "")


_TRIBE_MODEL_SEMANTICS = {
    semantic_id: TribeModelSemantic(
        semantic_id=semantic_id,
        prompt_id=prompt_id,
        cognitive_tier=(
            "A"
            if semantic_id == "glymphatic_consolidation_summary"
            else "B"
            if semantic_id
            in {
                "cerebellum_failure_pattern",
                "immune_semantic_intent",
                "language_wernicke_deep_parse",
            }
            else "C"
        ),
    )
    for semantic_id, prompt_id in TRIBE_PROMPT_IDS.items()
}


def tribe_model_semantic(semantic_id: str) -> TribeModelSemantic:
    normalized = str(semantic_id or "").strip()
    semantic = _TRIBE_MODEL_SEMANTICS.get(normalized)
    if semantic is None:
        raise KeyError(f"TRIBE_MODEL_SEMANTIC_UNKNOWN:{normalized or '<empty>'}")
    return semantic


def complete_tribe_json(
    *,
    attempt_service: ModelAttemptService,
    semantic_id: str,
    system_prompt: str,
    user_prompt: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Complete one registered Tribe JSON prompt through process composition."""

    semantic = tribe_model_semantic(semantic_id)
    result = complete_compiled_prompt_sync(
        attempt_service=attempt_service,
        prompt_id=semantic.prompt_id,
        messages=(
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": str(user_prompt)},
        ),
        request_id=str(request_id or new_request_id()),
        scene_type="TRIBE_INTERNAL",
        cognitive_tier=semantic.cognitive_tier,
        deadline_ms=8_000,
        foreground=False,
        metadata={
            "owner": "tribe",
            "tribe_semantic_id": semantic.semantic_id,
        },
    )
    if not result.success:
        raise RuntimeError(
            f"TRIBE_MODEL_ATTEMPT_FAILED:{result.error_code}:{result.error_message}"
        )
    return extract_json_object(result.content)


def complete_tribe_compiled_sync(
    *,
    attempt_service: ModelAttemptService,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> TribeModelCompletion:
    result = complete_compiled_prompt_sync(
        attempt_service=attempt_service,
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=max(1, int(deadline_ms)),
        foreground=foreground,
        metadata={"caller": "tribe_model_attempt", **dict(metadata or {})},
    )
    return TribeModelCompletion(result=result, trace=model_attempt_result_trace(result))


async def complete_tribe_compiled(
    *,
    attempt_service: ModelAttemptService,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> TribeModelCompletion:
    result = await complete_compiled_prompt(
        attempt_service=attempt_service,
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=max(1, int(deadline_ms)),
        foreground=foreground,
        metadata={"caller": "tribe_model_attempt", **dict(metadata or {})},
    )
    return TribeModelCompletion(result=result, trace=model_attempt_result_trace(result))


__all__ = [
    "TribeModelSemantic",
    "TribeModelCompletion",
    "complete_tribe_compiled",
    "complete_tribe_compiled_sync",
    "complete_tribe_json",
    "tribe_model_semantic",
]
