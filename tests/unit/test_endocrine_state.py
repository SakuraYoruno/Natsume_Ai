from __future__ import annotations

from host_brain.core.endocrine import derive_endocrine_state
from host_brain.core.metabolism import normalize_hard_state


def test_derive_endocrine_state_returns_expected_keys() -> None:
    hard_state = normalize_hard_state(
        {
            "energy_level": 66,
            "mood_state": "engaged",
            "sys_mode": "normal",
            "safety_flag": False,
            "ts_ms": 1,
        }
    )
    endocrine = derive_endocrine_state(hard_state=hard_state).as_dict()
    assert set(endocrine.keys()) == {
        "dopamine_level",
        "serotonin_level",
        "cortisol_level",
        "energy_reserve",
        "gate_state",
        "source",
    }
    assert endocrine["gate_state"] in {"open", "cautious", "protective"}


def test_critical_low_energy_enters_protective_gate() -> None:
    hard_state = normalize_hard_state(
        {
            "energy_level": 10,
            "mood_state": "critical",
            "sys_mode": "locked",
            "safety_flag": True,
            "ts_ms": 1,
        }
    )
    endocrine = derive_endocrine_state(hard_state=hard_state).as_dict()
    assert endocrine["gate_state"] == "protective"
    assert endocrine["cortisol_level"] >= 0.78
