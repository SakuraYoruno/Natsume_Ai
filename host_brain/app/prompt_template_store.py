from __future__ import annotations

from typing import Any

from host_brain.app.prompt_specs import resolve_prompt_spec_json


class PromptTemplateStore:
    """Retired compatibility facade for the old YAML template store.

    The external prompt template system has been removed from runtime. This
    class deliberately does not read YAML files or SQLite overrides. Legacy
    callers may still import it while they are being migrated, but unresolved
    ids return structural empty values instead of persona/fallback text.
    """

    def __init__(self, ledger: Any | None = None, **_: Any) -> None:
        self._ledger = ledger

    def clear_cache(self) -> None:
        return None

    def list_templates(
        self,
        *,
        scope: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_template(self, template_id: str) -> dict[str, Any]:
        payload = resolve_prompt_template_json(str(template_id or "").strip())
        resolved = payload if payload is not None else ""
        return {
            "template_id": str(template_id or "").strip(),
            "category": "retired",
            "scope": "retired",
            "locale": "*",
            "content_type": "json" if isinstance(resolved, (dict, list)) else "text",
            "description": "retired prompt template facade",
            "default_content": resolved,
            "override_content": None,
            "resolved_content": resolved,
            "source": "prompt_spec" if payload is not None else "retired",
            "enabled": False,
            "has_override": False,
            "version": 0,
            "created_at_ts_ms": None,
            "updated_at_ts_ms": None,
        }

    def resolve_text(self, template_id: str) -> str:
        return ""

    def resolve_json(self, template_id: str) -> Any:
        return resolve_prompt_template_json(template_id)

    def upsert_template(
        self,
        template_id: str,
        *,
        content: str,
        enabled: bool = True,
        description: str | None = None,
    ) -> None:
        raise RuntimeError("prompt template store is retired; edit code-side PromptSpec instead")

    def reset_template(self, template_id: str) -> dict[str, Any]:
        return self.get_template(template_id)


def get_prompt_template_default_text(template_id: str) -> str:
    return ""


def get_prompt_template_default_json(template_id: str) -> Any:
    return resolve_prompt_template_json(template_id)


def resolve_prompt_template_text(template_id: str) -> str:
    return ""


def resolve_prompt_template_json(template_id: str) -> Any:
    key = str(template_id or "").strip()
    if not key:
        return None
    try:
        return resolve_prompt_spec_json(key)
    except KeyError:
        return None


def resolve_prompt_template_json_merged(template_id: str) -> Any:
    return resolve_prompt_template_json(template_id)


__all__ = [
    "PromptTemplateStore",
    "get_prompt_template_default_json",
    "get_prompt_template_default_text",
    "resolve_prompt_template_json",
    "resolve_prompt_template_json_merged",
    "resolve_prompt_template_text",
]
