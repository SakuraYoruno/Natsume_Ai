"""C2-4 主动落地回流校准 单测。

验证：apply_proactive_outcome 把落地结果 EMA 进耐久 reach_out_calibration（被暖回↑、被嫌烦↓）；
compute_proactive_readiness 按校准阻尼；record_dialogue_aftereffect 在用户回应待定主动后更新校准。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace

from host_brain.persona.emotional_proactive import compute_proactive_readiness
from host_brain.runtime import life_continuity as lc
from host_brain.runtime.autonomous_self import (
    _CALIBRATION_BASELINE,
    apply_proactive_outcome,
    get_reach_out_calibration,
)


def _ledger() -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    return ledger


# ── 纯函数：校准 EMA ──

def test_calibration_default_baseline() -> None:
    assert get_reach_out_calibration({}) == _CALIBRATION_BASELINE


def test_warm_response_raises_calibration() -> None:
    state: dict = {}
    apply_proactive_outcome(state, outcome="responded", reply_warmth=0.8, now_ms=0)
    assert state["reach_out_calibration"]["value"] > _CALIBRATION_BASELINE
    assert state["reach_out_calibration"]["last_outcome"] == "responded"
    assert state["reach_out_calibration"]["samples"] == 1


def test_annoyed_lowers_calibration() -> None:
    state: dict = {}
    apply_proactive_outcome(state, outcome="annoyed", reply_warmth=0.18, now_ms=0)
    assert state["reach_out_calibration"]["value"] < _CALIBRATION_BASELINE


def test_repeated_annoyed_trends_down() -> None:
    state: dict = {}
    for _ in range(8):
        apply_proactive_outcome(state, outcome="annoyed", now_ms=0)
    # EMA 朝 0.15 收敛
    assert state["reach_out_calibration"]["value"] < 0.3
    assert state["reach_out_calibration"]["samples"] == 8


def test_repeated_warm_trends_up() -> None:
    state: dict = {}
    for _ in range(8):
        apply_proactive_outcome(state, outcome="responded", reply_warmth=0.9, now_ms=0)
    assert state["reach_out_calibration"]["value"] > 0.85


# ── readiness 阻尼 ──

def test_baseline_calibration_does_not_damp() -> None:
    base = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0)
    with_cal = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0, calibration=_CALIBRATION_BASELINE)
    assert with_cal == base  # 0.6 基线 → factor 1.0


def test_low_calibration_damps_readiness() -> None:
    base = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0)
    damped = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0, calibration=0.15)
    assert damped < base


def test_none_calibration_backward_compatible() -> None:
    base = compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0)
    assert compute_proactive_readiness(reach_out_bias=0.8, active_projects=None, anticipation=0.0, now_ms=0, calibration=None) == base


# ── 集成：用户回应待定主动 → 校准更新 ──

def test_aftereffect_updates_calibration_on_warm_reply(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LIFE_DIALOGUE_AFTEREFFECT_EMBODIMENT_ENABLED", "false")
    from host_brain.runtime.life_organs import open_proactive_episode

    ledger = _ledger()
    person = "default"
    open_proactive_episode(ledger=ledger, person_id=person, motive="lonely_murmur", message_text="想你了", sent_at_ms=int(time.time() * 1000))
    asyncio.run(lc.record_dialogue_aftereffect(ledger=ledger, person_id=person, user_message="在呀，怎么啦", reply_text="嗯"))
    cal = lc.get_life_state(ledger, person)["reach_out_calibration"]
    assert cal["last_outcome"] == "responded"
    assert cal["value"] > _CALIBRATION_BASELINE


def test_aftereffect_lowers_calibration_on_annoyed_reply(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LIFE_DIALOGUE_AFTEREFFECT_EMBODIMENT_ENABLED", "false")
    from host_brain.runtime.life_organs import open_proactive_episode

    ledger = _ledger()
    person = "default"
    open_proactive_episode(ledger=ledger, person_id=person, motive="lonely_murmur", message_text="想你了", sent_at_ms=int(time.time() * 1000))
    asyncio.run(lc.record_dialogue_aftereffect(ledger=ledger, person_id=person, user_message="别一直发了，很烦", reply_text="……"))
    cal = lc.get_life_state(ledger, person)["reach_out_calibration"]
    assert cal["last_outcome"] == "annoyed"
    assert cal["value"] < _CALIBRATION_BASELINE
