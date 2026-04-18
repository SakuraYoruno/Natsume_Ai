import asyncio

from host_brain.app.obs_routes_summary import (
    DigitalLifeOverviewSnapshotResponse,
    InsightsSummarySnapshotResponse,
    get_digital_life_overview_snapshot,
    get_insights_summary_snapshot,
)


def test_insights_summary_snapshot_prefers_warmed_payload(monkeypatch):
    warmed = InsightsSummarySnapshotResponse(
        window_minutes=60,
        timestamp=1700000000000,
        govData={"total": 3},
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._get_warmed_insights_summary_snapshot",
        lambda window_minutes: warmed.model_dump(),
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


def test_digital_life_overview_snapshot_prefers_warmed_payload(monkeypatch):
    warmed = DigitalLifeOverviewSnapshotResponse(
        window_minutes=360,
        timestamp=1700000000000,
        servicesData={"total": 4},
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_summary._get_warmed_digital_life_overview_snapshot",
        lambda window_minutes: warmed.model_dump(),
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
