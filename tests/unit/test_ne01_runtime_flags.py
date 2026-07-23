from __future__ import annotations

import base64
import json

import pytest

from host_brain.app.settings.core import get_core_settings


def test_ne01_atomic_graph_success_defaults_on_while_fanout_stays_closed(monkeypatch) -> None:
    monkeypatch.delenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_CANARY_RECEIPT", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_BUILD_COMMIT_SHA", raising=False)
    monkeypatch.delenv(
        "HOST_TASK_RUNTIME_CANARY_ED25519_PUBLIC_KEYS_JSON",
        raising=False,
    )
    get_core_settings.cache_clear()

    settings = get_core_settings()

    assert settings.task_runtime_bounded_fanout_enabled is False
    assert settings.task_runtime_atomic_graph_success_enabled is True
    assert settings.task_runtime_fanout_event_buffer_size == 32


def test_ne01_runtime_graph_flags_are_explicitly_composed_from_environment(monkeypatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
    monkeypatch.setenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", "true")
    monkeypatch.setenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", "1")
    monkeypatch.setenv(
        "HOST_TASK_RUNTIME_CANARY_RECEIPT",
        "task-runtime-canary://sha256/" + "a" * 64,
    )
    monkeypatch.setenv("HOST_TASK_RUNTIME_BUILD_COMMIT_SHA", "b" * 40)
    monkeypatch.setenv(
        "HOST_TASK_RUNTIME_CANARY_ED25519_PUBLIC_KEYS_JSON",
        json.dumps({"task-runtime-canary-v1": "ed25519:" + key}),
    )
    get_core_settings.cache_clear()

    settings = get_core_settings()

    assert settings.task_runtime_bounded_fanout_enabled is True
    assert settings.task_runtime_atomic_graph_success_enabled is True
    assert settings.task_runtime_canary_public_keys() == {
        "task-runtime-canary-v1": b"k" * 32
    }


def test_ne01_runtime_graph_flags_require_canary_receipt(monkeypatch) -> None:
    monkeypatch.setenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", "true")
    monkeypatch.delenv("HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_CANARY_RECEIPT", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_BUILD_COMMIT_SHA", raising=False)
    monkeypatch.delenv(
        "HOST_TASK_RUNTIME_CANARY_ED25519_PUBLIC_KEYS_JSON",
        raising=False,
    )
    get_core_settings.cache_clear()

    with pytest.raises(ValueError, match="DURABLE_CANARY_RECEIPT"):
        get_core_settings()


def test_ne01_runtime_graph_flags_reject_an_unbound_receipt_string(
    monkeypatch,
) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
    monkeypatch.setenv("HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED", "true")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANARY_RECEIPT", "task-canary://legacy")
    monkeypatch.setenv("HOST_TASK_RUNTIME_BUILD_COMMIT_SHA", "b" * 40)
    monkeypatch.setenv(
        "HOST_TASK_RUNTIME_CANARY_ED25519_PUBLIC_KEYS_JSON",
        json.dumps({"task-runtime-canary-v1": "ed25519:" + key}),
    )
    get_core_settings.cache_clear()

    with pytest.raises(ValueError, match="DURABLE_CANARY_RECEIPT"):
        get_core_settings()
