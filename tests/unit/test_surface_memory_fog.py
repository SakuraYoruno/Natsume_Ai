"""B1 活路：内在天气失真偏置让对话召回"变糊"（浮现更少记忆）。

_distorted_max_facts 是核心衰减；_surface_memory_distortion_bias 从 life-state 读偏置（app 层读，作 float 传给召回，不让 memory 反向依赖 runtime）。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import host_brain.app.chat_retrieval_helpers as crh
from host_brain.persona.surface_memory_recall import _distorted_max_facts
from host_brain.runtime import life_continuity as lc


# ── 纯函数：失真削减 max_facts ──

def test_no_fog_keeps_full_facts() -> None:
    assert _distorted_max_facts(4, 0.0) == 4
    assert _distorted_max_facts(4, 0.3) == 4  # 轻雾（<0.4）不动


def test_fog_reduces_facts() -> None:
    assert _distorted_max_facts(4, 0.5) == 3
    assert _distorted_max_facts(4, 0.8) == 2


def test_fog_floor_is_one() -> None:
    assert _distorted_max_facts(1, 1.0) == 1
    assert _distorted_max_facts(2, 1.0) == 1


def test_none_bias_is_safe() -> None:
    assert _distorted_max_facts(4, None) == 4  # type: ignore[arg-type]


# ── 接线：从 life-state 内在天气读偏置 ──

def _seed_ledger(bias: float) -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    state = lc.get_life_state(ledger, "default")
    state["inner_weather"] = {"memory_distortion_bias": bias}
    lc._save_state(ledger, state)
    return ledger


def test_distortion_bias_read_from_life_state(monkeypatch) -> None:
    ledger = _seed_ledger(0.7)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    assert crh._surface_memory_distortion_bias("default") == 0.7


def test_distortion_bias_safe_default_on_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("ledger down")

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", _boom)
    assert crh._surface_memory_distortion_bias("default") == 0.0
