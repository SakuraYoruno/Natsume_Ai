"""Canonical, centrally governed model invocation for Tribe agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.core.ids import new_request_id
from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.model_runtime.json_parser import extract_json_object
from host_brain.model_runtime.live_prompts import TRIBE_PROMPT_IDS


@dataclass(frozen=True, slots=True)
class TribeModelSemantic:
    semantic_id: str
    prompt_id: str
    cognitive_tier: str


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
    semantic_id: str,
    system_prompt: str,
    user_prompt: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Complete one registered Tribe JSON prompt through process composition."""

    semantic = tribe_model_semantic(semantic_id)
    result = complete_compiled_prompt_sync(
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


__all__ = [
    "TribeModelSemantic",
    "complete_tribe_json",
    "tribe_model_semantic",
]
