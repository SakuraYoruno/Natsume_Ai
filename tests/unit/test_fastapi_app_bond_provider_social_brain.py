from __future__ import annotations

import host_brain.app.bond_routes as bond_routes
import host_brain.app.asgi as asgi


def test_asgi_bond_provider_wired_to_social_brain() -> None:
    assert bond_routes._engine_provider is asgi.resolve_bond_engine


def test_asgi_bond_provider_resolves_stage_engine_from_social_brain(monkeypatch) -> None:
    sentinel = object()

    class _SocialBrain:
        def resolve_stage_engine(self):
            return sentinel

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _SocialBrain(),
    )

    assert asgi.resolve_bond_engine() is sentinel
