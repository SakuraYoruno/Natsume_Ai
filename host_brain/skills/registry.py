"""Skill registry with enforced metadata contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SkillHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]

ALLOWED_CLEARANCE = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_SCOPE = {"host", "memory", "system"}


@dataclass(frozen=True)
class SkillPermissionModel:
    clearance: str
    scope: str
    require_confirm: bool


@dataclass(frozen=True)
class SkillResult:
    status: str
    result_code: str
    payload: dict[str, Any]
    detail: str | None = None


@dataclass(frozen=True)
class SkillMeta:
    skill_name: str
    clearance: str
    risk_level: int
    scope: str
    reversible: bool
    handler: SkillHandler
    require_confirm: bool = False
    description: str = ""

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
        if self.description and not isinstance(self.description, str):
            raise ValueError("description must be string")

    @property
    def permission_model(self) -> SkillPermissionModel:
        return SkillPermissionModel(
            clearance=self.clearance,
            scope=self.scope,
            require_confirm=self.require_confirm,
        )


def skill(
    *,
    skill_name: str | None = None,
    clearance: str = "LOW",
    risk_level: int = 1,
    scope: str = "host",
    reversible: bool = True,
    require_confirm: bool = False,
    description: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach SkillMeta config on handler callables."""

    def _decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            handler,
            "__skill_meta_config__",
            {
                "skill_name": str(skill_name or handler.__name__).strip(),
                "clearance": str(clearance).strip().upper(),
                "risk_level": int(risk_level),
                "scope": str(scope).strip().lower(),
                "reversible": bool(reversible),
                "require_confirm": bool(require_confirm),
                "description": str(description or "").strip(),
            },
        )
        return handler

    return _decorator


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

    def register_decorated(self, provider: Any) -> int:
        """Scan provider object and register all `@skill` handlers."""
        count = 0
        for attr_name in sorted(dir(provider)):
            candidate = getattr(provider, attr_name)
            cfg = getattr(candidate, "__skill_meta_config__", None)
            if not isinstance(cfg, dict):
                continue
            meta = SkillMeta(
                skill_name=str(cfg.get("skill_name") or attr_name).strip(),
                clearance=str(cfg.get("clearance") or "LOW").strip().upper(),
                risk_level=int(cfg.get("risk_level") or 1),
                scope=str(cfg.get("scope") or "host").strip().lower(),
                reversible=bool(cfg.get("reversible", True)),
                handler=candidate,  # bound method or function
                require_confirm=bool(cfg.get("require_confirm", False)),
                description=str(cfg.get("description") or "").strip(),
            )
            self.register(meta)
            count += 1
        return count
