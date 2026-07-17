from __future__ import annotations

import asyncio
import time

import pytest

from natsume_contracts.operator.v1 import ProjectionDegradedState


class _ProjectionDocument:
    schema_version = "Natsume.operator.query_projection.v1"
    projection_version = 1
    projection_cursor = 1
    source_cursor = 1
    source_digest = "sha256:test"
    degraded_state = ProjectionDegradedState.HEALTHY
    degraded_detail = None

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.freshness_ts_ms = int(time.time() * 1000)


class _ProjectionStore:
    def __init__(self, *, omit: str | None = None) -> None:
        self.omit = omit

    def get(self, kind: str, _key: str):
        if kind == self.omit:
            return None
        payloads = {
            "agent_loop_summary": {"loops": [], "timestamp": 1},
            "service_health": {"services": [], "timestamp": 1},
            "capability_forge_operator": {"gap": {}, "timestamp": 1},
        }
        payload = payloads.get(kind)
        return _ProjectionDocument(payload) if payload is not None else None


class _MutableProjectionStore:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], _ProjectionDocument] = {}

    def get(self, kind: str, key: str):
        return self.documents.get((kind, key))

    def upsert(
        self,
        kind: str,
        key: str,
        payload: dict,
        *,
        source_cursor: int,
        degraded_state=ProjectionDegradedState.HEALTHY,  # noqa: ANN001
        degraded_detail=None,  # noqa: ANN001
        **_kwargs,
    ):
        document = _ProjectionDocument(dict(payload))
        document.source_cursor = int(source_cursor)
        document.projection_cursor = int(source_cursor)
        document.freshness_ts_ms = int(time.time() * 1000)
        document.degraded_state = degraded_state
        document.degraded_detail = degraded_detail
        self.documents[(kind, key)] = document
        return document


class _ProjectionView:
    def __init__(self, *, unavailable_table: str | None = None) -> None:
        self.unavailable_table = unavailable_table
        self.last_degraded_reason: str | None = None

    @staticmethod
    def resolve_fetch_table(*candidates: str) -> str | None:
        return candidates[0] if candidates else None

    def fetch_by_time_range(self, table: str, **_kwargs):  # noqa: ANN003
        self.last_degraded_reason = (
            "PROJECTION_REBUILDING" if table == self.unavailable_table else None
        )
        return []

    def fetch_by_request_id(self, _table: str, _request_id: str):
        return []


def _forbidden_locator(*_args, **_kwargs):  # noqa: ANN002, ANN003
    raise AssertionError("aggregate callback rediscovered a process-global service")


def test_summary_callbacks_use_only_explicit_app_local_sources(monkeypatch) -> None:
    from host_brain.app import (
        obs_routes_stats_agent,
        obs_routes_stats_capability_agent,
        obs_routes_stats_governor,
        obs_routes_stats_input_humanoid,
        obs_routes_stats_system,
        obs_routes_summary,
    )
    from host_brain.composition import process_services

    monkeypatch.setattr(
        process_services,
        "peek_query_projection_store",
        _forbidden_locator,
    )
    monkeypatch.setattr(
        process_services,
        "get_query_projection_store",
        _forbidden_locator,
    )
    for module in (
        obs_routes_stats_agent,
        obs_routes_stats_capability_agent,
        obs_routes_stats_governor,
        obs_routes_stats_input_humanoid,
        obs_routes_stats_system,
    ):
        if hasattr(module, "get_projection_ledger_view"):
            monkeypatch.setattr(module, "get_projection_ledger_view", _forbidden_locator)

    store = _ProjectionStore()
    view = _ProjectionView()

    insights = obs_routes_summary._build_insights_summary_snapshot_sync(
        60,
        projection_view=view,
        projection_store=store,
    )
    digital_life = asyncio.run(
        obs_routes_summary._build_digital_life_overview_snapshot_async(
            360,
            projection_view=view,
            projection_store=store,
        )
    )

    assert insights["window_minutes"] == 60
    assert insights["agentData"]["degraded_state"] == "HEALTHY"
    assert digital_life["window_minutes"] == 360
    assert digital_life["servicesData"]["degraded_state"] == "HEALTHY"
    assert digital_life["capabilityGapProjection"]["available"] is True


