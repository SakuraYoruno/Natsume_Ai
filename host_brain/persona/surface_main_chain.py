from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.persona.isolated_surface_harness import GenerateSurface
from host_brain.persona.surface_affect_control import SurfaceAffectControlPacket
from host_brain.persona.surface_memory_recall import MemoryRecallPacket
from host_brain.persona.surface_influence_packet import SurfaceInfluencePacket
from host_brain.persona.surface_packet_compiler import (
    CompiledSurfaceInput,
    SurfaceTaskControl,
    compile_surface_input,
)
from host_brain.persona.surface_runtime import SurfaceRuntime, SurfaceRuntimeResult


_ALLOWED_INPUT_KEYS = frozenset(
    {
        "affect_control_packet",
        "affect_packet",
        "body_state_hint",
        "care_signal",
        "case_id",
        "continuity_signal",
        "inner_weather",
        "liveliness",
        "max_facts",
        "max_must_not_do",
        "max_style_anchors",
        "active_loops",
        "accepted_task_results",
        "influence_packet",
        "memory_packet",
        "memory_recall_packet",
        "must_not_do",
        "necessary_facts",
        "soul_md",
        "style_anchor",
        "surface_affect_packet",
        "surface_influence_packet",
        "task_control",
        "tension_hint",
        "user_message",
    }
)
_LOW_LEVEL_BYPASS_KEYS = frozenset(
    {
        "control",
        "facts",
        "reply_control_packet",
    }
)


@dataclass(frozen=True)
class SurfaceMainChainInput:
    soul_md: str
    user_message: str
    case_id: str = ""
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None
    influence_packet: SurfaceInfluencePacket | Mapping[str, Any] | None = None
    necessary_facts: Sequence[Any] = ()
    must_not_do: Sequence[Any] = ()
    style_anchor: Sequence[Any] = ()
    accepted_task_results: Sequence[Any] = ()
    max_facts: int = 8
    max_style_anchors: int = 10
    max_must_not_do: int = 12
    # --- Inner-state leakage fields (life-feel renovation A5) ---
    inner_weather: str = ""
    body_state_hint: str = ""
    care_signal: float = 0.0
    continuity_signal: float = 0.0
    tension_hint: str = ""
    liveliness: float = 0.5
    active_loops: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "soul_md": self.soul_md,
            "user_message": self.user_message,
            "case_id": self.case_id,
            "task_control": _safe_payload(self.task_control),
            "memory_packet": _safe_payload(self.memory_packet),
            "affect_packet": _safe_payload(self.affect_packet),
            "surface_influence_packet": _safe_payload(self.influence_packet),
            "necessary_facts": list(self.necessary_facts),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
            "accepted_task_results": list(self.accepted_task_results),
            "max_facts": int(self.max_facts),
            "max_style_anchors": int(self.max_style_anchors),
            "max_must_not_do": int(self.max_must_not_do),
            # --- Inner-state leakage fields ---
            "inner_weather": self.inner_weather,
            "body_state_hint": self.body_state_hint,
            "care_signal": float(self.care_signal),
            "continuity_signal": float(self.continuity_signal),
            "tension_hint": self.tension_hint,
            "liveliness": float(self.liveliness),
            "active_loops": int(self.active_loops),
        }


@dataclass(frozen=True)
class SurfaceMainChainResult:
    compiled: CompiledSurfaceInput
    runtime: SurfaceRuntimeResult

    @property
    def selected_text(self) -> str:
        return self.runtime.selected_text

    @property
    def tts_text_ja(self) -> str:
        return self.runtime.tts_text_ja

    @property
    def observation_codes(self) -> tuple[str, ...]:
        return self.runtime.observation_codes

    @property
    def natsume_emotion_tag(self) -> str:
        return self.runtime.natsume_emotion_tag

    @property
    def user_emotion_tag(self) -> str:
        return self.runtime.user_emotion_tag

    @property
    def vts_expression_id(self) -> str:
        return self.runtime.vts_expression_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface_main_chain": True,
            "selected_text": self.selected_text,
            "tts_text_ja": self.tts_text_ja,
            "natsume_emotion_tag": self.natsume_emotion_tag,
            "user_emotion_tag": self.user_emotion_tag,
            "vts_expression_id": self.vts_expression_id,
            "emotion_confidence": float(self.runtime.emotion_confidence or 0.0),
            "observation_codes": list(self.observation_codes),
            "packet_trace": dict(self.compiled.trace),
            "compiled": self.compiled.as_dict(),
            "runtime": self.runtime.as_dict(),
        }


