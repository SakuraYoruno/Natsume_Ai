"""Frontend audit skill pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _repo_root_from_context(ctx: dict[str, Any] | None) -> Path:
    raw = str((ctx or {}).get("repo_root") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.cwd()


def _default_monitor_ui_root(ctx: dict[str, Any] | None) -> Path:
    root = _repo_root_from_context(ctx)
    candidate = root / "host_brain" / "app" / "monitor_ui"
    return candidate if candidate.exists() else root


def _normalize_frontend_root(args: dict[str, Any], ctx: dict[str, Any] | None) -> Path:
    raw = str(
        (args or {}).get("app_root")
        or (args or {}).get("frontend_root")
        or (args or {}).get("source_root")
        or (args or {}).get("path")
        or ""
    ).strip()
    if raw:
        return Path(raw).expanduser()
    return _default_monitor_ui_root(ctx)


def _normalize_max_files(args: dict[str, Any], default: int = 600) -> int:
    try:
        return max(1, min(5000, int((args or {}).get("max_files") or default)))
    except (TypeError, ValueError):
        return default


def _normalize_route_audit(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    app_root = _normalize_frontend_root(args or {}, ctx)
    source_root = Path(str((args or {}).get("source_root") or "")).expanduser() if (args or {}).get("source_root") else app_root / "src"
    return {
        "app_root": str(app_root),
        "source_root": str(source_root),
        "include_static": bool((args or {}).get("include_static", False)),
        "max_files": _normalize_max_files(args or {}, 600),
        "manifest_ref": f"frontend_route_audit::{app_root}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_component_value_audit(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    frontend_root = _normalize_frontend_root(args or {}, ctx)
    source_root = Path(str((args or {}).get("source_root") or "")).expanduser() if (args or {}).get("source_root") else frontend_root / "src"
    try:
        min_repeated = max(2, min(20, int((args or {}).get("min_repeated_label_count") or 5)))
    except (TypeError, ValueError):
        min_repeated = 5
    try:
        max_card_surfaces = max(5, min(200, int((args or {}).get("max_card_surfaces_per_file") or 35)))
    except (TypeError, ValueError):
        max_card_surfaces = 35
    return {
        "source_root": str(source_root),
        "max_files": _normalize_max_files(args or {}, 800),
        "min_repeated_label_count": min_repeated,
        "max_card_surfaces_per_file": max_card_surfaces,
        "manifest_ref": f"frontend_component_value_audit::{source_root}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


class FrontendSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="frontend_route_audit",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="Read-only frontend route graph audit for route declarations, navigation targets, lazy loaders, and likely broken links.",
            tool="frontend",
            action="route_audit",
            normalize_args=_normalize_route_audit,
            family="code",
            capability_tags=("frontend", "routes", "audit"),
        ),
        DeclarativeSkillSpec(
            skill_name="frontend_component_value_audit",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="Read-only frontend component audit for dense card layouts, repeated labels, placeholder markers, and heavy page files.",
            tool="frontend",
            action="component_value_audit",
            normalize_args=_normalize_component_value_audit,
            family="code",
            capability_tags=("frontend", "components", "ux-audit"),
        ),
    )


def build_frontend_skill_registry(provider: FrontendSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or FrontendSkillProvider())
    return registry
