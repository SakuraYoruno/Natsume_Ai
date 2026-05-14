from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACE_STYLE_SEED_BANK_PATH = ROOT / "data" / "style_bank" / "surface_style_seed_bank.json"


@dataclass(frozen=True)
class SurfaceStyleSeed:
    move_id: str
    response_moves: tuple[str, ...] = field(default_factory=tuple)
    task_modes: tuple[str, ...] = field(default_factory=tuple)
    evidence_cues: tuple[str, ...] = field(default_factory=tuple)
    source_refs: tuple[str, ...] = field(default_factory=tuple)
    source_digests: tuple[str, ...] = field(default_factory=tuple)
    derived_guidance: tuple[str, ...] = field(default_factory=tuple)
    avoid: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SurfaceStyleSeed":
        return cls(
            move_id=str(payload.get("move_id") or ""),
            response_moves=tuple(str(item) for item in payload.get("response_moves") or [] if str(item).strip()),
            task_modes=tuple(str(item) for item in payload.get("task_modes") or [] if str(item).strip()),
            evidence_cues=tuple(str(item) for item in payload.get("evidence_cues") or [] if str(item).strip()),
            source_refs=tuple(str(item) for item in payload.get("source_refs") or [] if str(item).strip()),
            source_digests=tuple(str(item) for item in payload.get("source_digests") or [] if str(item).strip()),
            derived_guidance=tuple(str(item) for item in payload.get("derived_guidance") or [] if str(item).strip()),
            avoid=tuple(str(item) for item in payload.get("avoid") or [] if str(item).strip()),
        )


@dataclass(frozen=True)
class SurfaceStyleSeedSelection:
    seeds: tuple[SurfaceStyleSeed, ...] = field(default_factory=tuple)
    degraded: bool = False
    reason: str = ""


class SurfaceStyleSeedBank:
    def __init__(self, seeds: Iterable[SurfaceStyleSeed] = ()) -> None:
        self.seeds: tuple[SurfaceStyleSeed, ...] = tuple(seed for seed in seeds if seed.move_id)

    def select(self, *, response_move: str, task_mode: str, limit: int = 3) -> SurfaceStyleSeedSelection:
        response = str(response_move or "").strip()
        task = str(task_mode or "").strip()
        if not self.seeds:
            return SurfaceStyleSeedSelection(degraded=True, reason="NO_SURFACE_STYLE_SEEDS")

        def score(seed: SurfaceStyleSeed) -> int:
            value = 0
            if response and response in seed.response_moves:
                value += 5
            if task and task in seed.task_modes:
                value += 3
            if response and any(response.startswith(item) or item.startswith(response) for item in seed.response_moves):
                value += 1
            if task and any(task.startswith(item) or item.startswith(task) for item in seed.task_modes):
                value += 1
            return value

        ranked = sorted(self.seeds, key=lambda seed: (score(seed), seed.move_id), reverse=True)
        selected = tuple(seed for seed in ranked if score(seed) > 0)[: max(1, int(limit or 3))]
        if selected:
            return SurfaceStyleSeedSelection(seeds=selected)
        return SurfaceStyleSeedSelection(seeds=tuple(ranked[: max(1, int(limit or 3))]), degraded=True, reason="FALLBACK_STYLE_SEEDS")


def load_surface_style_seed_bank(path: str | os.PathLike[str] | None = None) -> SurfaceStyleSeedBank:
    bank_path = Path(path or os.environ.get("HOST_SURFACE_STYLE_SEED_BANK_PATH") or DEFAULT_SURFACE_STYLE_SEED_BANK_PATH)
    if not bank_path.exists():
        return SurfaceStyleSeedBank()
    payload = json.loads(bank_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        return SurfaceStyleSeedBank()
    seeds = [SurfaceStyleSeed.from_dict(item) for item in payload.get("moves") or [] if isinstance(item, Mapping)]
    return SurfaceStyleSeedBank(seeds)


_BANK_CACHE: dict[str, SurfaceStyleSeedBank] = {}


def get_surface_style_seed_bank(path: str | os.PathLike[str] | None = None) -> SurfaceStyleSeedBank:
    bank_path = Path(path or os.environ.get("HOST_SURFACE_STYLE_SEED_BANK_PATH") or DEFAULT_SURFACE_STYLE_SEED_BANK_PATH)
    key = str(bank_path.resolve())
    cached = _BANK_CACHE.get(key)
    if cached is None:
        cached = load_surface_style_seed_bank(bank_path)
        _BANK_CACHE[key] = cached
    return cached


def clear_surface_style_seed_bank_cache() -> None:
    _BANK_CACHE.clear()


def render_surface_style_seed_guidance(
    *,
    response_move: str,
    task_mode: str,
    limit: int = 3,
) -> str:
    selection = get_surface_style_seed_bank().select(response_move=response_move, task_mode=task_mode, limit=limit)
    if not selection.seeds:
        return ""
    lines = [
        "## 真实中文台词派生风格种子",
        "这些种子只提供句法动作，禁止照抄、复述或改写原台词；当前内容必须仍然来自本轮用户输入和 ReplyControlPacket。",
    ]
    for seed in selection.seeds:
        guidance = "；".join(seed.derived_guidance[:2])
        avoid = " / ".join(seed.avoid[:3])
        lines.append(f"- {seed.move_id}: {guidance} 避免：{avoid}。")
    if selection.degraded and selection.reason:
        lines.append(f"- degraded_reason={selection.reason}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SURFACE_STYLE_SEED_BANK_PATH",
    "SurfaceStyleSeed",
    "SurfaceStyleSeedBank",
    "SurfaceStyleSeedSelection",
    "clear_surface_style_seed_bank_cache",
    "get_surface_style_seed_bank",
    "load_surface_style_seed_bank",
    "render_surface_style_seed_guidance",
]
