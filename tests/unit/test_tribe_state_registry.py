from __future__ import annotations

from host_brain.tribe.tribe_state import TribeStateRegistry


def test_tribe_state_registry_set_and_get() -> None:
    registry = TribeStateRegistry()

    written = registry.set_state("limbic", {"gate_state": "cautious"}, writer="limbic_agent")
    loaded = registry.get_state("limbic")

    assert written["namespace"] == "limbic"
    assert written["writer"] == "limbic_agent"
    assert isinstance(written["updated_at_ms"], int)
    assert loaded is not None
    assert loaded["state"]["gate_state"] == "cautious"


def test_tribe_state_registry_snapshot_contains_namespaces() -> None:
    registry = TribeStateRegistry()
    registry.set_state("ans", {"tick": 1})
    registry.set_state("hippocampus", {"recall": "ok"})

    snapshot = registry.snapshot()

    assert "ans" in snapshot
    assert "hippocampus" in snapshot
