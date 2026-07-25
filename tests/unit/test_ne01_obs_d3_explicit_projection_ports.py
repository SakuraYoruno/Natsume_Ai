from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
import threading
import time

from natsume_contracts.operator.v1 import ProjectionDegradedState
import pytest

from host_brain.app.observability_runtime.host_projection_sources import (
    AuthenticatedHostProjectionSources,
)
from host_brain.app.observability_runtime.operator_live_sources import (
    OperatorSourceUnavailable,
)
from host_brain.composition import process_services
from host_brain.app.obs_application import (
    OBS_PROJECTION_JOBS_MIGRATED_TO_EXPLICIT_PORTS,
    ObsApplicationContainer,
    build_default_obs_application_ports,
)
from host_brain.app.obs_projection_jobs import (
    AUTHENTICATED_HOST_READ,
    DURABLE_READ,
    DURABLE_READ_PARTIAL,
    STATIC_READ,
    TYPED_UNAVAILABLE,
)
from host_brain.app.observability_runtime.projection_runtime import (
    TASK_EXECUTION_INDEX_KEY,
    TASK_EXECUTION_KIND,
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_KIND,
    TRIBE_EVENTS_INDEX_KEY,
    TRIBE_EVENTS_KIND,
    projection_meta,
)


ROOT = Path(__file__).resolve().parents[2]


def _forbidden_locator(*_args, **_kwargs):  # noqa: ANN002, ANN003
    raise AssertionError("app-local projection callback touched process locator")


