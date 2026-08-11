from __future__ import annotations

import pytest

from host_brain.persona.motive_engine import MotiveEngine, MotiveInput, get_motive_engine


@pytest.mark.parametrize(
    ("willingness", "expected_withdraw"),
    [
        (0.0, 0.60),
        (0.20, 0.45),
        (0.50, 0.25),
        (0.70, 0.10),
        (1.00, 0.00),
    ],
)
def test_willingness_withdraw_mapping_is_monotonic(
    willingness: float,
    expected_withdraw: float,
) -> None:
    state = MotiveEngine().compute(
        MotiveInput(silence_state={"response_willingness": willingness})
    )

    assert state.withdraw_drive == expected_withdraw


def test_motive_engine_factory_has_no_process_global_state() -> None:
    """动机只是输入快照的纯投影，不能成为跨 turn 的情感 authority。"""

    first = get_motive_engine()
    second = get_motive_engine()
    input_snapshot = MotiveInput(
        silence_state={"response_willingness": 0.5},
        body_sense={"fatigue_level": "WEARY"},
        offline_state={"offline_hours": 9.0},
        bond_summary={"stage": "CLOSE"},
    )

    assert first is not second
    assert first.compute(input_snapshot).as_dict() == second.compute(input_snapshot).as_dict()
