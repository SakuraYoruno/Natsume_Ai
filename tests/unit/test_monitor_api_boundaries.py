from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MONITOR_SRC = ROOT / "host_brain" / "app" / "monitor_ui" / "src"
ENDPOINT_REGISTRY = MONITOR_SRC / "shared" / "api" / "endpointRegistry.js"

LEGACY_OBS_IMPORT = re.compile(r"from\s+['\"][^'\"]*api/obs\.js['\"]")
BARE_HOST_FETCH = re.compile(r"\bfetch\s*\(\s*['\"`](?:/obs|/v1|/internal)")
LEGACY_OBS_REEXPORT = re.compile(r"from\s+['\"]\./obs\.js['\"]")
DEPRECATED_QUERY_IMPORT = re.compile(
    r"from\s+['\"][^'\"]*queries/"
    r"(?:"
    r"agentLoopQueries|alertsQueries|automationQueries|bondQueries|codexBioOpsQueries|diagnosticQueries|digitalLifeQueries|"
    r"canonQueries|forgeQueries|gatewayQueries|insightsQueries|lifeBodyQueries|logQueries|memoryQueries|"
    r"metricsQueries|overviewQueries|promptQueries|runtimeModeQueries|runtimeQueries|sessionsQueries|"
    r"skillsHubQueries|systemConfigQueries|toolsQueries|traceQueries|wechatQueries"
    r")\.js['\"]"
)
LEGACY_FEATURE_QUERY_IMPORT = re.compile(
    r"from\s+['\"][^'\"]*(?:\.\./)+queries/[^'\"]+Queries\.js['\"]"
)
LEGACY_FEATURE_API_IMPORT = re.compile(
    r"from\s+['\"][^'\"]*(?:\.\./)+api/[^'\"]+\.js['\"]"
)
SIBLING_FEATURE_IMPORT = re.compile(
    r"from\s+['\"]\.\./(?P<feature>execution|integrations|life|observability|operations|security)/"
)
LEGACY_API_DIRECT_REQUEST = re.compile(
    r"\b(?:obsGet|obsPost|obsPut|httpRequest|httpGet|httpPost)\b|\bfetch\s*\("
)
ENDPOINT_ENTRY = re.compile(
    r"\w+:\s*\{\s*service:\s*['\"](?P<service>[^'\"]+)['\"],\s*"
    r"path:\s*['\"](?P<path>[^'\"]+)['\"],\s*"
    r"risk:\s*['\"](?P<risk>[^'\"]+)['\"]\s*\}"
)
OBSERVABILITY_FEATURE_ENDPOINT_LITERAL = re.compile(
    r"['\"`]/(?:obs|api/ui|metrics|canon)(?:/[^'\"`]*)?['\"`]"
)
LIFE_FEATURE_ENDPOINT_LITERAL = re.compile(
    r"['\"`](?:"
    r"/canon/documents|"
    r"/obs/memory/resident-context|"
    r"/obs/distillation/stats|"
    r"/obs/context-compressor/stats|"
    r"/obs/episodic-store/stats"
    r")['\"`]"
)
OBS_CLIENT_HOT_GET_LITERAL = re.compile(
    r"['\"`](?:"
    r"/obs/runtime-snapshot|"
    r"/obs/prompt-builder/stats|"
    r"/obs/capability-gaps/stats|"
    r"/obs/insights/summary-snapshot|"
    r"/obs/digital-life/overview-snapshot|"
    r"/obs/digital-life/wall-clock-soak|"
    r"/obs/avatar/state|"
    r"/obs/codex-bio-ops|"
    r"/obs/life-body|"
    r"/obs/life-timeline|"
    r"/obs/pathologies|"
    r"/obs/trace-feed/snapshot"
    r")['\"`]"
)
SHARED_REGISTRY_PATHS = [
    "/canon/documents",
    "/obs/agent-loop/history",
    "/obs/agent_loop/stats",
    "/obs/codex-bio-ops",
    "/obs/context-compressor/stats",
    "/obs/distillation/stats",
    "/obs/episodic-store/stats",
    "/obs/llm/call-stats",
    "/obs/llm/route-stats",
    "/obs/permission/stats",
    "/obs/prompt-builder/stats",
    "/obs/rate-limit/backend-stats",
    "/obs/retrieval/runtime",
    "/obs/scheduler/history",
    "/obs/services/health",
    "/obs/ui-assertion/stats",
]

