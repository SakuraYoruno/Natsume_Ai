from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH = (
    ROOT / "data" / "style_bank" / "continuity_surface_generation_bank.json"
)


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _clean_list(raw: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in (_clean_text(value) for value in raw) if item))


@dataclass(frozen=True)
class ContinuityRewriteSample:
    bad: str
    good: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContinuityRewriteSample":
        return cls(bad=_clean_text(payload.get("bad")), good=_clean_text(payload.get("good")))


@dataclass(frozen=True)
class ContinuityMoveSample:
    sample_id: str
    response_moves: tuple[str, ...] = field(default_factory=tuple)
    task_modes: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContinuityMoveSample":
        return cls(
            sample_id=_clean_text(payload.get("sample_id")),
            response_moves=_clean_list(payload.get("response_moves") or ()),
            task_modes=_clean_list(payload.get("task_modes") or ()),
            examples=_clean_list(payload.get("examples") or ()),
        )


@dataclass(frozen=True)
class ContinuitySurfaceSampleSelection:
    protocol_lines: tuple[str, ...] = field(default_factory=tuple)
    term_map: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    rewrite_samples: tuple[ContinuityRewriteSample, ...] = field(default_factory=tuple)
    move_samples: tuple[ContinuityMoveSample, ...] = field(default_factory=tuple)
    degraded: bool = False
    reason: str = ""


class ContinuitySurfaceSampleBank:
    def __init__(
        self,
        *,
        protocol_lines: Iterable[str] = (),
        term_map: Iterable[tuple[str, str]] = (),
        rewrite_samples: Iterable[ContinuityRewriteSample] = (),
        move_samples: Iterable[ContinuityMoveSample] = (),
    ) -> None:
        self.protocol_lines = _clean_list(protocol_lines)
        self.term_map = tuple((internal, surface) for internal, surface in term_map if internal and surface)
        self.rewrite_samples = tuple(sample for sample in rewrite_samples if sample.bad and sample.good)
        self.move_samples = tuple(sample for sample in move_samples if sample.sample_id and sample.examples)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContinuitySurfaceSampleBank":
        term_map: list[tuple[str, str]] = []
        for item in payload.get("term_map") or []:
            if not isinstance(item, Mapping):
                continue
            internal = _clean_text(item.get("internal"))
            surface = _clean_text(item.get("surface"))
            if internal and surface:
                term_map.append((internal, surface))
        return cls(
            protocol_lines=_clean_list(payload.get("protocol_lines") or ()),
            term_map=term_map,
            rewrite_samples=tuple(
                ContinuityRewriteSample.from_dict(item)
                for item in payload.get("rewrite_samples") or ()
                if isinstance(item, Mapping)
            ),
            move_samples=tuple(
                ContinuityMoveSample.from_dict(item)
                for item in payload.get("move_samples") or ()
                if isinstance(item, Mapping)
            ),
        )

    def select(
        self,
        *,
        response_move: str,
        task_mode: str,
        limit: int = 2,
    ) -> ContinuitySurfaceSampleSelection:
        response = _clean_text(response_move).lower()
        task = _clean_text(task_mode).lower()
        if not self.move_samples:
            return ContinuitySurfaceSampleSelection(
                protocol_lines=self.protocol_lines,
                term_map=self.term_map,
                rewrite_samples=self.rewrite_samples,
                degraded=True,
                reason="NO_CONTINUITY_SURFACE_SAMPLES",
            )

        def score(sample: ContinuityMoveSample) -> int:
            value = 0
            response_moves = {item.lower() for item in sample.response_moves}
            task_modes = {item.lower() for item in sample.task_modes}
            if response and response in response_moves:
                value += 6
            if task and task in task_modes:
                value += 4
            if response and any(response.startswith(item) or item.startswith(response) for item in response_moves):
                value += 1
            if task and any(task.startswith(item) or item.startswith(task) for item in task_modes):
                value += 1
            if sample.sample_id == "default_continuity":
                value += 1
            return value

        ranked = sorted(self.move_samples, key=lambda sample: (score(sample), sample.sample_id), reverse=True)
        selected = tuple(sample for sample in ranked if score(sample) > 1)[: max(1, int(limit or 2))]
        if selected:
            return ContinuitySurfaceSampleSelection(
                protocol_lines=self.protocol_lines,
                term_map=self.term_map,
                rewrite_samples=self.rewrite_samples,
                move_samples=selected,
            )
        fallback = tuple(sample for sample in ranked if sample.sample_id == "default_continuity") or tuple(ranked[:1])
        return ContinuitySurfaceSampleSelection(
            protocol_lines=self.protocol_lines,
            term_map=self.term_map,
            rewrite_samples=self.rewrite_samples,
            move_samples=fallback,
            degraded=True,
            reason="FALLBACK_CONTINUITY_SURFACE_SAMPLES",
        )


