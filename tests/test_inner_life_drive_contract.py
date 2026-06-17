from __future__ import annotations

from host_brain.runtime.life_continuity import build_life_action_plan


def test_dream_action_requires_residue() -> None:
    state = {
        "life_pressure": {"absence": 0.1, "dream": 0.9, "work": 0.0, "fatigue": 0.1, "relationship": 0.4},
        "inner_world": {"dream_residue": "", "numeric_state": {}},
        "work": {"active": False},
        "affect": {},
    }
    action = build_life_action_plan(state=state, director_pack={"scene": "dream_residue", "dominant_motive": "curious"})
    assert action["action_kind"] != "dream_afterglow"


def test_work_action_requires_live_work_event() -> None:
    state = {
        "life_pressure": {"absence": 0.1, "dream": 0.0, "work": 0.9, "fatigue": 0.1, "relationship": 0.4},
        "inner_world": {"numeric_state": {}},
        "work": {"active": False, "last_event_type": "task_stale"},
        "affect": {},
    }
    action = build_life_action_plan(state=state, director_pack={"scene": "work_waiting", "dominant_motive": "curious_guarded"})
    assert action["action_kind"] != "work_watch"


def test_unanchored_inner_state_becomes_quiet_presence() -> None:
    state = {
        "life_pressure": {"absence": 0.4, "dream": 0.0, "work": 0.0, "fatigue": 0.2, "relationship": 0.4},
        "inner_world": {"numeric_state": {"attention_kind": "diffuse_contact_pressure", "inhibition": 0.75, "monologue_suppression_reason": "NO_CONCRETE_INNER_ANCHOR"}},
        "work": {"active": False},
        "affect": {},
    }
    action = build_life_action_plan(state=state, director_pack={"scene": "idle", "dominant_motive": "wait"})
    assert action["action_kind"] == "quiet_presence"
    assert "proactive" not in action["channels"]