ALLOWED_OBS_IMPORTS: set[str] = set()

ALLOWED_BARE_FETCH = {
    "shared/api/httpClient.js",
}


def _source_files() -> list[Path]:
    return sorted(
        path
        for path in MONITOR_SRC.rglob("*")
        if path.suffix in {".js", ".jsx", ".ts", ".tsx"}
    )


def _relative(path: Path) -> str:
    return path.relative_to(MONITOR_SRC).as_posix()


def test_pages_queries_and_components_do_not_import_legacy_obs_api_directly() -> None:
    violations: list[str] = []
    for path in _source_files():
        rel = _relative(path)
        if rel in ALLOWED_OBS_IMPORTS:
            continue
        if LEGACY_OBS_IMPORT.search(path.read_text(encoding="utf-8")):
            violations.append(rel)

    assert violations == []


def test_direct_host_fetches_stay_inside_shared_or_legacy_api_layers() -> None:
    violations: list[str] = []
    for path in _source_files():
        rel = _relative(path)
        if rel in ALLOWED_BARE_FETCH:
            continue
        if BARE_HOST_FETCH.search(path.read_text(encoding="utf-8")):
            violations.append(rel)

    assert violations == []


def test_legacy_obs_module_is_not_used_as_api_wrapper_source() -> None:
    violations: list[str] = []
    api_dir = MONITOR_SRC / "api"
    for path in sorted(api_dir.glob("*.js")):
        rel = _relative(path)
        if rel == "api/obs.js":
            continue
        if LEGACY_OBS_REEXPORT.search(path.read_text(encoding="utf-8")):
            violations.append(rel)

    assert violations == []


def test_legacy_api_wrappers_are_compatibility_reexports_only() -> None:
    violations: list[str] = []
    api_dir = MONITOR_SRC / "api"
    for path in sorted(api_dir.glob("*.js")):
        rel = _relative(path)
        if rel == "api/obs.js":
            continue
        if LEGACY_API_DIRECT_REQUEST.search(path.read_text(encoding="utf-8")):
            violations.append(rel)

    assert violations == []


def test_migrated_feature_queries_are_not_imported_from_global_wrappers() -> None:
    violations: list[str] = []
    checked_roots = [
        MONITOR_SRC / "components",
        MONITOR_SRC / "features",
        MONITOR_SRC / "hooks",
        MONITOR_SRC / "pages",
    ]
    for root in checked_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
                continue
            if DEPRECATED_QUERY_IMPORT.search(path.read_text(encoding="utf-8")):
                violations.append(_relative(path))

    assert violations == []


def test_pages_components_hooks_and_features_do_not_import_global_api_or_query_wrappers() -> None:
    violations: list[str] = []
    checked_roots = [
        MONITOR_SRC / "components",
        MONITOR_SRC / "features",
        MONITOR_SRC / "hooks",
        MONITOR_SRC / "pages",
    ]
    for root in checked_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
                continue
            source = path.read_text(encoding="utf-8")
            if LEGACY_FEATURE_QUERY_IMPORT.search(source) or LEGACY_FEATURE_API_IMPORT.search(source):
                violations.append(_relative(path))

    assert violations == []


def test_legacy_api_and_query_wrapper_files_are_removed() -> None:
    legacy_files = [
        _relative(path)
        for legacy_dir in (MONITOR_SRC / "api", MONITOR_SRC / "queries")
        if legacy_dir.exists()
        for path in sorted(legacy_dir.glob("*.js"))
    ]

    assert legacy_files == []


