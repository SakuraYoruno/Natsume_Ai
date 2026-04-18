from host_brain.app.obs_routes_sysmode import (
    DigitalLifeRuntimeSnapshotResponse,
    get_digital_life_runtime_snapshot,
)


def _make_snapshot() -> DigitalLifeRuntimeSnapshotResponse:
    return DigitalLifeRuntimeSnapshotResponse(
        persona_user_id="persona-user-warm",
        sys_mode="normal",
        sys_mode_status={},
        timestamp=1700000000000,
    )


def test_runtime_snapshot_prefers_warmed_payload(monkeypatch):
    warmed = _make_snapshot()

    monkeypatch.setattr(
        "host_brain.app.obs_routes_sysmode._get_warmed_runtime_snapshot",
        lambda current_topic: warmed,
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
