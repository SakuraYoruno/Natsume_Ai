from __future__ import annotations

from typing import Any, Callable


def _resolve_user_id(args: dict[str, Any], context: dict[str, Any] | None) -> str:
    raw_user_id = str((args or {}).get("user_id") or (context or {}).get("user_id") or "").strip()
    return raw_user_id or "user_1"


def _resolve_llm_fn(args: dict[str, Any], context: dict[str, Any] | None) -> Callable[[str], str] | None:
    use_llm = bool((args or {}).get("use_llm"))
    if not use_llm:
        return None
    candidate = (context or {}).get("emergent_growth_llm_fn")
    if callable(candidate):
        return candidate
    return None


def execute_emergent_growth_weekly(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
    from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    try:
        lookback_days = max(1, int((args or {}).get("lookback_days") or 7))
    except (TypeError, ValueError):
        lookback_days = 7

    return dict(
        get_prefrontal_agent().run_emergent_growth_weekly(
            user_id=user_id,
            lookback_days=lookback_days,
            episodic_store=get_episodic_store(),
            llm_fn=_resolve_llm_fn(args, context),
        )
        or {
            "status": "SKIPPED",
            "user_id": user_id,
            "lookback_days": lookback_days,
            "pattern_result": {},
            "growth_result": {},
        }
    )


def execute_emergent_growth_status(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    return dict(
        get_prefrontal_agent().resolve_emergent_growth_status(user_id=user_id, recent_limit=5)
        or {
            "status": "OK",
            "user_id": user_id,
            "growth_summary": {},
            "recent_growth_log": [],
        }
    )
