from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MONITOR_SRC = ROOT / "host_brain" / "app" / "monitor_ui" / "src"
ENDPOINT_REGISTRY = MONITOR_SRC / "shared" / "api" / "endpointRegistry.js"
ENDPOINT_CLIENT = MONITOR_SRC / "shared" / "api" / "endpointClient.js"
SHARED_OBSERVABILITY_API = MONITOR_SRC / "shared" / "api" / "sharedObservability.js"
SHARED_FORMATTERS = MONITOR_SRC / "shared" / "formatters.js"
SHARED_PENTEST_META = MONITOR_SRC / "shared" / "pentestMeta.js"
SHARED_STATUS_META = MONITOR_SRC / "shared" / "statusMeta.js"
OBSERVABILITY_COMPONENTS = MONITOR_SRC / "features" / "observability" / "components"
OBSERVABILITY_TRACE_COMPONENTS = MONITOR_SRC / "features" / "observability" / "components" / "trace"
OPERATIONS_COMPONENTS = MONITOR_SRC / "features" / "operations" / "components"
APP_SHELL = MONITOR_SRC / "app" / "shell"

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
IMPORT_SOURCE = re.compile(r"from\s+['\"](?P<source>[^'\"]+)['\"]")
SIBLING_FEATURE_IMPORT = re.compile(
    r"from\s+['\"](?:\.\./)+(?P<feature>execution|integrations|life|observability|operations|security)/"
)
LEGACY_API_DIRECT_REQUEST = re.compile(
    r"\b(?:obsGet|obsPost|obsPut|httpRequest|httpGet|httpPost)\b|\bfetch\s*\("
)
FETCH_FUNCTION_EXPORT = re.compile(r"export function (?P<name>fetch[A-Za-z0-9_]+)\s*\(")
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


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


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


def test_endpoint_client_dispatches_by_registry_service() -> None:
    source = ENDPOINT_CLIENT.read_text(encoding="utf-8")

    assert "endpointPath(entry, pathParams, query)" in source
    assert "import { httpRequest } from './httpClient.js'" in source
    assert "import { obsRequest } from './obsClient.js'" in source
    assert "entry.service === 'obs'" in source
    assert "entry.service === 'host'" in source
    assert "export function endpointGet(" in source
    assert "export function endpointPost(" in source
    assert "export function endpointPut(" in source


def test_operations_feature_api_uses_endpoint_aware_client() -> None:
    source = (MONITOR_SRC / "features" / "operations" / "api.js").read_text(encoding="utf-8")

    assert "from '../../shared/api/endpointClient.js'" in source
    assert "endpointGet(" in source
    assert "endpointPost(" in source
    assert "httpGet" not in source
    assert "httpPost" not in source
    assert "obsGet" not in source
    assert "obsPost" not in source
    assert "endpointPath(" not in source


def test_integrations_feature_api_uses_endpoint_aware_client() -> None:
    source = (MONITOR_SRC / "features" / "integrations" / "api.js").read_text(encoding="utf-8")

    assert "from '../../shared/api/endpointClient.js'" in source
    assert "endpointGet(" in source
    assert "endpointPost(" in source
    assert "httpGet" not in source
    assert "httpPost" not in source


def test_security_feature_api_uses_endpoint_aware_client_for_json_requests() -> None:
    source = (MONITOR_SRC / "features" / "security" / "api.js").read_text(encoding="utf-8")

    assert "from '../../shared/api/endpointClient.js'" in source
    assert "endpointGet(" in source
    assert "endpointPost(" in source
    assert "httpGet" not in source
    assert "httpPost" not in source
    assert "httpText" in source
    assert "pentestRunEventsPath" in source
    assert "endpointPath(pentest.events" in source


def test_life_feature_api_uses_endpoint_aware_client() -> None:
    source = (MONITOR_SRC / "features" / "life" / "api.js").read_text(encoding="utf-8")

    assert "from '../../shared/api/endpointClient.js'" in source
    assert "endpointGet(" in source
    assert "endpointPost(" in source
    assert "endpointPut(" in source
    assert "httpGet" not in source
    assert "obsGet" not in source
    assert "obsPost" not in source
    assert "obsPut" not in source
    assert "endpointPath(" not in source


def test_observability_feature_api_uses_endpoint_aware_client_for_json_requests() -> None:
    compat = (MONITOR_SRC / "features" / "observability" / "api.js").read_text(encoding="utf-8")
    client = (
        MONITOR_SRC / "features" / "observability" / "observabilityApiClient.js"
    ).read_text(encoding="utf-8")
    sessions_api = (MONITOR_SRC / "features" / "observability" / "sessionsApi.js").read_text(encoding="utf-8")
    trace_api = (MONITOR_SRC / "features" / "observability" / "traceApi.js").read_text(encoding="utf-8")
    overview_api = (MONITOR_SRC / "features" / "observability" / "overviewApi.js").read_text(encoding="utf-8")

    for forbidden in [
        "endpointGet(",
        "endpointPost(",
        "requestObsGet",
        "requestObsPost",
        "httpBlob",
        "createObsEventSource",
        "createObsWebSocket",
    ]:
        assert forbidden not in compat

    assert "from './sessionsApi.js'" in compat
    assert "from './traceApi.js'" in compat
    assert "from './bioOpsApi.js'" in compat
    assert "from '../../shared/api/endpointClient.js'" in client
    assert "endpointGet(endpoint, query, pathParams)" in client
    assert "endpointPost(endpoint, body || {}, pathParams, query)" in client
    assert "httpBlob(" in sessions_api
    assert "createObsEventSource(" in trace_api
    assert "createObsWebSocket(" in overview_api


def test_execution_feature_api_uses_endpoint_aware_client_for_json_requests() -> None:
    source = (MONITOR_SRC / "features" / "execution" / "api.js").read_text(encoding="utf-8")

    assert "from '../../shared/api/endpointClient.js'" in source
    assert "endpointGet(" in source
    assert "endpointPost(" in source
    assert "endpointRequest(" in source
    assert "httpStreamPost" in source
    assert "httpGet" not in source
    assert "httpPost" not in source
    assert "httpRequest" not in source
    assert "obsGet" not in source
    assert "obsPost" not in source


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


def test_feature_queries_do_not_import_shared_api_directly() -> None:
    violations: list[str] = []
    feature_dir = MONITOR_SRC / "features"
    for path in sorted(feature_dir.rglob("queries.js")):
        source = path.read_text(encoding="utf-8")
        if re.search(r"from\s+['\"][^'\"]*shared/api/", source):
            violations.append(_relative(path))

    assert violations == []


def test_feature_queries_only_depend_on_local_api_and_shared_query_runtime() -> None:
    violations: list[str] = []
    feature_dir = MONITOR_SRC / "features"
    for path in sorted(feature_dir.rglob("queries.js")):
        rel = _relative(path)
        for match in IMPORT_SOURCE.finditer(path.read_text(encoding="utf-8")):
            source = match.group("source")
            if not source.startswith("."):
                continue
            if source == "./api.js" or source.startswith("../../shared/query/"):
                continue
            violations.append(f"{rel} -> {source}")

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


def test_execution_feature_containers_are_not_left_under_global_components() -> None:
    legacy_dirs = [
        MONITOR_SRC / "components" / "tools",
        MONITOR_SRC / "components" / "automation",
    ]
    legacy_paths = [
        MONITOR_SRC / "components" / "tools" / "ToolsOperationsSection.jsx",
        MONITOR_SRC / "components" / "automation" / "AutomationImmuneSectionContent.jsx",
        MONITOR_SRC / "components" / "automation" / "AutomationHistorySectionContent.jsx",
    ]
    violations: list[str] = []
    for path in sorted((MONITOR_SRC / "components").rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"from\s+['\"][^'\"]*features/execution/(?:api|queries)\.js['\"]", source):
            violations.append(_relative(path))

    assert [path.as_posix() for path in legacy_dirs if path.exists()] == []
    assert [path.as_posix() for path in legacy_paths if path.exists()] == []
    assert violations == []


def test_agent_loop_console_sections_are_feature_owned() -> None:
    page = MONITOR_SRC / "pages" / "AgentLoop.jsx"
    feature_component = MONITOR_SRC / "features" / "execution" / "components" / "AgentLoopConsoleSections.jsx"
    page_source = page.read_text(encoding="utf-8")

    assert feature_component.exists()
    assert "AgentLoopConsoleSections.jsx" in page_source
    assert "function PhaseBadge" not in page_source
    assert "isAgentLoopRunReady" not in page_source


def test_trace_observability_components_are_not_left_under_global_components() -> None:
    legacy_dir = MONITOR_SRC / "components" / "trace"
    page_paths = [
        MONITOR_SRC / "pages" / "Trace.jsx",
        MONITOR_SRC / "pages" / "TraceRequestDetail.jsx",
        MONITOR_SRC / "pages" / "ChatDebug.jsx",
    ]
    violations = [
        _relative(path)
        for path in page_paths
        if "../components/trace" in path.read_text(encoding="utf-8")
    ]

    assert OBSERVABILITY_TRACE_COMPONENTS.exists()
    assert not legacy_dir.exists()
    assert violations == []


def test_trace_request_detail_surface_is_feature_owned() -> None:
    page = MONITOR_SRC / "pages" / "TraceRequestDetail.jsx"
    surface = OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestDetailSurface.jsx"
    controller = MONITOR_SRC / "features" / "observability" / "traceRequestDetailController.js"
    page_source = page.read_text(encoding="utf-8")
    surface_source = surface.read_text(encoding="utf-8")
    controller_source = controller.read_text(encoding="utf-8")

    assert surface.exists()
    assert controller.exists()
    assert "TraceRequestDetailSurface.jsx" in page_source
    assert "useTraceRequestDetailController" in page_source
    assert "export function useTraceRequestDetailController()" in controller_source
    assert "export default function TraceRequestDetailSurface(" in surface_source
    for forbidden in [
        "PageHero",
        "LivingPulseStrip",
        "TraceMetricCard",
        "TraceMiniStat",
        "TraceSectionCard",
        "NodeDetailPanel",
        "StageOverviewPanel",
        "ImmuneReplayPanel",
        "StatusBadge",
        "LoadingNotice",
        "NoticeBanner",
        "connectTraceRequestLive",
        "useTraceSnapshotQuery",
        "useTraceStreamEventsQuery",
        "useTraceTribeEventsQuery",
        "useTraceRetrievalRuntimeQuery",
        "useImmuneGateReplayQuery",
        "useAppStore",
        "dedupeStreamEvents",
        "buildRetrievalRuntimeMeta",
        "getSummaryInputText",
        "getSummaryOutputText",
    ]:
        assert forbidden not in page_source

    for controller_token in [
        "connectTraceRequestLive(requestId",
        "useTraceSnapshotQuery({",
        "useTraceStreamEventsQuery({",
        "useTraceTribeEventsQuery({",
        "useTraceRetrievalRuntimeQuery({",
        "useImmuneGateReplayQuery({",
        "useAppStore((state) => state.pollingEnabled)",
        "dedupeStreamEvents(traceData?.stream_events, fallbackEvents)",
        "buildRetrievalRuntimeMeta(retrievalRuntime)",
        "getSummaryInputText(summary)",
        "getSummaryOutputText(summary)",
        "navigate('/observability/trace')",
    ]:
        assert controller_token in controller_source


def test_observability_data_containers_are_not_left_under_global_components() -> None:
    legacy_dirs = [
        MONITOR_SRC / "components" / "bioOps",
        MONITOR_SRC / "components" / "insights",
        MONITOR_SRC / "components" / "metrics",
    ]
    target_dirs = [
        OBSERVABILITY_COMPONENTS / "bioOps",
        OBSERVABILITY_COMPONENTS / "insights",
        OBSERVABILITY_COMPONENTS / "metrics",
    ]
    page_paths = [
        MONITOR_SRC / "pages" / "CodexBioOpsObservability.jsx",
        MONITOR_SRC / "pages" / "Insights.jsx",
        MONITOR_SRC / "pages" / "Metrics.jsx",
    ]
    old_imports = [
        _relative(path)
        for path in page_paths
        if re.search(r"['\"]\.\./components/(?:bioOps|insights|metrics)/", path.read_text(encoding="utf-8"))
    ]

    assert [path.as_posix() for path in legacy_dirs if path.exists()] == []
    assert [path.as_posix() for path in target_dirs if not path.exists()] == []
    assert old_imports == []


def test_digital_life_cockpit_sections_are_not_left_under_global_components() -> None:
    legacy_dir = MONITOR_SRC / "components" / "digitalLife"
    feature_dir = MONITOR_SRC / "features" / "life" / "components"
    page = MONITOR_SRC / "pages" / "DigitalLifeOverview.jsx"
    expected_files = [
        "DigitalLifeOverviewSurface.jsx",
        "DigitalLifeCapabilitySection.jsx",
        "DigitalLifeGatewaySection.jsx",
        "DigitalLifeSecondarySections.jsx",
        "MainChainReshapePanel.jsx",
        "digitalLifeSectionShared.jsx",
    ]
    page_source = page.read_text(encoding="utf-8")

    assert not legacy_dir.exists()
    assert [name for name in expected_files if not (feature_dir / name).exists()] == []
    assert "../components/digitalLife" not in page_source
    assert "../features/life/components" in page_source


def test_digital_life_overview_surface_is_feature_owned() -> None:
    page = MONITOR_SRC / "pages" / "DigitalLifeOverview.jsx"
    surface = MONITOR_SRC / "features" / "life" / "components" / "DigitalLifeOverviewSurface.jsx"
    controller = MONITOR_SRC / "features" / "life" / "digitalLifeOverviewController.js"
    page_source = page.read_text(encoding="utf-8")
    surface_source = surface.read_text(encoding="utf-8")
    controller_source = controller.read_text(encoding="utf-8")

    assert surface.exists()
    assert controller.exists()
    assert "DigitalLifeOverviewSurface from '../features/life/components/DigitalLifeOverviewSurface.jsx'" in page_source
    assert "useDigitalLifeOverviewController" in page_source
    assert "export function useDigitalLifeOverviewController()" in controller_source
    assert "export default function DigitalLifeOverviewSurface(" in surface_source

    for stale_component in [
        "AvatarStateCard",
        "BackgroundRuntimeCard",
        "DigitalLifeRuntimeCard",
        "DigitalLifeWallClockSoakCard",
        "LifeBodyFocusPanel",
        "LifeBodyPathologyPanel",
        "LifeBodyTimeline",
        "LifeBodyTopology",
        "LifeBodyVitalsStrip",
        "PageHero",
        "SampleMeta",
        "TimeWindowSelect",
        "ViewportLazySection",
        "VitalLoopSummaryPanel",
    ]:
        assert stale_component not in page_source


def test_life_body_components_are_not_left_under_global_components() -> None:
    legacy_dir = MONITOR_SRC / "components" / "lifeBody"
    feature_dir = MONITOR_SRC / "features" / "life" / "components" / "lifeBody"
    expected_files = [
        "LifeBodyFocusPanel.jsx",
        "LifeBodyPathologyPanel.jsx",
        "LifeBodyTimeline.jsx",
        "LifeBodyTopology.jsx",
        "LifeBodyVitalsStrip.jsx",
        "OrganSystemOverview.jsx",
    ]
    source_files = [
        *sorted((MONITOR_SRC / "pages").glob("*.jsx")),
        *sorted((MONITOR_SRC / "features").rglob("*.jsx")),
    ]
    stale_imports = [
        _relative(path)
        for path in source_files
        if "../components/lifeBody" in path.read_text(encoding="utf-8")
    ]

    assert not legacy_dir.exists()
    assert [name for name in expected_files if not (feature_dir / name).exists()] == []
    assert stale_imports == []


def test_life_runtime_cards_are_feature_owned() -> None:
    feature_dir = MONITOR_SRC / "features" / "life" / "components"
    expected_files = [
        "AvatarStateCard.jsx",
        "BackgroundRuntimeCard.jsx",
        "DigitalLifeNav.jsx",
        "DigitalLifeRuntimeCard.jsx",
        "DigitalLifeWallClockSoakCard.jsx",
        "InnerContinuityCard.jsx",
        "VitalLoopSummaryPanel.jsx",
    ]
    source_files = [
        *sorted((MONITOR_SRC / "pages").glob("*.jsx")),
        *sorted((MONITOR_SRC / "features").rglob("*.jsx")),
    ]
    stale_imports = [
        _relative(path)
        for path in source_files
        if re.search(
            r"from\s+['\"][^'\"]*(?:\.\./)+components/(?:AvatarStateCard|BackgroundRuntimeCard|DigitalLifeNav|DigitalLifeRuntimeCard|DigitalLifeWallClockSoakCard|InnerContinuityCard|VitalLoopSummaryPanel)\.jsx",
            path.read_text(encoding="utf-8"),
        )
    ]

    assert [name for name in expected_files if (MONITOR_SRC / "components" / name).exists()] == []
    assert [name for name in expected_files if not (feature_dir / name).exists()] == []
    assert stale_imports == []


def test_chart_primitives_live_under_shared_ui() -> None:
    legacy_dir = MONITOR_SRC / "components" / "charts"
    shared_chart_dir = MONITOR_SRC / "shared" / "ui" / "charts"
    expected_files = [
        "BarChart.jsx",
        "LineChart.jsx",
        "echartsBarCore.js",
        "echartsLineCore.js",
    ]
    source_files = [
        *sorted((MONITOR_SRC / "pages").glob("*.jsx")),
        *sorted((MONITOR_SRC / "features").rglob("*.jsx")),
    ]
    stale_imports = [
        _relative(path)
        for path in source_files
        if "components/charts/" in path.read_text(encoding="utf-8")
    ]

    assert not legacy_dir.exists()
    assert [name for name in expected_files if not (shared_chart_dir / name).exists()] == []
    assert stale_imports == []


def test_observability_ui_helpers_live_under_shared_ui() -> None:
    legacy_dir = MONITOR_SRC / "components" / "obs"
    shared_obs_dir = MONITOR_SRC / "shared" / "ui" / "observability"
    expected_files = [
        "DrilldownLink.jsx",
        "FreshnessPill.jsx",
        "InlineQueryMeta.jsx",
        "LoadStateBadge.jsx",
        "RepresentativeRequests.jsx",
        "SampleMeta.jsx",
        "ViewportLazySection.jsx",
    ]
    source_files = [
        *sorted((MONITOR_SRC / "pages").glob("*.jsx")),
        *sorted((MONITOR_SRC / "features").rglob("*.jsx")),
    ]
    stale_imports = [
        _relative(path)
        for path in source_files
        if "components/obs/" in path.read_text(encoding="utf-8")
    ]

    assert not legacy_dir.exists()
    assert [name for name in expected_files if not (shared_obs_dir / name).exists()] == []
    assert stale_imports == []


def test_legacy_global_components_directory_is_retired() -> None:
    components_dir = MONITOR_SRC / "components"
    shared_ui_dir = MONITOR_SRC / "shared" / "ui"
    expected_files = {
        "EmptyStateCard.jsx",
        "ErrorBoundary.jsx",
        "KeyValueRow.jsx",
        "LivingPulseStrip.jsx",
        "LoadingNotice.jsx",
        "MetricCard.jsx",
        "NoticeBanner.jsx",
        "PageHero.jsx",
        "SectionHeading.jsx",
        "ServiceCard.jsx",
        "Skeleton.jsx",
        "StatusBadge.jsx",
        "TimeWindowSelect.jsx",
    }
    source_files = [
        path
        for path in _source_files()
        if "node_modules" not in path.parts
    ]
    stale_imports = [
        _relative(path)
        for path in source_files
        if re.search(r"from\s+['\"][^'\"]*components/(?:" + "|".join(name.removesuffix(".jsx") for name in expected_files) + r")\.jsx['\"]", path.read_text(encoding="utf-8"))
    ]

    assert not components_dir.exists()
    assert [name for name in expected_files if not (shared_ui_dir / name).exists()] == []
    assert stale_imports == []


def test_shared_ui_does_not_import_feature_or_api_layers() -> None:
    violations: list[str] = []
    forbidden_import = re.compile(
        r"from\s+['\"][^'\"]*(?:features/|shared/api/|endpointClient|httpClient|obsClient)"
    )
    for path in sorted((MONITOR_SRC / "shared" / "ui").rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        if forbidden_import.search(path.read_text(encoding="utf-8")):
            violations.append(_relative(path))

    assert violations == []


def test_global_components_do_not_import_observability_data_modules_except_shell() -> None:
    allowlist = {"components/Layout.jsx"}
    violations: list[str] = []
    pattern = re.compile(
        r"from\s+['\"][^'\"]*features/observability/(?:api|queries|traceQueries|diagnosticQueries)\.js['\"]"
    )
    for path in sorted((MONITOR_SRC / "components").rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        rel = _relative(path)
        if rel in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(rel)

    assert violations == []


def test_operations_data_containers_are_not_left_under_global_components() -> None:
    legacy_paths = [
        MONITOR_SRC / "components" / "ForgeRiskActionBar.jsx",
        MONITOR_SRC / "components" / "canon" / "CanonDocumentsSection.jsx",
        MONITOR_SRC / "components" / "canon" / "CanonProposalsSection.jsx",
        MONITOR_SRC / "components" / "canon" / "CanonSubmitModal.jsx",
        MONITOR_SRC / "components" / "promptOps" / "PromptTemplateEditorPanel.jsx",
    ]
    target_paths = [
        OPERATIONS_COMPONENTS / "ForgeRiskActionBar.jsx",
        OPERATIONS_COMPONENTS / "canon" / "CanonDocumentsSection.jsx",
        OPERATIONS_COMPONENTS / "canon" / "CanonProposalsSection.jsx",
        OPERATIONS_COMPONENTS / "canon" / "CanonSubmitModal.jsx",
        OPERATIONS_COMPONENTS / "promptOps" / "PromptTemplateEditorPanel.jsx",
    ]
    import_checks = [
        (MONITOR_SRC / "components" / "Layout.jsx", "./ForgeRiskActionBar.jsx"),
        (MONITOR_SRC / "pages" / "Canon.jsx", "../components/canon/CanonDocumentsSection.jsx"),
        (MONITOR_SRC / "pages" / "Canon.jsx", "../components/canon/CanonProposalsSection.jsx"),
        (MONITOR_SRC / "pages" / "Canon.jsx", "../components/canon/CanonSubmitModal.jsx"),
        (MONITOR_SRC / "pages" / "PromptConfig.jsx", "../components/promptOps/PromptTemplateEditorPanel.jsx"),
        (MONITOR_SRC / "pages" / "PersonaPromptOps.jsx", "../components/promptOps/PromptTemplateEditorPanel.jsx"),
    ]
    old_imports = [
        _relative(path)
        for path, needle in import_checks
        if path.exists()
        if needle in path.read_text(encoding="utf-8")
    ]

    assert [path.as_posix() for path in legacy_paths if path.exists()] == []
    assert [path.as_posix() for path in target_paths if not path.exists()] == []
    assert old_imports == []


def test_global_components_do_not_import_operations_data_modules() -> None:
    violations: list[str] = []
    pattern = re.compile(
        r"from\s+['\"][^'\"]*features/operations/(?:api|queries)\.js['\"]"
    )
    for path in sorted((MONITOR_SRC / "components").rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(_relative(path))

    assert violations == []


def test_app_shell_layout_is_not_left_under_global_components() -> None:
    legacy_layout = MONITOR_SRC / "components" / "Layout.jsx"
    shell_layout = APP_SHELL / "Layout.jsx"
    app_source = (MONITOR_SRC / "App.jsx").read_text(encoding="utf-8")

    assert not legacy_layout.exists()
    assert shell_layout.exists()
    assert "./components/Layout.jsx" not in app_source
    assert "./app/shell/Layout.jsx" in app_source


def test_observability_feature_api_uses_endpoint_registry_for_paths() -> None:
    api_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((MONITOR_SRC / "features" / "observability").glob("*Api.js"))
    }
    api_sources["observabilityApiClient.js"] = (
        MONITOR_SRC / "features" / "observability" / "observabilityApiClient.js"
    ).read_text(encoding="utf-8")
    api_sources["api.js"] = (MONITOR_SRC / "features" / "observability" / "api.js").read_text(encoding="utf-8")
    combined = "\n".join(api_sources.values())

    assert "const endpoints = Object.freeze" not in combined
    assert "function obsGetPath(" not in combined
    assert "from '../execution/api.js'" not in combined
    for filename, source in api_sources.items():
        assert OBSERVABILITY_FEATURE_ENDPOINT_LITERAL.findall(source) == [], filename
    assert "ENDPOINT_REGISTRY.observability" in api_sources["observabilityApiClient.js"]
    assert "observabilityGet(" in combined


def test_observability_surfaces_import_domain_api_not_compat_wrapper() -> None:
    violations: list[str] = []
    patterns = [
        re.compile(r"from\s+['\"][^'\"]*features/observability/api\.js['\"]"),
        re.compile(r"from\s+['\"]\./api\.js['\"]"),
        re.compile(r"from\s+['\"]\.\./\.\./api\.js['\"]"),
    ]
    checked_roots = [
        MONITOR_SRC / "features" / "observability",
        MONITOR_SRC / "pages",
    ]
    for root in checked_roots:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
                continue
            if _relative(path) == "features/observability/api.js":
                continue
            source = path.read_text(encoding="utf-8")
            if any(pattern.search(source) for pattern in patterns):
                violations.append(_relative(path))

    assert violations == []


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
    source = (OBSERVABILITY_TRACE_COMPONENTS / "TraceSurfaces.jsx").read_text(encoding="utf-8")

    assert "RenderValue from '../../../../shared/ui/RenderValue.jsx'" in source
    assert source.count("<RenderValue value={value}") >= 3
    assert ">{value}</div>" not in source
    assert ">{value ?? '-'}</div>" not in source


def test_trace_node_detail_panel_uses_shared_rendering_primitives() -> None:
    source = (OBSERVABILITY_TRACE_COMPONENTS / "NodeDetailPanel.jsx").read_text(encoding="utf-8")
    flow_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceFlowModel.js").read_text(encoding="utf-8")

    assert "JsonBlock from '../../../../shared/ui/JsonBlock.jsx'" in source
    assert "RenderValue from '../../../../shared/ui/RenderValue.jsx'" in source
    assert "normalizeBadgeStatus } from './traceFlowModel.js'" in source
    assert "function normalizeBadgeStatus(" not in source
    assert "export function normalizeBadgeStatus(" in flow_model
    assert "function JsonViewer(" not in source
    assert "JSON.stringify(data" not in source
    assert "String(value)" not in source
    assert source.count("<RenderValue value=") >= 4
    assert source.count("<JsonBlock data=") >= 2


def test_trace_visual_metadata_lives_in_trace_feature_module() -> None:
    custom_node = (OBSERVABILITY_TRACE_COMPONENTS / "CustomNode.jsx").read_text(encoding="utf-8")
    advanced_views = (OBSERVABILITY_TRACE_COMPONENTS / "TraceAdvancedViews.jsx").read_text(encoding="utf-8")
    advanced_view_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceAdvancedViewModel.js").read_text(encoding="utf-8")
    visual_meta = (OBSERVABILITY_TRACE_COMPONENTS / "traceVisualMeta.js").read_text(encoding="utf-8")

    for export_name in [
        "TRACE_NODE_TYPE_META",
        "TRACE_PROVIDER_BADGE_META",
        "TRACE_STATUS_BORDER_COLORS",
        "TRACE_LAYER_BADGE_META",
        "resolveTraceNodeTypeColor",
        "resolveTraceStatusBorder",
    ]:
        assert export_name in visual_meta

    assert "from './traceVisualMeta.js'" in custom_node
    assert "const NODE_TYPE_CONFIG" not in custom_node
    assert "const PROVIDER_COLORS" not in custom_node
    assert "const STATUS_BORDER" not in custom_node
    assert "const LAYER_BADGE" not in custom_node
    assert "label: '意图'" not in custom_node
    assert "openai:" not in custom_node
    assert "DEGRADED: '#F79009'" not in custom_node

    assert "resolveTraceNodeTypeColor } from './traceVisualMeta.js'" in advanced_view_model
    assert "resolveTraceNodeTypeColor } from './traceVisualMeta.js'" not in advanced_views
    assert "const NODE_TYPE_COLOR" not in advanced_views
    assert "function nodeColor(" not in advanced_views
    assert "governor: '#7A5AF8'" not in advanced_views


def test_trace_advanced_view_model_lives_in_trace_feature_module() -> None:
    advanced_views = (OBSERVABILITY_TRACE_COMPONENTS / "TraceAdvancedViews.jsx").read_text(encoding="utf-8")
    full_chain_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "FullChainView.jsx"
    ).read_text(encoding="utf-8")
    gantt_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "GanttView.jsx"
    ).read_text(encoding="utf-8")
    group_dag_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "GroupDagView.jsx"
    ).read_text(encoding="utf-8")
    inline_overview = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "InlineFullChainOverview.jsx"
    ).read_text(encoding="utf-8")
    advanced_view_surfaces = "\n".join([full_chain_view, gantt_view, group_dag_view, inline_overview])
    advanced_view_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceAdvancedViewModel.js").read_text(encoding="utf-8")

    for export_name in [
        "buildTraceAdvancedFlowData",
        "computeTraceGanttTimeline",
        "filterTraceGanttTimeline",
        "buildTraceGanttTicks",
        "buildTraceGanttRowView",
        "buildGroupDagFlowInputs",
        "buildGroupDagHeaderMeta",
    ]:
        assert f"export function {export_name}(" in advanced_view_model

    assert "from './advancedViews/FullChainView.jsx'" in advanced_views
    assert "from './advancedViews/GroupDagView.jsx'" in advanced_views
    assert "from './advancedViews/InlineFullChainOverview.jsx'" in advanced_views
    assert "from '../traceAdvancedViewModel.js'" in advanced_view_surfaces
    assert "buildTraceAdvancedFlowData(" in full_chain_view
    assert "buildTraceAdvancedFlowData(" in inline_overview
    assert "computeTraceGanttTimeline(traceData)" in gantt_view
    assert "filterTraceGanttTimeline(timeline, filter)" in gantt_view
    assert "buildTraceGanttRowView(node, totalMs)" in gantt_view
    assert "buildGroupDagFlowInputs({" in group_dag_view
    assert "buildGroupDagHeaderMeta({" in group_dag_view
    assert "function buildFlowData(" not in advanced_view_surfaces
    assert "function computeGanttTimeline(" not in advanced_view_surfaces
    assert "MarkerType" not in advanced_view_surfaces
    assert "applyDagreLayout" not in advanced_view_surfaces
    assert "getTraceNodeDisplayName" not in advanced_view_surfaces
    assert "resolveTraceNodeTypeColor" not in advanced_view_surfaces
    assert "FULL_CHAIN_SCHEMA" not in advanced_view_surfaces
    assert "buildImmuneStageMeta" not in advanced_view_surfaces
    assert "getTraceGroupNodeIds" not in advanced_view_surfaces
    assert "Object.fromEntries" not in advanced_view_surfaces
    assert "new Set(group.nodeIds)" not in advanced_view_surfaces
    assert "Array.from({ length: 6" not in advanced_view_surfaces
    assert "Math.max(((node.latency_ms" not in advanced_view_surfaces


