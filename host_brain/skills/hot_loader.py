"""Skill 热加载与版本管理（M12-D）。

提供：
  SkillVersionRegistry  — 版本注册表（存储历史版本）
  SkillHotLoader        — 热加载/回滚/一致性检查
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    HOTLOAD_META_INVALID,
    HOTLOAD_ROLLBACK_FAILED,
    HOTLOAD_SKILL_NOT_FOUND,
    HOTLOAD_VERSION_NOT_FOUND,
)
from host_brain.core.ids import new_event_id, now_ms
from host_brain.skills.registry import SkillMeta, SkillRegistry


# ── Schema ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SkillVersion:
    skill_name: str
    version: str
    meta: SkillMeta
    ts_ms: int


@dataclass(frozen=True)
class SkillReloadResult:
    success: bool
    skill_name: str
    version: str | None
    reason_code: str | None
    detail: str
    reload_id: str
    ts_ms: int


# ── SkillVersionRegistry ──────────────────────────────────────────────────────

class SkillVersionRegistry:
    """存储每个 Skill 的历史版本快照。"""

    def __init__(self) -> None:
        # skill_name → list[SkillVersion]（按时间顺序）
        self._versions: dict[str, list[SkillVersion]] = {}

    def record(self, meta: SkillMeta, *, version: str, ts_ms: int | None = None) -> SkillVersion:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        sv = SkillVersion(
            skill_name=meta.skill_name,
            version=version,
            meta=meta,
            ts_ms=now_value,
        )
        self._versions.setdefault(meta.skill_name, []).append(sv)
        return sv

    def list_versions(self, skill_name: str) -> list[SkillVersion]:
        return list(self._versions.get(skill_name, []))

    def get_version(self, skill_name: str, version: str) -> SkillVersion | None:
        for sv in self._versions.get(skill_name, []):
            if sv.version == version:
                return sv
        return None

    def latest_version(self, skill_name: str) -> SkillVersion | None:
        versions = self._versions.get(skill_name, [])
        return versions[-1] if versions else None


# ── SkillHotLoader ────────────────────────────────────────────────────────────

class SkillHotLoader:
    """热加载 Skill（不重启服务）+ 回滚 + 一致性检查。"""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        version_registry: SkillVersionRegistry | None = None,
        audit_log: list[dict[str, Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._version_registry = version_registry or SkillVersionRegistry()
        self._audit_log: list[dict[str, Any]] = audit_log if audit_log is not None else []

    def reload_skill(
        self,
        new_meta: SkillMeta,
        *,
        version: str | None = None,
        ts_ms: int | None = None,
    ) -> SkillReloadResult:
        """热加载指定 Skill（替换注册表中的现有条目）。"""
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        reload_id = new_event_id()
        version_str = str(version or "auto").strip() or "auto"

        # 一致性检查
        try:
            new_meta.validate()
        except ValueError as exc:
            self._write_audit(
                reload_id=reload_id,
                skill_name=new_meta.skill_name,
                version=version_str,
                success=False,
                reason_code=HOTLOAD_META_INVALID,
                detail=str(exc),
                ts_ms=now_value,
            )
            return SkillReloadResult(
                success=False,
                skill_name=new_meta.skill_name,
                version=None,
                reason_code=HOTLOAD_META_INVALID,
                detail=str(exc),
                reload_id=reload_id,
                ts_ms=now_value,
            )

        # 记录旧版本（如果存在）
        old_meta = self._registry.get(new_meta.skill_name)
        if old_meta is not None:
            old_version = self._version_registry.latest_version(new_meta.skill_name)
            old_ver_str = old_version.version if old_version else "0.0.0"
            self._version_registry.record(old_meta, version=old_ver_str, ts_ms=now_value)
            # 从注册表中移除旧版本（SkillRegistry 不支持覆盖，需要内部替换）
            self._registry._skills[new_meta.skill_name] = new_meta
        else:
            # 新 Skill，直接注册
            self._registry._skills[new_meta.skill_name] = new_meta

        # 记录新版本
        self._version_registry.record(new_meta, version=version_str, ts_ms=now_value)

        self._write_audit(
            reload_id=reload_id,
            skill_name=new_meta.skill_name,
            version=version_str,
            success=True,
            reason_code=None,
            detail="skill hot-loaded successfully",
            ts_ms=now_value,
        )
        return SkillReloadResult(
            success=True,
            skill_name=new_meta.skill_name,
            version=version_str,
            reason_code=None,
            detail="skill hot-loaded successfully",
            reload_id=reload_id,
            ts_ms=now_value,
        )

    def rollback_skill(
        self,
        skill_name: str,
        target_version: str,
        *,
        ts_ms: int | None = None,
    ) -> SkillReloadResult:
        """回滚到指定版本。"""
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        reload_id = new_event_id()

        current = self._registry.get(skill_name)
        if current is None:
            self._write_audit(
                reload_id=reload_id,
                skill_name=skill_name,
                version=target_version,
                success=False,
                reason_code=HOTLOAD_SKILL_NOT_FOUND,
                detail=f"skill not found: {skill_name}",
                ts_ms=now_value,
            )
            return SkillReloadResult(
                success=False,
                skill_name=skill_name,
                version=None,
                reason_code=HOTLOAD_SKILL_NOT_FOUND,
                detail=f"skill not found: {skill_name}",
                reload_id=reload_id,
                ts_ms=now_value,
            )

        target_sv = self._version_registry.get_version(skill_name, target_version)
        if target_sv is None:
            self._write_audit(
                reload_id=reload_id,
                skill_name=skill_name,
                version=target_version,
                success=False,
                reason_code=HOTLOAD_VERSION_NOT_FOUND,
                detail=f"version not found: {target_version}",
                ts_ms=now_value,
            )
            return SkillReloadResult(
                success=False,
                skill_name=skill_name,
                version=target_version,
                reason_code=HOTLOAD_VERSION_NOT_FOUND,
                detail=f"version not found: {target_version}",
                reload_id=reload_id,
                ts_ms=now_value,
            )

        # 执行回滚
        self._registry._skills[skill_name] = target_sv.meta
        self._version_registry.record(target_sv.meta, version=target_version, ts_ms=now_value)

        self._write_audit(
            reload_id=reload_id,
            skill_name=skill_name,
            version=target_version,
            success=True,
            reason_code=None,
            detail=f"skill rolled back to version {target_version}",
            ts_ms=now_value,
        )
        return SkillReloadResult(
            success=True,
            skill_name=skill_name,
            version=target_version,
            reason_code=None,
            detail=f"skill rolled back to version {target_version}",
            reload_id=reload_id,
            ts_ms=now_value,
        )

    def list_versions(self, skill_name: str) -> list[SkillVersion]:
        return self._version_registry.list_versions(skill_name)

    def get_audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)

    def _write_audit(
        self,
        *,
        reload_id: str,
        skill_name: str,
        version: str,
        success: bool,
        reason_code: str | None,
        detail: str,
        ts_ms: int,
    ) -> None:
        self._audit_log.append(
            {
                "reload_id": reload_id,
                "skill_name": skill_name,
                "version": version,
                "success": success,
                "reason_code": reason_code,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )
