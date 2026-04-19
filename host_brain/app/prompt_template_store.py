from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_template_defs import (
    get_prompt_template_definition,
    list_prompt_template_definitions,
)
from host_brain.core.ids import now_ms

_logger = logging.getLogger("Natsume.prompt_template_store")

_PROMPT_TEMPLATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompt_template_config (
    template_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    scope TEXT NOT NULL,
    locale TEXT NOT NULL DEFAULT '*',
    content_type TEXT NOT NULL DEFAULT 'text',
    override_content TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at_ts_ms INTEGER NOT NULL,
    updated_at_ts_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_template_config_scope ON prompt_template_config(scope);
CREATE INDEX IF NOT EXISTS idx_prompt_template_config_category ON prompt_template_config(category);
CREATE INDEX IF NOT EXISTS idx_prompt_template_config_enabled ON prompt_template_config(enabled);
CREATE INDEX IF NOT EXISTS idx_prompt_template_config_updated ON prompt_template_config(updated_at_ts_ms);
"""


class PromptTemplateStore:
    def __init__(
        self,
        ledger: Any,
        *,
        cache_ttl_ms: int = 2000,
    ) -> None:
        self._ledger = ledger
        self._cache_ttl_ms = max(0, int(cache_ttl_ms))
        self._cache_lock = threading.Lock()
        self._cache_expires_at_ms = 0
        self._override_cache: dict[str, dict[str, Any]] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = getattr(self._ledger, "_conn", None)
        write_lock = getattr(self._ledger, "_write_lock", None)
        if conn is None:
            raise RuntimeError("ledger connection unavailable")
        if write_lock is None:
            conn.executescript(_PROMPT_TEMPLATE_SCHEMA_SQL)
            conn.commit()
            return
        with write_lock:
            with conn:
                conn.executescript(_PROMPT_TEMPLATE_SCHEMA_SQL)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._override_cache = {}
            self._cache_expires_at_ms = 0

    def _load_override_rows(self) -> dict[str, dict[str, Any]]:
        conn = getattr(self._ledger, "_conn", None)
        if conn is None:
            return {}
        rows = conn.execute(
            """
            SELECT template_id, category, scope, locale, content_type, override_content,
                   description, enabled, version, created_at_ts_ms, updated_at_ts_ms
            FROM prompt_template_config
            """
        ).fetchall()
        return {
            str(row["template_id"]): dict(row)
            for row in rows
            if str(row["template_id"] or "").strip()
        }

    def _get_override_map(self) -> dict[str, dict[str, Any]]:
        current_ts = now_ms()
        with self._cache_lock:
            if self._cache_expires_at_ms > current_ts:
                return dict(self._override_cache)
        loaded = self._load_override_rows()
        with self._cache_lock:
            self._override_cache = dict(loaded)
            self._cache_expires_at_ms = current_ts + self._cache_ttl_ms
        return loaded

    def _merge_template(self, template_id: str) -> dict[str, Any]:
        definition = get_prompt_template_definition(template_id)
        override = self._get_override_map().get(definition.template_id)
        has_override = override is not None
        enabled = bool(override.get("enabled")) if override is not None else False
        override_content = str(override.get("override_content") or "") if override is not None else None
        resolved_content = override_content if has_override and enabled and override_content is not None else definition.default_content
        return {
            "template_id": definition.template_id,
            "category": definition.category,
            "scope": definition.scope,
            "locale": definition.locale,
            "content_type": definition.content_type,
            "description": definition.description,
            "default_content": definition.default_content,
            "override_content": override_content,
            "resolved_content": resolved_content,
            "source": "override" if has_override and enabled else "default",
            "enabled": enabled,
            "has_override": has_override,
            "version": int(override.get("version") or 0) if override is not None else 0,
            "created_at_ts_ms": int(override.get("created_at_ts_ms") or 0) if override is not None else None,
            "updated_at_ts_ms": int(override.get("updated_at_ts_ms") or 0) if override is not None else None,
        }

    def list_templates(
        self,
        *,
        scope: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        scope_text = str(scope or "").strip().lower()
        category_text = str(category or "").strip().lower()
        keyword_text = str(keyword or "").strip().lower()
        source_text = str(source or "").strip().lower()
        items = [self._merge_template(item.template_id) for item in list_prompt_template_definitions()]
        filtered: list[dict[str, Any]] = []
        for item in items:
            if scope_text and str(item["scope"]).lower() != scope_text:
                continue
            if category_text and str(item["category"]).lower() != category_text:
                continue
            if source_text and str(item["source"]).lower() != source_text:
                continue
            if keyword_text:
                haystack = " ".join(
                    [
                        str(item.get("template_id") or ""),
                        str(item.get("scope") or ""),
                        str(item.get("category") or ""),
                        str(item.get("description") or ""),
                    ]
                ).lower()
                if keyword_text not in haystack:
                    continue
            filtered.append(item)
        return filtered

    def get_template(self, template_id: str) -> dict[str, Any]:
        return self._merge_template(template_id)

    def resolve_text(self, template_id: str) -> str:
        return str(self.get_template(template_id)["resolved_content"])

    def resolve_json(self, template_id: str) -> Any:
        merged = self.get_template(template_id)
        raw = str(merged["resolved_content"] or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            _logger.warning("prompt template json invalid: %s (%s)", template_id, exc)
            default_raw = str(merged["default_content"] or "").strip()
            return json.loads(default_raw) if default_raw else None

    def upsert_template(
        self,
        template_id: str,
        *,
        content: str,
        enabled: bool = True,
        description: str | None = None,
    ) -> None:
        definition = get_prompt_template_definition(str(template_id))
        override = self._get_override_map().get(definition.template_id) or {}
        conn = getattr(self._ledger, "_conn", None)
        write_lock = getattr(self._ledger, "_write_lock", None)
        if conn is None:
            raise RuntimeError("ledger connection unavailable")

        created_at_ts_ms = int(override.get("created_at_ts_ms") or now_ms())
        updated_at_ts_ms = now_ms()
        version = int(override.get("version") or 0) + 1
        payload = (
            definition.template_id,
            definition.category,
            definition.scope,
            definition.locale,
            definition.content_type,
            str(content or ""),
            str(description or definition.description or ""),
            1 if enabled else 0,
            version,
            created_at_ts_ms,
            updated_at_ts_ms,
        )
        sql = """
            INSERT INTO prompt_template_config (
                template_id, category, scope, locale, content_type, override_content,
                description, enabled, version, created_at_ts_ms, updated_at_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(template_id) DO UPDATE SET
                category=excluded.category,
                scope=excluded.scope,
                locale=excluded.locale,
                content_type=excluded.content_type,
                override_content=excluded.override_content,
                description=excluded.description,
                enabled=excluded.enabled,
                version=excluded.version,
                created_at_ts_ms=excluded.created_at_ts_ms,
                updated_at_ts_ms=excluded.updated_at_ts_ms
        """
        if write_lock is None:
            with conn:
                conn.execute(sql, payload)
        else:
            with write_lock:
                with conn:
                    conn.execute(sql, payload)
        self.clear_cache()

    def reset_template(self, template_id: str) -> dict[str, Any]:
        definition = get_prompt_template_definition(str(template_id))
        conn = getattr(self._ledger, "_conn", None)
        write_lock = getattr(self._ledger, "_write_lock", None)
        if conn is None:
            raise RuntimeError("ledger connection unavailable")
        sql = "DELETE FROM prompt_template_config WHERE template_id = ?"
        payload = (definition.template_id,)
        if write_lock is None:
            with conn:
                conn.execute(sql, payload)
        else:
            with write_lock:
                with conn:
                    conn.execute(sql, payload)
        self.clear_cache()
        return self.get_template(definition.template_id)


def get_prompt_template_default_text(template_id: str) -> str:
    return get_prompt_template_definition(template_id).default_content


def get_prompt_template_default_json(template_id: str) -> Any:
    raw = str(get_prompt_template_definition(template_id).default_content or "").strip()
    return json.loads(raw) if raw else None


def _deep_merge_template_json(default_value: Any, override_value: Any) -> Any:
    if isinstance(default_value, Mapping) and isinstance(override_value, Mapping):
        merged = {str(key): value for key, value in default_value.items()}
        for key, value in override_value.items():
            key_text = str(key)
            if key_text in merged:
                merged[key_text] = _deep_merge_template_json(merged[key_text], value)
            else:
                merged[key_text] = value
        return merged
    return override_value if override_value is not None else default_value


def resolve_prompt_template_text(template_id: str) -> str:
    default_content = get_prompt_template_default_text(template_id)
    try:
        from host_brain.app.dependencies import get_prompt_template_store  # noqa: PLC0415

        return get_prompt_template_store().resolve_text(template_id)
    except Exception:
        return default_content


def resolve_prompt_template_json(template_id: str) -> Any:
    default_content = get_prompt_template_default_json(template_id)
    try:
        from host_brain.app.dependencies import get_prompt_template_store  # noqa: PLC0415

        return get_prompt_template_store().resolve_json(template_id)
    except Exception:
        return default_content


def resolve_prompt_template_json_merged(template_id: str) -> Any:
    default_content = get_prompt_template_default_json(template_id)
    resolved_content = resolve_prompt_template_json(template_id)
    return _deep_merge_template_json(default_content, resolved_content)