def test_trace_advanced_views_are_split_by_surface() -> None:
    aggregate = (OBSERVABILITY_TRACE_COMPONENTS / "TraceAdvancedViews.jsx").read_text(encoding="utf-8")
    full_chain_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "FullChainView.jsx"
    ).read_text(encoding="utf-8")
    gantt_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "GanttView.jsx"
    ).read_text(encoding="utf-8")
    group_dag_view = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "GroupDagView.jsx"
    ).read_text(encoding="utf-8")
    inline_overview = (
        OBSERVABILITY_TRACE_COMPONENTS / "advancedViews" / "InlineFullChainOverview.jsx"
    ).read_text(encoding="utf-8")

    assert "export { default as FullChainView }" in aggregate
    assert "export { GroupDagRoute }" in aggregate
    assert "export { default as InlineFullChainOverview }" in aggregate
    assert "ReactFlow" not in aggregate
    assert "useNodesState" not in aggregate
    assert "useEffect" not in aggregate

    assert "export default function FullChainView(" in full_chain_view
    assert "export default function GanttView(" in gantt_view
    assert "export default function GroupDagView(" in group_dag_view
    assert "export function GroupDagRoute(" in group_dag_view
    assert "export default function InlineFullChainOverview(" in inline_overview
    assert "NodeDetailPanel" in full_chain_view
    assert "NodeDetailPanel" in group_dag_view
    assert "NodeDetailPanel" in inline_overview
    assert "buildTraceGanttTicks(totalMs)" in gantt_view
    assert "getLiveTraceFlow(traceData)" in inline_overview


def test_trace_stage_model_lives_in_trace_feature_module() -> None:
    detail_model = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestDetailModel.jsx").read_text(encoding="utf-8")
    stage_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceStageModel.js").read_text(encoding="utf-8")

    for export_name in [
        "TRACE_STAGE_GROUPS",
        "TRACE_STAGE_OWNER_BY_NODE_ID",
        "TRACE_STAGE_GROUP_META_BY_ID",
        "TRACE_TIMELINE_STAGE_ORDER",
        "TRACE_FALLBACK_STAGE_META",
        "TRACE_STABLE_STAGE_LABELS",
        "normalizeTraceStageNodeId",
        "resolveTraceStageGroupId",
        "getStableStageLabel",
    ]:
        assert export_name in stage_model

    assert "from './traceStageModel.js'" in detail_model
    assert "const TRACE_STAGE_GROUPS = [" not in detail_model
    assert "const TRACE_STAGE_OWNER_BY_NODE_ID" not in detail_model
    assert "const TRACE_STAGE_GROUP_META_BY_ID" not in detail_model
    assert "const TRACE_TIMELINE_STAGE_ORDER" not in detail_model
    assert "const STABLE_STAGE_LABEL_BY_ID" not in detail_model
    assert "function normalizeTraceStageNodeId(" not in detail_model
    assert "function resolveTraceStageGroupId(" not in detail_model
    assert "function getStableStageLabel(" not in detail_model
    assert "label: '边缘输入'" not in detail_model
    assert "nodeIds: ['asr', 'gateway_webhook', 'input_reporter']" not in detail_model


def test_trace_unified_timeline_model_lives_in_trace_feature_module() -> None:
    detail_model = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestDetailModel.jsx").read_text(encoding="utf-8")
    timeline_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceUnifiedTimelineModel.js").read_text(encoding="utf-8")
    runtime_panels = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestRuntimePanels.jsx").read_text(encoding="utf-8")
    unified_timeline_panel = (
        OBSERVABILITY_TRACE_COMPONENTS / "runtimePanels" / "UnifiedTimelinePanel.jsx"
    ).read_text(encoding="utf-8")

    for export_name in [
        "resolveTribeEventStageId",
        "resolveTribeEventStatus",
        "buildUnifiedTimelineItems",
        "buildUnifiedTimelineStageOptions",
        "filterUnifiedTimelineItems",
        "buildUnifiedTimelineView",
    ]:
        assert export_name in timeline_model

    assert "from './traceUnifiedTimelineModel.js'" in detail_model
    assert "from '../traceUnifiedTimelineModel.js'" in unified_timeline_panel
    assert "buildUnifiedTimelineItems as buildTraceUnifiedTimelineItems" in detail_model
    assert "return buildTraceUnifiedTimelineItems(nodes, tribeEvents" in detail_model
    assert "buildUnifiedTimelineView({ items, typeFilter, stageFilter, keyword })" in unified_timeline_panel
    assert "stageLabel: getStableStageLabel(stageId)" in timeline_model
    assert "TRACE_TIMELINE_STAGE_ORDER.filter((stageId) => stageIds.has(stageId))" in timeline_model
    assert "function resolveTribeEventStageId(" not in detail_model
    assert "function resolveTribeEventStatus(" not in detail_model
    assert "voice_" not in detail_model
    assert "channel_message" not in detail_model
    assert "type: 'tribe'" not in detail_model
    assert "resolveTraceStageGroupId(node)" in timeline_model

    assert "TRACE_TIMELINE_STAGE_ORDER" not in runtime_panels
    assert "getStableStageLabel" not in runtime_panels
    assert "new Set(items.map((item) => item.stageId)" not in runtime_panels
    assert "const ordered = TRACE_TIMELINE_STAGE_ORDER.filter" not in runtime_panels
    assert "const extras = [...stageIds]" not in runtime_panels
    assert "keyword.trim().toLowerCase()" not in runtime_panels
    assert "item.title || ''" not in runtime_panels
    assert "item.stageId !== stageFilter" not in runtime_panels


def test_trace_preview_model_lives_in_trace_feature_module() -> None:
    detail_model = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestDetailModel.jsx").read_text(encoding="utf-8")
    preview_model = (OBSERVABILITY_TRACE_COMPONENTS / "tracePreviewModel.js").read_text(encoding="utf-8")

    for export_name in [
        "safeStringify",
        "truncateText",
        "pickPreviewText",
        "formatIntent",
        "getSummaryInputText",
        "getSummaryOutputText",
        "getNodePreview",
        "getEventPreview",
        "getTribePayloadPreview",
    ]:
        assert f"export function {export_name}(" in preview_model

    assert "from './tracePreviewModel.js'" in detail_model
    assert "function safeStringify(" not in detail_model
    assert "function truncateText(" not in detail_model
    assert "function pickPreviewText(" not in detail_model
    assert "function formatIntent(" not in detail_model
    assert "function getSummaryInputText(" not in detail_model
    assert "function getSummaryOutputText(" not in detail_model
    assert "function getNodePreview(" not in detail_model
    assert "function getEventPreview(" not in detail_model
    assert "function getTribePayloadPreview(" not in detail_model
    assert "value.output_text" not in detail_model
    assert "summary?.audit_snapshot?.input_text" not in detail_model


def test_trace_request_runtime_model_lives_in_trace_feature_module() -> None:
    detail_model = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestDetailModel.jsx").read_text(encoding="utf-8")
    runtime_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceRequestRuntimeModel.js").read_text(encoding="utf-8")
    immune_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceImmuneModel.js").read_text(encoding="utf-8")
    flow_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceFlowModel.js").read_text(encoding="utf-8")

    for export_name in [
        "ISSUE_STATUSES",
        "RUNNING_STATUSES",
    ]:
        assert f"export const {export_name}" in runtime_model

    for export_name in [
        "normalizeStatus",
        "buildStageSnapshots",
        "buildStageTimelineSections",
        "buildRetrievalRuntimeMeta",
    ]:
        assert f"export function {export_name}(" in runtime_model

    assert "export { buildImmuneStageMeta } from './traceImmuneModel.js'" in runtime_model
    assert "export function buildImmuneStageMeta(" in immune_model

    assert "from './traceRequestRuntimeModel.js'" in detail_model
    assert "export const ISSUE_STATUSES" not in detail_model
    assert "export const RUNNING_STATUSES" not in detail_model
    assert "function normalizeStatus(" not in detail_model
    assert "function getStatusSeverity(" not in detail_model
    assert "function getStageStatus(" not in detail_model
    assert "function getStagePreview(" not in detail_model
    assert "function buildStageSnapshots(" not in detail_model
    assert "function buildImmuneStageMeta(" not in detail_model
    assert "function buildStageTimelineSections(" not in detail_model
    assert "function buildRetrievalRuntimeMeta(" not in detail_model
    assert "TRACE_STAGE_OWNER_BY_NODE_ID[normalizedId]" not in detail_model
    assert "read_write_model_aligned" not in detail_model
    assert "recent_embedding_fallbacks" not in detail_model
    assert "reason_codes" not in detail_model

    assert "from './traceImmuneModel.js'" in flow_model
    assert "export function buildImmuneStageMeta(" not in flow_model
    assert "reason_codes" not in flow_model


def test_trace_request_runtime_panel_model_lives_in_trace_feature_module() -> None:
    runtime_panels = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestRuntimePanels.jsx").read_text(encoding="utf-8")
    runtime_panel_dir = OBSERVABILITY_TRACE_COMPONENTS / "runtimePanels"
    subgraph_flow_panel = (runtime_panel_dir / "SubgraphMiniFlow.jsx").read_text(encoding="utf-8")
    unified_timeline_panel = (runtime_panel_dir / "UnifiedTimelinePanel.jsx").read_text(encoding="utf-8")
    panel_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceRequestRuntimePanelModel.js").read_text(encoding="utf-8")

    for export_name in [
        "dedupeStreamEvents",
        "buildDeltaEntries",
        "mergeDeltaEntries",
        "buildSubgraphFlow",
    ]:
        assert f"export function {export_name}(" in panel_model

    assert "from './traceRequestRuntimePanelModel.js'" in runtime_panels
    assert "export { buildDeltaEntries, buildSubgraphFlow, dedupeStreamEvents, mergeDeltaEntries }" in runtime_panels
    assert "export { default as StageOverviewPanel } from './runtimePanels/StageOverviewPanel.jsx'" in runtime_panels
    assert "export { default as UnifiedTimelinePanel } from './runtimePanels/UnifiedTimelinePanel.jsx'" in runtime_panels
    assert "ReactFlow" in subgraph_flow_panel
    assert "@xyflow/react/dist/style.css" in subgraph_flow_panel
    assert "buildUnifiedTimelineView({ items, typeFilter, stageFilter, keyword })" in unified_timeline_panel
    assert "MarkerType } from '@xyflow/react'" in panel_model
    assert "applyDagreLayout } from '../../../../utils/traceLayout.js'" in panel_model
    assert "getTraceNodeDisplayName } from './traceNodeText.js'" in panel_model
    assert "getNodePreview } from './tracePreviewModel.js'" in panel_model

    assert "MarkerType" not in runtime_panels
    assert "applyDagreLayout" not in runtime_panels
    assert "export function dedupeStreamEvents(" not in runtime_panels
    assert "export function buildDeltaEntries(" not in runtime_panels
    assert "export function mergeDeltaEntries(" not in runtime_panels
    assert "export function buildSubgraphFlow(" not in runtime_panels
    assert "item?.chunk ?? ''" not in runtime_panels
    assert "change?.change_type || 'updated'" not in runtime_panels
    assert "MarkerType.ArrowClosed" not in runtime_panels


def test_trace_live_surface_model_lives_in_trace_feature_module() -> None:
    live_surfaces = (OBSERVABILITY_TRACE_COMPONENTS / "TraceLiveSurfaces.jsx").read_text(encoding="utf-8")
    live_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceLiveSurfaceModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getLiveTraceStageClasses",
        "getLiveTraceOutputPreview",
        "getLiveTraceCurrentLabel",
        "buildLiveTraceMetrics",
        "getTraceIssueCount",
        "getTraceRunningCount",
        "getTraceActiveStageMeta",
        "getSessionDisplayTitle",
        "mergeLiveTraceTimelineEntries",
        "getLiveTraceTimelineEntries",
        "getLiveTraceChangeBadgeClass",
        "formatEventPayloadPreview",
        "getRecentRequestStatusMeta",
        "formatRecentTraceTime",
        "isTribeEventLinkedToSelection",
    ]:
        assert f"export function {export_name}(" in live_model

    assert "from './traceLiveSurfaceModel.js'" in live_surfaces
    assert "formatDateTime } from '../../../../shared/formatters.js'" in live_model
    assert "formatAlertTitle, humanizeToken } from '../../../../utils/digitalLife.js'" in live_model
    assert "getLatestPrimaryTraceNode" in live_model
    assert "normalizeTraceGroupNodeId" in live_model
    assert "const preview = pickLiveTracePreviewValue(candidate)" in live_model
    assert "function renderImmuneStageTags(" in live_surfaces
    assert "<RenderValue value={code} />" in live_surfaces

    assert "function getLiveTraceStageClasses(" not in live_surfaces
    assert "function getLiveTraceOutputPreview(" not in live_surfaces
    assert "function getLiveTraceCurrentLabel(" not in live_surfaces
    assert "function buildLiveTraceMetrics(" not in live_surfaces
    assert "function getTraceIssueCount(" not in live_surfaces
    assert "function getTraceRunningCount(" not in live_surfaces
    assert "function getTraceActiveStageMeta(" not in live_surfaces
    assert "function getSessionDisplayTitle(" not in live_surfaces
    assert "function pickLiveTracePreviewValue(" not in live_surfaces
    assert "function buildSnapshotTimelineEntries(" not in live_surfaces
    assert "function buildDeltaTimelineEntries(" not in live_surfaces
    assert "function mergeLiveTraceTimelineEntries(" not in live_surfaces
    assert "function getRecentRequestStatusMeta(" not in live_surfaces
    assert "function formatRecentTraceTime(" not in live_surfaces
    assert "function normalizeOptionalKeyValue(" not in live_surfaces
    assert "function isTribeEventLinkedToSelection(" not in live_surfaces
    assert "LIVE_TRACE_KEY_NODE_IDS" not in live_surfaces
    assert "RECENT_TRACE_TIME_OPTIONS" not in live_surfaces
    assert "formatAlertTitle" not in live_surfaces
    assert "humanizeToken" not in live_surfaces
    assert "getLatestPrimaryTraceNode" not in live_surfaces
    assert "normalizeTraceGroupNodeId" not in live_surfaces


def test_trace_home_model_lives_in_trace_feature_module() -> None:
    trace_home = (OBSERVABILITY_TRACE_COMPONENTS / "TraceHome.jsx").read_text(encoding="utf-8")
    trace_home_controller = (OBSERVABILITY_TRACE_COMPONENTS / "traceHomeController.js").read_text(encoding="utf-8")
    trace_home_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceHomeModel.js").read_text(encoding="utf-8")

    for export_name in [
        "buildTraceRecentRequests",
        "getSelectedTraceSessionForQuery",
        "buildTraceTribeFeedParams",
        "buildTraceLiveSnapshotParams",
        "buildTraceLiveFeedConnectParams",
        "getTracePayloadItems",
        "pruneTraceTimelineEntriesForSnapshot",
        "buildTraceInfoFromTrace",
        "buildTraceSearchState",
        "buildTraceDetailPath",
        "getTraceOverviewFocusAction",
        "getLiveTraceItemFocusAction",
        "buildTraceGroupStats",
        "getTraceStatusHealth",
        "buildSelectedTraceRequestContext",
        "getNextAutoTraceSelection",
        "getTraceLiveFeedStatus",
        "buildTraceHistoryViewState",
        "getTraceLiveModeLabel",
        "buildTracePulseNodes",
    ]:
        assert f"export function {export_name}(" in trace_home_model

    assert "from './traceHomeModel.js'" in trace_home_controller
    assert "buildTraceRecentRequests({" in trace_home_controller
    assert "getSelectedTraceSessionForQuery({" in trace_home_controller
    assert "buildTraceTribeFeedParams({" in trace_home_controller
    assert "buildTraceLiveSnapshotParams({ liveTraceLimit, recentKeyword })" in trace_home_controller
    assert "buildTraceLiveFeedConnectParams({ liveTraceLimit, recentKeyword })" in trace_home_controller
    assert "getTracePayloadItems(payload)" in trace_home_controller
    assert "getTracePayloadItems(liveSnapshotQuery.liveSnapshot)" in trace_home_controller
    assert "pruneTraceTimelineEntriesForSnapshot(previous, items)" in trace_home_controller
    assert "buildTraceInfoFromTrace(selectedTraceQuery.traceSnapshot)" in trace_home_controller
    assert "buildTraceInfoFromTrace(item.trace)" in trace_home_controller
    assert "buildTraceSearchState(inputId)" in trace_home_controller
    assert "navigate(buildTraceDetailPath(requestId))" in trace_home_controller
    assert "getTraceOverviewFocusAction({ requestId, traceData, liveTraceItems })" in trace_home_controller
    assert "getLiveTraceItemFocusAction({" in trace_home_controller
    assert "buildTraceGroupStats(traceData)" in trace_home_controller
    assert "getTraceStatusHealth(traceInfo)" in trace_home_controller
    assert "buildSelectedTraceRequestContext({" in trace_home_controller
    assert "getNextAutoTraceSelection({ liveTraceItems, selectedRequestId, traceData })" in trace_home_controller
    assert "getTraceLiveFeedStatus({" in trace_home_controller
    assert "buildTraceHistoryViewState({" in trace_home_controller
    assert "buildTracePulseNodes({" in trace_home_controller
    assert "useTraceHomeController" in trace_home
    assert "traceHomeController.js" in trace_home
    assert "useEffect" not in trace_home
    assert "useState" not in trace_home
    assert "useCallback" not in trace_home
    assert "useMemo" not in trace_home
    assert "connectTraceLiveFeed" not in trace_home
    assert "useSessionsListQuery" not in trace_home
    assert "useTraceLiveSnapshotQuery" not in trace_home
    assert "useTraceSnapshotQuery" not in trace_home
    assert "useTraceTribeFeedQuery" not in trace_home
    assert "useAppStore" not in trace_home
    assert "new Map(recentRequestsBase" not in trace_home
    assert "const params = { limit: selectedRequestId ? 200 : 60 }" not in trace_home
    assert "getTraceGroupState(" not in trace_home
    assert "active_within_ms: 60000" not in trace_home
    assert "interval_ms: 1000" not in trace_home
    assert "Array.isArray(payload?.items)" not in trace_home
    assert "Array.isArray(liveSnapshotQuery.liveSnapshot?.items)" not in trace_home
    assert "new Set(items.map(" not in trace_home
    assert "status: selectedTraceQuery.traceSnapshot.status" not in trace_home
    assert "status: item.trace.status" not in trace_home
    assert "inputId.trim()" not in trace_home
    assert "startsWith('req_')" not in trace_home
    assert "navigate(`/observability/trace/${requestId}`)" not in trace_home
    assert "const value = searchValue" not in trace_home
    assert "String(traceInfo?.status || '').toUpperCase()" not in trace_home
    assert "liveTraceItems.find((item) => item.session.request_id === selectedRequestId)" not in trace_home
    assert "liveTraceItems.find((item) => item.session.request_id === requestId)" not in trace_home
    assert "if (!item?.session?.request_id) return" not in trace_home
    assert "recentRequests.find((item) => item.request_id === selectedRequestId)" not in trace_home
    assert "tribeEvents.filter((event) => isTribeEventLinkedToSelection" not in trace_home
    assert "linkedTribeEvents.slice(0, 20)" not in trace_home
    assert "getTraceGroupNodeIds(" not in trace_home
    assert "buildImmuneStageMeta(" not in trace_home
    assert "实时流已暂停" not in trace_home
    assert "SSE 实时流已连接" not in trace_home
    assert "const tracePulseNodes = [" not in trace_home
    assert "label: '当前请求'" not in trace_home
    assert "历史请求结果" not in trace_home
    assert "实时活跃链路流" not in trace_home


def test_trace_home_linked_tribe_events_panel_is_split() -> None:
    trace_home = (OBSERVABILITY_TRACE_COMPONENTS / "TraceHome.jsx").read_text(encoding="utf-8")
    control_console = (
        OBSERVABILITY_TRACE_COMPONENTS / "traceHome" / "TraceHomeControlConsole.jsx"
    ).read_text(encoding="utf-8")
    tribe_panel = (
        OBSERVABILITY_TRACE_COMPONENTS / "TraceLinkedTribeEventsPanel.jsx"
    ).read_text(encoding="utf-8")

    assert "TraceHomeControlConsole from './traceHome/TraceHomeControlConsole.jsx'" in trace_home
    assert "<TraceHomeControlConsole" in trace_home
    assert "onRefreshTribeEvents={() => { void traceHome.refreshTribeEvents() }}" in trace_home
    assert "onOpenTribeInsights={() => traceHome.navigate('/observability/insights/safety')}" in trace_home

    assert "TraceLinkedTribeEventsPanel from '../TraceLinkedTribeEventsPanel.jsx'" in control_console
    assert "<TraceLinkedTribeEventsPanel" in control_console
    assert "selectedRequestId={selectedRequestId}" in control_console
    assert "linkedTribeEventList={linkedTribeEventList}" in control_console
    assert "onRefresh={onRefreshTribeEvents}" in control_console
    assert "onOpenInsights={onOpenTribeInsights}" in control_console

    assert "export default function TraceLinkedTribeEventsPanel(" in tribe_panel
    assert "Tribe 事件联动" in tribe_panel
    assert "formatEventPayloadPreview(event?.payload, 220)" in tribe_panel
    assert "formatRecentTraceTime(event.ts_ms)" in tribe_panel
    assert "EmptyStateCard" in tribe_panel
    assert "NoticeBanner" in tribe_panel

    assert "Tribe 事件联动" not in trace_home
    assert "linkedTribeEventList.map" not in trace_home
    assert "formatEventPayloadPreview(event?.payload" not in trace_home
    assert "Tribe 事件读取失败" not in trace_home
    assert "当前请求暂无 Tribe 事件" not in trace_home


