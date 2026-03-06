"""Skill registry with enforced metadata contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SkillHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]

ALLOWED_CLEARANCE = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_SCOPE = {"host", "memory", "system"}


@dataclass(frozen=True)
class SkillMeta:
    skill_name: str
    clearance: str
    risk_level: int
    scope: str
    reversible: bool
    handler: SkillHandler
    require_confirm: bool = False

    def validate(self) -> None:
        if not self.skill_name or not isinstance(self.skill_name, str):
            raise ValueError("skill_name must be non-empty string")
        if self.clearance not in ALLOWED_CLEARANCE:
            raise ValueError(f"clearance must be one of {sorted(ALLOWED_CLEARANCE)}")
        if not isinstance(self.risk_level, int) or self.risk_level < 1 or self.risk_level > 5:
            raise ValueError("risk_level must be int in [1,5]")
        if self.scope not in ALLOWED_SCOPE:
            raise ValueError(f"scope must be one of {sorted(ALLOWED_SCOPE)}")
        if not isinstance(self.reversible, bool):
            raise ValueError("reversible must be bool")
        if not callable(self.handler):
            raise ValueError("handler must be callable")
        if self.risk_level >= 4 and not self.require_confirm:
            raise ValueError("high-risk skill must set require_confirm=True")


class SkillRegistry:
    """Single source of truth for skill metadata."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillMeta] = {}

    def register(self, meta: SkillMeta) -> None:
        meta.validate()
        if meta.skill_name in self._skills:
            raise ValueError(f"skill already registered: {meta.skill_name}")
        self._skills[meta.skill_name] = meta

    def get(self, skill_name: str) -> SkillMeta | None:
        return self._skills.get(skill_name)

    def require(self, skill_name: str) -> SkillMeta:
        meta = self.get(skill_name)
        if meta is None:
            raise KeyError(f"skill not found: {skill_name}")
        return meta

    def all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def list_skill_names(self) -> list[str]:
        """返回所有已注册 Skill 的名称列表（M42：用于动态 action 白名单）。"""
        return list(self._skills.keys())
