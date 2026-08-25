"""C2-2 状态驱动主动触发 单测。

验证：compute_proactive_readiness = max(天气冲动, 项目成熟, 期待*0.7)；
execute_proactive_talk 的 readiness 软化门——缺省走纯空闲门（向后兼容）、
极低 readiness 按兵不动、低 readiness 拉长空闲门槛。
"""

from __future__ import annotations

import time

from host_brain.persona.emotional_proactive import compute_proactive_readiness

RECENT_PROJECT = {
    "project_id": "emergent_天文", "origin": "emergent", "status": "active",
    "progress": 0.5, "last_worked_at_ms": 0, "title": "陪你追天文",
}


# ── 纯函数：readiness ──

def test_readiness_from_reach_out_bias() -> None:
    r = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0)
    assert r == 0.8


def test_readiness_from_project() -> None:
    proj = {**RECENT_PROJECT, "last_worked_at_ms": int(time.time() * 1000)}
    r = compute_proactive_readiness(reach_out_bias=0.0, active_projects=[proj], anticipation=0.0, now_ms=int(time.time() * 1000))
    assert r > 0.5


def test_readiness_all_low() -> None:
    r = compute_proactive_readiness(reach_out_bias=0.1, active_projects=None, anticipation=0.1, now_ms=0)
    assert r <= 0.12


def test_readiness_takes_max() -> None:
    r = compute_proactive_readiness(reach_out_bias=0.3, active_projects=None, anticipation=0.9, now_ms=0)
    assert r == 0.9 * 0.7  # 期待*0.7 胜出