def test_default_obs_projection_factory_has_registered_21_of_21_coverage(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-coverage.sqlite3",
    )
    try:
        names = tuple(job.name for job in ports.projection_jobs)
        assert len(names) == 21
        assert len(set(names)) == 21
        assert names[:2] == ("ledger-stats", "life-evolution")
        assert names[2:] == OBS_PROJECTION_JOBS_MIGRATED_TO_EXPLICIT_PORTS
        assert ports.projection_coverage == "REGISTERED_21_OF_21"
        assert ports.missing_projection_jobs == ()
        assert all(job.source_mode != "CALLER_DEFINED" for job in ports.projection_jobs)
        assert sum(
            job.source_mode == TYPED_UNAVAILABLE
            for job in ports.projection_jobs
        ) == 0
        assert sum(
            job.source_mode == AUTHENTICATED_HOST_READ
            for job in ports.projection_jobs
        ) == 4
        assert sum(job.source_mode == DURABLE_READ for job in ports.projection_jobs) == 7
        assert sum(job.source_mode == STATIC_READ for job in ports.projection_jobs) == 2
        assert sum(
            job.source_mode == DURABLE_READ_PARTIAL
            for job in ports.projection_jobs
        ) == 8
        assert sum(
            job.source_mode != TYPED_UNAVAILABLE
            for job in ports.projection_jobs
        ) == 21
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_d4_route_scope_and_producer_metadata_remain_truthful(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-boundary.sqlite3",
    )
    try:
        # Producer counts remain declared source metadata, while D4 separately
        # proves that the identified HTTP/WebSocket projection routes no
        # longer rediscover their store through read_process_projection.
        assert ports.projection_coverage == "REGISTERED_21_OF_21"
        assert len(ports.projection_jobs) == 21
        assert sum(
            job.source_mode != TYPED_UNAVAILABLE
            for job in ports.projection_jobs
        ) == 21
        assert sum(
            job.source_mode == TYPED_UNAVAILABLE
            for job in ports.projection_jobs
        ) == 0
        assert sum(
            job.source_mode == AUTHENTICATED_HOST_READ
            for job in ports.projection_jobs
        ) == 4

        route_sources = [
            path.read_text(encoding="utf-8-sig")
            for path in (ROOT / "host_brain" / "app").glob("obs_routes*.py")
        ]
        assert sum(source.count("process_services") for source in route_sources) == 0
        assert sum(
            source.count("get_projection_ledger_view") for source in route_sources
        ) == 0
        assert sum(
            source.count("read_capability_forge_projection(")
            for source in route_sources
        ) == 0

        process_projection_source = (
            ROOT
            / "host_brain"
            / "app"
            / "observability_runtime"
            / "process_state_projection.py"
        ).read_text(encoding="utf-8")
        summary_source = (
            ROOT / "host_brain" / "app" / "obs_routes_summary.py"
        ).read_text(encoding="utf-8")
        assert "def read_process_projection(" in process_projection_source
        assert "peek_query_projection_store" not in process_projection_source
        assert "read_process_projection(" in summary_source
        assert "require_obs_query_projection_store(request)" in summary_source

        machine = json.loads(
            (ROOT / "configs" / "release" / "current_architecture_status.json")
            .read_text(encoding="utf-8")
        )["capabilities"]["explicit_composition_root"]
        assert machine["obs_process_local_module_cache_coupled_jobs"] == []
        assert machine["obs_app_local_query_projection_endpoint_count"] == 36
        assert machine["obs_app_local_query_projection_http_endpoint_count"] == 35
        assert machine["obs_app_local_query_projection_websocket_endpoint_count"] == 1
        assert machine["obs_d4_migrated_read_process_projection_http_get_count"] == 19
        assert machine["obs_read_process_projection_indirect_http_get_count"] == 0
        assert (
            machine["obs_indirect_process_projection_locator"]
            == "REMOVED_EXPLICIT_STORE_PARAMETER"
        )
        assert machine["obs_route_global_query_projection_helper_occurrences"] == 0
        assert machine["obs_route_process_service_locator_occurrences"] == 0

        compatibility_modules = {
            "capability_stats_projection.py": (
                "read_capability_forge_projection",
                "refresh_capability_forge_projection",
            ),
            "domain_state_projection.py": (
                "read_domain_projection",
                "refresh_security_immune_projection",
                "refresh_skill_pack_catalog_projection",
            ),
            "projection_ledger_view.py": ("get_projection_ledger_view",),
            "remaining_query_projection.py": (
                "read",
                "read_all",
                "refresh_remaining_query_projections",
            ),
        }
        runtime_root = ROOT / "host_brain" / "app" / "observability_runtime"
        for module_name, removed_functions in compatibility_modules.items():
            source = (runtime_root / module_name).read_text(encoding="utf-8")
            assert "composition.process_services" not in source
            for function_name in removed_functions:
                assert f"def {function_name}(" not in source
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_all_app_local_projection_callbacks_run_without_process_locator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-no-locator.sqlite3",
    )
    for name in dir(process_services):
        if name.startswith(("get_", "peek_", "configure_")):
            monkeypatch.setattr(process_services, name, _forbidden_locator)

    container = ObsApplicationContainer.compose(ports)
    try:
        results = container.projection_daemon.run_once(
            store=ports.query_projection_store,
            owner_id="obs-d3-no-locator",
            lease_ms=120_000,
        )
        assert set(results) == {job.name for job in ports.projection_jobs}
        assert set(results.values()) == {"OK"}
        assert (
            ports.query_projection_store.get(
                TASK_EXECUTION_KIND,
                TASK_EXECUTION_INDEX_KEY,
            )
            is not None
        )
        assert (
            ports.query_projection_store.get(
                TRACE_FEED_KIND,
                TRACE_FEED_INDEX_KEY,
            )
            is not None
        )
        tribe = ports.query_projection_store.get(
            TRIBE_EVENTS_KIND,
            TRIBE_EVENTS_INDEX_KEY,
        )
        assert tribe is not None
        assert tribe.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert (
            tribe.payload["reason_code"]
            == "OBS_HOST_TRIBE_EVENT_PORT_NOT_INJECTED"
        )
    finally:
        container.close()


def test_two_obs_apps_producers_never_read_process_local_health_or_receipt_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from host_brain.app.observability_runtime import health_device_projection
    from host_brain.app.observability_runtime import service_health_heartbeat
    from host_brain.app.observability_runtime.projection_daemon import (
        ProjectionJobDaemon,
    )

    def forbidden_heartbeat_cache(_service_id: str):
        raise AssertionError("standalone producer touched process heartbeat cache")

    monkeypatch.setattr(
        service_health_heartbeat,
        "get_cached",
        forbidden_heartbeat_cache,
    )
    # Device projection 已切到 durable receipt ledger，进程内 receipt cache
    # 不再是可猴补的兼容入口；缺少该属性本身就是生产边界证据。
    assert not hasattr(health_device_projection, "_latest_receipts")

    for index in (1, 2):
        ports = build_default_obs_application_ports(
            canonical_db_path=tmp_path / f"obs-d3-cache-isolation-{index}.sqlite3",
        )
        daemon = ProjectionJobDaemon(owner_id=f"obs-d3-cache-isolation-{index}")
        selected = {
            job.name: job
            for job in ports.projection_jobs
            if job.name in {"health-passive", "device-status"}
        }
        assert set(selected) == {"health-passive", "device-status"}
        for job in selected.values():
            daemon.register(job.name, job.callback)
        try:
            assert daemon.run_once(store=ports.query_projection_store) == {
                "device-status": "OK",
                "health-passive": "OK",
            }
        finally:
            daemon.stop(timeout_s=1.0)
            assert ports.close_owned_resources is not None
            ports.close_owned_resources()


