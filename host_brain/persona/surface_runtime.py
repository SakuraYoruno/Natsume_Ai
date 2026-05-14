import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.persona.isolated_surface_harness import (
    GenerateSurface,
    IsolatedCandidateScore,
    IsolatedSurfaceInput,
    build_isolated_surface_input,
    build_isolated_surface_messages,
    build_minimal_repair_messages,
    derive_style_reference_anchor,
    parse_surface_candidates,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)


_OBSERVATION_REASON_PREFIXES = (
    "AFFECT_PROCESS_LEAK",
    "AWKWARD_COLLOCATION",
    "BLAND_PASS_LINE",
    "BOUNDARY_REPAIR_",
    "CORE_DIMENSION_FLOOR",
    "CONTINUITY_THIN_GENERIC_REPLY",
    "CONTINUITY_TASK_PUSHBACK",
    "CURRENT_USER_TOPIC_MISMATCH",
    "CUSTOMER_SERVICE_TONE",
    "FORBIDDEN_TEMPLATE_TEXT",
    "MISPLACED_EMOTIONAL_CONTAINMENT",
    "MISPLACED_SUPPORT_TEMPLATE",
    "MORNING_GENERIC_BLESSING",
    "OBVIOUS_CUSTOMER_SERVICE_TEMPLATE",
    "OILY_OR_OVERBOUND_TONE",
    "PRESENCE_REPAIR_",
    "RAW_CONTEXT_BACKFILL_REQUEST",
    "RELATION_DISTANCE_OVERSTEP",
    "RELATION_LOVER_UNDERINTIMATE",
    "RELATION_NON_ROMANTIC_OVERSTEP",
    "SIGNATURE_TEXTURE_MISSING",
    "SURFACE_PROCESS_META_LANGUAGE",
    "THIN_DAILY_FOLLOWUP_REPLY",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
    "WAIT_SCENE_PRESSURES_RETURN",
    "WRITTEN_REGISTER_TONE",
)


@dataclass(frozen=True)
class SurfaceRuntimeResult:
    request: IsolatedSurfaceInput
    messages: tuple[Mapping[str, str], ...]
    raw_output: str
    candidates: tuple[IsolatedCandidateScore, ...]
    selected: IsolatedCandidateScore | None
    selected_text: str
    observation_codes: tuple[str, ...]
    score_trace: Mapping[str, Any]
    style_reference: Mapping[str, Any]
    repair_attempted: bool = False
    repair_raw_output: str = ""
    repair_candidates: tuple[str, ...] = ()
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_text": self.selected_text,
            "selected": self.selected.as_dict() if self.selected else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "observation_codes": list(self.observation_codes),
            "score_trace": dict(self.score_trace),
            "style_reference": dict(self.style_reference),
            "repair_attempted": bool(self.repair_attempted),
            "repair_raw_output": self.repair_raw_output,
            "repair_candidates": list(self.repair_candidates),
            "latency_ms": int(self.latency_ms),
        }


def surface_observation_codes(selected: IsolatedCandidateScore | Mapping[str, Any] | None) -> tuple[str, ...]:
    if not selected:
        return ("NO_SELECTED",)
    if isinstance(selected, IsolatedCandidateScore):
        selected_map = selected.as_dict()
    else:
        selected_map = dict(selected)

    codes: list[str] = []
    score = float(selected_map.get("score") or 0.0)
    if not selected_map.get("passed"):
        codes.append("SELECTED_FAILED")
    if selected_map.get("hard_fail"):
        codes.append("HARD_FAIL")
    if selected_map.get("control_violations"):
        codes.append("CONTROL_VIOLATION")
    if 0.0 < score < 85.0:
        codes.append("LOW_SCORE")
    for reason in selected_map.get("reason_codes") or []:
        reason_text = str(reason)
        if reason_text.startswith(_OBSERVATION_REASON_PREFIXES):
            codes.append(reason_text.split(":", 1)[0])
    return tuple(dict.fromkeys(codes))


def build_surface_score_trace(
    *,
    candidates: Sequence[IsolatedCandidateScore],
    selected: IsolatedCandidateScore | None,
    observation_codes: Sequence[str],
    repair_attempted: bool = False,
) -> dict[str, Any]:
    return {
        "selected": selected.as_dict() if selected else None,
        "candidates": [candidate.as_dict() for candidate in candidates],
        "observation_codes": list(observation_codes),
        "candidate_count": len(candidates),
        "repair_attempted": bool(repair_attempted),
    }


def _coerce_surface_request(payload: IsolatedSurfaceInput | Mapping[str, Any]) -> IsolatedSurfaceInput:
    if isinstance(payload, IsolatedSurfaceInput):
        return payload
    return build_isolated_surface_input(payload)


async def _maybe_await_text(value: str | Awaitable[str]) -> str:
    if inspect.isawaitable(value):
        value = await value
    return str(value or "").strip()


class SurfaceRuntime:
    def __init__(
        self,
        generate: GenerateSurface,
        *,
        repair_failed: bool = True,
        use_style_reference_bank: bool = True,
    ) -> None:
        self._generate = generate
        self._repair_failed = bool(repair_failed)
        self._use_style_reference_bank = bool(use_style_reference_bank)

    async def run(self, payload: IsolatedSurfaceInput | Mapping[str, Any]) -> SurfaceRuntimeResult:
        request = _coerce_surface_request(payload)
        started = time.perf_counter()
        style_reference = derive_style_reference_anchor(request).as_dict()
        messages = build_isolated_surface_messages(
            request,
            use_style_reference_bank=self._use_style_reference_bank,
        )
        raw_output = await _maybe_await_text(self._generate(messages))
        scores = list(score_isolated_surface_candidates(request, parse_surface_candidates(raw_output)))
        selected = select_best_isolated_candidate(scores)

        repair_attempted = bool(self._repair_failed and selected is not None and not selected.passed)
        repair_raw_output = ""
        repair_candidate_texts: tuple[str, ...] = ()
        if repair_attempted and selected is not None:
            repair_messages = build_minimal_repair_messages(
                request,
                selected,
                use_style_reference_bank=self._use_style_reference_bank,
            )
            repair_raw_output = await _maybe_await_text(self._generate(repair_messages))
            repair_candidate_texts = tuple(parse_surface_candidates(repair_raw_output))
            scores.extend(score_isolated_surface_candidates(request, repair_candidate_texts))
            selected = select_best_isolated_candidate(scores)

        observation_codes = surface_observation_codes(selected)
        score_trace = build_surface_score_trace(
            candidates=scores,
            selected=selected,
            observation_codes=observation_codes,
            repair_attempted=repair_attempted,
        )
        return SurfaceRuntimeResult(
            request=request,
            messages=tuple(messages),
            raw_output=raw_output,
            candidates=tuple(scores),
            selected=selected,
            selected_text=selected.text if selected else "",
            observation_codes=observation_codes,
            score_trace=score_trace,
            style_reference=style_reference,
            repair_attempted=repair_attempted,
            repair_raw_output=repair_raw_output,
            repair_candidates=repair_candidate_texts,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


__all__ = [
    "SurfaceRuntime",
    "SurfaceRuntimeResult",
    "build_surface_score_trace",
    "surface_observation_codes",
]
