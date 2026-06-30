"""C2-6 主动自主性可观测路由 单测。

验证：build_proactive_autonomy_snapshot 从 life-state 聚合出
内在天气 / 校准 / 活跃项目 / readiness / 意图 / 对话闭环，并优雅降级。
"""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

from host_brain.app.obs_routes_proactive_autonomy import build_proactive_autonomy_snapshot
from host_brain.runtime import life_continuity as lc


def _ledger() -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    return ledger


def test_snapshot_surfaces_proactive_signals() -> None:
    ledger = _ledger()
    state = lc.get_life_state(ledger, "default")
    state["inner_weather"] = {"label": "空·想找人", "reach_out_bias": 0.7}
    state["reach_out_calibration"] = {"value": 0.45, "samples": 3, "last_outcome": "annoyed"}
    state["self_growth"] = {
        "active_projects": [
            {"project_id": "emergent_天文", "origin": "emergent", "status": "active",
             "title": "陪你追天文", "progress": 0.5, "last_worked_at_ms": int(time.time() * 1000)}
        ]
    }
    lc._save_state(ledger, state)

    snap = build_proactive_autonomy_snapshot(ledger, "default")
    assert snap["available"] is True
    assert snap["inner_weather"]["label"] == "空·想找人"
    assert snap["reach_out_calibration"]["value"] == 0.45
    assert snap["proactive_readiness"] is not None and snap["proactive_readiness"] > 0
    assert len(snap["active_projects"]) == 1
    assert snap["active_projects"][0]["project_id"] == "emergent_天文"
    assert snap["top_intention"]["project_id"] == "emergent_天文"


def test_snapshot_low_calibration_damps_readiness() -> None:
    ledger = _ledger()
    state = lc.get_life_state(ledger, "default")
    state["inner_weather"] = {"reach_out_bias": 0.8}
    state["reach_out_calibration"] = {"value": 0.15}  # 被冷落
    lc._save_state(ledger, state)
    snap = build_proactive_autonomy_snapshot(ledger, "default")
    assert snap["proactive_readiness"] < 0.8  # 校准阻尼


def test_snapshot_default_state_available() -> None:
    ledger = _ledger()
    snap = build_proactive_autonomy_snapshot(ledger, "default")
    assert snap["available"] is True
    assert "proactive_readiness" in snap
    assert snap["top_intention"] is None  # 无项目
