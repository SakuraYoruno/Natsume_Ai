"""Text and numeric utility functions for the executive runtime.

All functions in this module are pure helpers with no side effects
beyond the explicit exception-raise paths.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from typing import Any

from host_brain.app.executive.constants import (
    _SKILL_EXEC_FAIL_CODES,
    _SKILL_EXEC_FAIL_STATUS,
    _SKILL_EXEC_NO_EFFECT_MARKERS,
)


def _parse_bool_env(*, name: str, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(*, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _run_awaitable_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error_box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, name="memory-skill-awaitable", daemon=True)
    thread.start()
    done.wait()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _iter_candidate_keys() -> tuple[str, ...]:
    return (
        "route",
        "target_id",
        "action",
    )


def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _extract_result_code(error_text: str | None) -> str | None:
    text = str(error_text or "").strip()
    if not text:
        return None
    match = re.match(r"([A-Z0-9_]+)\s*:", text)
    if match:
        return match.group(1).strip()
    if text.isupper() and " " not in text:
        return text
    return None


def _is_effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False
    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False
    digest = f"{str(raw_result or '') } {str(result_text or '') }".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True
