"""C2-2 状态驱动主动触发 单测。

验证：compute_proactive_readiness = max(天气冲动, 项目成熟, 期待*0.7)；
execute_proactive_talk 的 readiness 软化门——缺省走纯空闲门（向后兼容）、
极低 readiness 按兵不动、低 readiness 拉长空闲门槛。
"""

from __future__ import annotations

import time

from host_brain.persona.emotional_proactive import compute_proactive_readiness
from host_brain.skills.impl.proactive_talk import STATUS_REJECTED, execute_proactive_talk

IDLE = 60 * 60 * 1000  # 1h
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


# ── 门禁：readiness 软化 ──

def _run(elapsed_ms, readiness=None, idle_threshold=IDLE):
    ctx = {"last_interaction_ts": int(time.time() * 1000) - elapsed_ms, "sys_mode": "normal"}
    if readiness is not None:
        ctx["proactive_readiness"] = readiness
    return execute_proactive_talk(args={"idle_threshold_ms": idle_threshold}, context=ctx)


def test_gate_backward_compatible_idle_reject() -> None:
    # readiness 缺省 → 纯空闲门：idle 不足阈值 → reject
    res = _run(elapsed_ms=int(IDLE * 0.5))
    assert res["status"] == STATUS_REJECTED
    assert "idle time" in res["detail"]


def test_gate_very_low_readiness_holds() -> None:
    # readiness 极低 → 即便空闲很久也按兵不动
    res = _run(elapsed_ms=IDLE * 10, readiness=0.05)
    assert res["status"] == STATUS_REJECTED
    assert "readiness" in res["detail"] and "too low" in res["detail"]


def test_gate_low_readiness_lengthens_threshold() -> None:
    # readiness 0.18（>0.12 不硬 hold，≤0.20 拉长阈值 ×1.6）；elapsed 1.2× 原阈值仍不够 → idle reject
    res = _run(elapsed_ms=int(IDLE * 1.2), readiness=0.18)
    assert res["status"] == STATUS_REJECTED
    assert "idle time" in res["detail"]


def test_gate_high_readiness_shortens_threshold() -> None:
    # readiness 0.7 → 阈值 ×0.4；elapsed 0.5× 原阈值 ≥ 0.4× → 不因空闲被拒
    res = _run(elapsed_ms=int(IDLE * 0.5), readiness=0.7)
    assert not (res["status"] == STATUS_REJECTED and "idle time" in str(res.get("detail") or ""))
