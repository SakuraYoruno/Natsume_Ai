from __future__ import annotations

import json

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


def test_tribe_state_registry_inline_persist_has_min_interval(tmp_path, monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr("host_brain.tribe.tribe_state.time.monotonic", lambda: clock["now"])
    path = tmp_path / "tribe_state_snapshot.json"
    registry = TribeStateRegistry(
        state_path=path,
        persist_throttle=2,
        persist_min_interval_s=30.0,
        persistence_enabled=True,
    )

    registry.set_state("limbic", {"tick": 1})
    registry.set_state("limbic", {"tick": 2})
    first = json.loads(path.read_text(encoding="utf-8"))
    first_saved_at = first["saved_at_ms"]

    registry.set_state("limbic", {"tick": 3})
    registry.set_state("limbic", {"tick": 4})
    second = json.loads(path.read_text(encoding="utf-8"))

    assert second["saved_at_ms"] == first_saved_at
    assert second["namespaces"]["limbic"]["state"]["tick"] == 2

    clock["now"] += 31.0
    registry.set_state("limbic", {"tick": 5})
    registry.set_state("limbic", {"tick": 6})
    third = json.loads(path.read_text(encoding="utf-8"))

    assert third["saved_at_ms"] >= first_saved_at
    assert third["namespaces"]["limbic"]["state"]["tick"] == 6


def test_default_registry_is_transient_and_never_writes_snapshot(tmp_path) -> None:
    path = tmp_path / "tribe_state_snapshot.json"
    registry = TribeStateRegistry(
        state_path=path,
        persist_throttle=1,
        persist_min_interval_s=0.0,
    )

    registry.set_state("limbic", {"tick": 1})

    assert registry.save_snapshot() is False
    assert registry.load_snapshot() is False
    assert path.exists() is False