def test_summary_source_failure_is_typed_instead_of_zero_filled() -> None:
    from host_brain.app.obs_routes_summary import (
        AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE,
        AggregateProjectionSourceUnavailable,
        _build_insights_summary_snapshot_sync,
    )

    with pytest.raises(AggregateProjectionSourceUnavailable) as captured:
        _build_insights_summary_snapshot_sync(
            60,
            projection_view=_ProjectionView(unavailable_table="governor_log"),
            projection_store=_ProjectionStore(),
        )

    assert captured.value.reason_code == AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE
    assert captured.value.source == "ledger:governor_log"
    assert "PROJECTION_REBUILDING" not in str(captured.value)


def test_digital_life_missing_query_dependency_is_typed() -> None:
    from host_brain.app.obs_routes_summary import (
        AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE,
        AggregateProjectionSourceUnavailable,
        _build_digital_life_overview_snapshot_async,
    )

    store = _ProjectionStore(omit="service_health")
    with pytest.raises(AggregateProjectionSourceUnavailable) as captured:
        asyncio.run(
            _build_digital_life_overview_snapshot_async(
                360,
                projection_view=_ProjectionView(),
                projection_store=store,
            )
        )

    assert captured.value.reason_code == AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE
    assert captured.value.source == "query:service-health"

    store.omit = None
    recovered = asyncio.run(
        _build_digital_life_overview_snapshot_async(
            360,
            projection_view=_ProjectionView(),
            projection_store=store,
        )
    )
    assert recovered["servicesData"]["degraded_state"] == "HEALTHY"


def test_capability_forge_unavailable_is_field_level_partial() -> None:
    from host_brain.app.obs_routes_summary import (
        CAPABILITY_FORGE_FIELD_UNAVAILABLE_REASON_CODE,
        _build_digital_life_overview_snapshot_async,
    )

    payload = asyncio.run(
        _build_digital_life_overview_snapshot_async(
            360,
            projection_view=_ProjectionView(),
            projection_store=_ProjectionStore(omit="capability_forge_operator"),
        )
    )

    assert payload["window_minutes"] == 360
    assert payload["capabilityGapData"] is None
    assert payload["capabilityGapProjection"] == {
        "available": False,
        "degraded_state": "REBUILDING",
        "reason_code": CAPABILITY_FORGE_FIELD_UNAVAILABLE_REASON_CODE,
        "upstream_reason_code": "OPERATOR_PROJECTION_NOT_READY",
        "projection_version": 0,
        "cursor": 0,
        "freshness_ts_ms": 0,
    }


def test_query_store_exception_is_redacted_to_stable_reason() -> None:
    from host_brain.app.obs_routes_summary import (
        AggregateProjectionSourceUnavailable,
        _build_digital_life_overview_snapshot_async,
    )

    class ExplodingStore:
        @staticmethod
        def get(_kind: str, _key: str):
            raise RuntimeError("private database detail")

    with pytest.raises(AggregateProjectionSourceUnavailable) as captured:
        asyncio.run(
            _build_digital_life_overview_snapshot_async(
                360,
                projection_view=_ProjectionView(),
                projection_store=ExplodingStore(),
            )
        )

    assert captured.value.source == "query:service-health"
    assert "private database detail" not in str(captured.value)


