"""C2-6 主动自主性可观测路由 单测。

验证：build_proactive_autonomy_snapshot 从 life-state 聚合出
内在天气 / 校准 / 活跃项目 / readiness / 意图 / 对话闭环，并优雅降级。
"""

from __future__ import annotations

import time

from host_brain.app.obs_routes_proactive_autonomy import build_proactive_autonomy_snapshot


def _install_snapshot(monkeypatch, state: dict, *, version: int = 1) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "host_brain.app.life_application_boundary.read_life_snapshot",
        lambda person_id: {
            "person_id": person_id,
            "version": version,
            "state": {"person_id": person_id, "version": version, **state},
        },
    )


def test_snapshot_surfaces_proactive_signals(monkeypatch) -> None:
    state: dict = {}
    state["inner_weather"] = {"label": "空·想找人", "reach_out_bias": 0.7}
    state["reach_out_calibration"] = {"value": 0.45, "samples": 3, "last_outcome": "annoyed"}
    state["self_growth"] = {
        "active_projects": [
            {"project_id": "emergent_天文", "origin": "emergent", "status": "active",
             "title": "陪你追天文", "progress": 0.5, "last_worked_at_ms": int(time.time() * 1000)}
        ]
    }
    _install_snapshot(monkeypatch, state, version=4)

    snap = build_proactive_autonomy_snapshot(object(), "default")
    assert snap["available"] is True
    assert snap["inner_weather"]["label"] == "空·想找人"
    assert snap["reach_out_calibration"]["value"] == 0.45
    assert snap["proactive_readiness"] is not None and snap["proactive_readiness"] > 0
    assert len(snap["active_projects"]) == 1
    assert snap["active_projects"][0]["project_id"] == "emergent_天文"
    assert snap["top_intention"]["project_id"] == "emergent_天文"


def test_snapshot_low_calibration_damps_readiness(monkeypatch) -> None:
    state: dict = {}
    state["inner_weather"] = {"reach_out_bias": 0.8}
    state["reach_out_calibration"] = {"value": 0.15}  # 被冷落
    _install_snapshot(monkeypatch, state, version=2)
    snap = build_proactive_autonomy_snapshot(object(), "default")
    assert snap["proactive_readiness"] < 0.8  # 校准阻尼


def test_snapshot_missing_state_is_explicitly_unavailable(monkeypatch) -> None:
    _install_snapshot(monkeypatch, {}, version=0)
    snap = build_proactive_autonomy_snapshot(object(), "default")
    assert snap["available"] is False
    assert "proactive_readiness" in snap
    assert snap["top_intention"] is None
