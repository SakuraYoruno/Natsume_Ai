from __future__ import annotations

import pytest

from host_brain.persona.motive_engine import MotiveEngine, MotiveInput


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
