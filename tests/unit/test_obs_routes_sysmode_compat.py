from __future__ import annotations

import host_brain.app.obs_routes as obs_routes
import host_brain.app.obs_routes_sysmode as obs_routes_sysmode


def test_obs_routes_exports_sysmode_singleton_handles() -> None:
    assert obs_routes._mode_transition_lock is obs_routes_sysmode._mode_transition_lock
    assert obs_routes._mode_transition_service is obs_routes_sysmode._mode_transition_service