def test_host_owned_sources_without_token_are_runtime_unavailable_documents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("HOST_OBS_READ_TOKEN", raising=False)
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-unavailable.sqlite3",
    )
    container = ObsApplicationContainer.compose(ports)
    try:
        results = container.projection_daemon.run_once(
            store=ports.query_projection_store,
            owner_id="obs-d3-unavailable",
            lease_ms=120_000,
        )
        assert set(results.values()) == {"OK"}

        unavailable_jobs = {
            job.name: job
            for job in ports.projection_jobs
            if job.source_mode == TYPED_UNAVAILABLE
        }
        assert unavailable_jobs == {}
        assert {
            job.name
            for job in ports.projection_jobs
            if job.source_mode == DURABLE_READ_PARTIAL
        } >= {"life-body"}
        authenticated_jobs = {
            job.name: job
            for job in ports.projection_jobs
            if job.source_mode == AUTHENTICATED_HOST_READ
        }
        assert set(authenticated_jobs) == {
            "capability-forge-operator",
            "governance-snapshot",
            "organ-tribe",
            "semantic-recall-stats",
        }
        documents = []
        for kind, key, job_name in (
            ("capability_forge_operator", "current", "capability-forge-operator"),
            ("semantic_recall_stats", "runtime", "semantic-recall-stats"),
            ("governance_snapshot", "current", "governance-snapshot"),
            ("organ_tribe_status", "current", "organ-tribe"),
        ):
            source_document = ports.query_projection_store.get(kind, key)
            assert source_document is not None
            assert (
                source_document.degraded_state
                is ProjectionDegradedState.SOURCE_UNAVAILABLE
            )
            assert source_document.payload["available"] is False
            assert source_document.payload["reason_code"] == "OBS_HOST_READ_CONFIG"
            assert source_document.payload["source_scope"] == "host-runtime"
            assert source_document.payload["job_name"] == job_name
            documents.append(source_document)

        document = documents[0]
        unavailable_meta = projection_meta(
            "capability_forge_operator",
            "current",
            document,
            max_age_ms=60_000,
        )
        assert unavailable_meta["available"] is False
        assert unavailable_meta["degraded_state"] == "SOURCE_UNAVAILABLE"
        assert (
            unavailable_meta["reason_code"]
            == "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE"
        )

        life_body = ports.query_projection_store.get(
            "life_body_snapshot",
            "window:360",
        )
        assert life_body is not None
        assert life_body.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert life_body.payload["job_name"] == "life-body"
        assert life_body.payload["reason_code"] == "OBS_LIFE_BODY_ROOT_SOURCE_UNAVAILABLE"

        # Simulate a committed projection response whose caller never saw the
        # return value.  An exact retry observes the durable state and is a
        # no-op rather than advancing cursor/version every daemon poll.
        job = authenticated_jobs["capability-forge-operator"]
        version = document.projection_version
        cursor = document.source_cursor
        job.callback()
        replay = ports.query_projection_store.get(
            "capability_forge_operator",
            "current",
        )
        assert replay is not None
        assert replay.projection_version == version
        assert replay.source_cursor == cursor
    finally:
        container.close()


