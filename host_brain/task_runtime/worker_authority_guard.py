"""检测 worker 不得提议的授权承载字段。"""

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
    """返回首个越权字段路径；完全无授权字段时返回 ``None``。

    验收 compiler 只接受 worker 的无权描述。这里递归扫描 Mapping 与列表，避免
    worker 把 grant、凭据或 token 藏进嵌套 payload 后跨越持久化效果边界。
    """

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
    """区分预算/统计 token 元数据与可能承载访问权的 token 字段。"""
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if _FORBIDDEN_NON_TOKEN_KEY.search(normalized):
        return True
    if _TOKEN_FIELD.search(normalized):
        return _SAFE_TOKEN_METADATA.fullmatch(normalized) is None
    return False


__all__ = ["forbidden_worker_authority_path"]
