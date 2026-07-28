"""Fail-closed Host resolver for lifecycle-managed Skill versions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol

from host_brain.skills.registry import (
    SkillMeta,
    SkillRegistry,
    SkillVersionBinding,
)


class ActiveSkillVersionProvider(Protocol):
    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> Mapping[str, Any] | None: ...

    def list_active_versions(self) -> list[Mapping[str, Any]]: ...


class LifecycleSkillRegistryError(RuntimeError):
    """A durable active projection cannot be mapped to a frozen adapter."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code
            if not self.detail
            else f"{self.reason_code}:{self.detail}"
        )


class LifecycleBoundSkillRegistry:
    """Overlay approved content-addressed versions on trusted built-ins.

    The only currently supported runtime adapter aliases an already-composed
    built-in handler.  No bundle path is imported or scanned at runtime.
    """

    _ADAPTER_PROTOCOLS = {
        "builtin-skill-alias.v1": "v1",
    }

    def __init__(
        self,
        *,
        base_registry: SkillRegistry,
        active_versions: ActiveSkillVersionProvider,
    ) -> None:
        if not isinstance(base_registry, SkillRegistry):
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_BASE_REGISTRY_INVALID"
            )
        if not callable(getattr(active_versions, "resolve_active", None)):
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_ACTIVE_VERSION_PROVIDER_INVALID"
            )
        if not callable(getattr(active_versions, "list_active_versions", None)):
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_ACTIVE_VERSION_PROVIDER_INVALID"
            )
        self._base = base_registry
        self._active_versions = active_versions
        self.validate_active_projections()

    def validate_active_projections(self) -> None:
        for raw in self._active_versions.list_active_versions():
            if str(raw.get("bundle_kind") or "").upper() != "SKILL":
                continue
            name = str(raw.get("bundle_name") or "").strip()
            base = self._base.get(name)
            if base is None:
                raise LifecycleSkillRegistryError(
                    "SKILL_RUNTIME_ACTIVE_HANDLER_MISSING",
                    name,
                )
            self._bind(base, raw)

    def register(self, meta: SkillMeta) -> None:
        self._base.register(meta)

    def unregister(self, skill_name: str) -> bool:
        return self._base.unregister(skill_name)

    def register_decorated(self, provider: Any) -> int:
        return self._base.register_decorated(provider)

    def get(self, skill_name: str) -> SkillMeta | None:
        name = str(skill_name or "").strip()
        if not name:
            return None
        base = self._base.get(name)
        projection = self._active_versions.resolve_active(
            bundle_kind="SKILL",
            bundle_name=name,
        )
        if projection is None:
            return base
        if base is None:
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_ACTIVE_HANDLER_MISSING",
                name,
            )
        return self._bind(base, projection)

    def require(self, skill_name: str) -> SkillMeta:
        meta = self.get(skill_name)
        if meta is None:
            raise KeyError(f"skill not found: {skill_name}")
        return meta

    def all(self) -> list[SkillMeta]:
        return [
            self.require(name)
            for name in self._base.list_skill_names()
        ]

    def list_skill_names(self) -> list[str]:
        return self._base.list_skill_names()

    def build_skill_manifest(
        self,
        *,
        scopes: list[str] | None = None,
        max_risk_level: int = 5,
        exclude_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = set(exclude_names or ())
        result: list[dict[str, Any]] = []
        for meta in sorted(self.all(), key=lambda item: item.skill_name):
            if meta.skill_name in excluded:
                continue
            if not meta.governed_execution_enabled:
                continue
            if scopes is not None and meta.scope not in scopes:
                continue
            if meta.risk_level > int(max_risk_level):
                continue
            result.append(meta.to_manifest_entry())
        return result

    @classmethod
    def _bind(
        cls,
        meta: SkillMeta,
        projection: Mapping[str, Any],
    ) -> SkillMeta:
        adapter = str(
            projection.get("runtime_adapter_id") or ""
        ).strip()
        protocol = str(
            projection.get("runtime_protocol_version") or ""
        ).strip()
        expected_protocol = cls._ADAPTER_PROTOCOLS.get(adapter)
        if expected_protocol is None:
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_ADAPTER_NOT_APPROVED",
                adapter,
            )
        if protocol != expected_protocol:
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_PROTOCOL_NOT_APPROVED",
                f"{adapter}:{protocol}",
            )
        binding = SkillVersionBinding.from_mapping(projection)
        maturity_ceiling = {1: 5, 2: 5, 3: 4, 4: 2, 5: 1}[
            int(meta.risk_level)
        ]
        if binding.maturity_level > maturity_ceiling:
            raise LifecycleSkillRegistryError(
                "SKILL_RUNTIME_MATURITY_EXCEEDS_RISK_CEILING",
                f"{meta.skill_name}:L{binding.maturity_level}>L{maturity_ceiling}",
            )
        return replace(
            meta,
            version_binding=binding,
        )


__all__ = [
    "ActiveSkillVersionProvider",
    "LifecycleBoundSkillRegistry",
    "LifecycleSkillRegistryError",
]
