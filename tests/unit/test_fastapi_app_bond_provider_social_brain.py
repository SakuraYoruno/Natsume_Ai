from __future__ import annotations

import inspect

import host_brain.app.bond_routes as bond_routes
import host_brain.app.asgi as asgi


def test_asgi_bond_provider_wired_to_canonical_composition() -> None:
    assert not hasattr(bond_routes, "_engine_provider")
    source = inspect.getsource(asgi.create_app)
    assert "bond_engine_provider=resolve_bond_engine" in source


def test_asgi_bond_provider_resolves_stage_engine_from_canonical_composition(monkeypatch) -> None:
    sentinel = object()

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_bond_stage_engine",
        lambda: sentinel,
    )

    assert asgi.resolve_bond_engine() is sentinel
