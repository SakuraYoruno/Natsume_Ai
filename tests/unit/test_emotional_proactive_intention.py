"""C2-1 意图驱动的主动动机 单测。

验证：活跃自主项目成熟时 project_nudge 动机参与竞争并在情绪低-中时胜出、开场白 grounded；
self_change_note 触发 share_growth；极度孤独时情绪动机仍压过意图；无意图输入向后兼容。
"""

from __future__ import annotations

from host_brain.persona.emotional_proactive import (
    _project_nudge_pull,
    build_emotional_proactive_episode,
)

NOW = 1_800_000_000_000
RECENT_EMERGENT = {
    "project_id": "emergent_天文",
    "origin": "emergent",
    "status": "active",
    "title": "陪你追天文",
    "theme": "天文",
    "progress": 0.5,
    "next_step": "想确认你最近想看哪个方向",
    "last_worked_at_ms": NOW,
}


def _ep(inner_state, *, active_projects=None, self_change_note="", loneliness=None):
    if loneliness is not None:
        inner_state = {**inner_state, "loneliness": loneliness}
    return build_emotional_proactive_episode(
        inner_state=inner_state,
        bond_summary={"stage": "CLOSE_FRIEND"},
        scene_awareness={},
        now_ms=NOW,
        last_foreground_ts_ms=NOW - 3 * 3600 * 1000,
        last_proactive_outbox_ts_ms=0,
        active_projects=active_projects,
        self_change_note=self_change_note,
    )


# ── 纯函数：项目 nudge 冲动 ──

def test_project_nudge_pull_from_recent_emergent() -> None:
    pull, proj = _project_nudge_pull([RECENT_EMERGENT], NOW)
    assert pull > 0.5
    assert proj["project_id"] == "emergent_天文"


def test_project_nudge_skips_completed_and_nascent() -> None:
    done = {**RECENT_EMERGENT, "progress": 1.0}
    nascent = {**RECENT_EMERGENT, "progress": 0.0}
    assert _project_nudge_pull([done, nascent], NOW)[0] == 0.0


def test_project_nudge_stale_pulls_less_than_recent() -> None:
    stale = {**RECENT_EMERGENT, "last_worked_at_ms": NOW - 20 * 24 * 3600 * 1000}
    assert _project_nudge_pull([stale], NOW)[0] < _project_nudge_pull([RECENT_EMERGENT], NOW)[0]


# ── 动机竞争 ──

def test_project_nudge_wins_when_emotion_low() -> None:
    ep = _ep({}, active_projects=[RECENT_EMERGENT], loneliness=0.2)
    assert ep.motive == "project_nudge"
    assert "天文" in ep.opening_message  # grounded


def test_extreme_loneliness_still_beats_intention() -> None:
    ep = _ep({}, active_projects=[RECENT_EMERGENT], loneliness=0.95)
    assert ep.motive == "lonely_murmur"


def test_share_growth_motive_from_self_change_note() -> None:
    ep = _ep({}, self_change_note="我好像比上个月更容易主动找你了", loneliness=0.1)
    assert ep.motive == "share_growth"
    assert "更容易主动" in ep.opening_message


# ── 向后兼容 ──

def test_no_intention_inputs_backward_compatible() -> None:
    ep = _ep({"loneliness": 0.3})
    assert ep.motive not in {"project_nudge", "share_growth"}
    assert ep.emotional_state["project_nudge_pull"] == 0.0
    assert ep.episode_id


def test_project_nudge_id_in_evidence_when_chosen() -> None:
    ep = _ep({}, active_projects=[RECENT_EMERGENT], loneliness=0.2)
    assert ep.evidence["project_nudge_id"] == "emergent_天文"
