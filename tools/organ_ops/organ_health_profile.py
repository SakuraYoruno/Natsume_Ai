"""Organ health profile primitives for Bio-Ops scans."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OrganHealthProfile:
    organ_id: str
    role: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    state_keys: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    health_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["OrganHealthProfile"]
