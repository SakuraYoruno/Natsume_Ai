from __future__ import annotations

from typing import Any

from host_brain.app.prompt_specs import (
    get_prompt_spec,
    list_prompt_specs,
    prompt_spec_metadata,
    resolve_prompt_spec_json,
)


class PromptTemplateStore:
    """Retired compatibility facade for the old YAML template store.

    The external prompt template system has been removed from runtime. This
    class deliberately does not read YAML files or SQLite overrides. Legacy
    callers may still import it while they are being migrated. Known prompt ids
    are resolved from the versioned code-side PromptSpec catalog; unresolved ids
    return structural empty values instead of persona/fallback text.
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
        scope_filter = str(scope or "").strip().lower()
        category_filter = str(category or "").strip().lower()
        keyword_filter = str(keyword or "").strip().lower()
        source_filter = str(source or "").strip().lower()
        items: list[dict[str, Any]] = []
        for spec in list_prompt_specs():
            item = self._template_item_from_spec(spec, include_content=False)
            if scope_filter and scope_filter not in str(item.get("scope") or "").lower():
                continue
            if category_filter and category_filter not in str(item.get("category") or "").lower():
                continue
            if source_filter:
                source_text = (
                    f"{item.get('source', '')} {item.get('source_path', '')} "
                    "default code-side"
                ).lower()
                if source_filter not in source_text:
                    continue
            if keyword_filter:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in (
                        "template_id",
                        "description",
                        "purpose",
                        "category",
                        "scope",
                        "owner",
                        "source_path",
                        "change_note",
                    )
                ).lower()
                if keyword_filter not in haystack:
                    continue
            items.append(item)
        return items

    def get_template(self, template_id: str) -> dict[str, Any]:
        key = str(template_id or "").strip()
        try:
            return self._template_item_from_spec(get_prompt_spec(key), include_content=True)
        except KeyError:
            pass
        payload = resolve_prompt_template_json(key)
        resolved = payload if payload is not None else ""
        return {
            "template_id": key,
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
        return resolve_prompt_template_text(template_id)

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

    @staticmethod
    def _template_item_from_spec(spec: Any, *, include_content: bool) -> dict[str, Any]:
        content = resolve_prompt_spec_json(spec.spec_id) if include_content else None
        content_type = "json" if spec.output_type != "text" else "text"
        item = {
            **prompt_spec_metadata(spec),
            "category": spec.category,
            "scope": spec.scope,
            "locale": "*",
            "content_type": content_type,
            "description": spec.purpose,
            "default_content": content if include_content else None,
            "override_content": None,
            "resolved_content": content if include_content else None,
            "enabled": True,
            "has_override": False,
            "created_at_ts_ms": None,
            "updated_at_ts_ms": None,
        }
        return item


def get_prompt_template_default_text(template_id: str) -> str:
    return resolve_prompt_template_text(template_id)


def get_prompt_template_default_json(template_id: str) -> Any:
    return resolve_prompt_template_json(template_id)


def resolve_prompt_template_text(template_id: str) -> str:
    resolved = resolve_prompt_template_json(template_id)
    return resolved if isinstance(resolved, str) else ""


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
