"""B1 内在天气交叉耦合（inner-weather cross-coupling）单测。

验证：疲劳/孤独/烦躁由同一份输入派生出共享天气与耦合系数（天然 co-vary）；
天气接入 A2 闭环——疲惫收缩时的短回复算"贴合状态"而非 flat；cycle 写入并经 adaptive pack 暴露。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import host_brain.runtime.autonomous_self as aself
from host_brain.runtime import life_continuity as lc
from host_brain.runtime.autonomous_self import compute_dialogue_state_closure, derive_inner_weather

NOW = 1_800_000_000_000


def _ledger() -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    return ledger


# ── 天气派生 ──

def test_weather_worn_out() -> None:
    w = derive_inner_weather({"fatigue": 0.8, "energy": 0.2})
    assert w["reply_brevity_bias"] >= 0.6
    assert w["withdrawn"] is True
    assert w["label"] == "低气压·乏力"


def test_weather_lonely_reaches_out() -> None:
    w = derive_inner_weather({"loneliness": 0.8, "fatigue": 0.1, "energy": 0.7})
    assert w["reach_out_bias"] >= 0.5
    assert w["withdrawn"] is False
    assert w["label"] == "空·想找人"


def test_weather_agitated() -> None:
    w = derive_inner_weather({"irritation": 0.8, "tension": 0.7, "fatigue": 0.2, "energy": 0.6})
    assert w["agitation"] >= 0.5
    assert w["label"] == "闷·烦躁"


def test_weather_calm_baseline() -> None:
    w = derive_inner_weather({})
    assert w["label"] == "平稳"
    assert w["withdrawn"] is False


def test_weather_biases_covary_from_shared_inputs() -> None:
    low = derive_inner_weather({"fatigue": 0.1, "loneliness": 0.1, "energy": 0.7})
    high = derive_inner_weather({"fatigue": 0.8, "loneliness": 0.8, "irritation": 0.7, "tension": 0.7, "energy": 0.2})
    assert high["reply_brevity_bias"] > low["reply_brevity_bias"]
    assert high["memory_distortion_bias"] > low["memory_distortion_bias"]


# ── 接入 A2 闭环：疲惫收缩时的短回复不再被判 flat ──

def test_weather_couples_closure_demand() -> None:
    base = {"affect": {"loneliness": 0.8, "attachment_warmth": 0.42}, "inner_world": {}}
    reply = "……嗯"
    without = compute_dialogue_state_closure(before_state=base, reply_text=reply, now_ms=NOW)
    worn = dict(base)
    worn["inner_weather"] = {"reply_brevity_bias": 0.7}
    with_weather = compute_dialogue_state_closure(before_state=worn, reply_text=reply, now_ms=NOW)
    # 同一句短回复，疲惫天气下表达需求被下调，presence 更高（贴合状态而非 flat）
    assert with_weather["presence_closure"] > without["presence_closure"]
    assert with_weather["demand"] < without["demand"]


# ── 写入与暴露 ──

def test_cycle_writes_inner_weather() -> None:
    ledger = _ledger()
    state = lc.get_life_state(ledger, "default")
    state["affect"]["fatigue"] = 0.7
    after = aself.apply_autonomous_self_cycle(ledger=ledger, person_id="default", state=state, now_ms=NOW)
    weather = after["inner_weather"]
    assert "label" in weather
    assert weather["reply_brevity_bias"] >= 0.6


def test_adaptive_pack_exposes_weather() -> None:
    state = {"inner_weather": {"label": "低气压·乏力", "reply_brevity_bias": 0.8}}
    pack = aself.build_adaptive_self_pack(state)
    assert pack["inner_weather"]["label"] == "低气压·乏力"
