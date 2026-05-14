from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.persona.isolated_surface_harness import GenerateSurface
from host_brain.persona.surface_affect_control import SurfaceAffectControlPacket
from host_brain.persona.surface_memory_recall import MemoryRecallPacket
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
        "case_id",
        "max_facts",
        "max_must_not_do",
        "max_style_anchors",
        "memory_packet",
        "memory_recall_packet",
        "must_not_do",
        "necessary_facts",
        "soul_md",
        "style_anchor",
        "surface_affect_packet",
        "task_control",
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
    necessary_facts: Sequence[Any] = ()
    must_not_do: Sequence[Any] = ()
    style_anchor: Sequence[Any] = ()
    max_facts: int = 8
    max_style_anchors: int = 10
    max_must_not_do: int = 12

    def as_dict(self) -> dict[str, Any]:
        return {
            "soul_md": self.soul_md,
            "user_message": self.user_message,
            "case_id": self.case_id,
            "task_control": _safe_payload(self.task_control),
            "memory_packet": _safe_payload(self.memory_packet),
            "affect_packet": _safe_payload(self.affect_packet),
            "necessary_facts": list(self.necessary_facts),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
            "max_facts": int(self.max_facts),
            "max_style_anchors": int(self.max_style_anchors),
            "max_must_not_do": int(self.max_must_not_do),
        }


@dataclass(frozen=True)
class SurfaceMainChainResult:
    compiled: CompiledSurfaceInput
    runtime: SurfaceRuntimeResult

    @property
    def selected_text(self) -> str:
        return self.runtime.selected_text

    @property
    def observation_codes(self) -> tuple[str, ...]:
        return self.runtime.observation_codes

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface_main_chain": True,
            "selected_text": self.selected_text,
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
        repair_failed: bool = True,
        use_style_reference_bank: bool = True,
    ) -> None:
        self._runtime = SurfaceRuntime(
            generate,
            repair_failed=repair_failed,
            use_style_reference_bank=use_style_reference_bank,
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
        necessary_facts=_coerce_sequence(values.get("necessary_facts")),
        must_not_do=_coerce_sequence(values.get("must_not_do")),
        style_anchor=_coerce_sequence(values.get("style_anchor")),
        max_facts=_coerce_int(values.get("max_facts"), default=8),
        max_style_anchors=_coerce_int(values.get("max_style_anchors"), default=10),
        max_must_not_do=_coerce_int(values.get("max_must_not_do"), default=12),
    )


async def run_surface_main_chain(
    generate: GenerateSurface,
    payload: SurfaceMainChainInput | Mapping[str, Any],
    *,
    repair_failed: bool = True,
    use_style_reference_bank: bool = True,
) -> SurfaceMainChainResult:
    return await SurfaceMainChain(
        generate,
        repair_failed=repair_failed,
        use_style_reference_bank=use_style_reference_bank,
    ).run(payload)


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
