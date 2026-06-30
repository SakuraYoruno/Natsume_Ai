"""Shared configuration utilities for host_brain and edge_client.

Extracts identical helper functions and dataclasses that were duplicated
between host_brain/app/config.py and edge_client/config.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Environment constants (shared by host and edge)
# ---------------------------------------------------------------------------

ENV_DEV = "dev"
ENV_TEST = "test"
ENV_PROD = "prod"
ALLOWED_ENVS = frozenset({ENV_DEV, ENV_TEST, ENV_PROD})

DEFAULT_KEY_PROFILES = Path("configs/secrets/key_profiles.runtime.json")
DEFAULT_KEY_PROFILES_FALLBACK = Path("configs/secrets/key_profiles.example.json")
DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")


# ---------------------------------------------------------------------------
# Shared dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigIssue:
    """A single validation issue found during config loading."""
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class ConfigSnapshotExportResult:
    """Result of exporting a config snapshot for diagnostics."""
    success: bool
    stage: str
    reason_code: str | None
    snapshot: dict[str, Any] | None
    issues: tuple[ConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "snapshot": dict(self.snapshot) if self.snapshot is not None else None,
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------------------------

def as_text(value: Any, default: str) -> str:
    """Coerce *value* to string, returning *default* on None/empty."""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def as_optional_text(value: Any) -> str | None:
    """Coerce *value* to optional string, returning None on None/empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def parse_bool(value: Any, *, default: bool) -> bool:
    """Parse a boolean-like value from env or config."""
    if value is None:
        return default
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def parse_int(
    value: Any,
    *,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    """Parse an integer value with optional bounds."""
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None and result < min_value:
        return min_value
    if max_value is not None and result > max_value:
        return max_value
    return result


def coerce_non_negative_int(value: Any, *, default: int) -> int:
    """Parse an int, clamping to >= 0."""
    result = parse_int(value, default=default)
    return max(0, result)


def pick_existing_path(primary: Path, fallback: Path) -> Path:
    """Return *primary* if it exists, else *fallback*."""
    if primary.is_file():
        return primary
    return fallback


def load_json_file(path: Path) -> dict[str, Any]:
    """Load a JSON file, returning {} on failure."""
    try:
        text = path.read_text(encoding="utf-8")
        result = json.loads(text)
        return dict(result) if isinstance(result, dict) else {}
    except Exception:
        return {}


def env_value(merged: dict[str, Any], key: str, *, default: str = "") -> str:
    """Read a string value from merged env dict."""
    return as_text(merged.get(key), default)


def failure(
    issues: list[ConfigIssue],
    *,
    result_cls: type,
    config_cls: type | None = None,
):
    """Create a failed load result."""
    return result_cls(
        success=False,
        stage="validate",
        reason_code=issues[0].reason_code if issues else None,
        config=None,
        issues=tuple(issues),
    )