def test_trace_home_surface_sections_are_split() -> None:
    trace_home = (OBSERVABILITY_TRACE_COMPONENTS / "TraceHome.jsx").read_text(encoding="utf-8")
    summary_band = (
        OBSERVABILITY_TRACE_COMPONENTS / "traceHome" / "TraceHomeSummaryBand.jsx"
    ).read_text(encoding="utf-8")
    control_console = (
        OBSERVABILITY_TRACE_COMPONENTS / "traceHome" / "TraceHomeControlConsole.jsx"
    ).read_text(encoding="utf-8")
    active_feed = (
        OBSERVABILITY_TRACE_COMPONENTS / "traceHome" / "TraceHomeActiveFeedPanel.jsx"
    ).read_text(encoding="utf-8")

    assert "TraceHomeSummaryBand from './traceHome/TraceHomeSummaryBand.jsx'" in trace_home
    assert "TraceHomeControlConsole from './traceHome/TraceHomeControlConsole.jsx'" in trace_home
    assert "TraceHomeActiveFeedPanel from './traceHome/TraceHomeActiveFeedPanel.jsx'" in trace_home
    assert "<TraceHomeSummaryBand" in trace_home
    assert "<TraceHomeControlConsole" in trace_home
    assert "<TraceHomeActiveFeedPanel" in trace_home

    assert "PageHero" in summary_band
    assert "LivingPulseStrip" in summary_band
    assert "HomeSummaryCard" in summary_band
    assert "TraceSection" in control_console
    assert "Search size={13}" in control_console
    assert "LiveTraceCard" in active_feed
    assert "LiveTraceStreamPreview" in active_feed
    assert "EmptyStateCard" in active_feed

    assert "PageHero" not in trace_home
    assert "LivingPulseStrip" not in trace_home
    assert "HomeSummaryCard" not in trace_home
    assert "TraceSection" not in trace_home
    assert "LiveTraceCard" not in trace_home
    assert "LiveTraceStreamPreview" not in trace_home


def test_trace_summary_panels_use_shared_json_block() -> None:
    source = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestSummaryPanels.jsx").read_text(encoding="utf-8")
    shared = (MONITOR_SRC / "shared" / "ui" / "JsonBlock.jsx").read_text(encoding="utf-8")

    assert "JsonBlock from '../../../../shared/ui/JsonBlock.jsx'" in source
    assert "export function JsonBlock(" not in source
    assert "safeStringify" not in source
    assert "JSON.stringify(data" not in source
    assert '<JsonBlock data={data} emptyLabel={emptyLabel} variant="light" />' in source
    assert "emptyLabel = null" in shared
    assert "variant = 'dark'" in shared
    assert "light:" in shared


def test_tools_execution_results_use_shared_json_block() -> None:
    tool_execute_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolExecutePanel.jsx"
    ).read_text(encoding="utf-8")
    tools_operations = (MONITOR_SRC / "features" / "execution" / "components" / "ToolsOperationsSection.jsx").read_text(encoding="utf-8")

    for rel, source in {
        "features/execution/components/ToolExecutePanel.jsx": tool_execute_panel,
        "features/execution/components/ToolsOperationsSection.jsx": tools_operations,
    }.items():
        assert "shared/ui/JsonBlock.jsx" in source, rel
        assert "JSON.stringify(result.data" not in source, rel
        assert 'variant="light"' in source, rel
        assert 'className="mt-1 border-green-200 bg-white/70 font-mono text-green-900"' in source, rel


def test_trace_live_history_and_runtime_panels_render_dynamic_values_safely() -> None:
    trace_surfaces = (OBSERVABILITY_TRACE_COMPONENTS / "TraceSurfaces.jsx").read_text(encoding="utf-8")
    live_surfaces = (OBSERVABILITY_TRACE_COMPONENTS / "TraceLiveSurfaces.jsx").read_text(encoding="utf-8")
    live_model = (OBSERVABILITY_TRACE_COMPONENTS / "traceLiveSurfaceModel.js").read_text(encoding="utf-8")
    history_panel = (OBSERVABILITY_TRACE_COMPONENTS / "TraceHistoryPanel.jsx").read_text(encoding="utf-8")
    advanced_view_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (OBSERVABILITY_TRACE_COMPONENTS / "advancedViews").glob("*.jsx")
    )
    runtime_panels = (OBSERVABILITY_TRACE_COMPONENTS / "TraceRequestRuntimePanels.jsx").read_text(encoding="utf-8")
    runtime_panel_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (OBSERVABILITY_TRACE_COMPONENTS / "runtimePanels").glob("*.jsx")
    )

    assert trace_surfaces.count("<RenderValue value={hint}") >= 3
    assert "<RenderValue value={caption}" in trace_surfaces
    assert "<RenderValue value={description}" in trace_surfaces
    assert "const preview = pickLiveTracePreviewValue(candidate)" in live_model
    assert "<RenderValue value={code} />" in live_surfaces
    assert "<RenderValue value={item.input_preview || item.request_id} />" in history_panel
    assert advanced_view_sources.count("<RenderValue value=") >= 4
    assert runtime_panel_sources.count("<RenderValue value=") >= 8
    assert "-reason-${code}" not in runtime_panel_sources
    assert "-rule-${rule}" not in runtime_panel_sources


def test_cross_feature_runtime_observability_fetchers_live_in_shared_api() -> None:
    shared = (MONITOR_SRC / "shared" / "api" / "runtimeObservability.js").read_text(encoding="utf-8")
    execution_api = (MONITOR_SRC / "features" / "execution" / "api.js").read_text(encoding="utf-8")
    observability_api = (MONITOR_SRC / "features" / "observability" / "api.js").read_text(encoding="utf-8")
    observability_runtime_api = "\n".join(
        [
            (MONITOR_SRC / "features" / "observability" / "overviewApi.js").read_text(encoding="utf-8"),
            (MONITOR_SRC / "features" / "observability" / "diagnosticsApi.js").read_text(encoding="utf-8"),
        ]
    )
    shared_fetchers = [
        "fetchAgentLoopStats",
        "fetchAgentLoopHistory",
        "fetchUIAssertionStats",
        "fetchPermissionStats",
        "fetchSchedulerHistory",
    ]

    for fetcher in shared_fetchers:
        assert f"export function {fetcher}(" in shared
        assert f"export function {fetcher}(" not in execution_api
        assert f"export function {fetcher}(" not in observability_api

    assert "from '../../shared/api/runtimeObservability.js'" in execution_api
    assert "from '../../shared/api/runtimeObservability.js'" in observability_runtime_api
    assert "ENDPOINT_REGISTRY.observability" in shared or "const { observability } = ENDPOINT_REGISTRY" in shared


def test_cross_feature_shared_observability_fetchers_live_in_shared_api() -> None:
    shared = SHARED_OBSERVABILITY_API.read_text(encoding="utf-8")
    feature_sources = {
        "life": (MONITOR_SRC / "features" / "life" / "api.js").read_text(encoding="utf-8"),
        "observability": "\n".join(
            [
                (MONITOR_SRC / "features" / "observability" / "api.js").read_text(encoding="utf-8"),
                (MONITOR_SRC / "features" / "observability" / "bioOpsApi.js").read_text(encoding="utf-8"),
                (MONITOR_SRC / "features" / "observability" / "diagnosticsApi.js").read_text(encoding="utf-8"),
                (MONITOR_SRC / "features" / "observability" / "metricsApi.js").read_text(encoding="utf-8"),
                (MONITOR_SRC / "features" / "observability" / "overviewApi.js").read_text(encoding="utf-8"),
                (MONITOR_SRC / "features" / "observability" / "traceApi.js").read_text(encoding="utf-8"),
            ]
        ),
        "operations": (MONITOR_SRC / "features" / "operations" / "api.js").read_text(encoding="utf-8"),
    }
    shared_fetchers = [
        "fetchCanonDocuments",
        "fetchCodexBioOpsObservability",
        "fetchContextCompressorStats",
        "fetchDistillationStats",
        "fetchEpisodicStoreStats",
        "fetchLlmCallStats",
        "fetchLlmRouteStats",
        "fetchRateLimitBackendStats",
        "fetchRetrievalRuntime",
        "fetchServicesHealth",
    ]

    assert "from './endpointClient.js'" in shared
    for fetcher in shared_fetchers:
        assert f"export function {fetcher}(" in shared
        for source in feature_sources.values():
            assert f"export function {fetcher}(" not in source

    assert "from '../../shared/api/sharedObservability.js'" in feature_sources["life"]
    assert "from '../../shared/api/sharedObservability.js'" in feature_sources["observability"]
    assert "from '../../shared/api/sharedObservability.js'" in feature_sources["operations"]


def test_feature_api_fetch_function_definitions_are_not_duplicated() -> None:
    owners: dict[str, list[str]] = {}
    for path in sorted((MONITOR_SRC / "features").glob("*/api.js")):
        rel = _relative(path)
        for match in FETCH_FUNCTION_EXPORT.finditer(path.read_text(encoding="utf-8")):
            owners.setdefault(match.group("name"), []).append(rel)

    duplicates = {
        name: locations
        for name, locations in sorted(owners.items())
        if len(locations) > 1
    }

    assert duplicates == {}


def test_repeated_time_and_percent_formatters_use_shared_module() -> None:
    shared = SHARED_FORMATTERS.read_text(encoding="utf-8")
    digital_life_utils = (MONITOR_SRC / "utils" / "digitalLife.js").read_text(encoding="utf-8")
    migrated_files = {
        "features/execution/components/AgentLoopConsoleSections.jsx": ["function formatTime("],
        "features/execution/components/AutomationResourceSchedulingPanel.jsx": [
            "function formatPercent(",
            "function formatLocalTime(",
        ],
        "pages/Gateway.jsx": ["function formatTime("],
        "features/observability/logStreamModel.js": ["function formatTime("],
        "features/execution/components/AutomationImmuneSectionContent.jsx": ["function formatPercent("],
        "features/operations/components/canon/CanonDocumentsSection.jsx": ["function formatDateTime("],
        "features/operations/components/canon/CanonProposalsSection.jsx": ["function formatDateTime("],
        "features/life/digitalLifeGatewayModel.js": ["function formatPercent("],
    }
    migrated_display_files = [
        "features/life/components/AvatarStateCard.jsx",
        "features/operations/components/canon/CanonDocumentsSection.jsx",
        "features/life/digitalLifeGatewayModel.js",
        "features/operations/components/promptOps/PromptTemplateEditorPanel.jsx",
        "features/execution/components/ToolsOperationsSection.jsx",
        "features/observability/components/insights/InsightsStateGroup.jsx",
        "features/observability/components/insights/InsightsLlmGroup.jsx",
        "features/observability/components/insights/insightsGroupShared.jsx",
        "features/observability/components/trace/NodeDetailPanel.jsx",
        "features/observability/components/trace/traceLiveSurfaceModel.js",
        "features/observability/components/trace/TraceRequestDetailModel.jsx",
        "features/operations/components/canon/CanonProposalsSection.jsx",
        "features/observability/components/chatDebug/ChatDebugEventStream.jsx",
        "features/observability/components/chatDebug/ChatTraceSidebar.jsx",
        "pages/CodexBioOpsObservability.jsx",
        "features/operations/promptOpsModel.js",
        "pages/RuntimeMode.jsx",
    ]

    assert "export function formatTime(" in shared
    assert "export function formatDateTime(" in shared
    assert "export function formatPercent(" in shared
    assert "export function formatNumber(" in shared
    assert "formatSharedDateTime" in digital_life_utils
    assert "formatSharedNumber" in digital_life_utils
    assert "new Date(" not in digital_life_utils
    assert ".toLocaleString(" not in digital_life_utils
    assert "new Intl.NumberFormat(" not in digital_life_utils

    for rel, forbidden in migrated_files.items():
        source = (MONITOR_SRC / rel).read_text(encoding="utf-8")
        assert "shared/formatters.js" in source
        for needle in forbidden:
            assert needle not in source, rel

    for rel in migrated_display_files:
        source = (MONITOR_SRC / rel).read_text(encoding="utf-8")
        assert "shared/formatters.js" in source, rel
        assert "new Date(" not in source, rel
        assert ".toLocaleString(" not in source, rel
        assert ".toLocaleTimeString(" not in source, rel


def test_digital_life_gateway_view_model_lives_in_life_feature() -> None:
    component = (MONITOR_SRC / "features" / "life" / "components" / "DigitalLifeGatewaySection.jsx").read_text(encoding="utf-8")
    gateway_components_dir = MONITOR_SRC / "features" / "life" / "components" / "digitalLifeGateway"
    trend_panel = (gateway_components_dir / "GatewayTrendPanel.jsx").read_text(encoding="utf-8")
    hotspot_panel = (gateway_components_dir / "GatewayHotspotsPanel.jsx").read_text(encoding="utf-8")
    hotspot_trend_panel = (gateway_components_dir / "GatewayHotspotTrendPanel.jsx").read_text(encoding="utf-8")
    hotspot_cards = (gateway_components_dir / "GatewayHotspotCards.jsx").read_text(encoding="utf-8")
    source_correlation_panel = (gateway_components_dir / "GatewaySourceCorrelationPanel.jsx").read_text(encoding="utf-8")
    diagnostics_panel = (gateway_components_dir / "GatewayDiagnosticsPanel.jsx").read_text(encoding="utf-8")
    remediation_panel = (gateway_components_dir / "GatewayRemediationPanel.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "life" / "digitalLifeGatewayModel.js").read_text(encoding="utf-8")

    for export_name in [
        "formatGatewayPercent",
        "toGatewayTrendIntervalMinutes",
        "getHumanoidTrendTone",
        "getHumanoidAlertBannerVariant",
        "getHumanoidDiagnosticStatusClass",
        "getHumanoidHotspotSeverityClass",
        "getHumanoidSourceImpactClass",
        "getHumanoidHotspotTrendColor",
        "buildDigitalLifeGatewayView",
    ]:
        assert export_name in model

    assert "digitalLifeGatewayModel.js" in component
    assert "buildDigitalLifeGatewayView(model)" in component
    assert "GatewayTrendPanel" in component
    assert "GatewayLoopStatsPanel" in component
    assert "GatewayRemediationPanel" in component
    assert "GatewayHotspotsPanel" in remediation_panel
    assert "GatewayDiagnosticsPanel" in remediation_panel
    assert "GatewayHotspotTrendPanel" in hotspot_panel
    assert "GatewaySourceCorrelationPanel" in hotspot_panel
    assert "GatewayHotspotCards" in hotspot_panel
    assert "getHumanoidHotspotTrendColor" in hotspot_trend_panel
    assert "getHumanoidSourceImpactClass" in source_correlation_panel
    assert "getHumanoidHotspotSeverityClass" in hotspot_cards
    assert "getHumanoidDiagnosticStatusClass" in diagnostics_panel
    assert "hasAlertsData && humanoidAlertStatus !== 'healthy'" in trend_panel
    assert "shared/formatters.js" in model
    assert "function toTrendIntervalMinutes(" not in component
    assert "function getHumanoidTrendTone(" not in component
    assert "function getHumanoidAlertBannerVariant(" not in component
    assert "function getHumanoidDiagnosticStatusClass(" not in component
    assert "function getHumanoidHotspotSeverityClass(" not in component
    assert "function getHumanoidSourceImpactClass(" not in component
    assert "function getHumanoidHotspotTrendColor(" not in component
    assert "formatPercent(" not in component
    assert "LineChart" not in component
    assert "RepresentativeRequests" not in component
    assert "compactText(" not in component
    assert "timeseries_closed_loop_rate" not in component
    assert "timeseries_total_gateway_requests" not in component
    assert "humanoidTrendRequestSeries.reduce" not in component
    assert "Math.max(2, Math.floor" not in component
    assert "createRequestRefs(humanoidLoopRemediationData" not in component
    assert "String(humanoidLoopAlertsData?.status" not in component
    assert "motor_execution: '#EF4444'" not in component


def test_digital_life_overview_view_model_lives_in_life_feature() -> None:
    page = (MONITOR_SRC / "pages" / "DigitalLifeOverview.jsx").read_text(encoding="utf-8")
    controller = (MONITOR_SRC / "features" / "life" / "digitalLifeOverviewController.js").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "life" / "digitalLifeOverviewModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getDigitalLifePollingInterval",
        "buildDigitalLifeHeroDescription",
        "buildDigitalLifeHeroMetaItems",
        "buildDigitalLifeCapabilitySectionModel",
        "buildDigitalLifeWiredDomains",
        "buildDigitalLifeSecondarySectionsModel",
        "buildDigitalLifeGatewaySectionModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/life/digitalLifeOverviewController.js" in page
    assert "./digitalLifeOverviewModel.js" in controller
    assert "getDigitalLifePollingInterval(pollingInterval, 60)" in controller
    assert "getDigitalLifePollingInterval(pollingInterval, 15)" in controller
    assert "buildDigitalLifeWiredDomains({" in controller
    assert "buildDigitalLifeCapabilitySectionModel(capabilityGapData)" in controller
    assert "buildDigitalLifeSecondarySectionsModel({" in controller
    assert "buildDigitalLifeGatewaySectionModel({" in controller
    assert "buildDigitalLifeHeroDescription({ runtimeSnapshot, lifeBodyModel, hasRuntimeSnapshot })" in controller
    assert "buildDigitalLifeHeroMetaItems({ modeMeta, lifeBodyModel, replyPosture })" in controller
    assert "postAvatarBehavior({ behavior_id: behaviorId, source: 'monitor_digital_life' })" in controller
    assert "useLifeBodyPageData({ windowMinutes })" in controller
    assert "useDigitalLifeWallClockSoakQuery({" in controller
    assert "useAvatarStateQuery({" in controller

    for controller_only_token in [
        "useLifeBodyPageData",
        "useDigitalLifeWallClockSoakQuery",
        "useAvatarStateQuery",
        "postAvatarBehavior",
        "buildMainChainReshapeModel",
        "buildDigitalLifeWiredDomains",
    ]:
        assert controller_only_token not in page

    assert "compactText(" not in page
    assert "formatTrend(" not in page
    assert ".sort((left, right) => Number(left?.health_score" not in page
    assert "forge_runtime_quality?.items" not in page
    assert "compactText(" not in controller
    assert "formatTrend(" not in controller
    assert ".sort((left, right) => Number(left?.health_score" not in controller
    assert "forge_runtime_quality?.items" not in controller
    assert "actionTo: '/life/runtime-mode'" not in page
    assert "等待运行快照恢复后补齐关系阶段" not in page
    assert "生命流正在穿过关系、记忆、节律与执行层" not in page
    assert "Math.max(60" not in page
    assert "Math.max(15" not in page


def test_insights_visual_metadata_lives_in_observability_feature() -> None:
    insights_global = (MONITOR_SRC / "features" / "observability" / "components" / "insights" / "InsightsGlobalGroup.jsx").read_text(encoding="utf-8")
    insights_global_model = (MONITOR_SRC / "features" / "observability" / "insightsGlobalModel.js").read_text(encoding="utf-8")
    visual_meta = (MONITOR_SRC / "features" / "observability" / "components" / "insights" / "insightsVisualMeta.js").read_text(encoding="utf-8")

    for export_name in [
        "INSIGHTS_CHAIN_NODE_COLORS",
        "resolveInsightsChainNodeColor",
    ]:
        assert export_name in visual_meta

    assert "from './components/insights/insightsVisualMeta.js'" in insights_global_model
    assert "from './insightsVisualMeta.js'" not in insights_global
    assert "const NODE_COLOR" not in insights_global
    assert "governor: '#7A5AF8'" not in insights_global
    assert "qa_contract: '#12B76A'" not in insights_global
    assert "resolveInsightsChainNodeColor(node.node_name)" in insights_global_model
    assert "resolveInsightsChainNodeColor(node.node_name)" not in insights_global


def test_canon_and_provider_status_metadata_live_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    migrated_files = {
        "features/operations/components/canon/CanonDocumentsSection.jsx": [
            "const MANIFEST_STATUS_LABELS",
        ],
        "features/operations/components/canon/CanonProposalsSection.jsx": [
            "const STATUS_STYLES",
            "const PROPOSAL_STATUS_LABELS",
        ],
        "features/execution/components/ToolsOperationsSection.jsx": [
            "const PROVIDER_STATUS_LABELS",
            "const styles = {",
        ],
        "features/operations/canonModel.js": [
            "const MANIFEST_STATUS_LABELS",
            "item.status === 'ACTIVE'",
            "item.status === 'UNDONE'",
        ],
    }

    for export_name in [
        "CANON_MANIFEST_STATUS_LABELS",
        "CANON_MANIFEST_STATUS_CLASSES",
        "CANON_PROPOSAL_STATUS_LABELS",
        "CANON_PROPOSAL_STATUS_CLASSES",
        "TOOL_PROVIDER_STATUS_LABELS",
        "TOOL_PROVIDER_STATUS_CLASSES",
        "resolveStatusLabel",
        "resolveStatusClass",
    ]:
        assert export_name in shared

    for rel, forbidden in migrated_files.items():
        source = (MONITOR_SRC / rel).read_text(encoding="utf-8")
        assert "shared/statusMeta.js" in source
        for needle in forbidden:
            assert needle not in source, rel


def test_canon_view_model_lives_in_operations_feature() -> None:
    page = (MONITOR_SRC / "pages" / "Canon.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "operations" / "canonModel.js").read_text(encoding="utf-8")

    assert "export const CANON_PAGE_SIZE" in model
    assert "export const CANON_TABS" in model
    for export_name in [
        "getCanonTabState",
        "buildCanonActiveLoadState",
        "buildCanonVaultLoadState",
        "buildCanonHeroView",
        "isCanonRefreshing",
        "buildCanonVaultMetricCards",
        "buildCanonVaultProgress",
        "buildCanonRecentManifestRows",
        "buildCanonViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/operations/canonModel.js" in page
    assert "getCanonTabState(activeTab)" in page
    assert "buildCanonViewModel({" in page
    assert "CANON_TABS.map" in page
    assert "CANON_PAGE_SIZE" in page
    assert "vaultMetricCards.map" in page
    assert "vaultProgress.map" in page
    assert "recentManifestRows.map" in page

    for forbidden in [
        "resolveLoadState",
        "CANON_MANIFEST_STATUS_CLASSES",
        "CANON_MANIFEST_STATUS_LABELS",
        "resolveStatusClass",
        "resolveStatusLabel",
        "const PAGE_SIZE",
        "activeTab === 'proposals'",
        "activeTab === 'documents'",
        "const activeError",
        "const activeLoadState",
        "const vaultLoadState",
        "proposalsActive ? `提案数",
        "statusFilter || '全部'",
        "vaultData.active_count",
        "vaultData.undone_count",
        "vaultData.gc_count",
        "Math.round((vaultData",
        "vaultData.recent_manifests",
        "item.status)",
        "item.status,",
        "item.is_reversible",
    ]:
        assert forbidden not in page


def test_skills_hub_display_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    skills_hub = (MONITOR_SRC / "pages" / "SkillsHub.jsx").read_text(encoding="utf-8")

    for export_name in [
        "SKILL_CLEARANCE_STYLES",
        "SKILL_SOURCE_STYLES",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in skills_hub
    assert "const CLEARANCE_STYLES" not in skills_hub
    assert "const SOURCE_STYLES" not in skills_hub
    assert "bg-purple-100', text: 'text-purple-700'" not in skills_hub
    assert "label: 'GitHub'" not in skills_hub
    assert "label: 'MEDIUM'" not in skills_hub


def test_skills_hub_view_model_lives_in_execution_feature() -> None:
    skills_page = (MONITOR_SRC / "pages" / "SkillsHub.jsx").read_text(encoding="utf-8")
    skills_model = (MONITOR_SRC / "features" / "execution" / "skillsHubModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getSkillSource",
        "getSkillSourceLabel",
        "buildSkillSourceOptions",
        "filterSkills",
        "buildSkillSourceGroups",
        "buildSkillsHubSummaryItems",
        "buildSkillsHubHeroMetaItems",
        "buildSkillsHubHeroTitle",
        "buildSkillsHubEmptyState",
        "buildSkillsHubLoadState",
        "buildSkillsHubSampleMeta",
        "buildSkillsHubNoticeView",
        "buildSkillsHubExternalDirsView",
        "buildSkillsHubViewModel",
    ]:
        assert f"export function {export_name}(" in skills_model

    assert "features/execution/skillsHubModel.js" in skills_page
    assert "buildSkillsHubViewModel({" in skills_page
    assert "SKILL_SOURCE_FILTER_ALL" in skills_page
    assert "sampleMeta.loadState" in skills_page
    assert "noticeView.showNoSample" in skills_page
    assert "externalDirsView.hasExternalDirs" in skills_page
    assert "externalDirsView.rows.map" in skills_page
    assert "buildSkillsHubLoadState({" in skills_model
    assert "resolveLoadState({" in skills_model
    assert "LOAD_STATES.NO_SAMPLE" in skills_model
    assert "buildSkillsHubExternalDirsView(data)" in skills_model

    for needle in [
        "new Set(skills.map((skill) => skill.source || 'builtin'))",
        "skills.filter((skill) => {",
        "const groups = new Map()",
        "Array.from(groups.entries()).sort",
        "skills.filter((skill) => (skill.source || 'builtin') === source).length",
        "source === 'ALL' ? '全部'",
        "source === 'builtin' ? '内置技能'",
        "`技能总数：${data?.total_skills",
        "LOAD_STATES",
        "resolveLoadState",
        "sampleLabel=\"技能\"",
        "source=\"skills hub\"",
        "definition=\"技能中心页聚合",
        "LOAD_STATES.NO_SAMPLE",
        "data?.hub_enabled ? 'Skills Hub 已启用'",
        "data?.external_dirs_scanned",
        "data.external_dirs_scanned.map",
    ]:
        assert needle not in skills_page


def test_tools_execution_badge_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    catalog_browser = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsCatalogBrowser.jsx"
    ).read_text(encoding="utf-8")
    detail_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolDetailPanel.jsx"
    ).read_text(encoding="utf-8")

    for export_name in [
        "TOOL_SIDE_EFFECT_STYLES",
        "TOOL_AUTH_LEVEL_STYLES",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in catalog_browser
    assert "shared/statusMeta.js" in detail_panel
    for source in [tools_page, catalog_browser, detail_panel]:
        assert "const SIDE_EFFECT_STYLES" not in source
        assert "const AUTH_LEVEL_STYLES" not in source
        assert "label: '只读'" not in source
        assert "label: '审批确认'" not in source


def test_tools_catalog_model_lives_in_execution_feature() -> None:
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    pentest_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsPentestMetadataSection.jsx"
    ).read_text(encoding="utf-8")
    execute_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolExecutePanel.jsx"
    ).read_text(encoding="utf-8")
    tools_model = (MONITOR_SRC / "features" / "execution" / "toolsCatalogModel.js").read_text(encoding="utf-8")
    dashboard_model = (MONITOR_SRC / "features" / "execution" / "toolsDashboardModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getPentestToolMetadataView",
        "filterToolsBySideEffect",
        "getToolsCatalogCounts",
        "reconcileSelectedTool",
        "buildToolsHeroMetaItems",
        "buildToolExecutionRequest",
    ]:
        assert f"export function {export_name}(" in tools_model

    assert "features/execution/toolsCatalogModel.js" in tools_page
    assert "features/execution/toolsDashboardModel.js" in tools_page
    assert "getPentestToolMetadataView(capabilities)" in pentest_section
    assert "buildToolExecutionRequest(tool, form)" in execute_panel
    assert "reconcileSelectedTool({ selectedTool, executeTarget, tools })" in tools_page
    assert "filterToolsBySideEffect(toolItems, filter)" in dashboard_model
    assert "getToolsCatalogCounts(toolItems)" in dashboard_model
    assert "buildToolsHeroMetaItems({" in dashboard_model
    assert "filterToolsBySideEffect(tools, filter)" not in tools_page
    assert "getToolsCatalogCounts(tools)" not in tools_page
    assert "buildToolsHeroMetaItems({" not in tools_page

    for source in [tools_page, pentest_section, execute_panel]:
        assert "flattenPentestTools" not in source
        assert "JSON.parse(form.args_json" not in source
        assert "approval_token_ref.trim()" not in source
        assert "tools.find((tool) => tool.id === selectedTool.id)" not in source
        assert "tool.side_effect_level !== 'READ'" not in source
        assert "tool.side_effect_level !== 'WRITE'" not in source
        assert "tools.filter((tool) => tool.side_effect_level === 'READ')" not in source
        assert "tools.filter((tool) => tool.side_effect_level === 'WRITE')" not in source
        assert "capabilities?.phases" not in source
        assert "工具总数：${health?.catalog_size" not in source


def test_tools_dashboard_view_model_lives_in_execution_feature() -> None:
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    dashboard_model = (MONITOR_SRC / "features" / "execution" / "toolsDashboardModel.js").read_text(encoding="utf-8")

    for export_name in [
        "buildToolsLoadState",
        "buildToolsSampleMeta",
        "buildToolsNoticeView",
        "buildToolsOrganModel",
        "buildToolsHealthMetricCards",
        "buildToolsDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in dashboard_model

    assert "features/execution/toolsDashboardModel.js" in tools_page
    assert "buildToolsDashboardViewModel({" in tools_page
    assert "sampleMeta.loadState" in tools_page
    assert "noticeView.showUnavailable" in tools_page
    assert "noticeView.showNoSample" in tools_page
    assert "OrganSystemOverview model={toolsOrganModel}" in tools_page

    for forbidden in [
        "LOAD_STATES",
        "resolveLoadState",
        "buildToolsOrganSystemModel",
        "toolsLoadState",
        "sampleLabel=\"工具\"",
        "updatedAt={lastUpdatedAt}",
        "source=\"tool catalog\"",
        "definition=\"工具页把工具目录",
        "LOAD_STATES.UNAVAILABLE",
        "LOAD_STATES.NO_SAMPLE",
        "hasSamples:",
        "hasData:",
        "health?.status === 'OK'",
        "{ label: '引擎状态'",
        "{ label: '工具总数'",
        "runtimeSnapshot?.runtime_summary?.headline",
    ]:
        assert forbidden not in tools_page


def test_tools_queries_are_split_from_execution_query_monolith() -> None:
    execution_queries = _read_optional(MONITOR_SRC / "features" / "execution" / "queries.js")
    tools_queries = (MONITOR_SRC / "features" / "execution" / "toolsQueries.js").read_text(encoding="utf-8")
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    operations_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsOperationsSection.jsx"
    ).read_text(encoding="utf-8")

    for export_name in [
        "useToolsCatalogQuery",
        "usePermissionStatsQuery",
        "useActionPlanStatsQuery",
        "useCustomSkillProvidersQuery",
        "useCustomSkillProviderTemplateQuery",
        "refreshPermissionStatsQuery",
        "refreshActionPlanStatsQuery",
    ]:
        assert f"export function {export_name}" in tools_queries
        assert f"export function {export_name}" not in execution_queries

    for fetcher_name in [
        "fetchToolHealth",
        "fetchToolMetaPack",
        "fetchPermissionStats",
        "fetchActionPlanStats",
        "fetchCustomSkillProviders",
        "fetchCustomSkillProviderTemplate",
    ]:
        assert fetcher_name in tools_queries
        assert fetcher_name not in execution_queries

    assert "features/execution/toolsQueries.js" in tools_page
    assert "../toolsQueries.js" in operations_section
    assert "features/execution/queries.js" not in tools_page
    assert "from '../queries.js'" not in operations_section
    assert "OBS_QUERY_KEYS.toolsCatalog" in tools_queries
    assert "OBS_QUERY_KEYS.toolPermissionStats" in tools_queries
    assert "OBS_QUERY_KEYS.toolActionPlanStats" in tools_queries


def test_automation_queries_are_split_from_execution_query_monolith() -> None:
    execution_queries = _read_optional(MONITOR_SRC / "features" / "execution" / "queries.js")
    automation_queries = (MONITOR_SRC / "features" / "execution" / "automationQueries.js").read_text(encoding="utf-8")
    immune_replay_api = (MONITOR_SRC / "shared" / "api" / "immuneGate.js").read_text(encoding="utf-8")
    immune_replay_query = (MONITOR_SRC / "shared" / "query" / "useImmuneGateReplayQuery.js").read_text(encoding="utf-8")
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    trace_detail = (MONITOR_SRC / "pages" / "TraceRequestDetail.jsx").read_text(encoding="utf-8")
    trace_detail_controller = (
        MONITOR_SRC / "features" / "observability" / "traceRequestDetailController.js"
    ).read_text(encoding="utf-8")
    immune_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "AutomationImmuneSectionContent.jsx"
    ).read_text(encoding="utf-8")
    history_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "AutomationHistorySectionContent.jsx"
    ).read_text(encoding="utf-8")

    for export_name in [
        "useAutomationOverviewQuery",
        "useImmuneGateStatsQuery",
        "useSchedulerHistoryQuery",
        "refreshSchedulerHistoryQuery",
    ]:
        assert f"export function {export_name}" in automation_queries
        assert f"export function {export_name}" not in execution_queries

    for fetcher_name in [
        "fetchSchedulerTasks",
        "fetchSchedulerHealth",
        "fetchResourceManagerStatus",
        "fetchImmuneGateStats",
        "fetchSchedulerHistory",
    ]:
        assert fetcher_name in automation_queries
        assert fetcher_name not in execution_queries

    assert "export { default as useImmuneGateReplayQuery } from '../../shared/query/useImmuneGateReplayQuery.js'" in automation_queries
    assert "export function useImmuneGateReplayQuery" not in automation_queries
    assert "fetchImmuneGateReplay" not in automation_queries
    assert "export function fetchImmuneGateReplay" in immune_replay_api
    assert "ENDPOINT_REGISTRY" in immune_replay_api
    assert "endpointGet(execution.immuneGateReplay" in immune_replay_api
    assert "export default function useImmuneGateReplayQuery" in immune_replay_query
    assert "fetchImmuneGateReplay(normalizedRequestId" in immune_replay_query
    assert "OBS_QUERY_KEYS.immuneGateReplay" in immune_replay_query
    assert "features/execution/automationQueries.js" in automation_page
    assert "shared/query/useImmuneGateReplayQuery.js" in trace_detail_controller
    assert "../execution/automationQueries.js" not in trace_detail_controller
    assert "../automationQueries.js" in immune_section
    assert "../automationQueries.js" in history_section
    assert "features/execution/queries.js" not in automation_page
    assert "features/execution/queries.js" not in trace_detail
    assert "features/execution/queries.js" not in trace_detail_controller
    assert "from '../queries.js'" not in immune_section
    assert "from '../queries.js'" not in history_section
    assert "OBS_QUERY_KEYS.automationOverview" in automation_queries
    assert "OBS_QUERY_KEYS.schedulerHistory" in automation_queries