def test_insights_callback_recovers_after_typed_unavailable_first_wave(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app.observability_runtime.projection_ledger_view import (
        LEDGER_STATS_KIND,
        LEDGER_STATS_RETENTION_MS,
        LEDGER_STATS_ROW_CAP,
    )
    from host_brain.app.observability_runtime.remaining_query_projection import (
        AGENT_LOOP_INDEX_KEY,
        AGENT_LOOP_KIND,
    )
    from host_brain.composition import process_services
    from host_brain.app.obs_projection_jobs import (
        ProjectionTarget,
        _aggregate_summary_callback,
    )

    monkeypatch.setattr(
        process_services,
        "peek_query_projection_store",
        _forbidden_locator,
    )
    monkeypatch.setattr(
        process_services,
        "get_query_projection_store",
        _forbidden_locator,
    )
    store = _MutableProjectionStore()
    for table in summary._INSIGHTS_LEDGER_TABLES:
        store.documents[(LEDGER_STATS_KIND, table)] = _ProjectionDocument(
            {
                "table": table,
                "rows": [],
                "retention_ms": LEDGER_STATS_RETENTION_MS,
                "row_cap": LEDGER_STATS_ROW_CAP,
                "truncated": False,
                "stable_cursor": "empty",
            }
        )

    target = ProjectionTarget(summary.INSIGHTS_SUMMARY_KIND, "window:60")
    callback = _aggregate_summary_callback(
        store,
        job_name="insights-summary",
        target=target,
        window_minutes=60,
        async_builder=False,
    )

    first = callback()
    unavailable = store.get(target.kind, target.key)
    assert first["unavailable_document_count"] == 1
    assert unavailable.payload["reason_code"] == summary.AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE
    assert unavailable.payload["source_scope"] == "app-local-query-projection"
    assert unavailable.degraded_state == ProjectionDegradedState.SOURCE_UNAVAILABLE

    store.documents[(AGENT_LOOP_KIND, AGENT_LOOP_INDEX_KEY)] = _ProjectionDocument(
        {"loops": [], "timestamp": 1}
    )
    second = callback()
    recovered = store.get(target.kind, target.key)
    assert second["unavailable_document_count"] == 0
    assert recovered.payload["window_minutes"] == 60
    assert recovered.degraded_state == ProjectionDegradedState.HEALTHY


def test_digital_life_callback_recovers_when_query_dependencies_arrive(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app.observability_runtime.capability_stats_projection import (
        CAPABILITY_FORGE_OPERATOR_KEY,
        CAPABILITY_FORGE_OPERATOR_KIND,
    )
    from host_brain.app.observability_runtime.health_device_projection import (
        SERVICE_HEALTH_KEY,
        SERVICE_HEALTH_KIND,
    )
    from host_brain.app.observability_runtime.projection_ledger_view import (
        LEDGER_STATS_KIND,
        LEDGER_STATS_RETENTION_MS,
        LEDGER_STATS_ROW_CAP,
    )
    from host_brain.composition import process_services
    from host_brain.app.obs_projection_jobs import (
        ProjectionTarget,
        _aggregate_summary_callback,
    )

    monkeypatch.setattr(
        process_services,
        "peek_query_projection_store",
        _forbidden_locator,
    )
    monkeypatch.setattr(
        process_services,
        "get_query_projection_store",
        _forbidden_locator,
    )
    store = _MutableProjectionStore()
    for table in summary._DIGITAL_LIFE_LEDGER_TABLES:
        store.documents[(LEDGER_STATS_KIND, table)] = _ProjectionDocument(
            {
                "table": table,
                "rows": [],
                "retention_ms": LEDGER_STATS_RETENTION_MS,
                "row_cap": LEDGER_STATS_ROW_CAP,
                "truncated": False,
                "stable_cursor": "empty",
            }
        )

    target = ProjectionTarget(summary.DIGITAL_LIFE_OVERVIEW_KIND, "window:360")
    callback = _aggregate_summary_callback(
        store,
        job_name="digital-life-overview",
        target=target,
        window_minutes=360,
        async_builder=True,
    )

    first = callback()
    assert first["unavailable_document_count"] == 1
    unavailable = store.get(target.kind, target.key)
    assert unavailable.degraded_state == ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert unavailable.payload["source_scope"] == "app-local-query-projection"

    store.documents[(SERVICE_HEALTH_KIND, SERVICE_HEALTH_KEY)] = _ProjectionDocument(
        {"services": [], "timestamp": 1}
    )
    second = callback()
    recovered = store.get(target.kind, target.key)
    assert second["unavailable_document_count"] == 0
    assert recovered.payload["window_minutes"] == 360
    assert recovered.payload["capabilityGapData"] is None
    assert recovered.payload["capabilityGapProjection"]["available"] is False
    assert recovered.degraded_state == ProjectionDegradedState.HEALTHY

    store.documents[
        (CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY)
    ] = _ProjectionDocument({"gap": {}, "timestamp": 1})
    third = callback()
    complete = store.get(target.kind, target.key)
    assert third["unavailable_document_count"] == 0
    assert complete.payload["capabilityGapData"] is not None
    assert complete.payload["capabilityGapProjection"]["available"] is True


@pytest.mark.parametrize(
    "ledger_payload_patch",
    (
        {"stable_cursor": "0:source-unavailable"},
        {"truncated": True},
    ),
)
def test_aggregate_preflight_rejects_incomplete_ledger_projection(
    ledger_payload_patch: dict,
) -> None:
    from host_brain.app import obs_routes_summary as summary
    from host_brain.app.observability_runtime.projection_ledger_view import (
        LEDGER_STATS_KIND,
        LEDGER_STATS_RETENTION_MS,
        LEDGER_STATS_ROW_CAP,
        ProjectionLedgerView,
    )
    from host_brain.app.observability_runtime.remaining_query_projection import (
        AGENT_LOOP_INDEX_KEY,
        AGENT_LOOP_KIND,
    )

    store = _MutableProjectionStore()
    for table in summary._INSIGHTS_LEDGER_TABLES:
        payload = {
            "table": table,
            "rows": [],
            "retention_ms": LEDGER_STATS_RETENTION_MS,
            "row_cap": LEDGER_STATS_ROW_CAP,
            "truncated": False,
            "stable_cursor": "0:empty",
        }
        if table == "governor_log":
            payload.update(ledger_payload_patch)
        store.documents[(LEDGER_STATS_KIND, table)] = _ProjectionDocument(payload)
    store.documents[(AGENT_LOOP_KIND, AGENT_LOOP_INDEX_KEY)] = _ProjectionDocument(
        {"loops": [], "timestamp": 1}
    )

    with pytest.raises(summary.AggregateProjectionSourceUnavailable) as captured:
        summary._build_insights_summary_snapshot_sync(
            60,
            projection_view=ProjectionLedgerView(store),
            projection_store=store,
        )

    assert captured.value.source == "ledger:governor_log"


@pytest.mark.parametrize(
    ("builder", "window", "kwargs"),
    (
        ("insights", 360, {}),
        ("digital-life", 60, {}),
    ),
)
def test_summary_source_materializes_only_canonical_windows(
    builder: str,
    window: int,
    kwargs: dict,
) -> None:
    from host_brain.app.obs_routes_summary import (
        AGGREGATE_WINDOW_UNSUPPORTED_REASON_CODE,
        AggregateProjectionSourceUnavailable,
        _build_digital_life_overview_snapshot_async,
        _build_insights_summary_snapshot_sync,
    )

    call_kwargs = {
        "projection_view": _ProjectionView(),
        "projection_store": _ProjectionStore(),
        **kwargs,
    }
    with pytest.raises(AggregateProjectionSourceUnavailable) as captured:
        if builder == "insights":
            _build_insights_summary_snapshot_sync(window, **call_kwargs)
        else:
            asyncio.run(
                _build_digital_life_overview_snapshot_async(window, **call_kwargs)
            )

    assert captured.value.reason_code == AGGREGATE_WINDOW_UNSUPPORTED_REASON_CODE