def test_semantic_and_governance_authenticated_jobs_materialize_independently(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now_ms = int(time.time() * 1000)
    monkeypatch.setenv("HOST_OBS_READ_TOKEN", "d5-independent-projections")
    monkeypatch.setattr(
        AuthenticatedHostProjectionSources,
        "semantic_recall",
        lambda _self: ({"status": "MOCK_READY", "timestamp": now_ms}, now_ms),
    )
    monkeypatch.setattr(
        AuthenticatedHostProjectionSources,
        "governance",
        lambda _self: (
            {
                "schema_version": "Natsume.governance.snapshot.v1",
                "read_only": True,
                "generated_at_ms": now_ms,
            },
            now_ms,
        ),
    )
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d5-independent-success.sqlite3",
    )
    try:
        jobs = {job.name: job for job in ports.projection_jobs}
        assert jobs["semantic-recall-stats"].callback() == {
            "document_count": 1,
            "unavailable_document_count": 0,
            "changed_document_count": 1,
        }
        assert jobs["governance-snapshot"].callback() == {
            "document_count": 1,
            "unavailable_document_count": 0,
            "changed_document_count": 1,
        }

        semantic = ports.query_projection_store.get(
            "semantic_recall_stats",
            "runtime",
        )
        governance = ports.query_projection_store.get(
            "governance_snapshot",
            "current",
        )
        assert semantic is not None and governance is not None
        assert semantic.degraded_state is ProjectionDegradedState.HEALTHY
        assert governance.degraded_state is ProjectionDegradedState.HEALTHY
        assert semantic.payload["status"] == "MOCK_READY"
        assert governance.payload["read_only"] is True
        assert semantic.projection_version == governance.projection_version == 1
        assert semantic.freshness_ts_ms == governance.freshness_ts_ms == now_ms
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


@pytest.mark.parametrize(
    ("failed_job", "failed_kind", "failed_key", "healthy_kind", "healthy_key"),
    (
        (
            "semantic-recall-stats",
            "semantic_recall_stats",
            "runtime",
            "governance_snapshot",
            "current",
        ),
        (
            "governance-snapshot",
            "governance_snapshot",
            "current",
            "semantic_recall_stats",
            "runtime",
        ),
    ),
)
def test_semantic_and_governance_authenticated_job_failures_are_independent(
    tmp_path: Path,
    monkeypatch,
    failed_job: str,
    failed_kind: str,
    failed_key: str,
    healthy_kind: str,
    healthy_key: str,
) -> None:
    now_ms = int(time.time() * 1000)
    monkeypatch.setenv("HOST_OBS_READ_TOKEN", "d5-independent-failure")

    def unavailable(_self):  # noqa: ANN001
        raise OperatorSourceUnavailable(
            "OBS_HOST_CHILD_SOURCE_TEST_UNAVAILABLE",
            "independent child source is unavailable",
            source_scope="host-runtime",
        )

    semantic_reader = (
        unavailable
        if failed_job == "semantic-recall-stats"
        else lambda _self: ({"status": "MOCK_READY", "timestamp": now_ms}, now_ms)
    )
    governance_reader = (
        unavailable
        if failed_job == "governance-snapshot"
        else lambda _self: (
            {
                "schema_version": "Natsume.governance.snapshot.v1",
                "read_only": True,
                "generated_at_ms": now_ms,
            },
            now_ms,
        )
    )
    monkeypatch.setattr(
        AuthenticatedHostProjectionSources,
        "semantic_recall",
        semantic_reader,
    )
    monkeypatch.setattr(
        AuthenticatedHostProjectionSources,
        "governance",
        governance_reader,
    )
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / f"obs-d5-{failed_job}.sqlite3",
    )
    try:
        jobs = {job.name: job for job in ports.projection_jobs}
        jobs["semantic-recall-stats"].callback()
        jobs["governance-snapshot"].callback()

        failed = ports.query_projection_store.get(failed_kind, failed_key)
        healthy = ports.query_projection_store.get(healthy_kind, healthy_key)
        assert failed is not None and healthy is not None
        assert failed.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert failed.payload["job_name"] == failed_job
        assert failed.payload["reason_code"] == (
            "OBS_HOST_CHILD_SOURCE_TEST_UNAVAILABLE"
        )
        assert healthy.degraded_state is ProjectionDegradedState.HEALTHY
        assert healthy.payload.get("available") is not False
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_d3_projection_adapter_source_has_no_host_locator_fallback() -> None:
    source = (
        ROOT / "host_brain" / "app" / "obs_projection_jobs.py"
    ).read_text(encoding="utf-8")
    assert "composition.process_services" not in source
    assert "PROCESS_LIFE_PROVIDER" not in source
    assert "ProcessApplicationPorts" not in source


