from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MONITOR_SRC = ROOT / "host_brain" / "app" / "monitor_ui" / "src"
ROUTE_MANIFEST = MONITOR_SRC / "app" / "routeManifest.jsx"
ROUTE_LOADERS = MONITOR_SRC / "routes" / "routeLoaders.js"

NAV_GROUPS = {
    "Cockpit",
    "Life Systems",
    "Observability",
    "Operations",
    "Execution",
    "Security Lab",
    "Integrations",
}
SERVICE_DEPS = {"host", "obs"}
RISK_LEVELS = {"low", "medium", "high"}

ROUTE_RE = re.compile(
    r"\{\s*"
    r"path:\s*'(?P<path>[^']+)'.*?"
    r"legacyPaths:\s*\[(?P<legacy>[^\]]*)\].*?"
    r"navGroup:\s*'(?P<nav_group>[^']+)'.*?"
    r"serviceDeps:\s*\[(?P<service_deps>[^\]]*)\].*?"
    r"riskLevel:\s*'(?P<risk_level>[^']+)'.*?"
    r"smokeTest:\s*(?P<smoke_test>true|false)",
    re.DOTALL,
)
STRING_RE = re.compile(r"'([^']*)'")
LOADER_KEY_RE = re.compile(r"^\s*'([^']+)':\s*\(\)\s*=>\s*import", re.MULTILINE)


@dataclass(frozen=True)
class ManifestRoute:
    path: str
    legacy_paths: tuple[str, ...]
    nav_group: str
    service_deps: tuple[str, ...]
    risk_level: str
    smoke_test: bool


def _manifest_routes() -> list[ManifestRoute]:
    source = ROUTE_MANIFEST.read_text(encoding="utf-8")
    routes = [
        ManifestRoute(
            path=match.group("path"),
            legacy_paths=tuple(STRING_RE.findall(match.group("legacy"))),
            nav_group=match.group("nav_group"),
            service_deps=tuple(STRING_RE.findall(match.group("service_deps"))),
            risk_level=match.group("risk_level"),
            smoke_test=match.group("smoke_test") == "true",
        )
        for match in ROUTE_RE.finditer(source)
    ]
    assert routes, "routeManifest.jsx routes could not be parsed"
    return routes


def _loader_keys() -> set[str]:
    source = ROUTE_LOADERS.read_text(encoding="utf-8")
    keys = set(LOADER_KEY_RE.findall(source))
    assert keys, "routeLoaders.js PAGE_LOADERS keys could not be parsed"
    return keys


def _preload_key(path: str) -> str:
    return path.removesuffix("/*")


def test_manifest_routes_have_stable_contract_fields() -> None:
    violations: list[str] = []
    canonical_paths: set[str] = set()
    legacy_paths: set[str] = set()

    for route in _manifest_routes():
        if route.path in canonical_paths:
            violations.append(f"duplicate canonical path: {route.path}")
        canonical_paths.add(route.path)

        if not route.path.startswith("/"):
            violations.append(f"{route.path}: path must be absolute")
        if route.nav_group not in NAV_GROUPS:
            violations.append(f"{route.path}: unknown navGroup {route.nav_group}")
        if not route.service_deps:
            violations.append(f"{route.path}: serviceDeps must be explicit")
        if set(route.service_deps) - SERVICE_DEPS:
            violations.append(f"{route.path}: invalid serviceDeps {route.service_deps}")
        if route.risk_level not in RISK_LEVELS:
            violations.append(f"{route.path}: invalid riskLevel {route.risk_level}")

        for legacy_path in route.legacy_paths:
            if not legacy_path.startswith("/"):
                violations.append(f"{route.path}: legacy path {legacy_path} must be absolute")
            if legacy_path in canonical_paths:
                violations.append(f"{route.path}: legacy path collides with canonical path {legacy_path}")
            if legacy_path in legacy_paths:
                violations.append(f"{route.path}: duplicate legacy path {legacy_path}")
            legacy_paths.add(legacy_path)

    assert violations == []


def test_manifest_canonical_routes_have_page_loaders() -> None:
    loader_keys = _loader_keys()
    missing = [
        route.path
        for route in _manifest_routes()
        if _preload_key(route.path) not in loader_keys and route.path not in loader_keys
    ]

    assert missing == []


def test_manifest_legacy_routes_are_redirect_only_not_page_loader_entries() -> None:
    loader_keys = _loader_keys()
    legacy_loader_keys = sorted(
        _preload_key(legacy_path)
        for route in _manifest_routes()
        for legacy_path in route.legacy_paths
        if ":" not in legacy_path and _preload_key(legacy_path) in loader_keys
    )

    assert legacy_loader_keys == []


def test_page_loader_orphans_are_documented_legacy_entries() -> None:
    routes = _manifest_routes()
    canonical_loader_keys = {_preload_key(route.path) for route in routes}
    unexpected = sorted(_loader_keys() - canonical_loader_keys)

    assert unexpected == []
