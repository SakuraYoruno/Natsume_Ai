"""Declarative helpers for action-oriented skills."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, Iterable

from host_brain.skills.registry import SkillMeta


NormalizeArgs = Callable[..., dict[str, Any]]


def _identity_normalizer(args: dict[str, Any]) -> dict[str, Any]:
    return dict(args or {})


def _normalize_context_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _is_empty_arg_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not bool(value)
    return False


def _merge_invocation_defaults(defaults: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults or {})
    for key, value in dict(args or {}).items():
        if key in merged and _is_empty_arg_value(value):
            continue
        merged[key] = value
    return merged


@dataclass(frozen=True)
class DeclarativeSkillSpec:
    skill_name: str
    clearance: str
    risk_level: int
    scope: str
    reversible: bool
    description: str
    tool: str
    action: str | None = None
    method_name: str | None = None
    require_confirm: bool = False
    normalize_args: NormalizeArgs = _identity_normalizer
    context_aliases: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    static_payload: dict[str, Any] = field(default_factory=dict)
    family: str = "general"
    capability_tags: tuple[str, ...] = field(default_factory=tuple)
    parallelizable: bool = False
    fallback_policy: str = "chat_fallback"
    scheduler_profile: dict[str, Any] = field(default_factory=dict)
    invocation_defaults: dict[str, Any] = field(default_factory=dict)

    def _normalize_args(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        try:
            signature = inspect.signature(self.normalize_args)
            if len(signature.parameters) >= 2:
                return self.normalize_args(args, ctx)
        except (TypeError, ValueError):
            pass
        return self.normalize_args(args)

    def build_handler(self) -> Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]:
        def _handler(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
            normalized_args = _merge_invocation_defaults(self.invocation_defaults, dict(args or {}))
            payload = {
                "status": "OK",
                "tool": self.tool,
            }
            if self.action is not None:
                payload["action"] = self.action
            payload.update(self._normalize_args(normalized_args, ctx))
            for source_key, target_key in self.context_aliases:
                payload[target_key] = _normalize_context_value((ctx or {}).get(source_key))
            payload.update(dict(self.static_payload))
            return payload

        _handler.__name__ = self.method_name or self.skill_name
        _handler.__qualname__ = _handler.__name__
        return _handler

    def to_meta(self) -> SkillMeta:
        return SkillMeta(
            skill_name=self.skill_name,
            clearance=self.clearance,
            risk_level=self.risk_level,
            scope=self.scope,
            reversible=self.reversible,
            require_confirm=self.require_confirm,
            description=self.description,
            handler=self.build_handler(),
            family=self.family,
            capability_tags=self.capability_tags,
            parallelizable=self.parallelizable,
            fallback_policy=self.fallback_policy,
            scheduler_profile=dict(self.scheduler_profile),
        )


class DeclarativeSkillProvider:
    """Base provider for data-driven action skills."""

    skill_specs: tuple[DeclarativeSkillSpec, ...] = ()

    def get_skill_specs(self) -> tuple[DeclarativeSkillSpec, ...]:
        return tuple(self.skill_specs)

    def __skill_meta_entries__(self) -> Iterable[SkillMeta]:
        return [spec.to_meta() for spec in self.get_skill_specs()]

    def __getattr__(self, name: str):
        for spec in self.get_skill_specs():
            method_name = spec.method_name or spec.skill_name
            if method_name == name:
                return spec.build_handler()
        raise AttributeError(name)
