import time
from types import SimpleNamespace

from host_brain.app.obs_routes_sysmode import (
    DigitalLifeRuntimeSnapshotResponse,
    get_digital_life_runtime_snapshot,
)
from natsume_contracts.operator.v1 import ProjectionDegradedState


def _make_snapshot() -> DigitalLifeRuntimeSnapshotResponse:
    return DigitalLifeRuntimeSnapshotResponse(
        persona_user_id="persona-user-warm",
        sys_mode="normal",
        sys_mode_status={},
        timestamp=1700000000000,
    )


def test_runtime_snapshot_prefers_durable_projection_and_never_reads_legacy_warm(monkeypatch):
    from host_brain.app import dependencies

    warmed = _make_snapshot()
    document = SimpleNamespace(
        payload=warmed.model_dump(),
        schema_version="Natsume.operator.query_projection.v1",
        projection_version=1,
        projection_cursor=2,
        source_cursor=1,
        source_digest="sha256:" + "0" * 64,
        freshness_ts_ms=int(time.time() * 1000),
        degraded_state=ProjectionDegradedState.HEALTHY,
        degraded_detail=None,
    )
    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        lambda: SimpleNamespace(get=lambda _kind, _key: document),
    )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_sysmode._get_warmed_runtime_snapshot",
        lambda _current_topic: (_ for _ in ()).throw(
            AssertionError("runtime GET must not read legacy warm state")
        ),
    )

    def _unexpected_builder(current_topic: str):
        raise AssertionError("runtime snapshot builder should not run when warm payload is available")

    monkeypatch.setattr(
        "host_brain.app.obs_routes_sysmode._build_runtime_snapshot_response",
        _unexpected_builder,
    )

    result = get_digital_life_runtime_snapshot(current_topic="")

    assert result.persona_user_id == "persona-user-warm"
    assert result.timestamp == 1700000000000
    assert result.projection["projection_version"] == 1
