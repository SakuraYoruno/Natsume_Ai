"""Strict access to immutable code-side prompt contracts.

Prompt compiler sources are model-domain data.  Reading them must not force
application composition, ledger construction, provider binding, or a global
registry lookup.  Runtime rendering still belongs to ``PromptRegistry``;
these helpers only expose defensive copies of declared compiler sources.
"""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.prompt_registry import PromptSchemaError
from host_brain.model_runtime.prompt_specs import get_prompt_spec, resolve_prompt_spec_json


def prompt_contract_exists(prompt_id: str) -> bool:
    try:
        get_prompt_spec(prompt_id)
    except KeyError:
        return False
    return True


def resolve_prompt_contract_json(prompt_id: str) -> Any:
    """Return the registered code-side compiler source for ``prompt_id``."""

    try:
        spec = get_prompt_spec(prompt_id)
    except KeyError as exc:
        raise PromptSchemaError(f"unknown prompt contract: {prompt_id}") from exc
    if spec.output_type == "text":
        raise PromptSchemaError(f"prompt contract is text, not JSON: {prompt_id}")
    value = resolve_prompt_spec_json(prompt_id)
    if not isinstance(value, (dict, list, tuple)):
        raise PromptSchemaError(f"prompt compilation source is not structured JSON: {prompt_id}")
    return value


def resolve_prompt_contract_text(prompt_id: str) -> str:
    """Return a registered text compiler source with strict type validation."""

    try:
        spec = get_prompt_spec(prompt_id)
    except KeyError as exc:
        raise PromptSchemaError(f"unknown prompt contract: {prompt_id}") from exc
    if spec.output_type != "text":
        raise PromptSchemaError(f"prompt contract is not text: {prompt_id}")
    value = resolve_prompt_spec_json(prompt_id)
    if not isinstance(value, str):
        raise PromptSchemaError(f"prompt compilation source is not text: {prompt_id}")
    return value


__all__ = [
    "prompt_contract_exists",
    "resolve_prompt_contract_json",
    "resolve_prompt_contract_text",
]