def test_remaining_execution_queries_are_split_and_monolith_is_retired() -> None:
    execution_queries_path = MONITOR_SRC / "features" / "execution" / "queries.js"
    execution_queries = _read_optional(execution_queries_path)
    agent_loop_queries = (MONITOR_SRC / "features" / "execution" / "agentLoopQueries.js").read_text(encoding="utf-8")
    gateway_queries = (MONITOR_SRC / "features" / "execution" / "gatewayQueries.js").read_text(encoding="utf-8")
    skills_queries = (MONITOR_SRC / "features" / "execution" / "skillsHubQueries.js").read_text(encoding="utf-8")
    wechat_queries = (MONITOR_SRC / "features" / "execution" / "wechatQueries.js").read_text(encoding="utf-8")
    agent_loop_page = (MONITOR_SRC / "pages" / "AgentLoop.jsx").read_text(encoding="utf-8")
    gateway_page = (MONITOR_SRC / "pages" / "Gateway.jsx").read_text(encoding="utf-8")
    skills_page = (MONITOR_SRC / "pages" / "SkillsHub.jsx").read_text(encoding="utf-8")

    assert not execution_queries_path.exists()

    expected_exports = {
        "agentLoopQueries.js": ("useAgentLoopConsoleQuery", agent_loop_queries),
        "gatewayQueries.js": ("useGatewayOverviewQuery", gateway_queries),
        "skillsHubQueries.js": ("useSkillsHubQuery", skills_queries),
        "wechatQueries.js": ("useWeChatChannelQuery", wechat_queries),
    }
    for filename, (export_name, source) in expected_exports.items():
        assert f"export function {export_name}" in source, filename
        assert f"export function {export_name}" not in execution_queries, filename

    for fetcher_name, source in {
        "fetchAgentInfo": agent_loop_queries,
        "fetchAgentLoopStats": agent_loop_queries,
        "fetchGatewayStats": gateway_queries,
        "fetchWeChatStats": f"{gateway_queries}\n{wechat_queries}",
        "fetchSkillsHubStats": skills_queries,
    }.items():
        assert fetcher_name in source
        assert fetcher_name not in execution_queries

    assert "features/execution/agentLoopQueries.js" in agent_loop_page
    assert "features/execution/gatewayQueries.js" in gateway_page
    assert "features/execution/skillsHubQueries.js" in skills_page
    for source in [agent_loop_page, gateway_page, skills_page]:
        assert "features/execution/queries.js" not in source


def test_agent_loop_stream_and_view_model_lives_in_execution_feature() -> None:
    agent_loop_page = (MONITOR_SRC / "pages" / "AgentLoop.jsx").read_text(encoding="utf-8")
    agent_loop_model = (MONITOR_SRC / "features" / "execution" / "agentLoopModel.js").read_text(encoding="utf-8")
    agent_loop_sections = (MONITOR_SRC / "features" / "execution" / "components" / "AgentLoopConsoleSections.jsx").read_text(encoding="utf-8")
    shared_sse = (MONITOR_SRC / "shared" / "streaming" / "sse.js").read_text(encoding="utf-8")

    assert "export function parseSseBlock(" in shared_sse
    assert "export function consumeSseBuffer(" in shared_sse
    assert "export const AGENT_LOOP_PHASE_BADGE_CLASSES" in agent_loop_model
    for export_name in [
        "getAgentLoopPhaseClass",
        "isAgentLoopDoneEvent",
        "buildAgentLoopRunSummary",
        "buildAgentLoopLoadState",
        "buildAgentLoopHeroTitle",
        "buildAgentLoopHeroMetaItems",
        "getAgentLoopSampleSize",
        "buildAgentLoopMetricCards",
        "isAgentLoopRunReady",
        "buildAgentLoopRunRequest",
        "buildAgentLoopRouteErrorEvent",
        "buildAgentLoopConsoleViewModel",
    ]:
        assert f"export function {export_name}(" in agent_loop_model

    assert "shared/streaming/sse.js" in agent_loop_page
    assert "features/execution/agentLoopModel.js" in agent_loop_page
    assert "consumeSseBuffer(buffer)" in agent_loop_page
    assert "buildAgentLoopConsoleViewModel({" in agent_loop_page
    assert "buildAgentLoopRunRequest({" in agent_loop_page
    assert "buildAgentLoopRunSummary(loopResult)" in agent_loop_page
    assert "buildAgentLoopRouteErrorEvent(error)" in agent_loop_page
    assert "isAgentLoopDoneEvent(event)" in agent_loop_page
    assert "metricCards.map((card)" in agent_loop_page
    assert "getAgentLoopPhaseClass(phase)" in agent_loop_sections
    assert "isAgentLoopRunReady(goal)" in agent_loop_sections

    for forbidden in [
        "function parseSseBlock(",
        "function consumeSseBuffer(",
        ".filter((line) => line.startsWith('data:'))",
        "resolveLoadState",
        "const map = {",
        "AGENT_INIT:",
        "String(event.phase || '').toUpperCase() === 'LOOP_DONE'",
        "agentInfo ? `默认",
        "`总执行：${agentLoopStats",
        "agentLoopStats?.achievement_rate != null",
        "uiAssertionData?.block_rate != null",
        "goal.trim()",
    ]:
        assert forbidden not in agent_loop_page


def test_life_queries_are_split_and_monolith_is_retired() -> None:
    life_queries_path = MONITOR_SRC / "features" / "life" / "queries.js"
    life_queries = _read_optional(life_queries_path)
    life_body_queries = (MONITOR_SRC / "features" / "life" / "lifeBodyQueries.js").read_text(encoding="utf-8")
    digital_life_queries = (MONITOR_SRC / "features" / "life" / "digitalLifeQueries.js").read_text(encoding="utf-8")
    memory_queries = (MONITOR_SRC / "features" / "life" / "memoryQueries.js").read_text(encoding="utf-8")
    emotion_queries = (MONITOR_SRC / "features" / "life" / "emotionQueries.js").read_text(encoding="utf-8")
    runtime_mode_queries = (MONITOR_SRC / "features" / "life" / "runtimeModeQueries.js").read_text(encoding="utf-8")
    bond_queries = (MONITOR_SRC / "features" / "life" / "bondQueries.js").read_text(encoding="utf-8")

    pages_and_hooks = {
        "Bond.jsx": (MONITOR_SRC / "pages" / "Bond.jsx").read_text(encoding="utf-8"),
        "DigitalLifeOverview.jsx": (MONITOR_SRC / "pages" / "DigitalLifeOverview.jsx").read_text(encoding="utf-8"),
        "digitalLifeOverviewController.js": (
            MONITOR_SRC / "features" / "life" / "digitalLifeOverviewController.js"
        ).read_text(encoding="utf-8"),
        "EmotionDashboard.jsx": (MONITOR_SRC / "pages" / "EmotionDashboard.jsx").read_text(encoding="utf-8"),
        "LifeTimelinePage.jsx": (MONITOR_SRC / "pages" / "LifeTimelinePage.jsx").read_text(encoding="utf-8"),
        "MemoryBrowser.jsx": (MONITOR_SRC / "pages" / "MemoryBrowser.jsx").read_text(encoding="utf-8"),
        "PathologyCenterPage.jsx": (MONITOR_SRC / "pages" / "PathologyCenterPage.jsx").read_text(encoding="utf-8"),
        "PathologyDetailPage.jsx": (MONITOR_SRC / "pages" / "PathologyDetailPage.jsx").read_text(encoding="utf-8"),
        "RuntimeMode.jsx": (MONITOR_SRC / "pages" / "RuntimeMode.jsx").read_text(encoding="utf-8"),
        "useDigitalLifeRuntimeSignals.js": (
            MONITOR_SRC / "hooks" / "useDigitalLifeRuntimeSignals.js"
        ).read_text(encoding="utf-8"),
        "useLifeBodyPageData.js": (MONITOR_SRC / "hooks" / "useLifeBodyPageData.js").read_text(encoding="utf-8"),
    }

    assert not life_queries_path.exists()

    expected_exports = {
        "lifeBodyQueries.js": (
            ("useLifeBodyQuery", "useLifeTimelineQuery", "usePathologiesQuery"),
            life_body_queries,
        ),
        "digitalLifeQueries.js": (
            (
                "useDigitalLifeOverviewQuery",
                "useDigitalLifeWallClockSoakQuery",
                "useRuntimeSignalsQuery",
                "useAvatarStateQuery",
            ),
            digital_life_queries,
        ),
        "memoryQueries.js": (("useMemoryOverviewQuery",), memory_queries),
        "emotionQueries.js": (("useEmotionOverviewQuery",), emotion_queries),
        "runtimeModeQueries.js": (("useRuntimeModeConsoleQuery",), runtime_mode_queries),
        "bondQueries.js": (("useBondOverviewQuery", "useBondUserDetailQuery"), bond_queries),
    }
    for filename, (export_names, source) in expected_exports.items():
        assert "from './api.js'" in source, filename
        assert "useObservabilityQuery" in source, filename
        for export_name in export_names:
            assert f"export function {export_name}" in source, f"{filename}:{export_name}"
            assert f"export function {export_name}" not in life_queries, f"old:{export_name}"

    for fetcher_name, source in {
        "fetchLifeBody": life_body_queries,
        "fetchLifeTimeline": life_body_queries,
        "fetchPathologies": life_body_queries,
        "fetchDigitalLifeOverviewSnapshot": digital_life_queries,
        "fetchDigitalLifeRuntimeSnapshot": digital_life_queries,
        "fetchDigitalLifeWallClockSoak": digital_life_queries,
        "fetchAvatarState": digital_life_queries,
        "fetchPromptBuilderStats": digital_life_queries,
        "fetchMemoryStats": f"{memory_queries}\n{emotion_queries}",
        "fetchEpisodicStoreStats": memory_queries,
        "fetchDistillationStats": memory_queries,
        "fetchCanonDocuments": memory_queries,
        "fetchContextCompressorStats": memory_queries,
        "fetchResidentMemoryContext": memory_queries,
        "fetchVtsStats": emotion_queries,
        "fetchSysModeStatus": runtime_mode_queries,
        "fetchSysModeHistory": runtime_mode_queries,
        "fetchBondHealth": bond_queries,
        "fetchBondUsers": bond_queries,
        "fetchBondStage": bond_queries,
    }.items():
        assert fetcher_name in source
        assert fetcher_name not in life_queries

    expected_imports = {
        "Bond.jsx": "features/life/bondQueries.js",
        "DigitalLifeOverview.jsx": "features/life/digitalLifeOverviewController.js",
        "digitalLifeOverviewController.js": "./digitalLifeQueries.js",
        "EmotionDashboard.jsx": "features/life/emotionQueries.js",
        "LifeTimelinePage.jsx": "features/life/lifeBodyQueries.js",
        "MemoryBrowser.jsx": "features/life/memoryQueries.js",
        "PathologyCenterPage.jsx": "features/life/lifeBodyQueries.js",
        "PathologyDetailPage.jsx": "features/life/lifeBodyQueries.js",
        "RuntimeMode.jsx": "features/life/runtimeModeQueries.js",
        "useDigitalLifeRuntimeSignals.js": "features/life/digitalLifeQueries.js",
        "useLifeBodyPageData.js": "features/life/lifeBodyQueries.js",
    }
    for name, expected_import in expected_imports.items():
        assert expected_import in pages_and_hooks[name], name

    for name, source in pages_and_hooks.items():
        assert "features/life/queries.js" not in source, name


def test_observability_queries_are_split_and_monolith_is_retired() -> None:
    observability_queries_path = MONITOR_SRC / "features" / "observability" / "queries.js"
    observability_queries = _read_optional(observability_queries_path)
    overview_queries = (
        MONITOR_SRC / "features" / "observability" / "overviewQueries.js"
    ).read_text(encoding="utf-8")
    sessions_queries = (
        MONITOR_SRC / "features" / "observability" / "sessionsQueries.js"
    ).read_text(encoding="utf-8")
    metrics_queries = (
        MONITOR_SRC / "features" / "observability" / "metricsQueries.js"
    ).read_text(encoding="utf-8")
    alerts_queries = (
        MONITOR_SRC / "features" / "observability" / "alertsQueries.js"
    ).read_text(encoding="utf-8")
    insights_queries = (
        MONITOR_SRC / "features" / "observability" / "insightsQueries.js"
    ).read_text(encoding="utf-8")
    log_queries = (
        MONITOR_SRC / "features" / "observability" / "logQueries.js"
    ).read_text(encoding="utf-8")
    codex_bio_ops_queries = (
        MONITOR_SRC / "features" / "observability" / "codexBioOpsQueries.js"
    ).read_text(encoding="utf-8")

    pages = {
        "Overview.jsx": (MONITOR_SRC / "pages" / "Overview.jsx").read_text(encoding="utf-8"),
        "Sessions.jsx": (MONITOR_SRC / "pages" / "Sessions.jsx").read_text(encoding="utf-8"),
        "Metrics.jsx": (MONITOR_SRC / "pages" / "Metrics.jsx").read_text(encoding="utf-8"),
        "Alerts.jsx": (MONITOR_SRC / "pages" / "Alerts.jsx").read_text(encoding="utf-8"),
        "Insights.jsx": (MONITOR_SRC / "pages" / "Insights.jsx").read_text(encoding="utf-8"),
        "LogStream.jsx": (MONITOR_SRC / "pages" / "LogStream.jsx").read_text(encoding="utf-8"),
        "CodexBioOpsObservability.jsx": (
            MONITOR_SRC / "pages" / "CodexBioOpsObservability.jsx"
        ).read_text(encoding="utf-8"),
        "TraceHome.jsx": (
            MONITOR_SRC / "features" / "observability" / "components" / "trace" / "TraceHome.jsx"
        ).read_text(encoding="utf-8"),
        "traceHomeController.js": (
            MONITOR_SRC / "features" / "observability" / "components" / "trace" / "traceHomeController.js"
        ).read_text(encoding="utf-8"),
    }

    assert not observability_queries_path.exists()

    expected_exports = {
        "overviewQueries.js": (("useSystemOverviewQuery",), overview_queries),
        "sessionsQueries.js": (("useSessionsListQuery",), sessions_queries),
        "metricsQueries.js": (("useMetricsOverviewQuery",), metrics_queries),
        "alertsQueries.js": (("useAlertsCenterQuery",), alerts_queries),
        "insightsQueries.js": (("useInsightsSummaryQuery",), insights_queries),
        "logQueries.js": (("useErrorLogBatchQuery",), log_queries),
        "codexBioOpsQueries.js": (("useCodexBioOpsObservabilityQuery",), codex_bio_ops_queries),
    }
    expected_query_api_imports = {
        "overviewQueries.js": "./overviewApi.js",
        "sessionsQueries.js": "./sessionsApi.js",
        "metricsQueries.js": "./metricsApi.js",
        "alertsQueries.js": "./alertsApi.js",
        "insightsQueries.js": "./insightsApi.js",
        "logQueries.js": "./logApi.js",
        "codexBioOpsQueries.js": "./bioOpsApi.js",
    }
    for filename, (export_names, source) in expected_exports.items():
        assert expected_query_api_imports[filename] in source, filename
        assert "from './api.js'" not in source, filename
        assert "useObservabilityQuery" in source, filename
        for export_name in export_names:
            assert f"export function {export_name}" in source, f"{filename}:{export_name}"
            assert f"export function {export_name}" not in observability_queries, f"old:{export_name}"

    for fetcher_name, source in {
        "fetchServicesHealth": f"{overview_queries}\n{alerts_queries}",
        "fetchSubsystemsStats": overview_queries,
        "fetchAgentLoopStats": overview_queries,
        "fetchUIAssertionStats": overview_queries,
        "fetchSessions": sessions_queries,
        "fetchMetricsTimeseries": metrics_queries,
        "fetchRateLimitBackendStats": metrics_queries,
        "fetchErrors": f"{alerts_queries}\n{log_queries}",
        "fetchCapabilityGapStats": alerts_queries,
        "fetchInsightsSummarySnapshot": insights_queries,
        "fetchCodexBioOpsObservability": codex_bio_ops_queries,
    }.items():
        assert fetcher_name in source
        assert fetcher_name not in observability_queries

    expected_imports = {
        "Overview.jsx": "features/observability/overviewQueries.js",
        "Sessions.jsx": "features/observability/sessionsQueries.js",
        "Metrics.jsx": "features/observability/metricsQueries.js",
        "Alerts.jsx": "features/observability/alertsQueries.js",
        "Insights.jsx": "features/observability/insightsQueries.js",
        "LogStream.jsx": "features/observability/logQueries.js",
        "CodexBioOpsObservability.jsx": "features/observability/codexBioOpsQueries.js",
        "traceHomeController.js": "../../sessionsQueries.js",
    }
    for name, expected_import in expected_imports.items():
        assert expected_import in pages[name], name

    for name, source in pages.items():
        assert "features/observability/queries.js" not in source, name


