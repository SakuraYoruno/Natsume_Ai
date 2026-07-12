"""Registered prompt bindings for live, already-compiled message lists.

Domain prompt compilers remain responsible for assembling their typed packets.
This module gives those compiled messages a versioned registry identity before
they cross the model-attempt boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.model_runtime.contracts import OutputContract
from host_brain.model_runtime.prompt_catalog import build_production_prompt_registry
from host_brain.model_runtime.prompt_registry import PromptRegistry, PromptSchemaError


SURFACE_MAIN_TEXT_PROMPT_ID = "surface.main_chain.live.text"
SURFACE_MAIN_JSON_PROMPT_ID = "surface.main_chain.live.json"
SURFACE_MEMORY_REPAIR_PROMPT_ID = "surface.memory_guard.repair.json"


def _compiled_messages(values: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = values.get("messages")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise PromptSchemaError("compiled live prompt messages must be a sequence")
    messages: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise PromptSchemaError("compiled live prompt message must be a mapping")
        messages.append(dict(item))
    if not messages:
        raise PromptSchemaError("compiled live prompt must contain at least one message")
    return tuple(messages)


def register_live_prompt_bindings(registry: PromptRegistry) -> PromptRegistry:
    """Install the three live prompt identities into ``registry``."""

    shared = {
        "version": "v1.0.0",
        "required_variables": ("messages",),
        "metadata": {
            "owner": "model_runtime",
            "source": "compiled_domain_prompt",
            "source_path": "host_brain/model_runtime/live_prompts.py",
        },
    }
    registry.register_renderer(
        SURFACE_MAIN_TEXT_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="text"),
        **shared,
    )
    registry.register_renderer(
        SURFACE_MAIN_JSON_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        SURFACE_MEMORY_REPAIR_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    return registry


def build_live_production_prompt_registry() -> PromptRegistry:
    """Build the code catalog plus the live compiled-message bindings."""

    return register_live_prompt_bindings(build_production_prompt_registry())


__all__ = [
    "SURFACE_MAIN_JSON_PROMPT_ID",
    "SURFACE_MAIN_TEXT_PROMPT_ID",
    "SURFACE_MEMORY_REPAIR_PROMPT_ID",
    "build_live_production_prompt_registry",
    "register_live_prompt_bindings",
]
