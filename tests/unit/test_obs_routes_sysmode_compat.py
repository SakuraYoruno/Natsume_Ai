from __future__ import annotations

import host_brain.app.obs_routes as obs_routes
def test_obs_routes_does_not_export_sysmode_singleton_handles() -> None:
    assert not hasattr(obs_routes, "_mode_transition_lock")
    assert not hasattr(obs_routes, "_mode_transition_service")
