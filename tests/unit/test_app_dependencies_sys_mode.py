from __future__ import annotations

import pytest

from host_brain.core.metabolism import HardState


def test_set_sys_mode_rejects_unknown_mode(monkeypatch) -> None:
    import host_brain.app.dependencies as deps

    original_state = deps._hard_state
    try:
        deps._hard_state = HardState(
            energy_level=60,
            mood_state="steady",
            sys_mode="normal",
            safety_flag=False,
            last_event_id=None,
            ts_ms=1,
        )
        monkeypatch.setenv("HOST_SYS_MODE", "normal")

        with pytest.raises(ValueError, match="invalid sys_mode"):
            deps.set_sys_mode("debug")

        assert deps._hard_state.sys_mode == "normal"
        assert deps.get_hard_state().sys_mode == "normal"
    finally:
        deps._hard_state = original_state
