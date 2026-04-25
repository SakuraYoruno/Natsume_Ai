from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptTemplateDefinition:
    template_id: str
    category: str = "retired"
    scope: str = "retired"
    locale: str = "*"
    content_type: str = "text"
    description: str = "retired prompt template definition"
    default_content: str = ""


def get_prompt_template_definition(template_id: str) -> PromptTemplateDefinition:
    return PromptTemplateDefinition(template_id=str(template_id or "").strip())


def list_prompt_template_definitions() -> list[PromptTemplateDefinition]:
    return []


def _invalidate_registry() -> None:
    return None


PROMPT_TEMPLATE_DEFINITIONS: dict[str, PromptTemplateDefinition] = {}

__all__ = [
    "PromptTemplateDefinition",
    "PROMPT_TEMPLATE_DEFINITIONS",
    "get_prompt_template_definition",
    "list_prompt_template_definitions",
    "_invalidate_registry",
]
