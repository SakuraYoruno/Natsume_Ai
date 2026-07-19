from __future__ import annotations

import pytest

from host_brain.app.host_hard_state import HostHardStateOwner


def test_set_sys_mode_rejects_unknown_mode_without_mutating_state_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal",
        clock_ms=lambda: 1,
    )
    original_state = owner.get_hard_state()

    with pytest.raises(ValueError, match="invalid sys_mode"):
        owner.set_sys_mode("debug")

    assert owner.get_hard_state() is original_state
    assert owner.get_hard_state().sys_mode == "normal"
    assert __import__("os").environ["HOST_SYS_MODE"] == "normal"
