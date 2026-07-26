from __future__ import annotations

from collections.abc import Mapping
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

    def __init__(
        self,
        *,
        request_id: str,
        attempt_service: Any | None = None,
        governed_runtime: Any | None = None,
    ) -> None:
        self._request_id = str(request_id or "emergent-growth")
        self._attempt_service = attempt_service
        self._governed_runtime = governed_runtime
        self.last_trace: dict[str, Any] = {}

    def __call__(self, prompt: str) -> str:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
            EMERGENT_GROWTH_PATTERN_PROMPT_ID,
        )
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        request = {
            "prompt_id": EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            "scene_type": "EMERGENT_GROWTH",
            "cognitive_tier": "B",
            "messages": ({"role": "user", "content": str(prompt or "")},),
            "deadline_ms": 5_000,
            "foreground": False,
            "metadata": {"chain": "emergent_growth_pattern"},
        }
        if self._governed_runtime is not None:
            result = self._governed_runtime.complete_sync(**request)
        else:
            result = complete_compiled_prompt_sync(
                attempt_service=self._attempt_service,
                request_id=self._request_id,
                **request,
            )
        self.last_trace = model_attempt_result_trace(result)
        return str(result.content or "").strip() if result.success else ""


def _resolve_llm_fn(args: dict[str, Any], context: dict[str, Any] | None) -> Callable[[str], str] | None:
    candidate = (context or {}).get("emergent_growth_llm_fn")
    if callable(candidate):
        return candidate
    governed = (context or {}).get("_governed_execution_context")
    if isinstance(governed, Mapping):
        from host_brain.model_runtime.governed_skill_budget import (  # noqa: PLC0415
            GovernedSkillModelBudgetRuntime,
        )

        governed_runtime = (context or {}).get(
            "_governed_skill_model_budget_runtime"
        )
        if not isinstance(governed_runtime, GovernedSkillModelBudgetRuntime):
            raise RuntimeError("GOVERNED_SKILL_MODEL_BUDGET_REQUIRED")
        user_id = _resolve_user_id(args, context)
        return _EmergentGrowthModelPort(
            request_id=str(
                (context or {}).get("request_id")
                or f"emergent_growth::{user_id}"
            ),
            governed_runtime=governed_runtime,
        )
    attempt_service = (context or {}).get("model_attempt_service")
    if attempt_service is None:
        return None
    user_id = _resolve_user_id(args, context)
    return _EmergentGrowthModelPort(
        request_id=str((context or {}).get("request_id") or f"emergent_growth::{user_id}"),
        attempt_service=attempt_service,
    )


def execute_emergent_growth_weekly(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_id = _resolve_user_id(args, context)
    try:
        lookback_days = max(1, int((args or {}).get("lookback_days") or 7))
    except (TypeError, ValueError):
        lookback_days = 7
    llm_fn = _resolve_llm_fn(args, context)
    prefrontal_agent = (context or {}).get("prefrontal_agent")
    episodic_store = (context or {}).get("episodic_store")
    if llm_fn is None or prefrontal_agent is None or episodic_store is None:
        return {
            "status": "SKIPPED",
            "reason_code": "EMERGENT_GROWTH_LLM_REQUIRED",
            "user_id": user_id,
            "lookback_days": lookback_days,
            "pattern_result": {},
            "growth_result": {},
        }

    result = dict(
        prefrontal_agent.run_emergent_growth_weekly(
            user_id=user_id,
            lookback_days=lookback_days,
            episodic_store=episodic_store,
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