def load_continuity_surface_sample_bank(
    path: str | os.PathLike[str] | None = None,
) -> ContinuitySurfaceSampleBank:
    bank_path = Path(
        path
        or os.environ.get("HOST_CONTINUITY_SURFACE_SAMPLE_BANK_PATH")
        or DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH
    )
    if not bank_path.exists():
        return ContinuitySurfaceSampleBank()
    payload = json.loads(bank_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        return ContinuitySurfaceSampleBank()
    return ContinuitySurfaceSampleBank.from_dict(payload)


_BANK_CACHE: dict[str, ContinuitySurfaceSampleBank] = {}


def get_continuity_surface_sample_bank(
    path: str | os.PathLike[str] | None = None,
) -> ContinuitySurfaceSampleBank:
    bank_path = Path(
        path
        or os.environ.get("HOST_CONTINUITY_SURFACE_SAMPLE_BANK_PATH")
        or DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH
    )
    key = str(bank_path.resolve())
    cached = _BANK_CACHE.get(key)
    if cached is None:
        cached = load_continuity_surface_sample_bank(bank_path)
        _BANK_CACHE[key] = cached
    return cached


def clear_continuity_surface_sample_bank_cache() -> None:
    _BANK_CACHE.clear()


def render_continuity_surface_generation_guidance(
    *,
    response_move: str,
    task_mode: str,
    limit: int = 2,
) -> str:
    selection = get_continuity_surface_sample_bank().select(
        response_move=response_move,
        task_mode=task_mode,
        limit=limit,
    )
    if not selection.protocol_lines and not selection.move_samples and not selection.rewrite_samples:
        return ""
    lines = ["## 连续链表层生成协议"]
    lines.extend(selection.protocol_lines)
    if selection.term_map:
        mapped = "；".join(f"{internal}={surface}" for internal, surface in selection.term_map[:8])
        lines.append("内部词先换成人话：" + mapped + "。")
    if selection.rewrite_samples:
        lines.append("坏壳改写示例，学习改写方向，禁止照抄：")
        for sample in selection.rewrite_samples[:24]:
            lines.append(f"- {sample.bad} -> {sample.good}")
    examples: list[str] = []
    for sample in selection.move_samples:
        examples.extend(sample.examples)
    examples = list(dict.fromkeys(examples))
    if examples:
        lines.append("可用句法样本，学习结构，禁止逐字照抄：")
        for example in examples[:4]:
            lines.append(f"- {example}")
    if selection.degraded and selection.reason:
        lines.append(f"- degraded_reason={selection.reason}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH",
    "ContinuityMoveSample",
    "ContinuityRewriteSample",
    "ContinuitySurfaceSampleBank",
    "ContinuitySurfaceSampleSelection",
    "clear_continuity_surface_sample_bank_cache",
    "get_continuity_surface_sample_bank",
    "load_continuity_surface_sample_bank",
    "render_continuity_surface_generation_guidance",
]