def test_default_job_set_is_explicit_and_no_module_registers_global_jobs(
    tmp_path: Path,
) -> None:
    global_registrations: set[str] = set()
    for path in (ROOT / "host_brain" / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            first = node.args[0]
            if (
                function_name == "register_projection_job"
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                global_registrations.add(first.value)

    assert global_registrations == set()
    expected_jobs = {
        "ledger-stats",
        "life-evolution",
        *OBS_PROJECTION_JOBS_MIGRATED_TO_EXPLICIT_PORTS,
    }

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-static-job-set.sqlite3",
    )
    try:
        assert {job.name for job in ports.projection_jobs} == expected_jobs
        assert len(expected_jobs) == 21
    finally:
        ports.close_owned_resources()


def test_projection_daemon_enforces_app_local_job_cadence(tmp_path: Path) -> None:
    from host_brain.app.observability_runtime.projection_daemon import (
        ProjectionJobDaemon,
    )

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-cadence.sqlite3",
    )
    clock = [100.0]
    calls: list[float] = []
    daemon = ProjectionJobDaemon(owner_id="obs-d3-cadence", clock=lambda: clock[0])
    daemon.register(
        "heavy-read",
        lambda: calls.append(clock[0]),
        min_interval_ms=30_000,
    )
    try:
        assert daemon.run_once(store=ports.query_projection_store) == {
            "heavy-read": "OK"
        }
        clock[0] += 5.0
        assert daemon.run_once(store=ports.query_projection_store) == {
            "heavy-read": "SKIPPED_NOT_DUE"
        }
        assert calls == [100.0]
        clock[0] += 25.0
        assert daemon.run_once(store=ports.query_projection_store) == {
            "heavy-read": "OK"
        }
        assert calls == [100.0, 130.0]
    finally:
        ports.close_owned_resources()


def test_projection_daemon_shutdown_fences_future_callbacks(tmp_path: Path) -> None:
    from host_brain.app.observability_runtime.projection_daemon import (
        ProjectionJobDaemon,
    )

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-shutdown.sqlite3",
    )
    entered = threading.Event()
    daemon = ProjectionJobDaemon(owner_id="obs-d3-shutdown")
    daemon.register("probe", entered.set, min_interval_ms=1)
    try:
        daemon.start(store=ports.query_projection_store, interval_ms=20)
        assert entered.wait(timeout=1.0)
        assert daemon.stop(timeout_s=1.0)["running"] is False
        entered.clear()
        time.sleep(0.06)
        assert entered.is_set() is False
        assert daemon.running is False
    finally:
        daemon.stop(timeout_s=1.0)
        ports.close_owned_resources()


