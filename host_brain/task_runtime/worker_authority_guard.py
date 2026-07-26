"""Detect authority-bearing fields that workers may not propose."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


_FORBIDDEN_NON_TOKEN_KEY = re.compile(
    r"(?:^|_)(?:authorization|governor_grant|approval|credentials?|secret)(?:$|_)",
    re.IGNORECASE,
)
_TOKEN_FIELD = re.compile(r"(?:^|_)tokens?(?:$|_)", re.IGNORECASE)
_SAFE_TOKEN_METADATA = re.compile(
    r"^(?:"
    r"token_(?:count|budget|limit|usage)|"
    r"(?:prompt|completion|input|output|total|cached|reasoning|actual|estimated|budget|context)_tokens?|"
    r"(?:max|min)(?:_context)?_tokens|"
    r"(?:enforce_)?context_token_budget|context_token_estimator|token_budget_tokens|"
    r"(?:first_token|time_to_first_token|stream_after_first_token)(?:_latency)?_ms"
    r")$",
    re.IGNORECASE,
)


def forbidden_worker_authority_path(
    value: Any,
    *,
    path: str = "payload",
) -> str | None:
    """Return the first forbidden field path, or ``None`` when authority-free."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_forbidden_key(key):
                return f"{path}.{key}"
            nested = forbidden_worker_authority_path(
                child,
                path=f"{path}.{key}",
            )
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for index, child in enumerate(value):
            nested = forbidden_worker_authority_path(
                child,
                path=f"{path}[{index}]",
            )
            if nested is not None:
                return nested
    return None


def _is_forbidden_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if _FORBIDDEN_NON_TOKEN_KEY.search(normalized):
        return True
    if _TOKEN_FIELD.search(normalized):
        return _SAFE_TOKEN_METADATA.fullmatch(normalized) is None
    return False


__all__ = ["forbidden_worker_authority_path"]
