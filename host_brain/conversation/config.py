from __future__ import annotations

import os


def _parse_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def conversation_max_turns() -> int:
    return _parse_int_env("HOST_CONVERSATION_MAX_TURNS", default=10, minimum=0, maximum=64)


def conversation_summary_threshold() -> int:
    return _parse_int_env("HOST_CONVERSATION_SUMMARY_THRESHOLD", default=20, minimum=0, maximum=200)


def conversation_summary_max_chars() -> int:
    return _parse_int_env("HOST_CONVERSATION_SUMMARY_MAX_CHARS", default=220, minimum=48, maximum=1000)