def test_summary_and_life_body_builders_receive_explicit_projection_port(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_life_body as life_body
    from host_brain.app import obs_routes_sessions as sessions
    from host_brain.app import obs_routes_summary as summary
    from host_brain.observability.api_models import SessionsResponse

    class ProjectionDocument:
        schema_version = "Natsume.operator.query_projection.v1"
        projection_version = 1
        projection_cursor = 1
        source_cursor = 1
        source_digest = "sha256:test"
        degraded_state = ProjectionDegradedState.HEALTHY
        degraded_detail = None

        def __init__(self, payload):  # noqa: ANN001
            self.payload = payload
            self.freshness_ts_ms = int(time.time() * 1000)

    class ProjectionPort:
        def get(self, kind, _key):  # noqa: ANN001
            if kind == "service_health":
                return ProjectionDocument({"services": [], "timestamp": 1})
            if kind == "capability_forge_operator":
                return ProjectionDocument({"gap": {}})
            return None

    class ProjectionView:
        last_degraded_reason = None

        @staticmethod
        def fetch_by_time_range(*_args, **_kwargs):  # noqa: ANN002, ANN003
            return []

    projection_store = ProjectionPort()
    observed: list[object] = []

    def services_response(store, **_kwargs):  # noqa: ANN001
        observed.append(store)
        return {}

    def sessions_response(_ledger, *, projection_store, **_kwargs):  # noqa: ANN001
        observed.append(projection_store)
        return SessionsResponse(
            total=0,
            page=1,
            page_size=20,
            items=[],
            timestamp=1,
        )

    monkeypatch.setattr(summary, "build_services_health_response", services_response)
    for name in (
        "get_vts_stats",
        "get_memory_stats",
        "get_subsystems_stats",
        "build_capability_gaps_stats_from_projection",
        "get_input_humanoid_loop_stats",
        "get_input_humanoid_loop_trend",
        "get_input_humanoid_loop_alerts",
        "get_input_humanoid_loop_remediation",
        "get_input_humanoid_loop_diagnostics",
        "get_input_humanoid_loop_hotspots",
        "get_input_humanoid_loop_hotspot_trend",
    ):
        monkeypatch.setattr(summary, name, lambda *_args, **_kwargs: {})

    async def correlation(**_kwargs):  # noqa: ANN003
        return {}

    monkeypatch.setattr(
        summary,
        "get_input_humanoid_loop_source_correlation",
        correlation,
    )
    monkeypatch.setattr(sessions, "_build_sessions_response", sessions_response)

    payload = asyncio.run(
        summary._build_digital_life_overview_snapshot_async(
            360,
            projection_view=ProjectionView(),
            projection_store=projection_store,
        )
    )
    recent = life_body._get_recent_sessions_projection(
        60,
        projection_store=projection_store,
    )

    assert payload["servicesData"] == {}
    assert recent["items"] == []
    assert observed == [projection_store, projection_store]


def test_sessions_source_builder_never_rediscovers_process_projection_store(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_sessions as sessions

    class DurableSourceLedger:
        canonical_db_path = "durable-source.sqlite3"

        @staticmethod
        def fetch_by_time_range(*_args, **_kwargs):  # noqa: ANN002, ANN003
            return []

    monkeypatch.setattr(
        process_services,
        "get_query_projection_store",
        _forbidden_locator,
        raising=False,
    )

    response = sessions._build_sessions_response(
        DurableSourceLedger(),
        page=1,
        page_size=20,
        status=None,
        window_minutes=60,
        keyword=None,
    )

    assert response.total == 0
    assert response.items == []


def test_projection_daemon_serializes_runs_and_stop_waits_for_inflight(
    tmp_path: Path,
) -> None:
    from host_brain.app.observability_runtime.projection_daemon import (
        ProjectionJobDaemon,
    )

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-run-single-flight.sqlite3",
    )
    daemon = ProjectionJobDaemon(owner_id="obs-d3-run-single-flight")
    entered = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    active = 0
    max_active = 0
    calls = 0
    results: list[dict[str, str]] = []

    def blocking_job() -> None:
        nonlocal active, calls, max_active
        with counter_lock:
            active += 1
            calls += 1
            max_active = max(max_active, active)
        entered.set()
        release.wait(timeout=2.0)
        with counter_lock:
            active -= 1

    daemon.register("probe", blocking_job)

    def run() -> None:
        results.append(daemon.run_once(store=ports.query_projection_store))

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    try:
        first.start()
        assert entered.wait(timeout=1.0)
        second.start()
        time.sleep(0.02)
        assert max_active == 1
        timed_out = daemon.stop(timeout_s=0.01)
        assert timed_out == {
            "running": True,
            "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
        }

        release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)
        assert first.is_alive() is False
        assert second.is_alive() is False
        assert calls == 1
        assert daemon.stop(timeout_s=1.0) == {"running": False}
        assert daemon.run_once(store=ports.query_projection_store) == {
            "probe": "SKIPPED_STOPPED"
        }
        assert {tuple(result.values()) for result in results} == {
            ("OK",),
            ("SKIPPED_STOPPING",),
        }
    finally:
        release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)
        daemon.stop(timeout_s=1.0)
        ports.close_owned_resources()


def test_projection_daemon_start_is_lifecycle_single_flight(tmp_path: Path) -> None:
    from host_brain.app.observability_runtime.projection_daemon import (
        ProjectionJobDaemon,
    )

    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-d3-start-single-flight.sqlite3",
    )
    daemon = ProjectionJobDaemon(owner_id="obs-d3-start-single-flight")
    gate = threading.Barrier(3)
    results: list[dict[str, object]] = []

    def start() -> None:
        gate.wait(timeout=1.0)
        results.append(
            daemon.start(
                store=ports.query_projection_store,
                interval_ms=1_000,
            )
        )

    threads = [threading.Thread(target=start) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        gate.wait(timeout=1.0)
        for thread in threads:
            thread.join(timeout=1.0)
        assert all(thread.is_alive() is False for thread in threads)
        assert sum(bool(result["started"]) for result in results) == 1
        assert all(result["running"] is True for result in results)
    finally:
        daemon.stop(timeout_s=1.0)
        ports.close_owned_resources()
