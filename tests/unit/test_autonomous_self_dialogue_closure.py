"""A2 对话内心态闭环（dialogue inner-state closure）单测。

验证：注入的内心态是否真的反映在回复里被量化为 closure，
并经 life state 往返存活、融合进 _self_reward 的 presence/coherence、
低 closure 驱动 inner_state_flat 学习信号。
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from host_brain.runtime import life_continuity as lc
from host_brain.runtime.autonomous_self import (
    _CLOSURE_MAX_WEIGHT,
    _closure_reward_weight,
    _self_reward,
    compute_dialogue_state_closure,
    extract_learning_signals,
)

NOW = 1_800_000_000_000


def _ledger() -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    return ledger


# ── 纯函数：closure 计算 ──

def test_warm_in_character_reply_leaks_inner_state() -> None:
    before = {"affect": {"loneliness": 0.7, "attachment_warmth": 0.6}, "inner_world": {}}
    reply = "阿晓你这个笨蛋，怎么这么晚才回我……哼，我才没担心你呢～"
    closure = compute_dialogue_state_closure(before_state=before, reply_text=reply, now_ms=NOW)
    assert closure["verdict"] == "leaked"
    assert closure["presence_closure"] >= 0.8
    assert closure["assistant_flavor_hits"] == 0


def test_flat_assistant_reply_is_flat() -> None:
    before = {"affect": {"loneliness": 0.8, "attachment_warmth": 0.6}, "inner_world": {}}
    reply = "好的，请问还有什么可以帮您的吗？"
    closure = compute_dialogue_state_closure(before_state=before, reply_text=reply, now_ms=NOW)
    assert closure["verdict"] == "flat"
    assert closure["presence_closure"] <= 0.2
    assert closure["assistant_flavor_hits"] >= 1


def test_warm_reply_when_irritated_is_incoherent() -> None:
    # 内心又怒又紧、负效价，却甜腻无锋利 → 情绪方向自我矛盾
    before = {"affect": {"irritation": 0.8, "tension": 0.7, "valence": -0.3}, "inner_world": {}}
    reply = "嗯嗯好呀好呀～开心！"
    closure = compute_dialogue_state_closure(before_state=before, reply_text=reply, now_ms=NOW)
    assert closure["coherence_closure"] < 0.6


def test_empty_reply_does_not_crash() -> None:
    closure = compute_dialogue_state_closure(before_state={}, reply_text="", now_ms=NOW)
    assert closure["expressiveness"] == 0.0
    assert closure["reply_len"] == 0
    assert 0.0 <= closure["closure_score"] <= 1.0


# ── closure 时效权重 ──

def test_closure_reward_weight_fresh_full() -> None:
    assert _closure_reward_weight({"ts_ms": NOW}, NOW) == _CLOSURE_MAX_WEIGHT


def test_closure_reward_weight_stale_zero() -> None:
    assert _closure_reward_weight({"ts_ms": NOW - 100 * 60 * 1000}, NOW) == 0.0


def test_closure_reward_weight_decays_in_window() -> None:
    mid = _closure_reward_weight({"ts_ms": NOW - 50 * 60 * 1000}, NOW)
    assert 0.0 < mid < _CLOSURE_MAX_WEIGHT


def test_closure_reward_weight_missing_ts() -> None:
    assert _closure_reward_weight({}, NOW) == 0.0


# ── _self_reward 融合 closure ──

def test_self_reward_blends_low_closure_down() -> None:
    state = {"life_pressure": {}, "dialogue_closure": {
        "ts_ms": NOW, "presence_closure": 0.1, "coherence_closure": 0.2, "closure_score": 0.14,
    }}
    reward = _self_reward(state=state, signals=[], agenda=[{"kind": "x"}], now_ms=NOW)
    assert reward["dialogue_closure_weight"] == _CLOSURE_MAX_WEIGHT
    # 基线 presence=0.70（有 agenda），融合 0.1 后被拉低
    assert reward["presence"] < 0.70
    assert reward["coherence"] < 1.0


def test_self_reward_without_closure_is_backward_compatible() -> None:
    state = {"life_pressure": {}}
    reward = _self_reward(state=state, signals=[], agenda=[{"kind": "x"}], now_ms=NOW)
    assert "dialogue_closure_weight" not in reward
    assert abs(reward["presence"] - 0.70) < 1e-9


def test_self_reward_ignores_stale_closure() -> None:
    state = {"life_pressure": {}, "dialogue_closure": {
        "ts_ms": NOW - 100 * 60 * 1000, "presence_closure": 0.0, "coherence_closure": 0.0, "closure_score": 0.0,
    }}
    reward = _self_reward(state=state, signals=[], agenda=[{"kind": "x"}], now_ms=NOW)
    assert "dialogue_closure_weight" not in reward
    assert abs(reward["presence"] - 0.70) < 1e-9


# ── 学习信号：低 closure 驱动 inner_state_flat ──

def test_flat_closure_emits_inner_state_flat_signal() -> None:
    ledger = _ledger()
    state = lc.get_life_state(ledger, "default")
    state["dialogue_closure"] = {"ts_ms": NOW, "verdict": "flat", "closure_score": 0.2}
    signals = extract_learning_signals(ledger=ledger, person_id="default", state=state, agenda=[], now_ms=NOW)
    kinds = {s["signal_kind"] for s in signals}
    assert "inner_state_flat" in kinds
    flat = next(s for s in signals if s["signal_kind"] == "inner_state_flat")
    assert flat["candidate_delta"]["trait"] == "warmth"


def test_leaked_closure_emits_no_flat_signal() -> None:
    ledger = _ledger()
    state = lc.get_life_state(ledger, "default")
    state["dialogue_closure"] = {"ts_ms": NOW, "verdict": "leaked", "closure_score": 0.9}
    signals = extract_learning_signals(ledger=ledger, person_id="default", state=state, agenda=[], now_ms=NOW)
    assert "inner_state_flat" not in {s["signal_kind"] for s in signals}


# ── 集成：record_dialogue_aftereffect 写入并往返存活 ──

def test_record_dialogue_aftereffect_persists_closure(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LIFE_DIALOGUE_AFTEREFFECT_EMBODIMENT_ENABLED", "false")
    ledger = _ledger()
    person = "default"
    seeded = lc.get_life_state(ledger, person)
    seeded["affect"]["loneliness"] = 0.82
    seeded["affect"]["attachment_warmth"] = 0.6
    lc._save_state(ledger, seeded)

    causality = asyncio.run(lc.record_dialogue_aftereffect(
        ledger=ledger,
        person_id=person,
        session_id="s1",
        request_id="r1",
        user_message="在吗",
        reply_text="好的，请问还有什么可以帮您的吗？",
    ))

    closure = causality["dialogue_closure"]
    assert closure["verdict"] in {"flat", "partial"}
    assert closure["assistant_flavor_hits"] >= 1
    # 往返存活：写进持久化 state，下一次 get_life_state 仍在
    persisted = lc.get_life_state(ledger, person)["dialogue_closure"]
    assert persisted["closure_score"] == closure["closure_score"]
    assert persisted["ts_ms"] == closure["ts_ms"]
