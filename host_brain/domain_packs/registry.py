"""Versioned enable/disable/rollback registry for declarative Domain Packs."""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time
from typing import Any, Mapping

from host_brain.capability_plane.contracts import (
    CapabilityGrant,
    CapabilityKind,
    CapabilityRequest,
    GrantDecision,
)

from .contracts import DomainLifecycleStageManifest, DomainPack, DomainPackRequest
from .ports import DomainPackPlatformPorts, DomainTaskPort


class DomainPackResolutionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = str(reason_code)


@dataclass(frozen=True, slots=True)
class DomainPackResolution:
    pack_id: str
    version: str
    capability_id: str
    handler_id: str
    handler: Any
    allowed_task_ids: tuple[str, ...]
    lifecycle_stages: tuple[DomainLifecycleStageManifest, ...]


@dataclass(frozen=True, slots=True)
class _ScopedDomainTaskPort(DomainTaskPort):
    delegate: DomainTaskPort
    pack_id: str
    capability_id: str
    allowed_task_ids: frozenset[str]

    def execute(self, task_id: str, payload):  # noqa: ANN001, ANN201
        normalized = str(task_id or "").strip()
        if normalized not in self.allowed_task_ids:
            raise DomainPackResolutionError(
                "DOMAIN_TASK_NOT_ALLOWED",
                f"{self.pack_id}:{self.capability_id} may not execute task {normalized}",
            )
        return self.delegate.execute(normalized, payload)


class DomainPackRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, dict[str, DomainPack]] = {}
        self._active: dict[str, str | None] = {}
        self._lock = threading.RLock()

    def install(self, pack: DomainPack, *, activate: bool | None = None) -> None:
        if not isinstance(pack, DomainPack):
            raise ValueError("pack must be DomainPack")
        manifest = pack.manifest
        with self._lock:
            versions = self._versions.setdefault(manifest.pack_id, {})
            if manifest.version in versions:
                raise ValueError(f"domain pack version already installed: {manifest.pack_id}@{manifest.version}")
            versions[manifest.version] = pack
            should_activate = manifest.enabled_by_default if activate is None else bool(activate)
            if should_activate:
                self._assert_no_active_capability_collision(pack, replacing_pack=manifest.pack_id)
                self._active[manifest.pack_id] = manifest.version
            else:
                self._active.setdefault(manifest.pack_id, None)

    def enable(self, pack_id: str, *, version: str | None = None) -> None:
        with self._lock:
            versions = self._versions.get(pack_id)
            if not versions:
                raise DomainPackResolutionError("DOMAIN_PACK_UNKNOWN", f"unknown pack: {pack_id}")
            selected = version or max(versions, key=lambda item: versions[item].manifest.version_key)
            pack = versions.get(selected)
            if pack is None:
                raise DomainPackResolutionError("DOMAIN_PACK_VERSION_UNKNOWN", f"unknown pack version: {pack_id}@{selected}")
            self._assert_no_active_capability_collision(pack, replacing_pack=pack_id)
            self._active[pack_id] = selected

    def disable(self, pack_id: str) -> None:
        with self._lock:
            if pack_id not in self._versions:
                raise DomainPackResolutionError("DOMAIN_PACK_UNKNOWN", f"unknown pack: {pack_id}")
            self._active[pack_id] = None

    def rollback(self, pack_id: str, *, to_version: str | None = None) -> str:
        with self._lock:
            versions = self._versions.get(pack_id)
            if not versions:
                raise DomainPackResolutionError("DOMAIN_PACK_UNKNOWN", f"unknown pack: {pack_id}")
            current = self._active.get(pack_id)
            ordered = sorted(versions, key=lambda item: versions[item].manifest.version_key)
            if to_version is not None:
                selected = to_version
            elif current in ordered and ordered.index(current) > 0:
                selected = ordered[ordered.index(current) - 1]
            else:
                raise DomainPackResolutionError("DOMAIN_PACK_ROLLBACK_UNAVAILABLE", f"no prior version for {pack_id}")
            if selected not in versions:
                raise DomainPackResolutionError("DOMAIN_PACK_VERSION_UNKNOWN", f"unknown pack version: {pack_id}@{selected}")
            self._assert_no_active_capability_collision(versions[selected], replacing_pack=pack_id)
            self._active[pack_id] = selected
            return selected

    def resolve(self, capability_id: str) -> DomainPackResolution:
        normalized = str(capability_id or "").strip()
        with self._lock:
            disabled_owner = False
            matches: list[DomainPackResolution] = []
            for pack_id, versions in self._versions.items():
                active_version = self._active.get(pack_id)
                all_declared = any(
                    any(item.capability_id == normalized for item in pack.manifest.capabilities)
                    for pack in versions.values()
                )
                if all_declared and active_version is None:
                    disabled_owner = True
                if active_version is None:
                    continue
                pack = versions[active_version]
                for capability in pack.manifest.capabilities:
                    if capability.capability_id == normalized:
                        matches.append(DomainPackResolution(
                            pack_id=pack_id,
                            version=active_version,
                            capability_id=normalized,
                            handler_id=capability.handler_id,
                            handler=pack.handlers[capability.handler_id],
                            allowed_task_ids=capability.allowed_task_ids,
                            lifecycle_stages=capability.lifecycle_stages,
                        ))
            if len(matches) > 1:
                raise DomainPackResolutionError("DOMAIN_PACK_CAPABILITY_AMBIGUOUS", f"multiple packs own {normalized}")
            if matches:
                return matches[0]
            if disabled_owner:
                raise DomainPackResolutionError("DOMAIN_PACK_DISABLED", f"owner pack disabled for {normalized}")
        raise DomainPackResolutionError("DOMAIN_CAPABILITY_UNKNOWN", f"unknown domain capability: {normalized}")

    def dispatch(
        self,
        request: DomainPackRequest,
        *,
        ports: DomainPackPlatformPorts,
        capability_request: CapabilityRequest,
        capability_grant: CapabilityGrant,
    ) -> Any:
        self._validate_grant(
            request,
            capability_request=capability_request,
            capability_grant=capability_grant,
        )
        resolution = self.resolve(request.capability_id)
        pack = self._versions[resolution.pack_id][resolution.version]
        for port_name in pack.manifest.required_ports:
            ports.require(port_name)
        scoped_ports = ports
        if ports.task_port is not None:
            scoped_ports = replace(
                ports,
                task_port=_ScopedDomainTaskPort(
                    delegate=ports.task_port,
                    pack_id=resolution.pack_id,
                    capability_id=resolution.capability_id,
                    allowed_task_ids=frozenset(resolution.allowed_task_ids),
                ),
            )
        lifecycle_binding = request.payload.get("lifecycle_stage")
        if lifecycle_binding is None:
            return resolution.handler.execute(request, scoped_ports)
        if not isinstance(lifecycle_binding, Mapping):
            raise DomainPackResolutionError(
                "DOMAIN_LIFECYCLE_STAGE_BINDING_INVALID",
                "lifecycle_stage must be an object bound to a declared Pack stage",
            )
        stage_id = str(lifecycle_binding.get("stage_id") or "").strip()
        stages = [item for item in resolution.lifecycle_stages if item.stage_id == stage_id]
        if len(stages) != 1:
            raise DomainPackResolutionError(
                "DOMAIN_LIFECYCLE_STAGE_UNKNOWN",
                f"{resolution.capability_id} does not declare lifecycle stage {stage_id}",
            )
        stage = stages[0]
        if (
            str(lifecycle_binding.get("phase") or "").strip() != stage.phase
            or str(lifecycle_binding.get("task_id") or "").strip() != stage.task_id
        ):
            raise DomainPackResolutionError(
                "DOMAIN_LIFECYCLE_STAGE_BINDING_INVALID",
                "lifecycle stage phase/task differs from the Pack declaration",
            )
        if stage.user_visible and not str(lifecycle_binding.get("delivery_id") or "").strip():
            raise DomainPackResolutionError(
                "DOMAIN_DELIVERY_ID_REQUIRED",
                "user-visible Pack delivery requires a stable delivery_id",
            )
        task_port = scoped_ports.require("task_port")
        return task_port.execute(stage.task_id, {
            **dict(request.payload),
            "domain_capability_id": resolution.capability_id,
        })

    @staticmethod
    def _validate_grant(
        request: DomainPackRequest,
        *,
        capability_request: CapabilityRequest,
        capability_grant: CapabilityGrant,
    ) -> None:
        if type(capability_request) is not CapabilityRequest or type(capability_grant) is not CapabilityGrant:
            raise DomainPackResolutionError(
                "DOMAIN_PACK_GRANT_MISSING",
                "Domain Pack dispatch requires the broker request and its typed grant",
            )
        if (
            capability_request.kind is not CapabilityKind.DOMAIN
            or capability_request.request_id != request.request_id
            or capability_request.capability_id != request.capability_id
            or dict(capability_request.args) != dict(request.payload)
        ):
            raise DomainPackResolutionError(
                "DOMAIN_PACK_REQUEST_BINDING_INVALID",
                "Domain Pack request differs from the broker-authorized request",
            )
        now_ms = int(time.time() * 1000)
        if (
            capability_grant.decision is not GrantDecision.APPROVE
            or capability_grant.kind is not CapabilityKind.DOMAIN
            or capability_grant.capability_id != request.capability_id
            or capability_grant.request_hash != capability_request.request_hash
            or capability_grant.issued_at_ms > now_ms
            or capability_grant.expires_at_ms <= now_ms
        ):
            raise DomainPackResolutionError(
                "DOMAIN_PACK_GRANT_INVALID",
                "Domain Pack grant is rejected, expired, or not request-bound",
            )

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = []
            for pack_id, versions in sorted(self._versions.items()):
                rows.append({
                    "pack_id": pack_id,
                    "active_version": self._active.get(pack_id),
                    "installed_versions": sorted(versions, key=lambda item: versions[item].manifest.version_key),
                    "enabled": self._active.get(pack_id) is not None,
                })
            return tuple(rows)

    def _assert_no_active_capability_collision(self, pack: DomainPack, *, replacing_pack: str) -> None:
        proposed = {item.capability_id for item in pack.manifest.capabilities}
        for pack_id, active_version in self._active.items():
            if pack_id == replacing_pack or active_version is None:
                continue
            active = self._versions[pack_id][active_version]
            overlap = proposed & {item.capability_id for item in active.manifest.capabilities}
            if overlap:
                raise ValueError(f"active domain capability collision: {sorted(overlap)}")


__all__ = ["DomainPackRegistry", "DomainPackResolution", "DomainPackResolutionError"]
