"""B1 活路：内在天气失真偏置让对话召回"变糊"（浮现更少记忆）。

_distorted_max_facts 是核心衰减；_surface_memory_distortion_bias 从 life-state 读偏置（app 层读，作 float 传给召回，不让 memory 反向依赖 runtime）。
"""

from __future__ import annotations

from types import SimpleNamespace

import host_brain.app.chat_retrieval_helpers as crh
from host_brain.persona.surface_memory_recall import _distorted_max_facts


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

def test_distortion_bias_read_from_life_state() -> None:
    reader = SimpleNamespace(
        get_snapshot=lambda person_id: {
            "person_id": person_id,
            "version": 3,
            "state": {
                "person_id": person_id,
                "version": 3,
                "inner_weather": {"memory_distortion_bias": 0.7},
            },
        },
    )
    assert crh._surface_memory_distortion_bias("default", life_snapshot_reader=reader) == 0.7


def test_distortion_bias_safe_default_on_error() -> None:
    def _boom(_person_id):
        raise RuntimeError("snapshot reader down")

    reader = SimpleNamespace(get_snapshot=_boom)
    assert crh._surface_memory_distortion_bias("default", life_snapshot_reader=reader) == 0.0
