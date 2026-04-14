"""Model routing helpers for provider/model/key selection."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from host_brain.app import config as host_config
from host_brain.app.model_capability_registry import resolve_model_capability_profile
from host_brain.integrations.llm_gateway import PROVIDER_GEMINI

_GEMINI_KEY_CURSOR = 0
_GEMINI_KEY_CURSOR_LOCK = threading.Lock()
_SECURE_POOL_CACHE_LOCK = threading.Lock()
_SECURE_POOL_CACHE: dict[str, Any] = {
    "fingerprint": None,
    "keys": [],
}

_GEMINI_CLOUD_DEFAULT_MODEL_ID = "gemini-2.5-flash"
_GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID = "gemini-3.1-pro-preview"
_GEMINI_DISPATCH_AGENT_DEFAULT_MODEL_ID = "gemini-2.5-flash"
_GEMINI_EXECUTIVE_ROUTER_DEFAULT_MODEL_ID = _GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID
_GEMINI_CORTEX_ORCHESTRATOR_DEFAULT_MODEL_ID = _GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID
_GEMINI_SUBAGENT_DEFAULT_MODEL_ID = "gemini-2.5-pro"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return _repo_root() / candidate


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    primary_abs = _resolve_path(primary)
    fallback_abs = _resolve_path(fallback)
    if primary_abs.exists() and primary_abs.is_file():
        return primary_abs
    return fallback_abs


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _parse_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def _file_fingerprint(path: Path) -> tuple[str, int, int]:
    if not path.exists() or not path.is_file():
        return (str(path), 0, -1)
    stat = path.stat()
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _resolve_gemini_pool_scope() -> str:
    return str(os.environ.get("HOST_GEMINI_KEY_POOL_SCOPE") or "host.llm").strip().lower() or "host.llm"


def _runtime_file_pool_enabled() -> bool:
    disabled = str(os.environ.get("HOST_GEMINI_KEY_POOL_DISABLE_RUNTIME_FILES") or "").strip().lower()
    return disabled not in {"1", "true", "yes", "on"}


def _resolve_runtime_gemini_keys() -> list[str]:
    key_profiles_path = _pick_existing_path(
        host_config.DEFAULT_KEY_PROFILES,
        host_config.DEFAULT_KEY_PROFILES_FALLBACK,
    )
    secret_store_path = _pick_existing_path(
        host_config.DEFAULT_SECRET_STORE,
        host_config.DEFAULT_SECRET_STORE_FALLBACK,
    )
    scope = _resolve_gemini_pool_scope()
    fingerprint = (
        _file_fingerprint(key_profiles_path),
        _file_fingerprint(secret_store_path),
        scope,
    )
    with _SECURE_POOL_CACHE_LOCK:
        if _SECURE_POOL_CACHE.get("fingerprint") == fingerprint:
            return list(_SECURE_POOL_CACHE.get("keys") or [])

    key_profiles_payload = _load_json_file(key_profiles_path)
    raw_profiles = key_profiles_payload.get("profiles")
    profiles = list(raw_profiles) if isinstance(raw_profiles, list) else []
    current_epoch = _parse_non_negative_int(key_profiles_payload.get("current_epoch"), default=0)

    secret_store: dict[str, Any] = {}
    inline_store = key_profiles_payload.get("secret_store")
    if isinstance(inline_store, dict):
        secret_store.update(dict(inline_store))
    secret_store.update(_load_json_file(secret_store_path))

    resolved_keys: list[str] = []
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        profile_scope = str(raw.get("scope") or "").strip().lower()
        status = str(raw.get("status") or "active").strip().lower() or "active"
        rotation_epoch = _parse_non_negative_int(raw.get("rotation_epoch"), default=0)
        if provider != PROVIDER_GEMINI:
            continue
        if profile_scope != scope:
            continue
        if status != "active":
            continue
        if current_epoch > 0 and rotation_epoch > 0 and rotation_epoch < current_epoch:
            continue
        secret_ref = str(raw.get("secret_ref") or "").strip()
        if not secret_ref:
            continue
        secret_value = str(secret_store.get(secret_ref) or "").strip()
        if secret_value:
            resolved_keys.append(secret_value)

    deduped = _dedupe_keep_order(resolved_keys)
    with _SECURE_POOL_CACHE_LOCK:
        _SECURE_POOL_CACHE["fingerprint"] = fingerprint
        _SECURE_POOL_CACHE["keys"] = list(deduped)
    return deduped


def _split_env_tokens(raw_value: str | None) -> list[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    normalized = text.replace("\r", "\n").replace(";", ",").replace("|", ",")
    tokens: list[str] = []
    for chunk in normalized.replace("\n", ",").split(","):
        token = str(chunk or "").strip()
        if token:
            tokens.append(token)
    return tokens


def _dedupe_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _stable_bucket(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16)


def resolve_gemini_key_pool(*, preferred_api_key: str | None = None) -> list[str]:
    keys: list[str] = []
    preferred = str(preferred_api_key or "").strip()
    if preferred:
        keys.append(preferred)

    if _runtime_file_pool_enabled():
        keys.extend(_resolve_runtime_gemini_keys())

    allow_env_fallback = str(os.environ.get("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK") or "").strip().lower()
    if allow_env_fallback in {"1", "true", "yes", "on"}:
        keys.extend(_split_env_tokens(os.environ.get("HOST_GEMINI_API_KEYS")))

        for idx in range(1, 65):
            key = str(os.environ.get(f"GEMINI_API_KEY_{idx}") or "").strip()
            if key:
                keys.append(key)

        single_key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
        if single_key:
            keys.append(single_key)

    return _dedupe_keep_order(keys)


def inspect_gemini_key_pool(*, preferred_api_key: str | None = None) -> dict[str, Any]:
    preferred = str(preferred_api_key or "").strip()
    runtime_file_enabled = _runtime_file_pool_enabled()
    runtime_keys: list[str] = _resolve_runtime_gemini_keys() if runtime_file_enabled else []
    allow_env_fallback = str(os.environ.get("HOST_GEMINI_KEY_POOL_ALLOW_ENV_FALLBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    env_keys: list[str] = []
    if allow_env_fallback:
        env_keys.extend(_split_env_tokens(os.environ.get("HOST_GEMINI_API_KEYS")))
        for idx in range(1, 65):
            value = str(os.environ.get(f"GEMINI_API_KEY_{idx}") or "").strip()
            if value:
                env_keys.append(value)
        single_key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
        if single_key:
            env_keys.append(single_key)

    merged = _dedupe_keep_order(
        ([preferred] if preferred else [])
        + runtime_keys
        + env_keys
    )
    pool_size = len(merged)
    with _GEMINI_KEY_CURSOR_LOCK:
        next_slot = (_GEMINI_KEY_CURSOR % pool_size) + 1 if pool_size > 0 else None

    source_labels: list[str] = []
    if preferred:
        source_labels.append("preferred")
    if runtime_keys:
        source_labels.append("runtime_files")
    if env_keys:
        source_labels.append("env_fallback")
    if not source_labels:
        source_labels.append("empty")

    return {
        "provider_name": PROVIDER_GEMINI,
        "scope": _resolve_gemini_pool_scope(),
        "strategy": _resolve_key_pool_strategy(),
        "pool_size": pool_size,
        "runtime_file_pool_enabled": runtime_file_enabled,
        "env_fallback_enabled": allow_env_fallback,
        "source": "+".join(source_labels),
        "source_breakdown": {
            "preferred": 1 if preferred else 0,
            "runtime_files": len(runtime_keys),
            "env_fallback": len(env_keys),
        },
        "next_slot": next_slot,
    }


def _resolve_gemini_model_id_for_role(role: str) -> str:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"chat_agent", "cortex_orchestrator"}:
        env_keys = (
            ("HOST_CORTEX_ORCHESTRATOR_MODEL_ID", "HOST_TOP_AGENT_MODEL_ID", "HOST_CHAT_AGENT_MODEL_ID")
            if normalized_role == "cortex_orchestrator"
            else ("HOST_CHAT_AGENT_MODEL_ID",)
        )
        for env_key in env_keys:
            value = str(os.environ.get(env_key) or "").strip()
            if value:
                return value
        return (
            _GEMINI_CORTEX_ORCHESTRATOR_DEFAULT_MODEL_ID
            if normalized_role == "cortex_orchestrator"
            else _GEMINI_CHAT_AGENT_DEFAULT_MODEL_ID
        )
    if normalized_role == "executive_router":
        env_keys = (
            "HOST_EXECUTIVE_ROUTER_MODEL_ID",
            "HOST_DISPATCH_AGENT_MODEL_ID",
        )
        for env_key in env_keys:
            value = str(os.environ.get(env_key) or "").strip()
            if value:
                return value
        return (
            _GEMINI_EXECUTIVE_ROUTER_DEFAULT_MODEL_ID
            if normalized_role == "executive_router"
            else _GEMINI_DISPATCH_AGENT_DEFAULT_MODEL_ID
        )
    if normalized_role == "subagent":
        return (
            str(os.environ.get("HOST_SUBAGENT_MODEL_ID") or _GEMINI_SUBAGENT_DEFAULT_MODEL_ID).strip()
            or _GEMINI_SUBAGENT_DEFAULT_MODEL_ID
        )
    return (
        str(os.environ.get("HOST_GEMINI_MODEL_ID") or _GEMINI_CLOUD_DEFAULT_MODEL_ID).strip()
        or _GEMINI_CLOUD_DEFAULT_MODEL_ID
    )


def _resolve_key_pool_strategy() -> str:
    strategy = str(os.environ.get("HOST_GEMINI_KEY_POOL_STRATEGY") or "round_robin").strip().lower()
    if strategy in {"hash", "first_available", "round_robin"}:
        return strategy
    return "round_robin"


def _next_round_robin_index(pool_size: int) -> int:
    if pool_size <= 0:
        return 0
    global _GEMINI_KEY_CURSOR
    with _GEMINI_KEY_CURSOR_LOCK:
        index = _GEMINI_KEY_CURSOR % pool_size
        _GEMINI_KEY_CURSOR = (_GEMINI_KEY_CURSOR + 1) % max(pool_size, 1)
        return index


def _select_key_slot(
    *,
    key_pool: list[str],
    strategy: str,
    role: str,
    route_key: str | None = None,
) -> int | None:
    if not key_pool:
        return None
    if strategy == "first_available":
        return 0
    if strategy == "hash":
        stable_key = str(route_key or "").strip() or role
        return _stable_bucket(f"{role}:{stable_key}") % len(key_pool)
    return _next_round_robin_index(len(key_pool))


def resolve_gemini_model_route(
    *,
    role: str,
    preferred_api_key: str | None = None,
    route_key: str | None = None,
) -> dict[str, Any]:
    normalized_role = str(role or "").strip().lower() or "cloud_primary"
    model_id = _resolve_gemini_model_id_for_role(normalized_role)
    key_pool = resolve_gemini_key_pool(preferred_api_key=preferred_api_key)
    strategy = _resolve_key_pool_strategy()
    selected_slot = _select_key_slot(
        key_pool=key_pool,
        strategy=strategy,
        role=normalized_role,
        route_key=route_key,
    )
    selected_api_key = key_pool[selected_slot] if selected_slot is not None else None
    capability_profile = resolve_model_capability_profile(
        provider_name=PROVIDER_GEMINI,
        model_id=model_id,
        role=normalized_role,
    )

    return {
        "provider_name": PROVIDER_GEMINI,
        "role": normalized_role,
        "model_id": model_id,
        "api_key": selected_api_key,
        "key_slot": (selected_slot + 1) if selected_slot is not None else None,
        "key_pool_size": len(key_pool),
        "key_strategy": strategy,
        "selection_reason": (
            "GEMINI_KEY_POOL_SELECTED"
            if selected_slot is not None
            else "GEMINI_KEY_POOL_EMPTY"
        ),
        "capability_profile": capability_profile,
    }


def _reset_gemini_key_pool_cursor_for_tests() -> None:
    global _GEMINI_KEY_CURSOR
    with _GEMINI_KEY_CURSOR_LOCK:
        _GEMINI_KEY_CURSOR = 0
    with _SECURE_POOL_CACHE_LOCK:
        _SECURE_POOL_CACHE["fingerprint"] = None
        _SECURE_POOL_CACHE["keys"] = []
