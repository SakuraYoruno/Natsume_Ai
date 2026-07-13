import asyncio
import time
from types import SimpleNamespace

from host_brain.app.obs_routes_summary import (
    DigitalLifeOverviewSnapshotResponse,
    InsightsSummarySnapshotResponse,
    get_digital_life_overview_snapshot,
    get_insights_summary_snapshot,
)
from natsume_contracts.operator.v1 import ProjectionDegradedState


def _document(payload: dict):
    return SimpleNamespace(
        payload=payload,
        schema_version="Natsume.operator.query_projection.v1",
        projection_version=1,
        projection_cursor=2,
        source_cursor=1,
        source_digest="sha256:" + "0" * 64,
        freshness_ts_ms=int(time.time() * 1000),
        degraded_state=ProjectionDegradedState.HEALTHY,
        degraded_detail=None,
    )


def test_insights_summary_snapshot_prefers_durable_projection(monkeypatch):
    from host_brain.app import dependencies

    warmed = InsightsSummarySnapshotResponse(
        window_minutes=60,
        timestamp=1700000000000,
        govData={"total": 3},
    )
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        lambda: SimpleNamespace(get=lambda _kind, _key: _document(warmed.model_dump())),
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._get_warmed_insights_summary_snapshot",
        lambda _window_minutes: (_ for _ in ()).throw(
            AssertionError("summary GET must not read legacy warm state")
        ),
    )

    def _unexpected_builder(window_minutes: int):
        raise AssertionError("insights summary builder should not run when warm payload is available")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._build_insights_summary_snapshot_sync",
        _unexpected_builder,
    )

    result = get_insights_summary_snapshot(window_minutes=60)

    assert result["window_minutes"] == 60
    assert result["govData"]["total"] == 3
    assert result["projection"]["projection_version"] == 1


def test_digital_life_overview_snapshot_prefers_durable_projection(monkeypatch):
    from host_brain.app import dependencies

    warmed = DigitalLifeOverviewSnapshotResponse(
        window_minutes=360,
        timestamp=1700000000000,
        servicesData={"total": 4},
    )
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        lambda: SimpleNamespace(get=lambda _kind, _key: _document(warmed.model_dump())),
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._get_warmed_digital_life_overview_snapshot",
        lambda _window_minutes: (_ for _ in ()).throw(
            AssertionError("overview GET must not read legacy warm state")
        ),
    )

    async def _unexpected_builder(window_minutes: int):
        raise AssertionError("digital life overview builder should not run when warm payload is available")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._build_digital_life_overview_snapshot_async",
        _unexpected_builder,
    )

    result = asyncio.run(get_digital_life_overview_snapshot(window_minutes=360))

    assert result["window_minutes"] == 360
    assert result["servicesData"]["total"] == 4
    assert result["projection"]["projection_version"] == 1
