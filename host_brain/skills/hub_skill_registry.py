from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_hub_skill_install(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "source": str((args or {}).get("source") or "").strip(),
        "force": bool((args or {}).get("force", False)),
        "activate_now": bool((args or {}).get("activate_now", True)),
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "manifest_ref": f"hub_skill_install::{str((args or {}).get('source') or '')[:80]}",
        "_context": ctx or {},
    }


def _normalize_hub_skill_search(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "query": str((args or {}).get("query") or "").strip(),
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "manifest_ref": f"hub_skill_search::{str((args or {}).get('query') or '')[:80]}",
        "_context": ctx or {},
    }


def _normalize_hub_skill_list(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "manifest_ref": "hub_skill_list::installed",
        "_context": ctx or {},
    }


def _normalize_hub_skill_remove(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "skill_name": str((args or {}).get("skill_name") or "").strip(),
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "manifest_ref": f"hub_skill_remove::{str((args or {}).get('skill_name') or '')[:80]}",
        "_context": ctx or {},
    }


class HubSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="hub_skill_install",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="安装 Hub/SkillHub 技能，并可立即激活。",
            tool="hub_skill",
            action="install",
            normalize_args=_normalize_hub_skill_install,
        ),
        DeclarativeSkillSpec(
            skill_name="hub_skill_search",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="搜索远程 Hub 技能列表。",
            tool="hub_skill",
            action="search",
            normalize_args=_normalize_hub_skill_search,
        ),
        DeclarativeSkillSpec(
            skill_name="hub_skill_list",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="列出当前已安装的 Hub 技能。",
            tool="hub_skill",
            action="list",
            normalize_args=_normalize_hub_skill_list,
        ),
        DeclarativeSkillSpec(
            skill_name="hub_skill_remove",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            require_confirm=True,
            description="卸载已安装的 Hub 技能。",
            tool="hub_skill",
            action="remove",
            normalize_args=_normalize_hub_skill_remove,
        ),
    )


def build_hub_skill_registry(provider: HubSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or HubSkillProvider())
    return registry
