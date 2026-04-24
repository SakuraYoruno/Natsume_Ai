from __future__ import annotations

import host_brain.app.dependencies as deps


class _Store:
    pass


class _TurnStore:
    pass


def test_prewarm_reply_surface_memory_store_creates_and_primes_once(monkeypatch) -> None:
    store = _Store()
    turn_store = _TurnStore()
    calls: list[dict] = []

    monkeypatch.setattr(deps, "_reply_surface_memory_store", None, raising=False)
    monkeypatch.setattr(deps, "_reply_surface_memory_primed", False, raising=False)
    monkeypatch.setattr(deps, "create_reply_surface_memory_store", lambda: store)
    monkeypatch.setattr(deps, "get_conversation_turn_store", lambda: turn_store)

    def _fake_prime(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return 3

    monkeypatch.setattr(deps, "prime_reply_surface_memory_from_turn_history", _fake_prime)

    assert deps.prewarm_reply_surface_memory_store(limit=99) == 3
    assert deps.prewarm_reply_surface_memory_store(limit=99) == 0
    assert deps.get_reply_surface_memory_store() is store
    assert len(calls) == 1
    assert calls[0]["memory_store"] is store
    assert calls[0]["turn_store"] is turn_store
    assert calls[0]["limit"] == 99


def test_prewarm_reply_surface_memory_store_force_reloads_history(monkeypatch) -> None:
    store = _Store()
    turn_store = _TurnStore()
    calls: list[dict] = []

    monkeypatch.setattr(deps, "_reply_surface_memory_store", store, raising=False)
    monkeypatch.setattr(deps, "_reply_surface_memory_primed", True, raising=False)
    monkeypatch.setattr(deps, "get_conversation_turn_store", lambda: turn_store)

    def _fake_prime(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return 2

    monkeypatch.setattr(deps, "prime_reply_surface_memory_from_turn_history", _fake_prime)

    assert deps.prewarm_reply_surface_memory_store(force=True, limit=12) == 2
    assert len(calls) == 1
    assert calls[0]["limit"] == 12


def test_prewarm_reply_surface_memory_store_degrades_cleanly(monkeypatch) -> None:
    store = _Store()

    monkeypatch.setattr(deps, "_reply_surface_memory_store", store, raising=False)
    monkeypatch.setattr(deps, "_reply_surface_memory_primed", False, raising=False)
    monkeypatch.setattr(
        deps,
        "prime_reply_surface_memory_from_turn_history",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )

    assert deps.prewarm_reply_surface_memory_store(limit=5) == 0
    assert deps._reply_surface_memory_primed is True
