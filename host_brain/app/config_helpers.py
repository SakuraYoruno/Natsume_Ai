"""Config helper utilities (extracted from app/config.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from host_brain.core.errors import CONFIG_VALUE_INVALID

if TYPE_CHECKING:
    from host_brain.app.config import ConfigIssue

DEFAULT_KEY_PROFILES = Path("configs/secrets/key_profiles.runtime.json")
DEFAULT_KEY_PROFILES_FALLBACK = Path("configs/secrets/key_profiles.example.json")
DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")
DEFAULT_HOST_NODE_ID = "natsu-node-01"
DEFAULT_HOST_NODE_ID_SENTINEL = "host.default"

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(
    value: Any,
    *,
    default: int,
    field_name: str,
    min_value: int,
    max_value: int,
    issues: "list[ConfigIssue]",
) -> int:
    from host_brain.app.config import ConfigIssue as _ConfigIssue  # noqa: PLC0415
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        issues.append(
            _ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be integer",
                field=field_name,
            )
        )
        return int(default)

    if parsed < min_value or parsed > max_value:
        issues.append(
            _ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be in range [{min_value}, {max_value}]",
                field=field_name,
            )
        )
        return int(default)
    return parsed


def _resolve_runtime_key_materials(
    *,
    env: Mapping[str, Any],
    key_profiles: Sequence[Mapping[str, Any]] | None,
    secret_store: Mapping[str, Any] | None,
    node_id: str | None,
    current_epoch: int | None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any], int, str]:
    resolved_profiles: list[Mapping[str, Any]] = list(key_profiles or [])
    resolved_store: dict[str, Any] = dict(secret_store or {})
    resolved_epoch = _coerce_non_negative_int(current_epoch, default=0)
    key_profiles_payload: dict[str, Any] = {}

    if not resolved_profiles or resolved_epoch <= 0 or _is_default_node_id(node_id):
        key_profiles_payload = _load_json_file(
            _pick_existing_path(DEFAULT_KEY_PROFILES, DEFAULT_KEY_PROFILES_FALLBACK),
        )
        if not resolved_profiles:
            raw_profiles = key_profiles_payload.get("profiles")
            if isinstance(raw_profiles, list):
                resolved_profiles = [row for row in raw_profiles if isinstance(row, Mapping)]
        if resolved_epoch <= 0:
            resolved_epoch = _coerce_non_negative_int(
                key_profiles_payload.get("current_epoch"),
                default=0,
            )

    resolved_node_id = _resolve_runtime_node_id(
        env=env,
        node_id=node_id,
        key_profiles_payload=key_profiles_payload,
    )
    if not resolved_store:
        resolved_store = _load_json_file(
            _pick_existing_path(DEFAULT_SECRET_STORE, DEFAULT_SECRET_STORE_FALLBACK),
        )
    return resolved_profiles, resolved_store, resolved_epoch, resolved_node_id


def _resolve_runtime_node_id(
    *,
    env: Mapping[str, Any],
    node_id: str | None,
    key_profiles_payload: Mapping[str, Any],
) -> str:
    explicit = str(node_id or "").strip()
    if explicit and not _is_default_node_id(explicit):
        return explicit
    from_env = str(
        env.get("HOST_RETR_NODE_ID")
        or env.get("Natsume_HOST_NODE_ID")
        or env.get("HOST_NODE_ID")
        or "",
    ).strip()
    if from_env:
        return from_env
    from_profiles = str(key_profiles_payload.get("host_node_id") or "").strip()
    if from_profiles:
        return from_profiles
    return DEFAULT_HOST_NODE_ID


def _inject_resolved_llm_key(*, provider: str, api_key: str | None) -> None:
    key_value = str(api_key or "").strip()
    if not key_value:
        return
    provider_to_env_key = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    }
    env_key = provider_to_env_key.get(str(provider or "").strip().lower())
    if not env_key:
        return
    if str(os.environ.get(env_key) or "").strip():
        return
    os.environ[env_key] = key_value


def _is_default_node_id(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return not text or text == DEFAULT_HOST_NODE_ID_SENTINEL


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    if primary.exists() and primary.is_file():
        return primary
    return fallback


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
