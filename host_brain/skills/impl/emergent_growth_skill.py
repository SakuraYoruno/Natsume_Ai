from __future__ import annotations

from typing import Any, Callable

from host_brain.persona.emergent_growth_engine import EmergentGrowthEngine
from host_brain.persona.interaction_pattern_analyzer import InteractionPatternAnalyzer
from host_brain.persona.persona_engine import PersonaEngine

_GLOBAL_EMERGENT_GROWTH_ENGINE: EmergentGrowthEngine | None = None


def _get_emergent_growth_engine() -> EmergentGrowthEngine:
    global _GLOBAL_EMERGENT_GROWTH_ENGINE
    if _GLOBAL_EMERGENT_GROWTH_ENGINE is None:
        _GLOBAL_EMERGENT_GROWTH_ENGINE = EmergentGrowthEngine(
            persona_engine=PersonaEngine.get_instance(),
        )
    return _GLOBAL_EMERGENT_GROWTH_ENGINE


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

    user_id = _resolve_user_id(args, context)
    try:
        lookback_days = max(1, int((args or {}).get("lookback_days") or 7))
    except (TypeError, ValueError):
        lookback_days = 7

    persona_engine = PersonaEngine.get_instance()
    episodic_store = get_episodic_store()
    analyzer = InteractionPatternAnalyzer(llm_fn=_resolve_llm_fn(args, context))
    pattern_result = analyzer.analyze(
        user_id=user_id,
        episodic_store=episodic_store,
        current_traits=persona_engine.get_traits(user_id),
        lookback_days=lookback_days,
    )
    growth_result = _get_emergent_growth_engine().apply(
        user_id=user_id,
        pattern_result=pattern_result,
    )
    return {
        "status": "OK" if growth_result.applied else "SKIPPED",
        "user_id": user_id,
        "lookback_days": lookback_days,
        "pattern_result": pattern_result.as_dict(),
        "growth_result": growth_result.as_dict(),
    }


def execute_emergent_growth_status(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_id = _resolve_user_id(args, context)
    engine = _get_emergent_growth_engine()
    growth_log = [record.as_dict() for record in engine.get_growth_log(user_id)[-5:]]
    return {
        "status": "OK",
        "user_id": user_id,
        "growth_summary": engine.get_growth_summary(user_id),
        "recent_growth_log": growth_log,
    }