def test_operations_queries_are_split_and_monolith_is_retired() -> None:
    operations_queries_path = MONITOR_SRC / "features" / "operations" / "queries.js"
    operations_queries = _read_optional(operations_queries_path)
    prompt_queries = (MONITOR_SRC / "features" / "operations" / "promptQueries.js").read_text(encoding="utf-8")
    persona_queries = (MONITOR_SRC / "features" / "operations" / "personaQueries.js").read_text(encoding="utf-8")
    system_config_queries = (
        MONITOR_SRC / "features" / "operations" / "systemConfigQueries.js"
    ).read_text(encoding="utf-8")
    canon_queries = (MONITOR_SRC / "features" / "operations" / "canonQueries.js").read_text(encoding="utf-8")
    forge_queries = (MONITOR_SRC / "features" / "operations" / "forgeQueries.js").read_text(encoding="utf-8")

    surfaces = {
        "PromptConfig.jsx": (MONITOR_SRC / "pages" / "PromptConfig.jsx").read_text(encoding="utf-8"),
        "PersonaPromptOps.jsx": (MONITOR_SRC / "pages" / "PersonaPromptOps.jsx").read_text(encoding="utf-8"),
        "SystemConfig.jsx": (MONITOR_SRC / "pages" / "SystemConfig.jsx").read_text(encoding="utf-8"),
        "Canon.jsx": (MONITOR_SRC / "pages" / "Canon.jsx").read_text(encoding="utf-8"),
        "ForgeRiskActionBar.jsx": (
            MONITOR_SRC / "features" / "operations" / "components" / "ForgeRiskActionBar.jsx"
        ).read_text(encoding="utf-8"),
    }

    assert not operations_queries_path.exists()

    expected_exports = {
        "promptQueries.js": (
            ("usePromptTemplateListQuery", "usePromptTemplateDetailQuery"),
            prompt_queries,
        ),
        "personaQueries.js": (("usePersonaPromptCatalogQuery",), persona_queries),
        "systemConfigQueries.js": (("useSystemConfigQuery",), system_config_queries),
        "canonQueries.js": (
            ("useCanonProposalsQuery", "useCanonDocumentsQuery", "useCanonVaultStatsQuery"),
            canon_queries,
        ),
        "forgeQueries.js": (("useCapabilityForgeProposalsQuery",), forge_queries),
    }
    for filename, (export_names, source) in expected_exports.items():
        assert "from './api.js'" in source, filename
        assert "useObservabilityQuery" in source, filename
        for export_name in export_names:
            assert f"export function {export_name}" in source, f"{filename}:{export_name}"
            assert f"export function {export_name}" not in operations_queries, f"old:{export_name}"

    for fetcher_name, source in {
        "fetchPromptTemplates": prompt_queries,
        "fetchPromptTemplate": prompt_queries,
        "fetchPersonaFirstBatchCatalog": persona_queries,
        "fetchLlmRouteStats": system_config_queries,
        "fetchLlmCallStats": system_config_queries,
        "fetchRateLimitBackendStats": system_config_queries,
        "fetchServicesHealth": system_config_queries,
        "fetchRetrievalRuntime": system_config_queries,
        "fetchCanonProposals": canon_queries,
        "fetchCanonDocuments": canon_queries,
        "fetchVaultStats": canon_queries,
        "fetchCapabilityForgeProposals": forge_queries,
    }.items():
        assert fetcher_name in source
        assert fetcher_name not in operations_queries

    expected_imports = {
        "PromptConfig.jsx": "features/operations/promptQueries.js",
        "PersonaPromptOps.jsx": "features/operations/personaQueries.js",
        "SystemConfig.jsx": "features/operations/systemConfigQueries.js",
        "Canon.jsx": "features/operations/canonQueries.js",
        "ForgeRiskActionBar.jsx": "../forgeQueries.js",
    }
    for name, expected_import in expected_imports.items():
        assert expected_import in surfaces[name], name
    assert "features/operations/promptQueries.js" in surfaces["PersonaPromptOps.jsx"]

    for name, source in surfaces.items():
        assert "features/operations/queries.js" not in source, name
        assert "from '../queries.js'" not in source, name


def test_system_config_view_model_lives_in_operations_feature() -> None:
    page = (MONITOR_SRC / "pages" / "SystemConfig.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "operations" / "systemConfigModel.js").read_text(encoding="utf-8")

    assert "export const SYSTEM_CONFIG_WINDOW_OPTIONS" in model
    for export_name in [
        "getSystemConfigCollections",
        "countNotLiveRuntimeSlots",
        "getSystemConfigRuntimeLabels",
        "getSystemConfigUpdatedAt",
        "buildSystemConfigLoadState",
        "buildSystemConfigHeroDescription",
        "buildSystemConfigHeroMetaItems",
        "getSystemConfigSampleSize",
        "buildSystemConfigMetricCards",
        "buildSystemConfigRouteRows",
        "buildSystemConfigCallRows",
        "buildSystemConfigRateKeyRows",
        "buildSystemConfigRetrievalRuntimeView",
        "buildSystemConfigViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/operations/systemConfigModel.js" in page
    assert "SYSTEM_CONFIG_WINDOW_OPTIONS" in page
    assert "buildSystemConfigViewModel({" in page
    assert "metricCards.map((card)" in page
    assert "routeRows.map((route)" in page
    assert "callRows.map((model)" in page
    assert "rateKeyRows.map((keyItem)" in page
    assert "retrievalRuntimeView" in page

    for forbidden in [
        "const WINDOW_OPTIONS = [",
        "const FALLBACK_TEXT",
        "Object.values(runtimeSnapshot?.slot_status",
        "resolveLoadState",
        "formatMode(",
        "formatReplyPosture(",
        "routeData?.model_routes",
        "callData?.models",
        "rateData?.top_keys",
        "replyPosture",
        "notLiveSlots",
        "Math.max(lastUpdatedAt",
        "routeStats.length + callStats.length",
        "retrievalRuntime?.episodic_write_embedding_model_id",
        "retrievalRuntime?.latest?.embedding_model_id",
        "retrievalRuntime?.read_write_model_aligned",
        "rateData?.backend_type",
        "healthData ? `${healthData.up}",
        "Number(model.avg_latency_ms).toFixed(0)",
    ]:
        assert forbidden not in page


def test_tools_operations_model_lives_in_execution_feature() -> None:
    component = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsOperationsSection.jsx"
    ).read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "execution" / "toolsOperationsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "CUSTOM_SKILL_PROVIDER_DEFAULT_FORM",
        "mergeCustomSkillProviderTemplate",
        "buildCustomSkillProviderInstallRequest",
        "getCustomSkillProvidersLoadState",
        "getCustomSkillProviderTemplateLoadState",
        "buildPermissionGuardStatsView",
        "buildActionPlanStatsView",
    ]:
        assert export_name in model

    assert "toolsOperationsModel.js" in component
    assert "CUSTOM_SKILL_PROVIDER_DEFAULT_FORM" in component
    assert "mergeCustomSkillProviderTemplate(prev, templateData)" in component
    assert "mergeCustomSkillProviderTemplate(prev, nextTemplate)" in component
    assert "buildCustomSkillProviderInstallRequest(form)" in component
    assert "getCustomSkillProvidersLoadState({" in component
    assert "getCustomSkillProviderTemplateLoadState({" in component
    assert "buildPermissionGuardStatsView({" in component
    assert "buildActionPlanStatsView({" in component

    for forbidden in [
        "models/loadState.js",
        "resolveLoadState(",
        "LOAD_STATES.NO_SAMPLE",
        "hasSamples:",
        "hasData:",
        "installCustomSkillProvider({",
        "filename: form.filename",
        "source_code: form.source_code",
        "activate_now: form.activate_now",
        "Number(data?.total_checks",
        "Number(data?.total_plans",
        "data?.top_denied_tools",
        "data?.top_action_types",
        "data?.success_rate > 80",
        "{ label: '总校验次数'",
        "{ label: '总计划数'",
    ]:
        assert forbidden not in component


def test_tools_page_delegates_catalog_and_execution_ui_to_execution_feature() -> None:
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    catalog_browser = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsCatalogBrowser.jsx"
    ).read_text(encoding="utf-8")
    detail_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolDetailPanel.jsx"
    ).read_text(encoding="utf-8")
    execute_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolExecutePanel.jsx"
    ).read_text(encoding="utf-8")
    pentest_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsPentestMetadataSection.jsx"
    ).read_text(encoding="utf-8")

    assert "features/execution/components/ToolsCatalogBrowser.jsx" in tools_page
    assert "features/execution/components/ToolDetailPanel.jsx" in tools_page
    assert "features/execution/components/ToolsPentestMetadataSection.jsx" in tools_page
    assert "ToolExecutePanel" in detail_panel
    assert "executeTool(" in execute_panel
    assert "getPentestToolMetadataView(capabilities)" in pentest_section

    for needle in [
        "function PentestToolMetadataSection(",
        "function ToolCard(",
        "function ExecutePanel(",
        "TOOL_SIDE_EFFECT_STYLES[selectedTool.side_effect_level]",
        "buildToolExecutionRequest(tool, form)",
        "executeTool(executionRequest.request)",
    ]:
        assert needle not in tools_page


def test_chat_debug_streaming_and_trace_models_are_split() -> None:
    chat_debug_page = (MONITOR_SRC / "pages" / "ChatDebug.jsx").read_text(encoding="utf-8")
    chat_controller = (
        MONITOR_SRC / "features" / "observability" / "chatDebugController.js"
    ).read_text(encoding="utf-8")
    chat_debug_sections = (
        MONITOR_SRC / "features" / "observability" / "components" / "chatDebug" / "ChatDebugConsoleSections.jsx"
    ).read_text(encoding="utf-8")
    chat_debug_main = (
        MONITOR_SRC / "features" / "observability" / "components" / "chatDebug" / "ChatDebugMainColumn.jsx"
    ).read_text(encoding="utf-8")
    chat_trace_sidebar = (
        MONITOR_SRC / "features" / "observability" / "components" / "chatDebug" / "ChatTraceSidebar.jsx"
    ).read_text(encoding="utf-8")
    chat_event_stream = (
        MONITOR_SRC / "features" / "observability" / "components" / "chatDebug" / "ChatDebugEventStream.jsx"
    ).read_text(encoding="utf-8")
    chat_model = (MONITOR_SRC / "features" / "observability" / "chatDebugModel.js").read_text(encoding="utf-8")
    sse_streaming = (MONITOR_SRC / "shared" / "streaming" / "sse.js").read_text(encoding="utf-8")

    for export_name in [
        "parseSseBlock",
        "consumeSseBuffer",
    ]:
        assert f"export function {export_name}(" in sse_streaming

    for export_name in [
        "truncateDebugText",
        "getChatDebugChainEvents",
        "getLatestTraceNode",
        "getTraceIssueCount",
        "getLatestChatContent",
        "buildChatDebugTracePreviews",
        "buildChatDebugHeroMetaItems",
        "buildChatDebugTraceLoadState",
        "buildChatDebugTraceView",
    ]:
        assert f"export function {export_name}(" in chat_model

    assert "chatDebugController.js" in chat_debug_page
    assert "shared/streaming/sse.js" in chat_controller
    assert "from './chatDebugModel.js'" in chat_controller
    assert "ChatDebugConsoleSections.jsx" in chat_debug_page
    assert "consumeSseBuffer(buffer)" in chat_controller
    assert "buildChatDebugTraceView({" in chat_controller
    assert "sendChatDebugStream(" in chat_controller
    assert "useTraceSnapshotQuery({" in chat_controller
    assert "traceLoadState" in chat_debug_page
    assert "traceNodeCount" in chat_debug_page
    assert "function ChatDebugEventRow(" in chat_event_stream
    assert "function ChatDebugPhaseBadge(" in chat_event_stream
    assert "ChatDebugMainColumn" in chat_debug_sections
    assert "ChatTraceSidebar" in chat_debug_sections
    assert "ChatDebugHeader" in chat_debug_main
    assert "ChatTranscriptPanel" in chat_debug_main
    assert "ChatInputBar" in chat_debug_main
    assert "ChatDebugEventStream" in chat_trace_sidebar
    assert "getTraceNodeDisplayName" in chat_trace_sidebar
    assert "formatEventPhase" in chat_trace_sidebar
    assert "formatTime(" in chat_trace_sidebar
    assert "resolveLoadState({" in chat_model
    assert "buildChatDebugTraceLoadState({" in chat_model
    assert "buildChatDebugHeroMetaItems({" in chat_model

    assert "function PhaseBadge(" not in chat_debug_page
    assert "function EventRow(" not in chat_debug_page
    assert "getTraceNodeDisplayName" not in chat_debug_page
    assert "formatEventPhase" not in chat_debug_page
    assert "formatTime(" not in chat_debug_page
    assert "sendChatDebugStream" not in chat_debug_page
    assert "useTraceSnapshotQuery" not in chat_debug_page
    assert "useDigitalLifeRuntimeSignals" not in chat_debug_page
    assert "consumeSseBuffer" not in chat_debug_page
    assert "useAppStore" not in chat_debug_page
    assert "useState" not in chat_debug_page
    assert "useEffect" not in chat_debug_page
    assert "useMemo" not in chat_debug_page
    assert "useCallback" not in chat_debug_page
    assert "useRef" not in chat_debug_page
    assert "buildChatDebugTraceView" not in chat_debug_page
    assert "function parseSseBlock(" not in chat_debug_page
    assert "function consumeSseBuffer(" not in chat_debug_page
    assert "function truncateDebugText(" not in chat_debug_page
    assert "resolveLoadState" not in chat_debug_page
    assert "function ChatDebugEventRow(" not in chat_debug_sections
    assert "function ChatDebugPhaseBadge(" not in chat_debug_sections
    assert "getTraceNodeDisplayName" not in chat_debug_sections
    assert "formatEventPhase" not in chat_debug_sections
    assert "formatTime(" not in chat_debug_sections
    assert "getChatDebugChainEvents(sseEvents)" not in chat_debug_page
    assert "getLatestTraceNode(traceSnapshot)" not in chat_debug_page
    assert "getTraceIssueCount(traceSnapshot)" not in chat_debug_page
    assert "buildChatDebugTracePreviews({" not in chat_debug_page
    assert "buildChatDebugHeroMetaItems({" not in chat_debug_page
    assert "traceSnapshot?.summary" not in chat_debug_page
    assert "traceSnapshot?.node_count" not in chat_debug_page
    assert "traceLoading || traceRefreshing" not in chat_debug_page
    assert "hasData: Boolean(traceSnapshot)" not in chat_debug_page
    assert "hasSamples: (traceSnapshot?.node_count" not in chat_debug_page
    assert "JSON.parse(data)" not in chat_debug_page
    assert "split('\\n\\n')" not in chat_debug_page
    assert "String(event.phase || '').toUpperCase() === 'NODE'" not in chat_debug_page
    assert "['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED']" not in chat_debug_page
    assert "chatHistory.filter((item) => item.role === 'user')" not in chat_debug_page
    assert "历史消息：${chatHistory.length}" not in chat_debug_page


def test_automation_status_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    migrated_files = {
        "features/execution/automationTaskModel.js": [
            "const STATUS_STYLES",
            "bg-blue-500 animate-pulse",
            "bg-yellow-400",
        ],
        "features/execution/components/AutomationHistorySectionContent.jsx": [
            "status === 'SUCCESS'",
            "status === 'FAILED'",
            "status === 'RUNNING'",
            "border-emerald-200 bg-emerald-50/70 text-emerald-700",
            "border-rose-200 bg-rose-50/70 text-rose-700",
            "border-sky-200 bg-sky-50/70 text-sky-700",
        ],
        "features/execution/components/AutomationImmuneSectionContent.jsx": [
            "border-green-200 bg-green-50 text-green-700",
            "border-orange-200 bg-orange-50 text-orange-700",
        ],
    }

    for export_name in [
        "AUTOMATION_TASK_STATUS_STYLES",
        "AUTOMATION_HISTORY_STATUS_CLASSES",
        "AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES",
    ]:
        assert export_name in shared

    for rel, forbidden in migrated_files.items():
        source = (MONITOR_SRC / rel).read_text(encoding="utf-8")
        assert "shared/statusMeta.js" in source
        for needle in forbidden:
            assert needle not in source, rel


def test_automation_resource_model_lives_in_execution_feature() -> None:
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    dashboard_model = (MONITOR_SRC / "features" / "execution" / "automationDashboardModel.js").read_text(encoding="utf-8")
    resource_panel = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationResourceSchedulingPanel.jsx"
    ).read_text(encoding="utf-8")
    resource_model = (MONITOR_SRC / "features" / "execution" / "automationResourceModel.js").read_text(encoding="utf-8")

    for export_name in [
        "AUTOMATION_PRIORITY_STYLES",
        "AUTOMATION_RESOURCE_CLASS_META",
        "AUTOMATION_RESOURCE_PRESSURE_TONES",
        "AUTOMATION_RESOURCE_GATE_DECISION_CLASSES",
        "AUTOMATION_RESOURCE_MANAGER_RUNTIME_CLASSES",
        "getAutomationResourceMeta",
        "getTaskResourceProfile",
        "buildResourceSchedulingSummary",
        "getResourceGroupBarWidth",
        "buildResourceManagerDecisionView",
        "buildResourceGateDecisionRowView",
        "buildResourceSchedulingView",
        "resolveResourcePressureTone",
        "resolveResourceGateDecisionClass",
    ]:
        assert export_name in resource_model

    assert "automationResourceModel.js" in dashboard_model
    assert "features/execution/automationResourceModel.js" not in automation_page
    assert "features/execution/components/AutomationResourceSchedulingPanel.jsx" in automation_page
    assert "from '../automationResourceModel.js'" in resource_panel
    assert "buildResourceSchedulingView(summary, resourceManager)" in resource_panel
    assert "buildResourceGateDecisionRowView(row)" in resource_panel
    assert "getResourceGroupBarWidth(group, summary.groups)" in resource_panel
    assert "const PRIORITY_STYLES" not in automation_page
    assert "const RESOURCE_CLASS_META" not in automation_page
    assert "function getResourceMeta(" not in automation_page
    assert "function getTaskResourceProfile(" not in automation_page
    assert "function buildResourceSchedulingSummary(" not in automation_page
    assert "getAutomationResourceMeta(" not in automation_page
    assert "pressureStatus === 'HIGH'" not in automation_page
    assert "pressureStatus === 'ELEVATED'" not in automation_page
    assert "resourceManager?.last_decision" not in automation_page
    assert "decision.cannot_release" not in automation_page
    assert "decision.can_release" not in automation_page
    assert "decision.needs_user_confirm" not in automation_page
    assert "decisionText === 'allow'" not in automation_page
    assert "decisionText === 'defer'" not in automation_page
    assert "Math.max(8, Math.min(100" not in automation_page
    assert "shortLabel: '轻 tick'" not in automation_page
    assert "description: '低成本内在脉冲" not in automation_page
    assert "chipClass: 'border-emerald-200 bg-emerald-50 text-emerald-700'" not in automation_page


def test_automation_task_view_model_lives_in_execution_feature() -> None:
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    task_section = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationTaskExecutionSection.jsx"
    ).read_text(encoding="utf-8")
    task_model = (MONITOR_SRC / "features" / "execution" / "automationTaskModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getTaskView",
        "taskMatchesAutomationFilters",
        "getAutomationTaskUrlFilters",
        "buildAutomationClearedTaskFilterParams",
        "buildAutomationTaskSelectionView",
    ]:
        assert f"export function {export_name}(" in task_model

    assert "features/execution/automationTaskModel.js" in automation_page
    assert "features/execution/components/AutomationTaskExecutionSection.jsx" in automation_page
    assert "from '../automationTaskModel.js'" in task_section
    assert "getTaskView(item)" in task_section
    assert "getAutomationTaskUrlFilters(searchParams)" in automation_page
    assert "buildAutomationClearedTaskFilterParams(searchParams)" in automation_page
    assert "buildAutomationTaskSelectionView({" in automation_page
    assert "taskMatchesAutomationFilters(item, { reasonFilter, gateFilter, keyword })" in task_model
    assert "filteredTasks.find((item) => item.task?.task_id === selectedTaskId)" in task_model
    assert "new URLSearchParams(searchParams)" in task_model
    assert "from '../../shared/statusMeta.js'" in task_model
    assert "from './automationResourceModel.js'" in task_model
    assert "AUTOMATION_TASK_STATUS_STYLES" in task_model
    assert "getTaskResourceProfile(item)" in task_model
    assert "last_endocrine_cortisol" in task_model
    assert "last_endocrine_energy" in task_model
    assert "last_immune_reason_codes" in task_model
    assert "resourceProfile.meta.label" in task_model

    assert "function getTaskView(" not in automation_page
    assert "AUTOMATION_TASK_STATUS_STYLES" not in automation_page
    assert "shared/statusMeta.js" not in automation_page
    assert "getTaskResourceProfile(item)" not in automation_page
    assert "last_endocrine_cortisol" not in automation_page
    assert "last_endocrine_energy" not in automation_page
    assert "last_immune_reason_codes" not in automation_page
    assert "const haystack = [" not in automation_page
    assert "taskMatchesAutomationFilters(item, { reasonFilter, gateFilter, keyword })" not in automation_page
    assert "new URLSearchParams(searchParams)" not in automation_page
    assert "searchParams.get('reason_code')" not in automation_page
    assert "searchParams.get('gate_state')" not in automation_page
    assert "searchParams.get('immune_replay_request_id')" not in automation_page
    assert "tasks.filter((item) => taskMatchesAutomationFilters" not in automation_page
    assert "filteredTasks.find((item) => item.task?.task_id === selectedTaskId)" not in automation_page
    assert "filteredTasks.some((item) => item.task?.task_id === selectedTaskId)" not in automation_page
    assert "taskSearch.trim().toLowerCase()" not in automation_page


def test_automation_scheduler_model_lives_in_execution_feature() -> None:
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    dashboard_model = (MONITOR_SRC / "features" / "execution" / "automationDashboardModel.js").read_text(encoding="utf-8")
    task_section = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationTaskExecutionSection.jsx"
    ).read_text(encoding="utf-8")
    scheduler_model = (MONITOR_SRC / "features" / "execution" / "automationSchedulerModel.js").read_text(encoding="utf-8")

    for export_name in [
        "AUTOMATION_ACTION_LABELS",
        "getAutomationActionLabel",
        "getAutomationSchedulerState",
        "getAutomationTaskCounts",
        "buildAutomationSchedulerPulseNodes",
        "buildAutomationHeroMetaItems",
    ]:
        assert export_name in scheduler_model

    assert "features/execution/automationDashboardModel.js" in automation_page
    assert "automationSchedulerModel.js" in dashboard_model
    assert "getAutomationSchedulerState(health)" in dashboard_model
    assert "getAutomationTaskCounts(tasks, filteredTasks)" in dashboard_model
    assert "buildAutomationSchedulerPulseNodes({" in dashboard_model
    assert "buildAutomationHeroMetaItems(taskCounts, resourceSummary)" in dashboard_model
    assert "getAutomationSchedulerState(health)" not in automation_page
    assert "getAutomationTaskCounts(tasks, filteredTasks)" not in automation_page
    assert "buildAutomationSchedulerPulseNodes({" not in automation_page
    assert "buildAutomationHeroMetaItems(taskCounts, resourceSummary)" not in automation_page
    assert "from '../automationSchedulerModel.js'" in task_section
    assert "getAutomationActionLabel(actionResult.action)" in task_section

    assert "const rawStatus = String(health?.scheduler_status" not in automation_page
    assert "['HEALTHY', 'RUNNING', 'UP', 'OK']" not in automation_page
    assert "['STOPPED', 'DOWN', 'DISABLED', 'OFFLINE']" not in automation_page
    assert "const actionNameMap" not in automation_page
    assert "enable: '启用'" not in automation_page
    assert "label: '节律态'" not in automation_page
    assert "label: '推进中'" not in automation_page
    assert "tasks.filter((item) => item.task?.enabled)" not in automation_page
    assert "tasks.filter((item) => item.health?.status === 'RUNNING')" not in automation_page
    assert "tasks.filter((item) => item.health?.status === 'FAILED')" not in automation_page
    assert "resourceSummary.deferred > 0 ? 'amber' : 'teal'" not in automation_page
    assert "metaItems={heroMetaItems}" in automation_page


def test_automation_dashboard_view_model_lives_in_execution_feature() -> None:
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    dashboard_model = (MONITOR_SRC / "features" / "execution" / "automationDashboardModel.js").read_text(encoding="utf-8")

    for export_name in [
        "buildAutomationLoadState",
        "buildAutomationSampleMeta",
        "buildAutomationNoticeView",
        "buildAutomationOrganModel",
        "buildAutomationDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in dashboard_model

    assert "features/execution/automationDashboardModel.js" in automation_page
    assert "buildAutomationDashboardViewModel({" in automation_page
    assert "sampleMeta.loadState" in automation_page
    assert "noticeView.overviewError" in automation_page
    assert "noticeView.noSample" in automation_page
    assert "OrganSystemOverview model={endocrineOrganModel}" in automation_page

    for forbidden in [
        "LOAD_STATES",
        "resolveLoadState",
        "buildEndocrineOrganSystemModel",
        "new Error(overviewError)",
        "automationLoadState",
        "sampleLabel=\"节律任务\"",
        "updatedAt={lastUpdatedAt}",
        "source=\"scheduler / automation\"",
        "definition=\"Automation 页汇总",
        "tasks.length > 0 || health ? 'warning' : 'error'",
        "runtimeError.message || String(runtimeError)",
        "LOAD_STATES.NO_SAMPLE",
    ]:
        assert forbidden not in automation_page


def test_automation_page_delegates_execution_ui_to_feature_components() -> None:
    automation_page = (MONITOR_SRC / "pages" / "Automation.jsx").read_text(encoding="utf-8")
    section = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationSection.jsx"
    ).read_text(encoding="utf-8")
    resource_panel = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationResourceSchedulingPanel.jsx"
    ).read_text(encoding="utf-8")
    task_section = (
        MONITOR_SRC
        / "features"
        / "execution"
        / "components"
        / "AutomationTaskExecutionSection.jsx"
    ).read_text(encoding="utf-8")

    assert "features/execution/components/AutomationResourceSchedulingPanel.jsx" in automation_page
    assert "features/execution/components/AutomationTaskExecutionSection.jsx" in automation_page
    assert "from './AutomationSection.jsx'" in resource_panel
    assert "from './AutomationSection.jsx'" in task_section
    assert "export default function AutomationSection(" in section

    for needle in [
        "function AutomationSection(",
        "function TaskRow(",
        "function TaskDetailPanel(",
        "function LoadingTasksNotice(",
        "function ResourceSchedulingPanel(",
    ]:
        assert needle not in automation_page


