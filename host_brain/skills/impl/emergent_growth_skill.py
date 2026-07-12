from __future__ import annotations

from typing import Any, Callable


def _resolve_user_id(args: dict[str, Any], context: dict[str, Any] | None) -> str:
    raw_user_id = str((args or {}).get("user_id") or (context or {}).get("user_id") or "").strip()
    if raw_user_id:
        from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

        return resolve_persona_self_id(raw_user_id)
    try:
        from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415

        return get_canonical_persona_self_id()
    except Exception:
        return "natsume_ai"


class _EmergentGrowthModelPort:
    canonical_model_attempt_port = True

    def __init__(self, *, request_id: str) -> None:
        self._request_id = str(request_id or "emergent-growth")
        self.last_trace: dict[str, Any] = {}

    def __call__(self, prompt: str) -> str:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
            EMERGENT_GROWTH_PATTERN_PROMPT_ID,
        )
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        result = complete_compiled_prompt_sync(
            prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            request_id=self._request_id,
            scene_type="EMERGENT_GROWTH",
            cognitive_tier="B",
            messages=({"role": "user", "content": str(prompt or "")},),
            deadline_ms=5_000,
            foreground=False,
            metadata={"chain": "emergent_growth_pattern"},
        )
        self.last_trace = model_attempt_result_trace(result)
        return str(result.content or "").strip() if result.success else ""


def _resolve_llm_fn(args: dict[str, Any], context: dict[str, Any] | None) -> Callable[[str], str] | None:
    candidate = (context or {}).get("emergent_growth_llm_fn")
    if isinstance(candidate, _EmergentGrowthModelPort):
        return candidate
    try:
        user_id = _resolve_user_id(args, context)
        return _EmergentGrowthModelPort(
            request_id=str((context or {}).get("request_id") or f"emergent_growth::{user_id}"),
        )
    except Exception:
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
    llm_fn = _resolve_llm_fn(args, context)
    if llm_fn is None:
        return {
            "status": "SKIPPED",
            "reason_code": "EMERGENT_GROWTH_LLM_REQUIRED",
            "user_id": user_id,
            "lookback_days": lookback_days,
            "pattern_result": {},
            "growth_result": {},
        }

    result = dict(
        get_prefrontal_agent().run_emergent_growth_weekly(
            user_id=user_id,
            lookback_days=lookback_days,
            episodic_store=get_episodic_store(),
            llm_fn=llm_fn,
        )
        or {
            "status": "SKIPPED",
            "user_id": user_id,
            "lookback_days": lookback_days,
            "pattern_result": {},
            "growth_result": {},
        }
    )
    attempt_trace = dict(getattr(llm_fn, "last_trace", {}) or {})
    if attempt_trace:
        result["model_attempt"] = attempt_trace
    return result


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