def test_endpoint_registry_entries_are_explicitly_annotated() -> None:
    source = ENDPOINT_REGISTRY.read_text(encoding="utf-8")
    entries = list(ENDPOINT_ENTRY.finditer(source))
    assert len(entries) >= 40

    violations: list[str] = []
    for entry in entries:
        service = entry.group("service")
        path = entry.group("path")
        risk = entry.group("risk")
        if service not in {"host", "obs"}:
            violations.append(f"{path}: invalid service {service}")
        if not path.startswith(("/api", "/bond", "/canon", "/internal", "/metrics", "/obs", "/prompt-config", "/scheduler", "/tools", "/v1")):
            violations.append(f"{path}: unexpected path prefix")
        if risk not in {"low", "medium", "high"}:
            violations.append(f"{path}: invalid risk {risk}")

    assert violations == []


def test_shared_endpoint_registry_paths_are_declared_once() -> None:
    source = ENDPOINT_REGISTRY.read_text(encoding="utf-8")

    assert "const SHARED_ENDPOINTS = Object.freeze" in source
    for path in SHARED_REGISTRY_PATHS:
        assert source.count(f"path: '{path}'") == 1, path


def test_feature_modules_use_shared_query_runtime_not_global_queries() -> None:
    violations: list[str] = []
    feature_dir = MONITOR_SRC / "features"
    for path in sorted(feature_dir.rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"from\s+['\"][^'\"]*\.\./\.\./queries/(?:freshness|queryCache|queryKeys|useObservabilityQuery)\.js['\"]", source):
            violations.append(_relative(path))

    assert violations == []


def test_feature_modules_do_not_import_sibling_feature_modules() -> None:
    violations: list[str] = []
    feature_root = MONITOR_SRC / "features"
    for path in sorted(feature_root.rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        rel = _relative(path)
        current_feature = path.relative_to(feature_root).parts[0]
        for match in SIBLING_FEATURE_IMPORT.finditer(path.read_text(encoding="utf-8")):
            target_feature = match.group("feature")
            if target_feature != current_feature:
                violations.append(f"{rel} -> {target_feature}")

    assert violations == []


def test_observability_feature_api_uses_endpoint_registry_for_paths() -> None:
    source = (MONITOR_SRC / "features" / "observability" / "api.js").read_text(encoding="utf-8")

    assert "const endpoints = Object.freeze" not in source
    assert "function obsGetPath(" not in source
    assert "from '../execution/api.js'" not in source
    assert OBSERVABILITY_FEATURE_ENDPOINT_LITERAL.findall(source) == []


def test_life_feature_api_uses_endpoint_registry_for_host_and_obs_paths() -> None:
    source = (MONITOR_SRC / "features" / "life" / "api.js").read_text(encoding="utf-8")

    assert LIFE_FEATURE_ENDPOINT_LITERAL.findall(source) == []


def test_obs_client_hot_get_policy_uses_endpoint_registry_paths() -> None:
    source = (MONITOR_SRC / "shared" / "api" / "obsClient.js").read_text(encoding="utf-8")

    assert "ENDPOINT_REGISTRY" in source
    assert "HOT_GET_ROUTES" in source
    assert "matchesHotGetRoute(" in source
    assert "startsWith(prefix)" not in source
    assert OBS_CLIENT_HOT_GET_LITERAL.findall(source) == []


def test_trace_shared_cards_render_dynamic_values_safely() -> None:
    source = (MONITOR_SRC / "components" / "trace" / "TraceSurfaces.jsx").read_text(encoding="utf-8")

    assert "RenderValue from '../../shared/ui/RenderValue.jsx'" in source
    assert source.count("<RenderValue value={value}") >= 3
    assert ">{value}</div>" not in source
    assert ">{value ?? '-'}</div>" not in source


def test_http_client_preserves_structured_error_details() -> None:
    source = (MONITOR_SRC / "shared" / "api" / "httpClient.js").read_text(encoding="utf-8")

    assert "function formatErrorDetail(" in source
    assert "JSON.stringify(detail)" in source
    assert "String(detail || `HTTP ${response.status}`)" not in source
