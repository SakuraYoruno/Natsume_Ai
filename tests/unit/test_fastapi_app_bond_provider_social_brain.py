from __future__ import annotations

import host_brain.app.bond_routes as bond_routes
import host_brain.app.asgi as asgi


def test_asgi_bond_provider_wired_to_canonical_composition() -> None:
    assert bond_routes._engine_provider is asgi.resolve_bond_engine


def test_asgi_bond_provider_resolves_stage_engine_from_canonical_composition(monkeypatch) -> None:
    sentinel = object()

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_bond_stage_engine",
        lambda: sentinel,
    )

    assert asgi.resolve_bond_engine() is sentinel