class SurfaceMainChain:
    """Narrow runtime facade for the current surface main chain."""

    def __init__(
        self,
        generate: GenerateSurface,
        *,
        model_retries_owned_externally: bool = False,
    ) -> None:
        self._runtime = SurfaceRuntime(
            generate,
            model_retries_owned_externally=model_retries_owned_externally,
        )

    async def run(self, payload: SurfaceMainChainInput | Mapping[str, Any]) -> SurfaceMainChainResult:
        compiled = compile_surface_main_chain_input(payload)
        runtime_result = await self._runtime.run(compiled.request)
        return SurfaceMainChainResult(compiled=compiled, runtime=runtime_result)


def compile_surface_main_chain_input(payload: SurfaceMainChainInput | Mapping[str, Any]) -> CompiledSurfaceInput:
    values = _coerce_surface_main_chain_payload(payload)
    return compile_surface_input(
        soul_md=_clean_text(values.get("soul_md")),
        user_message=_clean_text(values.get("user_message")),
        case_id=_clean_text(values.get("case_id")),
        task_control=values.get("task_control"),
        memory_packet=values.get("memory_packet") or values.get("memory_recall_packet"),
        affect_packet=values.get("affect_packet") or values.get("affect_control_packet") or values.get("surface_affect_packet"),
        influence_packet=values.get("surface_influence_packet") or values.get("influence_packet"),
        necessary_facts=_coerce_sequence(values.get("necessary_facts")),
        must_not_do=_coerce_sequence(values.get("must_not_do")),
        style_anchor=_coerce_sequence(values.get("style_anchor")),
        accepted_task_results=_coerce_sequence(
            values.get("accepted_task_results")
        ),
        max_facts=_coerce_int(values.get("max_facts"), default=8),
        max_style_anchors=_coerce_int(values.get("max_style_anchors"), default=10),
        max_must_not_do=_coerce_int(values.get("max_must_not_do"), default=12),
        # --- Inner-state leakage pass-through ---
        inner_weather=_clean_text(values.get("inner_weather")),
        body_state_hint=_clean_text(values.get("body_state_hint")),
        care_signal=float(values.get("care_signal") or 0.0),
        continuity_signal=float(values.get("continuity_signal") or 0.0),
        tension_hint=_clean_text(values.get("tension_hint")),
        liveliness=float(values.get("liveliness") or 0.5),
        active_loops=int(values.get("active_loops") or 0),
    )


async def run_surface_main_chain(
    generate: GenerateSurface,
    payload: SurfaceMainChainInput | Mapping[str, Any],
) -> SurfaceMainChainResult:
    return await SurfaceMainChain(generate).run(payload)


def _coerce_surface_main_chain_payload(payload: SurfaceMainChainInput | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, SurfaceMainChainInput):
        values = payload.as_dict()
    elif isinstance(payload, Mapping):
        values = dict(payload)
    else:
        raise ValueError("surface main chain input must be a mapping or SurfaceMainChainInput")
    unexpected = sorted(str(key) for key in values if str(key) not in _ALLOWED_INPUT_KEYS)
    bypass = [key for key in unexpected if key in _LOW_LEVEL_BYPASS_KEYS]
    if bypass:
        raise ValueError("surface main chain facade does not accept low-level fields: " + ", ".join(bypass))
    if unexpected:
        raise ValueError("surface main chain input contains unexpected fields: " + ", ".join(unexpected[:8]))
    if not _clean_text(values.get("soul_md")):
        raise ValueError("surface main chain input requires soul_md")
    if not _clean_text(values.get("user_message")):
        raise ValueError("surface main chain input requires user_message")
    return values


def _safe_payload(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "as_dict"):
        return value.as_dict()  # type: ignore[no-any-return]
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "SurfaceMainChain",
    "SurfaceMainChainInput",
    "SurfaceMainChainResult",
    "compile_surface_main_chain_input",
    "run_surface_main_chain",
]
