from __future__ import annotations

from host_brain.app.settings.core import get_core_settings


def test_ne01_runtime_graph_flags_default_closed(monkeypatch) -> None:
    monkeypatch.delenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", raising=False)
    get_core_settings.cache_clear()

    settings = get_core_settings()

    assert settings.task_runtime_bounded_fanout_enabled is False
    assert settings.task_runtime_atomic_graph_success_enabled is False


def test_ne01_runtime_graph_flags_are_explicitly_composed_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", "true")
    monkeypatch.setenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", "1")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANARY_RECEIPT", "task-canary://ne01/receipt")
    get_core_settings.cache_clear()

    settings = get_core_settings()

    assert settings.task_runtime_bounded_fanout_enabled is True
    assert settings.task_runtime_atomic_graph_success_enabled is True


def test_ne01_runtime_graph_flags_require_canary_receipt(monkeypatch) -> None:
    monkeypatch.setenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", "true")
    monkeypatch.delenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_CANARY_RECEIPT", raising=False)
    get_core_settings.cache_clear()

    import pytest

    with pytest.raises(ValueError, match="CANARY_RECEIPT"):
        get_core_settings()
