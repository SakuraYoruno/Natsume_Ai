from __future__ import annotations

import pytest

from host_brain.persona.offline_continuity import (
    absence_recovery_factor_for_absence_band,
    reconnect_style_for_absence_band,
    resolve_absence_band,
    warmth_floor_for_absence_band,
)


@pytest.mark.parametrize(
    ("offline_state", "expected_band"),
    [
        ({"offline_hours": 2.0}, "recent"),
        ({"offline_hours": 8.0}, "6h_plus"),
        ({"offline_hours": 26.0}, "24h_plus"),
        ({"offline_hours": 80.0}, "72h_plus"),
        ({"offline_duration_ms": 8 * 24 * 3600 * 1000}, "7d_plus"),
        ({"absence_band": "24h_plus", "offline_hours": 80.0}, "24h_plus"),
    ],
)
def test_resolve_absence_band_prefers_explicit_band_and_shared_thresholds(
    offline_state: dict[str, float | str],
    expected_band: str,
) -> None:
    assert resolve_absence_band(offline_state) == expected_band


def test_shared_offline_semantics_align_reconnect_warmth_and_recovery() -> None:
    assert reconnect_style_for_absence_band("recent") == ""
    assert reconnect_style_for_absence_band("6h_plus") == "soft_resume"
    assert reconnect_style_for_absence_band("24h_plus") == "light_reconnect"
    assert reconnect_style_for_absence_band("72h_plus") == "reunion"
    assert reconnect_style_for_absence_band("7d_plus") == "deep_reunion"

    assert warmth_floor_for_absence_band("6h_plus") == pytest.approx(0.08, abs=1e-6)
    assert warmth_floor_for_absence_band("24h_plus") == pytest.approx(0.15, abs=1e-6)
    assert warmth_floor_for_absence_band("72h_plus") == pytest.approx(0.18, abs=1e-6)
    assert warmth_floor_for_absence_band("7d_plus") == pytest.approx(0.22, abs=1e-6)

    assert absence_recovery_factor_for_absence_band("6h_plus") == pytest.approx(1.03, abs=1e-6)
    assert absence_recovery_factor_for_absence_band("24h_plus") == pytest.approx(1.08, abs=1e-6)
    assert absence_recovery_factor_for_absence_band("72h_plus") == pytest.approx(1.14, abs=1e-6)
    assert absence_recovery_factor_for_absence_band("7d_plus") == pytest.approx(1.20, abs=1e-6)