def test_prompt_source_and_type_badge_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    prompt_ops_model = (MONITOR_SRC / "features" / "operations" / "promptOpsModel.js").read_text(encoding="utf-8")
    migrated_files = {
        "pages/PromptConfig.jsx": [
            "bg-green-100 text-green-700 border-green-200",
            "bg-gray-100 text-gray-600 border-gray-200",
            "bg-purple-100 text-purple-700 border-purple-200",
            "bg-blue-100 text-blue-700 border-blue-200",
        ],
        "pages/PersonaPromptOps.jsx": [
            "bg-green-100 text-green-700 border-green-200",
            "bg-gray-100 text-gray-600 border-gray-200",
            "bg-purple-100 text-purple-700 border-purple-200",
            "bg-blue-100 text-blue-700 border-blue-200",
        ],
    }

    for export_name in [
        "PROMPT_SOURCE_BADGE_CLASSES",
        "PROMPT_SOURCE_LABELS",
        "PROMPT_CONTENT_TYPE_BADGE_CLASSES",
        "PROMPT_CONTENT_TYPE_LABELS",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in prompt_ops_model
    for rel, forbidden in migrated_files.items():
        source = (MONITOR_SRC / rel).read_text(encoding="utf-8")
        assert "shared/statusMeta.js" not in source
        assert "features/operations/promptOpsModel.js" in source
        for needle in forbidden:
            assert needle not in source, rel


def test_prompt_ops_view_model_lives_in_operations_feature() -> None:
    prompt_page = (MONITOR_SRC / "pages" / "PromptConfig.jsx").read_text(encoding="utf-8")
    persona_page = (MONITOR_SRC / "pages" / "PersonaPromptOps.jsx").read_text(encoding="utf-8")
    prompt_ops_model = (MONITOR_SRC / "features" / "operations" / "promptOpsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getPromptFiltersFromSearchParams",
        "buildPromptFilterOptions",
        "buildPromptTemplateSummary",
        "buildPromptTemplateListMetaState",
        "buildPromptTemplateSampleMeta",
        "getNextPromptTemplateSelection",
        "getPromptSourceBadgeView",
        "getPromptContentTypeBadgeView",
        "flattenPersonaPromptEntries",
        "getNextPersonaPromptSelection",
        "findSelectedPersonaPromptEntry",
        "buildPersonaPromptSummary",
        "buildPersonaPromptCatalogLoadState",
        "buildPersonaPromptSampleMeta",
        "formatPromptTemplateUpdatedAt",
    ]:
        assert f"export function {export_name}" in prompt_ops_model

    assert "resolveLoadState({" in prompt_ops_model
    assert "formatDateTime(value, { placeholder: '-' })" in prompt_ops_model
    assert "resolveStatusClass(PROMPT_SOURCE_BADGE_CLASSES" in prompt_ops_model
    assert "resolveStatusLabel(PROMPT_SOURCE_LABELS" in prompt_ops_model
    assert "new Set((Array.isArray(items) ? items : []).map" in prompt_ops_model
    assert "safeItems.filter((item) => item?.source === 'override')" in prompt_ops_model
    assert "safeEntries.filter((entry) => entry?.item?.content_type === 'json')" in prompt_ops_model

    assert "getPromptFiltersFromSearchParams(searchParams)" in prompt_page
    assert "buildPromptFilterOptions(items, 'scope')" in prompt_page
    assert "buildPromptTemplateSummary(items)" in prompt_page
    assert "buildPromptTemplateListMetaState({" in prompt_page
    assert "buildPromptTemplateSampleMeta({" in prompt_page
    assert "sampleMeta.loadState" in prompt_page
    assert "getNextPromptTemplateSelection(items, selectedId)" in prompt_page
    assert "getPromptSourceBadgeView(source)" in prompt_page
    assert "getPromptContentTypeBadgeView(value)" in prompt_page
    assert "formatPromptTemplateUpdatedAt(selected.updated_at_ts_ms)" in prompt_page

    assert "flattenPersonaPromptEntries(bundles)" in persona_page
    assert "buildPersonaPromptSummary({ bundles, entries })" in persona_page
    assert "buildPersonaPromptCatalogLoadState({" in persona_page
    assert "buildPersonaPromptSampleMeta({" in persona_page
    assert "sampleMeta.loadState" in persona_page
    assert "getNextPersonaPromptSelection(bundles, current)" in persona_page
    assert "findSelectedPersonaPromptEntry(bundles, selectedId)" in persona_page
    assert "getPromptSourceBadgeView(source, { resolveLabel: true })" in persona_page
    assert "getPromptContentTypeBadgeView(value, { resolveLabel: true })" in persona_page
    assert "formatPromptTemplateUpdatedAt(selected.updated_at_ts_ms)" in persona_page

    for source in [prompt_page, persona_page]:
        for forbidden in [
            "PROMPT_SOURCE_BADGE_CLASSES",
            "PROMPT_CONTENT_TYPE_BADGE_CLASSES",
            "resolveStatusClass(",
            "resolveStatusLabel(",
            "formatDateTime(selected.updated_at_ts_ms",
            "resolveLoadState",
            "sampleLabel=\"模板\"",
            "source=\"prompt-config\"",
            "source=\"persona-first-batch\"",
            "definition=\"提示模板页当前是只读观测台",
            "definition=\"人格文案页当前是只读观测台",
            "hasData: Boolean(",
            "hasSamples:",
            "bg-green-100 text-green-700 border-green-200",
            "bg-purple-100 text-purple-700 border-purple-200",
        ]:
            assert forbidden not in source

    for forbidden in [
        "String(searchParams.get('keyword') || '')",
        "...new Set(items.map((item) => item.scope).filter(Boolean))",
        "items.filter((item) => item.source === 'override')",
        "items.filter((item) => item.content_type === 'json')",
        "items.filter((item) => String(item.category || '').includes('fallback')",
        "items.some((item) => item.template_id === selectedId)",
        "items[0].template_id",
    ]:
        assert forbidden not in prompt_page

    for forbidden in [
        "function flattenEntries(",
        "bundles.flatMap((bundle) => bundle.entries || [])",
        "const entryIds = flattenEntries(bundles).map",
        "entryIds.includes(current)",
        "for (const bundle of bundles)",
        "entries.filter((entry) => entry.item?.source === 'override')",
        "entries.filter((entry) => entry.item?.content_type === 'json')",
    ]:
        assert forbidden not in persona_page


def test_alert_severity_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    alerts_page = (MONITOR_SRC / "pages" / "Alerts.jsx").read_text(encoding="utf-8")
    alerts_model = (MONITOR_SRC / "features" / "observability" / "alertsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "ALERT_SEVERITY_RANK",
        "ALERT_SEVERITY_LABELS",
        "ALERT_SEVERITY_BADGE_CLASSES",
        "ALERT_SEVERITY_CARD_CLASSES",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in alerts_model
    assert "shared/statusMeta.js" not in alerts_page
    assert "const SEVERITY_RANK" not in alerts_page
    assert "const SEVERITY_META" not in alerts_page
    assert "badge: 'text-red-700 bg-red-50 border-red-200'" not in alerts_page
    assert "card: 'border-red-200 bg-red-50/70'" not in alerts_page


def test_alerts_view_model_lives_in_observability_feature() -> None:
    alerts_page = (MONITOR_SRC / "pages" / "Alerts.jsx").read_text(encoding="utf-8")
    alerts_model = (MONITOR_SRC / "features" / "observability" / "alertsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "FORGE_REASON_CODES",
        "normalizeAlertSeverity",
        "buildAlertTitle",
        "buildAlertDetail",
        "buildAlertCardView",
        "getAlertsFilterCodeFromSearch",
        "buildAlertsFilterSearch",
        "buildErrorAlerts",
        "buildServiceAlerts",
        "mergeAndSortAlerts",
        "buildAlertReasonBreakdown",
        "filterAlertsByReason",
        "getAlertSeverityCounts",
        "isForgeRiskFilter",
        "getAlertTopReasonLabel",
        "buildAlertsHeroContent",
        "buildAlertsHeroMetaItems",
        "getAlertReasonBreakdownBarWidth",
        "buildAlertsLoadState",
        "buildAlertsSampleMeta",
        "buildAlertsCenterView",
    ]:
        assert export_name in alerts_model

    assert "features/observability/alertsModel.js" in alerts_page
    assert "buildAlertCardView(alert)" in alerts_page
    assert "getAlertsFilterCodeFromSearch(location.search)" in alerts_page
    assert "buildAlertsFilterSearch(location.search, next)" in alerts_page
    assert "buildAlertsCenterView({" in alerts_page
    assert "sampleMeta.loadState" in alerts_page
    assert "getAlertReasonBreakdownBarWidth(count, allAlerts.length)" in alerts_page
    assert "buildDigitalLifeAlerts(runtimeSnapshot" in alerts_model
    assert "createRequestRefs(filteredAlerts.filter" in alerts_model
    assert "buildImmuneOrganSystemModel({" in alerts_model
    assert "resolveLoadState({" in alerts_model
    assert "sampleLabel: '告警项'" in alerts_model

    assert "function normalizeAlertSeverity(" not in alerts_page
    assert "function buildAlertTitle(" not in alerts_page
    assert "function buildAlertDetail(" not in alerts_page
    assert "new URLSearchParams" not in alerts_page
    assert "buildDigitalLifeAlerts(" not in alerts_page
    assert "createRequestRefs(" not in alerts_page
    assert "buildImmuneOrganSystemModel(" not in alerts_page
    assert "resolveLoadState" not in alerts_page
    assert "buildErrorAlerts(errorRes)" not in alerts_page
    assert "buildServiceAlerts(healthRes)" not in alerts_page
    assert "mergeAndSortAlerts({ runtimeAlerts, serviceAlerts, alerts })" not in alerts_page
    assert "buildAlertReasonBreakdown(allAlerts)" not in alerts_page
    assert "filterAlertsByReason(allAlerts, filterCode)" not in alerts_page
    assert "getAlertSeverityCounts(filteredAlerts)" not in alerts_page
    assert "isForgeRiskFilter(filterCode)" not in alerts_page
    assert "getAlertTopReasonLabel(reasonBreakdown)" not in alerts_page
    assert "buildAlertsHeroContent({" not in alerts_page
    assert "buildAlertsHeroMetaItems({" not in alerts_page
    assert "alertsUpdatedAt" not in alerts_page
    assert "representativeRefs" not in alerts_page
    assert "sampleLabel=\"告警项\"" not in alerts_page
    assert "source=\"runtime + errors + services + capability gap\"" not in alerts_page
    assert "definition=\"告警中心把运行态" not in alerts_page
    assert "ALERT_SEVERITY_RANK" not in alerts_page
    assert "ALERT_SEVERITY_BADGE_CLASSES" not in alerts_page
    assert "ALERT_SEVERITY_CARD_CLASSES" not in alerts_page
    assert "ALERT_SEVERITY_LABELS" not in alerts_page
    assert "FORGE_REASON_CODES" not in alerts_page
    assert "formatServiceStatus" not in alerts_page
    assert "compactText(" not in alerts_page
    assert "reason_code: `SERVICE_${String(service.status" not in alerts_page
    assert "Math.min(100, (count / Math.max(allAlerts.length" not in alerts_page


def test_gateway_status_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    gateway_page = (MONITOR_SRC / "pages" / "Gateway.jsx").read_text(encoding="utf-8")

    for export_name in [
        "GATEWAY_STATUS_LABELS",
        "GATEWAY_STATUS_TEXT_CLASSES",
        "GATEWAY_STATUS_BADGE_CLASSES",
        "GATEWAY_PLATFORM_META",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in gateway_page
    assert "const map = {" not in gateway_page
    assert "const PLATFORM_META" not in gateway_page
    assert "label: '在线'" not in gateway_page
    assert "label: '微信'" not in gateway_page
    assert "color: '#07C160'" not in gateway_page
    assert "bg-green-50 border-green-200" not in gateway_page
    assert "color: 'text-green-600'" not in gateway_page


def test_gateway_view_model_lives_in_execution_feature() -> None:
    gateway_page = (MONITOR_SRC / "pages" / "Gateway.jsx").read_text(encoding="utf-8")
    gateway_model = (MONITOR_SRC / "features" / "execution" / "gatewayModel.js").read_text(encoding="utf-8")

    assert "export const GATEWAY_WINDOW_OPTIONS" in gateway_model
    for export_name in [
        "getGatewayPlatforms",
        "isActiveGatewayPlatform",
        "getActiveGatewayPlatforms",
        "getGatewayEventCount",
        "isGatewayInitialState",
        "countGatewayPlatformsByStatus",
        "buildGatewayLoadState",
        "buildGatewayHeroDescription",
        "buildGatewayHeroMetaItems",
        "buildGatewayMetricCards",
        "buildGatewayViewModel",
    ]:
        assert f"export function {export_name}(" in gateway_model

    assert "features/execution/gatewayModel.js" in gateway_page
    assert "buildGatewayViewModel({" in gateway_page
    assert "GATEWAY_WINDOW_OPTIONS" in gateway_page
    assert "metricCards.map((card)" in gateway_page
    assert "GATEWAY_METRIC_ICONS[card.key]" in gateway_page

    for forbidden in [
        "const WINDOW_OPTIONS = [",
        "LOAD_STATES",
        "resolveLoadState",
        "buildMotorOrganSystemModel",
        "data?.platforms ?? []",
        "platforms.filter(",
        "activePlatforms",
        "isInitialState",
        "downPlatformCount",
        "degradedPlatformCount",
        "Number(data?.total_inbound",
        "hasSamples: !",
        "平台卡显示的是适配器健康态",
        "在线平台：${",
        "data?.humanize_enabled ? '开启' : '关闭'",
        "data?.total_errors ?? '—'",
    ]:
        assert forbidden not in gateway_page


def test_log_stream_level_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    log_stream_page = (MONITOR_SRC / "pages" / "LogStream.jsx").read_text(encoding="utf-8")
    log_stream_model = (MONITOR_SRC / "features" / "observability" / "logStreamModel.js").read_text(encoding="utf-8")

    for export_name in [
        "LOG_LEVEL_STYLES",
        "classifyLogLevel",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in log_stream_model
    assert "features/observability/logStreamModel.js" in log_stream_page
    assert "shared/statusMeta.js" not in log_stream_page
    assert "const LEVEL_STYLES" not in log_stream_page
    assert "function classifyLogLevel(" not in log_stream_page
    assert "bg-red-100 text-red-700" not in log_stream_page
    assert "bg-orange-100 text-orange-700" not in log_stream_page


def test_log_stream_view_model_lives_in_observability_feature() -> None:
    log_stream_page = (MONITOR_SRC / "pages" / "LogStream.jsx").read_text(encoding="utf-8")
    log_stream_model = (MONITOR_SRC / "features" / "observability" / "logStreamModel.js").read_text(encoding="utf-8")

    for export_name in [
        "clampLogPollingInterval",
        "formatLogTime",
        "getLogBatchEntries",
        "getLogEntryKey",
        "mergeLogEntries",
        "buildLogLineText",
        "splitHighlightedLogText",
        "buildLogLineView",
        "filterLogEntries",
        "buildLogHeroMetaItems",
        "buildLogLiveStatusLabel",
        "buildLogLoadState",
        "buildLogSampleMeta",
        "buildLogNoticeView",
        "buildLogStreamViewModel",
    ]:
        assert f"export function {export_name}" in log_stream_model

    assert "export const LOG_STREAM_MAX_ITEMS" in log_stream_model
    assert "export const LOG_STREAM_MAX_POLLING_INTERVAL_SECONDS" in log_stream_model
    assert "formatTime(tsMs, { placeholder: '', options: LOG_STREAM_TIME_OPTIONS })" in log_stream_model
    assert "new Set(previous.map((item) => getLogEntryKey(item)))" in log_stream_model
    assert "LOG_LEVEL_STYLES[level] || LOG_LEVEL_STYLES.INFO" in log_stream_model
    assert "JSON.stringify(entry).toLowerCase().includes(normalizedKeyword)" in log_stream_model

    assert "buildLogLineView(entry, highlight)" in log_stream_page
    assert "clampLogPollingInterval(pollingInterval)" in log_stream_page
    assert "getLogBatchEntries(logBatch)" in log_stream_page
    assert "mergeLogEntries(prev, entries)" in log_stream_page
    assert "filterLogEntries(logs, { keyword, levelFilter })" in log_stream_page
    assert "buildLogStreamViewModel({" in log_stream_page
    assert "buildLogLiveStatusLabel({ paused, pollingInterval })" in log_stream_page
    assert "sampleMeta.loadState" in log_stream_page
    assert "noticeView.batchError" in log_stream_page
    assert "noticeView.noSample" in log_stream_page

    for forbidden in [
        "const LOG_TIME_OPTIONS",
        "function formatLogTime(",
        "LOAD_STATES",
        "resolveLoadState",
        "new Error(batchError)",
        "hasSamples: logs.length",
        "available: Boolean(logBatch",
        "batchError && logs.length === 0",
        "logs.length > 0 ? 'warning' : 'error'",
        "logLoadState ===",
        "sampleLabel=\"日志\"",
        "updatedAt={lastUpdatedAt}",
        "source=\"errors\"",
        "definition=\"日志流页会持续拉取最近错误批次",
        "Math.min(pollingInterval, 15)",
        "new Set(prev.map",
        "`${item.ts_ms}-${item.reason_code}-${item.stage}`",
        "JSON.stringify(entry).toLowerCase()",
        "highlight.replace(/[.*+?^${}()|[\\]\\\\]/g",
        "new RegExp(`(${escaped})`, 'gi')",
        "buildLogHeroMetaItems({",
        "`当前显示：${filtered.length} 条`",
        "`拉取周期：${Math.min(pollingInterval, 15)}s`",
        "`● 实时：${Math.min(pollingInterval, 15)}s`",
    ]:
        assert forbidden not in log_stream_page


def test_pentest_display_metadata_lives_in_shared_module() -> None:
    shared = SHARED_PENTEST_META.read_text(encoding="utf-8")
    pentest_page = (MONITOR_SRC / "pages" / "SecurityPentest.jsx").read_text(encoding="utf-8")
    tools_page = (MONITOR_SRC / "pages" / "Tools.jsx").read_text(encoding="utf-8")
    tools_pentest_section = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolsPentestMetadataSection.jsx"
    ).read_text(encoding="utf-8")
    tool_detail_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolDetailPanel.jsx"
    ).read_text(encoding="utf-8")
    tool_execute_panel = (
        MONITOR_SRC / "features" / "execution" / "components" / "ToolExecutePanel.jsx"
    ).read_text(encoding="utf-8")

    for export_name in [
        "PENTEST_PHASE_OPTIONS",
        "PENTEST_PHASE_LABELS",
        "PENTEST_RISK_STYLES",
        "isPentestTool",
        "flattenPentestTools",
        "countPentestToolsByPhase",
        "countReadonlyPentestToolsByPhase",
        "resolvePentestRiskStyle",
    ]:
        assert export_name in shared

    assert "shared/pentestMeta.js" in pentest_page
    assert "const PHASE_OPTIONS" not in pentest_page
    assert "function phaseCounts(" not in pentest_page
    assert "label: 'Phase 1 Passive'" not in pentest_page

    assert "shared/pentestMeta.js" in tools_pentest_section
    assert "shared/pentestMeta.js" in tool_detail_panel
    assert "shared/pentestMeta.js" in tool_execute_panel
    for source in [tools_page, tools_pentest_section, tool_detail_panel, tool_execute_panel]:
        assert "const PENTEST_PHASE_LABELS" not in source
        assert "const PENTEST_RISK_STYLES" not in source
        assert "function isPentestTool(" not in source
        assert "function flattenPentestTools(" not in source
        assert "function resolvePentestRiskStyle(" not in source
        assert "label: 'critical', className: 'bg-red-100 text-red-700'" not in source


def test_pentest_run_detail_model_lives_in_security_feature() -> None:
    run_detail_page = (MONITOR_SRC / "pages" / "SecurityPentestRunDetail.jsx").read_text(encoding="utf-8")
    run_detail_controller = (MONITOR_SRC / "features" / "security" / "pentestRunDetailController.js").read_text(encoding="utf-8")
    run_detail_surface = (MONITOR_SRC / "features" / "security" / "components" / "SecurityPentestRunDetailSurface.jsx").read_text(encoding="utf-8")
    run_detail_model = (MONITOR_SRC / "features" / "security" / "pentestRunDetailModel.js").read_text(encoding="utf-8")
    run_detail_components = MONITOR_SRC / "features" / "security" / "components" / "pentestRunDetail"
    attack_graph_panel = (run_detail_components / "AttackGraphPanel.jsx").read_text(encoding="utf-8")
    live_timeline_section = (run_detail_components / "LiveTimelineSection.jsx").read_text(encoding="utf-8")
    approval_rail_section = (run_detail_components / "ApprovalRailSection.jsx").read_text(encoding="utf-8")

    for export_name in [
        "normalizePentestEvent",
        "pentestEventKey",
        "mergePentestEvents",
        "mergeRunWithExistingEvents",
        "buildPentestAttackGraph",
        "buildPentestApprovalRailView",
        "buildPentestRunHeroMetaItems",
    ]:
        assert f"export function {export_name}(" in run_detail_model

    assert "./pentestRunDetailModel.js" in run_detail_controller
    assert "buildPentestAttackGraph(run)" in attack_graph_panel
    assert "buildPentestApprovalRailView(run, environment)" in run_detail_controller
    assert "buildPentestRunHeroMetaItems(run, loading)" in run_detail_controller
    assert "mergeRunWithExistingEvents(runData, previousRun)" in run_detail_controller
    assert "mergePentestEvents(previousRun?.events || [], [normalized])" in run_detail_controller
    assert "normalizePentestEvent(event)" in run_detail_controller
    assert "pentestEventKey(event)" in live_timeline_section
    assert "ApprovalRailSection" in run_detail_surface
    assert "AttackGraphPanel" in run_detail_surface
    assert "LiveTimelineSection" in run_detail_surface

    assert "function normalizePentestEvent(" not in run_detail_page
    assert "function pentestEventKey(" not in run_detail_page
    assert "function mergePentestEvents(" not in run_detail_page
    assert "function mergeRunWithExistingEvents(" not in run_detail_page
    assert "fetchPentestRun(" not in run_detail_page
    assert "subscribePentestRunEvents(" not in run_detail_page
    assert "setLiveStatus(" not in run_detail_page
    assert "ScopeGuard" not in run_detail_page
    assert "const openSideEffectSteps" not in run_detail_page
    assert "const openSideEffectSteps" not in run_detail_surface
    assert "const approvalRecorded" not in run_detail_page
    assert "const approvalRecorded" not in run_detail_surface
    assert "const continuationLabel = !approvalRequired" not in run_detail_page
    assert "const continuationLabel = !approvalRequired" not in approval_rail_section
    assert "canApprove: Boolean(approvalRequired && environment?.lab_mode" in run_detail_model
    assert "canContinue: Boolean(approvalRequired && environment?.lab_mode" in run_detail_model


def test_pentest_run_detail_surface_is_feature_owned() -> None:
    page = MONITOR_SRC / "pages" / "SecurityPentestRunDetail.jsx"
    surface = MONITOR_SRC / "features" / "security" / "components" / "SecurityPentestRunDetailSurface.jsx"
    controller = MONITOR_SRC / "features" / "security" / "pentestRunDetailController.js"
    page_source = page.read_text(encoding="utf-8")
    surface_source = surface.read_text(encoding="utf-8")
    controller_source = controller.read_text(encoding="utf-8")

    assert surface.exists()
    assert controller.exists()
    assert "SecurityPentestRunDetailSurface from '../features/security/components/SecurityPentestRunDetailSurface.jsx'" in page_source
    assert "usePentestRunDetailController" in page_source
    assert "export function usePentestRunDetailController(runId)" in controller_source
    assert "export default function SecurityPentestRunDetailSurface(" in surface_source
    assert "subscribePentestRunEvents(runId" in controller_source
    assert "approvePentestSideEffect(runId)" in controller_source
    assert "continuePentestSideEffect(runId" in controller_source
    assert "cancelPentestRun(runId)" in controller_source

    for stale_component in [
        "AttackGraph",
        "CheckCircle2",
        "FileText",
        "JsonBlock",
        "Link",
        "NoticeBanner",
        "PageHero",
        "RefreshCw",
        "ShieldAlert",
        "StatusBadge",
        "XCircle",
    ]:
        assert stale_component not in page_source

    for stale_controller_detail in [
        "useEffect",
        "useMemo",
        "useRef",
        "useState",
        "fetchPentestReport",
        "approvePentestSideEffect",
        "continuePentestSideEffect",
        "cancelPentestRun",
        "subscribePentestRunEvents",
    ]:
        assert stale_controller_detail not in page_source


def test_security_immune_query_and_view_model_live_in_security_feature() -> None:
    page = (MONITOR_SRC / "pages" / "SecurityImmune.jsx").read_text(encoding="utf-8")
    query = (MONITOR_SRC / "features" / "security" / "securityImmuneQueries.js").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "security" / "securityImmuneModel.js").read_text(encoding="utf-8")
    query_keys = (MONITOR_SRC / "shared" / "query" / "queryKeys.js").read_text(encoding="utf-8")
    freshness = (MONITOR_SRC / "shared" / "query" / "freshness.js").read_text(encoding="utf-8")

    for export_name in [
        "SECURITY_RISK_LEVELS",
        "SECURITY_MIN_RISK_OPTIONS",
        "getSecurityRiskCounts",
        "buildSecurityImmuneHeroMetaItems",
        "buildSecurityRiskCards",
        "buildSecurityIncidentRows",
        "buildSecurityRuntimeBadge",
        "buildSecurityImmuneViewModel",
    ]:
        assert export_name in model

    assert "export function useSecurityImmuneQuery" in query
    assert "fetchSecurityRuntime()" in query
    assert "fetchSecurityAssets()" in query
    assert "fetchSecurityIncidents(minRisk)" in query
    assert "Promise.allSettled([" in query
    assert "OBS_QUERY_KEYS.securityImmune(minRisk)" in query
    assert "OBS_STALE_TIMES.securityImmune" in query
    assert "securityImmune: (minRisk)" in query_keys
    assert "securityImmune: 60_000" in freshness

    assert "features/security/securityImmuneQueries.js" in page
    assert "features/security/securityImmuneModel.js" in page
    assert "useSecurityImmuneQuery({" in page
    assert "buildSecurityImmuneViewModel({" in page
    assert "SECURITY_MIN_RISK_OPTIONS.map" in page
    assert "riskCards.map" in page
    assert "incidentRows.map" in page
    assert "StatusBadge status={runtimeBadge.status}" in page
    assert "runSecurityHuntOnce('manual_web_security_immune')" in page

    for forbidden in [
        "fetchSecurityRuntime",
        "fetchSecurityAssets",
        "fetchSecurityIncidents",
        "Promise.allSettled",
        "incidents?.risk_counts",
        "runtime?.risk_counts",
        "['critical', 'high', 'medium', 'low'].map",
        "['low', 'medium', 'high', 'critical'].map",
        "incidents.incidents.map",
        "incident.title || incident.kind",
        "incident.detail || incident.reason",
        "runtime ? 'healthy' : 'degraded'",
        "`${assets?.asset_count",
    ]:
        assert forbidden not in page


def test_sessions_and_overview_status_metadata_live_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    sessions_page = (MONITOR_SRC / "pages" / "Sessions.jsx").read_text(encoding="utf-8")
    sessions_model = (MONITOR_SRC / "features" / "observability" / "sessionsModel.js").read_text(encoding="utf-8")
    overview_page = (MONITOR_SRC / "pages" / "Overview.jsx").read_text(encoding="utf-8")
    overview_model = (MONITOR_SRC / "features" / "observability" / "overviewModel.js").read_text(encoding="utf-8")

    for export_name in [
        "SESSION_STATUS_TONES",
        "SUBSYSTEM_STATUS_ORDER",
        "normalizeSessionStatus",
        "resolveSessionStatusTone",
        "normalizeSubsystemStatus",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in sessions_model
    assert "features/observability/sessionsModel.js" in sessions_page
    assert "shared/statusMeta.js" not in sessions_page
    assert "function normalizeStatus(" not in sessions_page
    assert "function getStatusTone(" not in sessions_page
    assert "bg-emerald-400" not in sessions_page
    assert "bg-rose-100 text-rose-700 border-rose-200" not in sessions_page

    assert "shared/statusMeta.js" in overview_model
    assert "shared/statusMeta.js" not in overview_page
    assert "const SUBSYSTEM_STATUS_ORDER" not in overview_page
    assert "function normalizeSubsystemStatus(" not in overview_page


def test_sessions_view_model_lives_in_observability_feature() -> None:
    sessions_page = (MONITOR_SRC / "pages" / "Sessions.jsx").read_text(encoding="utf-8")
    sessions_model = (MONITOR_SRC / "features" / "observability" / "sessionsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getSessionsWindowLabel",
        "getSuppressedSessionCount",
        "buildSessionsPaginationState",
        "buildSessionsLoadState",
        "buildSessionsSummary",
        "buildSessionsPulseNodes",
        "buildSessionsHeroTitle",
        "buildSessionsHeroDescription",
        "buildSessionsHeroMetaItems",
        "buildSessionsSampleMeta",
        "shouldShowSessionsNoSampleNotice",
        "buildSessionsQueryView",
        "buildSessionsExportParams",
        "getPreviousSessionsPage",
        "getNextSessionsPage",
        "buildSessionTracePath",
        "buildSessionCardView",
    ]:
        assert f"export function {export_name}" in sessions_model

    assert "export const SESSIONS_PAGE_SIZE" in sessions_model
    assert "export const SESSIONS_WINDOW_OPTIONS" in sessions_model
    assert "normalizeSessionStatus(item?.status)" in sessions_model
    assert "resolveSessionStatusTone(item.status)" in sessions_model
    assert "latencyValues.reduce((sum, value) => sum + value, 0)" in sessions_model
    assert "compactText(item.input_preview || '暂无输入摘要', 180)" in sessions_model
    assert "formatDateTime(item.ts_ms)" in sessions_model
    assert "buildSessionTracePath(requestId)" in sessions_page
    assert "buildSessionsExportParams({ windowMinutes, keyword, status })" in sessions_page
    assert "buildSessionsQueryView({" in sessions_page
    assert "sampleMeta.loadState" in sessions_page
    assert "showNoSampleNotice" in sessions_page
    assert "getPreviousSessionsPage(currentPage)" in sessions_page
    assert "getNextSessionsPage(currentPage, totalPages)" in sessions_page
    assert "buildSessionCardView(item)" in sessions_page
    assert "sanitizeRecentRequests(rawItems, { keyword })" in sessions_model
    assert "resolveLoadState({" in sessions_model
    assert "buildSessionsLoadState({" in sessions_model
    assert "buildSessionsSampleMeta({" in sessions_model
    assert "sampleLabel: '请求'" in sessions_model

    for forbidden in [
        "const PAGE_SIZE = 20",
        "const WINDOW_OPTIONS = [",
        "function getWindowLabel(",
        "LOAD_STATES",
        "resolveLoadState",
        "sanitizeRecentRequests",
        "sessionsData?.items",
        "getSuppressedSessionCount(rawItems, sessions)",
        "buildSessionsPaginationState({",
        "buildSessionsSummary(sessions)",
        "buildSessionsPulseNodes({",
        "buildSessionsHeroTitle({ windowMinutes, total })",
        "buildSessionsHeroDescription({ keyword, status })",
        "buildSessionsHeroMetaItems({ currentPage, totalPages, summary })",
        "sessionsLoadState === LOAD_STATES.NO_SAMPLE",
        "sampleLabel=\"请求\"",
        "source=\"sessions\"",
        "definition=\"会话页按时间窗",
        "Math.max(0, rawItems.length - sessions.length)",
        "Math.max(1, Math.ceil(total / PAGE_SIZE))",
        "sessions.filter((item) => normalizeSessionStatus(item.status)",
        "latencyValues.reduce((sum, value) => sum + value, 0)",
        "{ label: '当前页', value: `${sessions.length}/${PAGE_SIZE}`",
        "title={`最近 ${getWindowLabel(windowMinutes)} 共 ${total} 条请求`}",
        "当前过滤生效${keyword ?",
        "`当前页：${currentPage} / ${totalPages}`",
        "window_minutes: windowMinutes",
        "keyword: keyword || undefined",
        "navigate(`/observability/trace/${requestId}`)",
        "const nextPage = Math.max(1, currentPage - 1)",
        "const nextPage = Math.min(totalPages, currentPage + 1)",
        "resolveSessionStatusTone(item.status)",
        "compactText(item.input_preview || '暂无输入摘要', 180)",
        "formatDateTime(item.ts_ms)",
        "formatAlertTitle(item.reason_code)",
    ]:
        assert forbidden not in sessions_page


def test_overview_view_model_lives_in_observability_feature() -> None:
    overview_page = (MONITOR_SRC / "pages" / "Overview.jsx").read_text(encoding="utf-8")
    overview_model = (MONITOR_SRC / "features" / "observability" / "overviewModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getSubsystemHeadline",
        "getSubsystemFootnote",
        "getOverviewServiceGroups",
        "getActiveSubsystems",
        "buildOverviewPulseNodes",
        "buildOverviewHeroMetaItems",
        "buildOverviewLoadState",
        "buildOverviewSampleMeta",
        "buildOverviewQueryView",
    ]:
        assert f"export function {export_name}(" in overview_model

    assert "features/observability/overviewModel.js" in overview_page
    assert "buildOverviewQueryView({" in overview_page
    assert "sampleMeta.loadState" in overview_page
    assert "getSubsystemHeadline(subsystem)" in overview_page
    assert "getSubsystemFootnote(subsystem)" in overview_page
    assert "status={subsystem.normalizedStatus}" in overview_page
    assert "resolveLoadState({" in overview_model
    assert "buildOverviewLoadState({" in overview_model
    assert "buildOverviewSampleMeta({" in overview_model
    assert "sampleLabel: '观测对象'" in overview_model

    assert "function getSubsystemHeadline(" not in overview_page
    assert "function getSubsystemFootnote(" not in overview_page
    assert "resolveLoadState" not in overview_page
    assert "wsConnected && liveHealthData" not in overview_page
    assert "Math.max(lastUpdatedAt || 0, liveHealthUpdatedAt || 0)" not in overview_page
    assert "hasData: Boolean(data || subsysData || agentLoopData || uiAssertionData)" not in overview_page
    assert "hasSamples: Boolean((data?.services ?? []).length" not in overview_page
    assert "const services = data?.services ?? []" not in overview_page
    assert "getOverviewServiceGroups(services)" not in overview_page
    assert "getActiveSubsystems(subsysData)" not in overview_page
    assert "buildOverviewPulseNodes({" not in overview_page
    assert "buildOverviewHeroMetaItems({" not in overview_page
    assert "sampleLabel=\"观测对象\"" not in overview_page
    assert "source={wsConnected ? 'health websocket + overview queries' : 'overview queries'}" not in overview_page
    assert "definition=\"Overview 保留实时健康快照" not in overview_page
    assert "SUBSYSTEM_STATUS_ORDER" not in overview_page
    assert "normalizeSubsystemStatus" not in overview_page
    assert "service.service_type === 'edge'" not in overview_page
    assert "label: '中枢稳态'" not in overview_page
    assert "label: '节律推进'" not in overview_page
    assert "活跃子系统：${activeSubsystems.length}" not in overview_page


def test_bio_ops_status_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    surfaces = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsSurfaces.jsx").read_text(encoding="utf-8")
    summary = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsSummarySections.jsx").read_text(encoding="utf-8")
    workbench = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsWorkbench.jsx").read_text(encoding="utf-8")
    labels = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsLabels.js").read_text(encoding="utf-8")

    for export_name in [
        "BIO_OPS_STATUS_CLASSES",
        "BIO_OPS_STATUS_LABELS",
        "BIO_OPS_STATUS_ORDER",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in surfaces
    assert "shared/statusMeta.js" in summary
    assert "shared/statusMeta.js" in workbench
    assert "export const STATUS_STYLES" not in surfaces
    assert "export const STATUS_LABELS" not in surfaces
    assert "STATUS_STYLES[normalized]" not in surfaces
    assert "import { STATUS_LABELS, StatusPill } from './BioOpsSurfaces.jsx'" not in workbench
    assert "export const STATUS_ORDER" not in labels
    assert "BIO_OPS_STATUS_ORDER" in summary


def test_bio_ops_page_view_model_lives_in_observability_feature() -> None:
    page = (MONITOR_SRC / "pages" / "CodexBioOpsObservability.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "bioOpsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "BIO_OPS_CAPABILITY_SECTION_DEFINITIONS",
        "getBioOpsStatusTotal",
        "getPendingBioOpsAdoptions",
        "getBioOpsObservabilityViewData",
        "buildBioOpsCapabilitySections",
        "buildBioOpsMetricTiles",
        "isBioOpsKeyBenefitObservable",
    ]:
        assert export_name in model

    assert "features/observability/bioOpsModel.js" in page
    assert "getBioOpsObservabilityViewData(data)" in page
    assert "buildBioOpsCapabilitySections(bioOps, BIO_OPS_CAPABILITY_PANELS)" in page
    assert "buildBioOpsMetricTiles(bioOpsView)" in page
    assert "isBioOpsKeyBenefitObservable({" in page
    assert "const capabilitySections = useMemo(() => [" not in page
    assert "Object.values(statusCounts).reduce" not in page
    assert "title: 'Inner Mechanisms'" not in page
    assert "subtitle: '可信基础层" not in page
    assert "statusCounts.PROPOSED" not in page
    assert "pendingAdoptions.length || statusCounts.CANDIDATE" not in page
    assert "codex.hard_cut_scan?.passed && blockedWrite.status === 'BLOCKED'" not in page


def test_bio_ops_owner_panel_view_model_lives_in_observability_feature() -> None:
    aggregate = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsOwnerPanels.jsx").read_text(encoding="utf-8")
    pending_adoptions = (
        OBSERVABILITY_COMPONENTS / "bioOps" / "owner" / "PendingAdoptions.jsx"
    ).read_text(encoding="utf-8")
    owner_inbox = (
        OBSERVABILITY_COMPONENTS / "bioOps" / "owner" / "OwnerInboxPanel.jsx"
    ).read_text(encoding="utf-8")
    component = f"{aggregate}\n{pending_adoptions}\n{owner_inbox}"
    model = (MONITOR_SRC / "features" / "observability" / "bioOpsOwnerModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getBioOpsOwnerRunParams",
        "getBioOpsPendingAdoptionReview",
        "buildBioOpsAdoptionDecisionBody",
        "buildBioOpsOwnerStateReviewBody",
        "buildBioOpsOwnerInboxViewModel",
    ]:
        assert f"export function {export_name}" in model

    assert "export { default as OwnerInboxPanel } from './owner/OwnerInboxPanel.jsx'" in aggregate
    assert "export { default as PendingAdoptions } from './owner/PendingAdoptions.jsx'" in aggregate
    assert "bioOpsOwnerModel.js" not in aggregate
    assert "getBioOpsPendingAdoptionReview({ items, selectedId, detail })" in pending_adoptions
    assert "buildBioOpsAdoptionDecisionBody(decision, active)" in pending_adoptions
    assert "getBioOpsOwnerRunParams(runId)" in pending_adoptions
    assert "buildBioOpsOwnerInboxViewModel({" in owner_inbox
    assert "buildBioOpsOwnerStateReviewBody(decision, ownerOrgan)" in owner_inbox
    assert "getBioOpsOwnerRunParams(runId)" in owner_inbox

    for forbidden in [
        "Array.isArray(data?.items)",
        "Array.isArray(queues?.items)",
        "Array.isArray(ownerState?.items)",
        "Array.isArray(ownerRuntimeHints?.items)",
        "data?.status_counts",
        "imports?.status_counts",
        "queues?.status_counts",
        "ownerState?.status_counts",
        "ownerState?.review_status_counts",
        "Number(counts.READY_FOR_OWNER_IMPORT",
        "Number(importCounts.READY_FOR_ORGAN_CONSUMPTION",
        "`${decision} from Codex BioOps owner review panel`",
        "owner accepted runtime hint",
        "['READY_FOR_OWNER_IMPORT', 'PENDING_REVIEW'",
        "['PENDING_OWNER_STATE_REVIEW', 'OWNER_STATE_ACCEPTED'",
        "runId ? { run_id: runId } : {}",
    ]:
        assert forbidden not in component


def test_bio_ops_owner_panels_are_split_by_workflow() -> None:
    aggregate = (OBSERVABILITY_COMPONENTS / "bioOps" / "BioOpsOwnerPanels.jsx").read_text(encoding="utf-8")
    pending_adoptions = (
        OBSERVABILITY_COMPONENTS / "bioOps" / "owner" / "PendingAdoptions.jsx"
    ).read_text(encoding="utf-8")
    owner_inbox = (
        OBSERVABILITY_COMPONENTS / "bioOps" / "owner" / "OwnerInboxPanel.jsx"
    ).read_text(encoding="utf-8")

    assert "export { default as PendingAdoptions }" in aggregate
    assert "export { default as OwnerInboxPanel }" in aggregate
    assert "export default function PendingAdoptions(" in pending_adoptions
    assert "export default function OwnerInboxPanel(" in owner_inbox
    assert "fetchCodexBioOpsAdoptionDetail" in pending_adoptions
    assert "acceptCodexBioOpsAdoption" in pending_adoptions
    assert "rejectCodexBioOpsAdoption" in pending_adoptions
    assert "fetchCodexBioOpsOwnerInbox" in owner_inbox
    assert "materializeCodexBioOpsOwnerInbox" in owner_inbox
    assert "consumeCodexBioOpsOwnerQueues" in owner_inbox

    assert "useState" not in aggregate
    assert "fetchCodexBioOpsOwnerInbox" not in pending_adoptions
    assert "fetchCodexBioOpsAdoptionDetail" not in owner_inbox


def test_metrics_overview_view_model_lives_in_observability_feature() -> None:
    page = (MONITOR_SRC / "pages" / "Metrics.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "metricsOverviewModel.js").read_text(encoding="utf-8")

    assert "export const METRICS_TIME_RANGES" in model
    for export_name in [
        "getMetricsRangeConfig",
        "getMetricsSummary",
        "formatMetricsPercent",
        "buildMetricsTimeseriesView",
        "buildMetricsReasonCodeChartView",
        "buildMetricsLatencyChartView",
        "buildMetricsLoadState",
        "isMetricsInitialState",
        "buildMetricsHeroMetaItems",
        "buildMetricsSummaryCards",
        "buildRateLimitBackendView",
        "buildMetricsOverviewViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/observability/metricsOverviewModel.js" in page
    assert "getMetricsRangeConfig(timeRange)" in page
    assert "buildMetricsOverviewViewModel({" in page
    assert "METRICS_TIME_RANGES.map" in page
    assert "summaryCards.map" in page
    assert "rateLimitView.topKeyRows.map" in page

    for forbidden in [
        "const TIME_RANGES = [",
        "resolveLoadState",
        "data?.timeseries",
        "data?.reason_codes",
        "data?.latency",
        "data?.summary",
        "summary.total_requests",
        "summary.total_rejections",
        "summary.rejection_rate",
        ".toFixed(1)",
        "rlData.is_degraded",
        "rlData.top_keys",
        "k.window_ms",
        "ts.",
        "rcCodes",
        "latSvcs",
    ]:
        assert forbidden not in page


def test_metrics_diagnostics_view_model_lives_in_observability_feature() -> None:
    component = (OBSERVABILITY_COMPONENTS / "metrics" / "MetricsDiagnosticsCards.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "metricsDiagnosticsModel.js").read_text(encoding="utf-8")

    for export_name in [
        "METRIC_DIAGNOSTIC_SAMPLE_KEYS",
        "getMetricDiagnosticSampleKeys",
        "hasMetricSamples",
        "resolveMetricQueryLoadState",
        "buildMetricDiagnosticStats",
        "buildMetricDiagnosticRateBar",
        "buildSemanticRecallStatusBadge",
        "buildCanonProposalPendingBadge",
        "buildSemanticRecallBackendLine",
        "shouldShowSemanticRecallMockWarning",
    ]:
        assert export_name in model

    assert "metricsDiagnosticsModel.js" in component
    assert "buildMetricDiagnosticStats('memoryGuard', data)" in component
    assert "buildMetricDiagnosticRateBar('chain', data)" in component
    assert "getMetricDiagnosticSampleKeys(metricKind)" in component
    assert "function hasMetricSamples(" not in component
    assert "resolveLoadState" not in component
    assert "sampleKeys={[" not in component
    assert "Math.min(" not in component
    assert "data?.block_rate != null" not in component
    assert "data.injection_detection_rate > 5" not in component
    assert "data.governor_block_rate > 10" not in component
    assert "const isOk = data?.status === 'OK'" not in component
    assert "ChromaDB: {data.chroma_host}" not in component
    assert "{data.pending} 待审核" not in component


def test_insights_dashboard_view_model_lives_in_observability_feature() -> None:
    page = (MONITOR_SRC / "pages" / "Insights.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "insightsDashboardModel.js").read_text(encoding="utf-8")

    for export_name in [
        "INSIGHTS_WINDOW_OPTIONS",
        "INSIGHT_GROUP_DEFS",
    ]:
        assert f"export const {export_name}" in model

    for export_name in [
        "getInsightsCurrentGroup",
        "getInsightsFocusTarget",
        "isInsightsRootGroup",
        "getInsightsGroupMeta",
        "buildInsightPulseNodes",
        "getInsightsUpdatedAt",
        "countInsightsSources",
        "buildInsightsLoadState",
        "buildInsightsHeaderStats",
        "buildInsightsHeroView",
        "buildInsightsSampleMeta",
        "buildInsightsDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/observability/insightsDashboardModel.js" in page
    assert "INSIGHTS_WINDOW_OPTIONS" in page
    assert "buildInsightsDashboardViewModel({" in page
    assert "headerStats.map" in page
    assert "getInsightIcon(heroView.iconKey)" in page
    assert "sampleMeta.loadState" in page
    assert "nodes={pulseNodes}" in page
    assert "insightGroups.map" in page

    for forbidden in [
        "const WINDOW_OPTIONS",
        "const INSIGHT_GROUPS",
        "resolveLoadState",
        "new URLSearchParams",
        "location.pathname.split",
        "INSIGHT_GROUPS.find",
        "Math.max(lastUpdatedAt",
        "].filter(Boolean).length",
        "errData?.total_errors ?? 0",
        "subsysData?.subsystems?.length ?? 0",
        "agentData?.total_loops ?? 0",
        "memoryData?.subsystems?.length",
        "vtsData?.total_events ?? 0",
        "llmCallData?.total_calls ?? 0",
        "hasData: insightsSourceCount > 0",
        "sampleLabel=\"摘要分区\"",
        "updatedAt={insightsUpdatedAt}",
        "source=\"insights summary + runtime\"",
        "definition=\"Insights 的页首摘要",
        "`时间窗口：${windowMinutes} 分钟`",
    ]:
        assert forbidden not in page


def test_insights_global_view_model_lives_in_observability_feature() -> None:
    component = (OBSERVABILITY_COMPONENTS / "insights" / "InsightsGlobalGroup.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "insightsGlobalModel.js").read_text(encoding="utf-8")

    for export_name in [
        "INSIGHTS_HEATMAP_HOURS",
        "buildFailureStatCards",
        "buildFailureEventRows",
        "buildChainStatCards",
        "buildChainNodeLatencyRows",
        "buildCapabilityGapStatCards",
        "buildForgeQualityStatCards",
        "buildCapabilityGapHotspotRows",
        "getWorstForgeRuntimeQualityItems",
        "buildForgeRuntimeQualityRows",
        "buildErrorHeatmapView",
        "buildGovernorDecisionStatCards",
        "buildGlobalErrorStatCards",
    ]:
        assert export_name in model

    assert "insightsGlobalModel.js" in component
    assert "buildChainNodeLatencyRows(data?.node_stats)" in component
    assert "buildErrorHeatmapView(data)" in component
    assert "buildForgeRuntimeQualityRows(qualityItems)" in component
    assert "buildGovernorDecisionStatCards(govData)" in component
    assert "resolveInsightsChainNodeColor" not in component
    assert "Math.max(" not in component
    assert "Math.min(" not in component
    assert ".sort(" not in component
    assert "cellMap" not in component
    assert "Array.from({ length: 24" not in component
    assert "node.total_calls > 0" not in component
    assert "node.avg_latency_ms" not in component
    assert "health_score" not in component
    assert "window_failure_rate" not in component
    assert "governor_block_rate > 10" not in component
    assert "rejection_rate > 20" not in component
    assert "abort_rate > 30" not in component
    assert "failure_rate || 0" not in component


def test_insights_safety_view_model_lives_in_observability_feature() -> None:
    component = (OBSERVABILITY_COMPONENTS / "insights" / "InsightsSafetyGroup.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "observability" / "insightsSafetyModel.js").read_text(encoding="utf-8")

    for export_name in [
        "buildTribeFeedParams",
        "buildTribeEventTypeSummary",
        "buildTribeEventRows",
        "buildTribeStatCards",
        "buildPermissionStatCards",
        "buildPermissionDeniedRows",
        "buildSchedulerGateStatCards",
        "buildAutomationGatePath",
        "buildMemoryGuardStatCards",
        "buildMemoryGuardSecondaryStatCards",
        "buildMemoryRejectReasonRows",
        "buildUiAssertionStatCards",
        "buildUiAssertionReasonRows",
        "buildQaViolationStatCards",
    ]:
        assert f"export function {export_name}(" in model

    assert "insightsSafetyModel.js" in component
    assert "buildTribeFeedParams({ eventTypeFilter, sourceFilter, targetFilter })" in component
    assert "buildTribeEventTypeSummary(items)" in component
    assert "buildAutomationGatePath(params)" in component
    assert "buildQaViolationStatCards(qaData)" in component

    assert "new Map(" not in component
    assert "Object.entries(params)" not in component
    assert "JSON.stringify(payload)" not in component
    assert "data?.gate_blocked_rate != null" not in component
    assert "data?.block_rate != null" not in component
    assert "qaData?.degrade_rate != null" not in component
    assert "qaData?.rewrite_success_rate != null" not in component
    assert "data.precondition_failed > 0" not in component
    assert "r.count / data.precondition_failed" not in component
    assert ".sort((left, right) => right[1] - left[1])" not in component


def test_digital_life_runtime_status_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    runtime_card = (MONITOR_SRC / "features" / "life" / "components" / "DigitalLifeRuntimeCard.jsx").read_text(encoding="utf-8")
    soak_card = (MONITOR_SRC / "features" / "life" / "components" / "DigitalLifeWallClockSoakCard.jsx").read_text(encoding="utf-8")
    organ_model = (MONITOR_SRC / "models" / "organSystemModel.js").read_text(encoding="utf-8")
    runtime_mode = (MONITOR_SRC / "pages" / "RuntimeMode.jsx").read_text(encoding="utf-8")
    runtime_mode_model = (MONITOR_SRC / "features" / "life" / "runtimeModeModel.js").read_text(encoding="utf-8")
    bond_page = (MONITOR_SRC / "pages" / "Bond.jsx").read_text(encoding="utf-8")
    bond_model = (MONITOR_SRC / "features" / "life" / "bondModel.js").read_text(encoding="utf-8")
    pathology_detail = (MONITOR_SRC / "pages" / "PathologyDetailPage.jsx").read_text(encoding="utf-8")
    digital_life_section_shared = (MONITOR_SRC / "features" / "life" / "components" / "digitalLifeSectionShared.jsx").read_text(encoding="utf-8")
    main_chain_panel = (MONITOR_SRC / "features" / "life" / "components" / "MainChainReshapePanel.jsx").read_text(encoding="utf-8")
    life_body_dir = MONITOR_SRC / "features" / "life" / "components" / "lifeBody"
    life_body_files = {
        "LifeBodyTopology.jsx": (life_body_dir / "LifeBodyTopology.jsx").read_text(encoding="utf-8"),
        "LifeBodyVitalsStrip.jsx": (life_body_dir / "LifeBodyVitalsStrip.jsx").read_text(encoding="utf-8"),
        "LifeBodyFocusPanel.jsx": (life_body_dir / "LifeBodyFocusPanel.jsx").read_text(encoding="utf-8"),
        "LifeBodyPathologyPanel.jsx": (life_body_dir / "LifeBodyPathologyPanel.jsx").read_text(encoding="utf-8"),
        "LifeBodyTimeline.jsx": (life_body_dir / "LifeBodyTimeline.jsx").read_text(encoding="utf-8"),
    }

    for export_name in [
        "DIGITAL_LIFE_ALERT_SEVERITY_CLASSES",
        "DIGITAL_LIFE_SOAK_VERDICT_LABELS",
        "DIGITAL_LIFE_SOAK_ACCEPTANCE_VERDICT_LABELS",
        "DIGITAL_LIFE_SOAK_LAYER_CLASSES",
        "LIFE_BODY_STATUS_LABELS",
        "LIFE_BODY_STATUS_CLASSES",
        "LIFE_BODY_TONE_LABELS",
        "LIFE_BODY_TONE_CLASSES",
        "LIFE_BODY_SEVERITY_CLASSES",
        "LIFE_BODY_PATHOLOGY_SEVERITY_LABELS",
        "LIFE_BODY_PATHOLOGY_SEVERITY_TONES",
        "LIFE_BODY_TIMELINE_SEVERITY_LABELS",
        "DIGITAL_LIFE_MODE_BUTTON_CLASSES",
        "BOND_STAGE_COLORS",
        "DIGITAL_LIFE_DOMAIN_STATUS_META",
        "DIGITAL_LIFE_MAIN_CHAIN_STAGE_CLASSES",
        "DIGITAL_LIFE_MAIN_CHAIN_DOT_CLASSES",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in runtime_card
    assert "shared/statusMeta.js" in soak_card
    assert "shared/statusMeta.js" in runtime_mode_model
    assert "shared/statusMeta.js" not in runtime_mode
    assert "const SEVERITY_STYLES" not in runtime_card
    assert "const VERDICT_LABELS" not in soak_card
    assert "const ACCEPTANCE_VERDICT_LABELS" not in soak_card
    assert "const LAYER_CLASS" not in soak_card
    assert "const MODE_COLOR_CLASS" not in runtime_mode
    assert "ring-2 ring-green-500 bg-green-50 border-green-300" not in runtime_mode
    assert "shared/statusMeta.js" in organ_model
    assert "ORGAN_STATUS_META" not in organ_model
    assert "className: 'border-" not in organ_model
    assert "shared/statusMeta.js" in bond_model
    assert "shared/statusMeta.js" not in bond_page
    assert "const STAGE_COLORS" not in bond_page
    assert "STRANGER: '#94a3b8'" not in bond_page
    assert "shared/statusMeta.js" in pathology_detail
    assert "const SEVERITY_META" not in pathology_detail
    assert "label: '高风险'" not in pathology_detail
    assert "tone: 'text-red-600'" not in pathology_detail
    assert "shared/statusMeta.js" in digital_life_section_shared
    assert "const DOMAIN_STYLES" not in digital_life_section_shared
    assert "status === 'wired'" not in digital_life_section_shared
    assert "待连通" not in digital_life_section_shared
    assert "shared/statusMeta.js" in main_chain_panel
    assert "const STAGE_CLASS" not in main_chain_panel
    assert "const DOT_CLASS" not in main_chain_panel
    assert "border-emerald-200 bg-emerald-50/70" not in main_chain_panel
    assert "bg-emerald-500" not in main_chain_panel

    for rel, source in life_body_files.items():
        assert "shared/statusMeta.js" in source, rel
        assert "const STATUS_CLASS" not in source, rel
        assert "const TONE_CLASS" not in source, rel
        assert "const SEVERITY_CLASS" not in source, rel
        assert "? '重压'" not in source, rel
        assert "? '高风险'" not in source, rel


def test_runtime_mode_view_model_lives_in_life_feature() -> None:
    runtime_mode = (MONITOR_SRC / "pages" / "RuntimeMode.jsx").read_text(encoding="utf-8")
    runtime_model = (MONITOR_SRC / "features" / "life" / "runtimeModeModel.js").read_text(encoding="utf-8")

    for export_name in [
        "RUNTIME_MODE_ORDER",
        "RUNTIME_MODE_AUTO_RULE_DEFS",
        "RUNTIME_MODE_STATE_AXIS_ITEMS",
        "formatRuntimeModeSourceText",
        "getRuntimeModeMeta",
        "getRuntimeModeSelection",
        "getRuntimeModeEnabledRuleCount",
        "buildRuntimeModeLoadState",
        "buildRuntimeModeHeroMetaItems",
        "buildRuntimeModeMetricCards",
        "buildRuntimeModeButtonViews",
        "buildRuntimeModeAutoRuleViews",
        "buildRuntimeModeSwitchConfirmMessage",
        "buildRuntimeModeSwitchPayload",
        "buildRuntimeModeAutoRulePatch",
        "buildRuntimeModeViewModel",
    ]:
        assert export_name in runtime_model

    assert "features/life/runtimeModeModel.js" in runtime_mode
    assert "buildRuntimeModeViewModel({" in runtime_mode
    assert "buildRuntimeModeSwitchConfirmMessage(targetMode)" in runtime_mode
    assert "switchSysMode(buildRuntimeModeSwitchPayload(targetMode))" in runtime_mode
    assert "updateAutoRules(buildRuntimeModeAutoRulePatch(ruleKey, currentValue))" in runtime_mode
    assert "formatRuntimeModeSourceText(item.source)" in runtime_mode
    assert "modeButtons.map((view)" in runtime_mode
    assert "autoRuleRows.map((rule)" in runtime_mode
    assert "stateAxisItems.map((item)" in runtime_mode
    assert "metricCards.map((card)" in runtime_mode

    for forbidden in [
        "const MODE_ORDER",
        "const AUTO_RULE_DEFS",
        "const STATE_AXIS_ITEMS",
        "function mapSourceText(",
        "Object.entries(AUTO_RULE_DEFS)",
        "Object.keys(AUTO_RULE_DEFS)",
        "resolveLoadState",
        "buildRuntimeModeOrganSystemModel",
        "DIGITAL_LIFE_MODE_BUTTON_CLASSES",
        "MODE_META",
        "currentMeta",
        "validTransitions",
        "enabledRules",
        "switchSysMode({",
        "updateAutoRules({ rules:",
        "status?.idle_minutes ?? 0",
    ]:
        assert forbidden not in runtime_mode


def test_bond_view_model_lives_in_life_feature() -> None:
    bond_page = (MONITOR_SRC / "pages" / "Bond.jsx").read_text(encoding="utf-8")
    bond_model = (MONITOR_SRC / "features" / "life" / "bondModel.js").read_text(encoding="utf-8")

    for export_name in [
        "formatBondHealthStatus",
        "buildBondStageDistribution",
        "buildBondHeroMetaItems",
        "buildBondLoadState",
        "getBondUpdatedAt",
        "buildBondLabels",
        "buildBondHeroView",
        "buildBondSampleMeta",
        "buildBondNoticeView",
        "buildBondOrganViewModel",
        "buildBondMetricCards",
        "buildBondDetailRows",
        "buildBondDetailView",
        "buildBondDashboardViewModel",
        "buildBondSubmitSuccessMessage",
    ]:
        assert f"export function {export_name}(" in bond_model

    assert "features/life/bondModel.js" in bond_page
    assert "buildBondDashboardViewModel({" in bond_page
    assert "buildBondStageDistribution(items)" in bond_page
    assert "buildBondSubmitSuccessMessage(submitResult.data)" in bond_page
    assert "sampleMeta.loadState" in bond_page
    assert "noticeView.noSample" in bond_page
    assert "noticeView.unavailable" in bond_page
    assert "metricCards.map" in bond_page
    assert "detailView.rows.map" in bond_page

    assert "function formatHealthStatus(" not in bond_page
    assert "LOAD_STATES" not in bond_page
    assert "resolveLoadState" not in bond_page
    assert "buildBondOrganSystemModel" not in bond_page
    assert "buildBondHeroMetaItems" not in bond_page
    assert "formatBondHealthStatus" not in bond_page
    assert "formatDateTime" not in bond_page
    assert "formatStage" not in bond_page
    assert "formatTrend" not in bond_page
    assert "BOND_STAGE_COLORS" not in bond_page
    assert "formatTransitionStatus" not in bond_page
    assert "Math.max(lastUpdatedAt" not in bond_page
    assert "health?.user_count ?? users.length" not in bond_page
    assert "userDetail?.total_score" not in bond_page
    assert "userDetail.current_stage" not in bond_page
    assert "detailUpdatedAt ? ` · 最近更新时间" not in bond_page
    assert "percent.toFixed(0)" not in bond_page
    assert "width: `${percent}%`" not in bond_page
    assert "score_delta > 0 ? '+'" not in bond_page


def test_pathology_center_model_lives_in_life_feature() -> None:
    pathology_page = (MONITOR_SRC / "pages" / "PathologyCenterPage.jsx").read_text(encoding="utf-8")
    pathology_detail_page = (MONITOR_SRC / "pages" / "PathologyDetailPage.jsx").read_text(encoding="utf-8")
    pathology_model = (MONITOR_SRC / "features" / "life" / "pathologyCenterModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getPathologyFilterSelection",
        "buildPathologyFilterParams",
        "buildPathologyOrganOptions",
        "buildPathologySeverityOptions",
        "filterPathologiesBySelection",
        "summarizePathologySeverity",
        "summarizePathologyOrgans",
        "getHottestPathologyOrgan",
        "buildPathologyCenterHeroTitle",
        "buildPathologyCenterHeroMetaItems",
        "buildPathologyCenterLoadState",
        "buildPathologyCenterSampleMeta",
        "buildPathologyCenterQueryView",
        "buildPathologyDetailLoadState",
        "buildPathologyDetailSampleMeta",
        "buildPathologyDetailQueryView",
    ]:
        assert f"export function {export_name}(" in pathology_model

    assert "features/life/pathologyCenterModel.js" in pathology_page
    assert "getPathologyFilterSelection(searchParams)" in pathology_page
    assert "buildPathologyCenterQueryView({" in pathology_page
    assert "buildPathologyOrganOptions({" in pathology_page
    assert "buildPathologySeverityOptions(allPathologies)" in pathology_page
    assert "filterPathologiesBySelection({" in pathology_page
    assert "buildPathologyFilterParams(searchParams, nextOrgan, nextSeverity)" in pathology_page
    assert "summarizePathologySeverity(filteredPathologies)" in pathology_page
    assert "summarizePathologyOrgans(filteredPathologies)" in pathology_page
    assert "getHottestPathologyOrgan(organSummary)" in pathology_page
    assert "buildPathologyCenterHeroTitle({" in pathology_page
    assert "buildPathologyCenterHeroMetaItems(severitySummary, hottestOrgan)" in pathology_page
    assert "sampleMeta.loadState" in pathology_page
    assert "buildPathologyCenterLoadState({" in pathology_model
    assert "resolveLoadState({" in pathology_model
    assert "buildPathologyCenterSampleMeta({" in pathology_model
    assert "source: '/obs/pathologies'" in pathology_model
    assert "features/life/pathologyCenterModel.js" in pathology_detail_page
    assert "buildPathologyDetailQueryView({" in pathology_detail_page
    assert "sampleMeta.loadState" in pathology_detail_page
    assert "buildPathologyDetailLoadState({" in pathology_model
    assert "buildPathologyDetailSampleMeta({" in pathology_model
    assert "sampleLabel: '病灶详情'" in pathology_model

    assert "new URLSearchParams(searchParams)" not in pathology_page
    assert "const counts = new Map" not in pathology_page
    assert "allPathologies.forEach((item)" not in pathology_page
    assert "if (selectedOrgan !== 'ALL' && item.organId !== selectedOrgan)" not in pathology_page
    assert "if (selectedSeverity !== 'ALL' && item.severity !== selectedSeverity)" not in pathology_page
    assert "filteredPathologies.reduce((acc, item)" not in pathology_page
    assert "Object.entries(organSummary).sort" not in pathology_page
    assert "title={hasPathologyError ? '病灶视图暂不可用'" not in pathology_page
    assert "高风险：${severitySummary.critical" not in pathology_page
    assert "resolveLoadState" not in pathology_page
    assert "pathologyQuery.data?.items" not in pathology_page
    assert "pathologyQuery.data?.organSystems" not in pathology_page
    assert "pathologyQuery.data?.primaryPathology" not in pathology_page
    assert "pathologyQuery.data?.timestamp" not in pathology_page
    assert "hasData: hasPathologyPayload" not in pathology_page
    assert "hasSamples: allPathologies.length > 0" not in pathology_page
    assert "sampleLabel=\"病灶聚合\"" not in pathology_page
    assert "source=\"/obs/pathologies\"" not in pathology_page
    assert "definition=\"病灶中心页现在直接消费" not in pathology_page
    assert "resolveLoadState" not in pathology_detail_page
    assert "pathologyQuery.data?.items" not in pathology_detail_page
    assert "pathologyQuery.data?.focusedItem" not in pathology_detail_page
    assert "pathologyQuery.data?.focusedOrgan" not in pathology_detail_page
    assert "pathologyQuery.data?.relatedEvents" not in pathology_detail_page
    assert "pathologyQuery.data?.timestamp" not in pathology_detail_page
    assert "hasData: hasPathologyPayload" not in pathology_detail_page
    assert "hasSamples: Boolean(pathology)" not in pathology_detail_page
    assert "sampleLabel=\"病灶详情\"" not in pathology_detail_page
    assert "source=\"/obs/pathologies\"" not in pathology_detail_page
    assert "definition=\"病灶详情页现在直接消费" not in pathology_detail_page


def test_memory_browser_view_model_lives_in_life_feature() -> None:
    memory_page = (MONITOR_SRC / "pages" / "MemoryBrowser.jsx").read_text(encoding="utf-8")
    memory_model = (MONITOR_SRC / "features" / "life" / "memoryBrowserModel.js").read_text(encoding="utf-8")

    assert "export const MEMORY_BROWSER_WINDOW_OPTIONS" in memory_model
    for export_name in [
        "getMemorySubsystems",
        "buildMemoryDocumentView",
        "countMemorySubsystemOperations",
        "hasMemoryObservabilityData",
        "hasMemoryTrafficSamples",
        "buildMemoryHeroTitle",
        "buildMemoryHeroDescription",
        "buildMemoryHeroMetaItems",
        "buildMemorySubsystemMetricCards",
        "buildMemoryBrowserViewModel",
    ]:
        assert f"export function {export_name}(" in memory_model

    assert "features/life/memoryBrowserModel.js" in memory_page
    assert "MEMORY_BROWSER_WINDOW_OPTIONS" in memory_page
    assert "buildMemoryBrowserViewModel({" in memory_page
    assert "memoryLoadState" in memory_page
    assert "subsystemMetricCards.map((card)" in memory_page

    for forbidden in [
        "const WINDOW_OPTIONS = [",
        "function formatDocStatus(",
        "LOAD_STATES",
        "resolveLoadState",
        "buildMemoryOrganSystemModel",
        "getSuccessRateTone",
        "summarizeSuccessRateSamples",
        "memoryData?.subsystems || []",
        "residentDocCount = worldDocs.length + personaDocs.length",
        "subsystems.reduce((sum, item)",
        "memoryData || episodicData || distillData",
        "sampledSubsystemCount > 0",
        "configuredSubsystemCount > 0 ? `${configuredSubsystemCount} 个记忆子系统`",
        "近窗口平均成功率 ${avgSuccessRate}%",
        "`窗口：${windowMinutes} 分钟`",
        "subsystem.success_rate != null ? `${subsystem.success_rate}%`",
    ]:
        assert forbidden not in memory_page


def test_life_timeline_view_model_lives_in_life_feature() -> None:
    timeline_page = (MONITOR_SRC / "pages" / "LifeTimelinePage.jsx").read_text(encoding="utf-8")
    timeline_model = (MONITOR_SRC / "features" / "life" / "lifeTimelineModel.js").read_text(encoding="utf-8")

    for export_name in [
        "getLifeTimelineFilterSelection",
        "buildLifeTimelineQueryFilters",
        "buildLifeTimelineFilterParams",
        "buildLifeTimelineTypeOptions",
        "buildLifeTimelineOrganOptions",
        "countLifeTimelineEventTypes",
        "filterLifeTimelineEvents",
        "buildLifeTimelineLoadState",
        "buildLifeTimelineSampleMeta",
        "buildLifeTimelineQueryView",
        "buildLifeTimelineHeroTitle",
        "buildLifeTimelineHeroMetaItems",
        "buildLifeTimelineFocusNotice",
        "buildLifeTimelineLatestEventHint",
        "getLifeTimelineSelectedOrganCount",
    ]:
        assert f"export function {export_name}" in timeline_model

    assert "export const LIFE_TIMELINE_ALL_FILTER" in timeline_model
    assert "export const LIFE_TIMELINE_WINDOW_OPTIONS" in timeline_model
    assert "features/life/lifeTimelineModel.js" in timeline_page
    assert "getLifeTimelineFilterSelection(searchParams)" in timeline_page
    assert "buildLifeTimelineQueryFilters(initialSelection)" in timeline_page
    assert "buildLifeTimelineQueryView({" in timeline_page
    assert "buildLifeTimelineTypeOptions(timelineItems)" in timeline_page
    assert "buildLifeTimelineOrganOptions(timelineItems)" in timeline_page
    assert "filterLifeTimelineEvents({ items: timelineItems, selectedType, selectedOrgan })" in timeline_page
    assert "buildLifeTimelineFilterParams(searchParams, nextType, nextOrgan)" in timeline_page
    assert "buildLifeTimelineHeroTitle({" in timeline_page
    assert "buildLifeTimelineHeroMetaItems({" in timeline_page
    assert "buildLifeTimelineFocusNotice('request', selectedRequestId)" in timeline_page
    assert "buildLifeTimelineLatestEventHint({" in timeline_page
    assert "getLifeTimelineSelectedOrganCount(selectedOrgan, organOptions)" in timeline_page
    assert "sampleMeta.loadState" in timeline_page
    assert "buildLifeTimelineLoadState({" in timeline_model
    assert "resolveLoadState({" in timeline_model
    assert "buildLifeTimelineSampleMeta({" in timeline_model
    assert "source: '/obs/life-timeline'" in timeline_model

    for forbidden in [
        "new Set(timelineItems.map",
        "const counts = new Map",
        "timelineItems.forEach((item)",
        "timelineItems.filter((item)",
        "new URLSearchParams(searchParams)",
        "params.set('type'",
        "params.delete('organ'",
        "最近 ${timelineItems.length} 个生命事件",
        "selectedRequestId.slice(0, 18)",
        "selectedRequestId.slice(0, 12)",
        "resolveLoadState",
        "timelineQuery.data?.items",
        "timelineQuery.data?.primaryPathology",
        "timelineQuery.data?.primaryOrgan",
        "timelineQuery.data?.timestamp",
        "hasData: hasTimelinePayload",
        "hasSamples: timelineItems.length > 0",
        "sampleLabel=\"聚合投影\"",
        "source=\"/obs/life-timeline\"",
        "definition=\"生命时间轴页现在直接消费",
    ]:
        assert forbidden not in timeline_page


def test_emotion_dashboard_display_metadata_lives_in_shared_module() -> None:
    shared = SHARED_STATUS_META.read_text(encoding="utf-8")
    emotion_dashboard = (MONITOR_SRC / "pages" / "EmotionDashboard.jsx").read_text(encoding="utf-8")
    emotion_model = (MONITOR_SRC / "features" / "life" / "emotionDashboardModel.js").read_text(encoding="utf-8")

    for export_name in [
        "EMOTION_MOOD_STYLES",
        "EMOTION_TAG_COLORS",
        "ENDOCRINE_GATE_LABELS",
    ]:
        assert export_name in shared

    assert "shared/statusMeta.js" in emotion_model
    assert "shared/statusMeta.js" not in emotion_dashboard
    assert "const MOOD_STYLES" not in emotion_dashboard
    assert "const EMOTION_COLORS" not in emotion_dashboard
    assert "const GATE_LABELS" not in emotion_dashboard
    assert "label: '稳定'" not in emotion_dashboard
    assert "HAPPY: '#22c55e'" not in emotion_dashboard
    assert "protective: '保护'" not in emotion_dashboard


def test_emotion_dashboard_view_model_lives_in_life_feature() -> None:
    page = (MONITOR_SRC / "pages" / "EmotionDashboard.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "life" / "emotionDashboardModel.js").read_text(encoding="utf-8")

    assert "export const EMOTION_WINDOW_OPTIONS" in model
    assert "export const EMOTION_GAUGE_COLOR_MAP" in model
    for export_name in [
        "clampEmotionUnitValue",
        "buildEmotionHormoneBarView",
        "buildEmotionGaugeView",
        "getEmotionRuntimeContext",
        "getEmotionEndocrineState",
        "getEmotionTopEmotions",
        "getEmotionBondMetrics",
        "buildEmotionState",
        "buildEmotionGateView",
        "getEmotionSampleState",
        "buildEmotionLoadState",
        "getEmotionUpdatedAt",
        "buildEmotionHeroView",
        "buildEmotionNoticeView",
        "buildEmotionMetricCards",
        "buildEmotionTopEmotionRows",
        "buildEmotionVtsStatCards",
        "buildEmotionMemorySubsystemRows",
        "buildEmotionLimbicOrganModel",
        "buildEmotionDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/life/emotionDashboardModel.js" in page
    assert "buildEmotionDashboardViewModel({" in page
    assert "EMOTION_WINDOW_OPTIONS" in page
    assert "metricCards.map" in page
    assert "gauges.map" in page
    assert "hormoneBars.map" in page
    assert "topEmotionRows.map" in page
    assert "vtsStatCards.map" in page
    assert "memorySubsystemRows.map" in page

    for forbidden in [
        "resolveLoadState",
        "buildLimbicOrganSystemModel",
        "LOAD_STATES",
        "runtimeSnapshot?.emotion_context",
        "runtimeSnapshot?.self_model_snapshot",
        "runtimeSnapshot?.bond_state",
        "vtsData?.top_emotions",
        "emotion_intensity",
        "bondState.event_count",
        "Math.max(0, Math.min(1",
        ".toFixed(2)",
        ".toFixed(0)",
        "formatEmotionTag(",
        "formatNumber(",
        "formatStage(",
        "getSuccessRateTone(",
        "EMOTION_TAG_COLORS",
        "ENDOCRINE_GATE_LABELS",
        "vtsData?.degrade_rate",
        "memoryData?.subsystems",
        "emotionLoadState",
        "emotionUpdatedAt",
        "emotionSampleCount",
        "topEmotions.length",
        "const bondTemperature",
        "bondTemperature =",
    ]:
        assert forbidden not in page


def test_self_model_dashboard_view_model_lives_in_life_feature() -> None:
    page = (MONITOR_SRC / "pages" / "SelfModelDashboard.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "life" / "selfModelDashboardModel.js").read_text(encoding="utf-8")

    assert "export const SELF_MODEL_RUNTIME_WINDOW_MINUTES" in model
    for export_name in [
        "getSelfModelRuntimePieces",
        "buildSelfModelCollections",
        "buildSelfModelLabels",
        "formatSelfModelTags",
        "buildSelfModelGrowthHeadline",
        "buildSelfModelLoadState",
        "getSelfModelUpdatedAt",
        "getSelfModelRiskTone",
        "buildSelfModelHeroView",
        "buildSelfModelMetricCards",
        "buildSelfModelSampleMeta",
        "buildSelfModelOrganModel",
        "buildSelfModelGrowthView",
        "buildSelfModelInfoRows",
        "buildSelfModelListBlocks",
        "buildSelfModelDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/life/selfModelDashboardModel.js" in page
    assert "SELF_MODEL_RUNTIME_WINDOW_MINUTES" in page
    assert "buildSelfModelDashboardViewModel({ runtimeSnapshot, loading, error })" in page
    assert "metricCards.map" in page
    assert "growthView.awarenessItems.map" in page
    assert "infoRows.selfRows.map" in page
    assert "infoRows.reflectionRows.map" in page
    assert "infoRows.guardrailRows.map" in page
    assert "infoRows.relationshipRows.map" in page
    assert "listBlocks.map" in page

    for forbidden in [
        "resolveLoadState",
        "buildSelfOrganSystemModel",
        "getGrowthSignals",
        "asArray",
        "compactText(",
        "formatAllowedStyle",
        "formatDigitalLifeTag",
        "formatEmotionTag",
        "formatNumber",
        "formatRiskLevel",
        "formatStage",
        "formatTrend",
        "runtimeSnapshot?.self_model_snapshot",
        "runtimeSnapshot?.self_reflection_pulse",
        "runtimeSnapshot?.bond_summary_human",
        "selfModel.",
        "selfReflection.",
        "guardrails.",
        "bondSummary.",
        "growthSignals",
        "milestones",
        "personalityChanges",
        "riskTags",
        "promptDirectives",
        "recentMoodTrendLabel",
        "riskLevelLabel",
        "riskLevelRaw",
        "allowedStyleLabel",
        "triggerReasonLabel",
        "bondTrendLabel",
        "selfModelLoadState",
        "selfUpdatedAt",
        "selfOrganModel",
    ]:
        assert forbidden not in page


def test_render_policy_dashboard_view_model_lives_in_life_feature() -> None:
    page = (MONITOR_SRC / "pages" / "RenderPolicyDashboard.jsx").read_text(encoding="utf-8")
    model = (MONITOR_SRC / "features" / "life" / "renderPolicyDashboardModel.js").read_text(encoding="utf-8")

    assert "export const RESPONSE_POLICY_RUNTIME_WINDOW_MINUTES" in model
    for export_name in [
        "getRenderPolicyRuntimePieces",
        "buildRenderPolicyCollections",
        "formatRenderPolicyTags",
        "buildRenderPolicyLabels",
        "getRenderPolicyActiveDrives",
        "buildRenderPolicyHeroView",
        "buildRenderPolicyMetricCards",
        "formatSentenceBudget",
        "buildRenderPolicyInfoRows",
        "buildRenderPolicyTagBlocks",
        "buildRenderPolicyDashboardViewModel",
    ]:
        assert f"export function {export_name}(" in model

    assert "features/life/renderPolicyDashboardModel.js" in page
    assert "RESPONSE_POLICY_RUNTIME_WINDOW_MINUTES" in page
    assert "buildRenderPolicyDashboardViewModel({ runtimeSnapshot })" in page
    assert "metricCards.map" in page
    assert "infoRows.silenceRows" in page
    assert "infoRows.bodySenseRows" in page
    assert "infoRows.renderPolicyRows" in page
    assert "infoRows.motiveRows" in page
    assert "tagBlocks.map" in page

    for forbidden in [
        "../utils/digitalLife",
        "asArray",
        "compactText(",
        "formatBoolean",
        "formatDigitalLifeTag",
        "formatDominantMotive",
        "formatDrowsinessLevel",
        "formatFatigueLevel",
        "formatNumber",
        "formatReplyPosture",
        "formatWillingnessLevel",
        "runtimeSnapshot?.silence_state",
        "runtimeSnapshot?.body_sense",
        "runtimeSnapshot?.motive_state",
        "runtimeSnapshot?.render_policy",
        "runtimeSnapshot?.runtime_summary",
        "silenceState.",
        "bodySense.",
        "motiveState.",
        "renderPolicy.",
        "runtimeSummary.",
        "whyReply",
        "decisionChain =",
        "policyReasonTags",
        "motiveReasonTags",
        "activeDrives",
        "formattedWhyReply",
        "formattedPolicyReasonTags",
        "formattedMotiveReasonTags",
    ]:
        assert forbidden not in page


def test_http_client_preserves_structured_error_details() -> None:
    source = (MONITOR_SRC / "shared" / "api" / "httpClient.js").read_text(encoding="utf-8")

    assert "function formatErrorDetail(" in source
    assert "JSON.stringify(detail)" in source
    assert "String(detail || `HTTP ${response.status}`)" not in source
