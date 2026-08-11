from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from host_brain.composition.host_memory_runtime import (
    HostMemoryRuntimeOwner,
    HostSemanticRecallSettings,
)


ROOT = Path(__file__).resolve().parents[2]


class _Ledger:
    canonical_db_path = ":memory:"

    def __init__(self) -> None:
        self.memory_rows: list[dict[str, Any]] = []
        self.error_rows: list[dict[str, Any]] = []

    def log_memory_op(self, *, row: dict[str, Any]) -> None:
        self.memory_rows.append(dict(row))

    def log_error(self, **row: Any) -> None:
        self.error_rows.append(dict(row))


def _settings(*, vector_dim: int = 7) -> HostSemanticRecallSettings:
    return HostSemanticRecallSettings(
        collection_name="host-memory",
        top_k=3,
        similarity_threshold=0.75,
        use_mock=True,
        chroma_host="127.0.0.1",
        chroma_port=18000,
        vector_dim=vector_dim,
    )


def _build_owner(
    *,
    ledger: _Ledger | None = None,
    ledger_provider=None,
    semantic_settings_provider=None,
    embedder=None,
) -> tuple[HostMemoryRuntimeOwner, _Ledger, dict[str, list[dict[str, Any]]]]:
    exact_ledger = ledger or _Ledger()
    calls: dict[str, list[dict[str, Any]]] = {
        "memory_guard": [],
        "state_arbiter": [],
        "state_write_arbiter": [],
        "episodic_store": [],
        "distillation_engine": [],
        "semantic_recaller": [],
    }

    def factory(name: str):
        def build(**kwargs: Any) -> Any:
            calls[name].append(dict(kwargs))
            return SimpleNamespace(kind=name, **kwargs)

        return build

    owner = HostMemoryRuntimeOwner(
        ledger_provider=ledger_provider or (lambda: exact_ledger),
        memory_guard_factory=factory("memory_guard"),
        state_arbiter_factory=factory("state_arbiter"),
        state_write_arbiter_factory=factory("state_write_arbiter"),
        episodic_store_adapter_factory=factory("episodic_store"),
        distillation_engine_factory=factory("distillation_engine"),
        semantic_recaller_factory=factory("semantic_recaller"),
        semantic_settings_provider=semantic_settings_provider or _settings,
        embedder=embedder or (lambda _text, *, dim: ([0.0] * dim, "test")),
        clock_ms=lambda: 1_900_000_000_000,
    )
    return owner, exact_ledger, calls


def test_owner_reuses_each_exact_host_object_and_never_crosses_host_roots() -> None:
    first, _first_ledger, _first_calls = _build_owner()
    second, _second_ledger, _second_calls = _build_owner()

    providers = (
        "require_memory_guard",
        "require_state_arbiter",
        "require_state_write_arbiter",
        "require_distillation_engine",
        "require_semantic_recaller",
        "require_emotion_timeline",
    )
    for provider_name in providers:
        first_provider = getattr(first, provider_name)
        second_provider = getattr(second, provider_name)
        first_value = first_provider()
        assert first_provider() is first_value
        assert second_provider() is not first_value


def test_owner_binds_guard_arbiters_distillation_and_timeline_to_one_ledger() -> None:
    ledger_calls: list[_Ledger] = []
    ledger = _Ledger()
    owner, _ledger, calls = _build_owner(
        ledger=ledger,
        ledger_provider=lambda: ledger_calls.append(ledger) or ledger,
    )

    guard = owner.require_memory_guard()
    state_arbiter = owner.require_state_arbiter()
    state_write_arbiter = owner.require_state_write_arbiter()
    engine = owner.require_distillation_engine()
    owner.require_emotion_timeline()

    assert ledger_calls == [ledger]
    assert calls["episodic_store"] == [
        {"memory_guard": guard, "ledger": ledger}
    ]
    assert engine.episodic_store is not None
    assert engine.memory_guard is guard
    assert engine.state_arbiter is state_arbiter
    assert engine.ledger is ledger
    assert state_write_arbiter is owner.require_state_write_arbiter()


def test_owner_audit_callbacks_write_only_to_the_bound_ledger() -> None:
    owner, ledger, _calls = _build_owner()

    owner.require_memory_guard().audit_writer(
        "MEMORY_ACCEPTED",
        {"request_id": "request-memory", "value": 1},
    )
    asyncio.run(
        owner.require_state_arbiter().audit_writer(
            "STATE_SELECTED",
            {"request_id": "request-state"},
        )
    )
    owner.require_state_write_arbiter().audit_writer(
        "STATE_WRITE_REJECTED",
        {
            "request_id": "request-write",
            "reason_code": "STATE_OWNER_MISMATCH",
        },
    )

    assert ledger.memory_rows == [
        {
            "op_type": "MEMORY_ACCEPTED",
            "request_id": "request-memory",
            "value": 1,
        }
    ]
    assert ledger.error_rows == [
        {
            "request_id": "request-state",
            "stage": "STATE_SELECTED",
            "reason_code": "STATE_SELECTED",
            "detail": "{'request_id': 'request-state'}",
        },
        {
            "request_id": "request-write",
            "stage": "STATE_WRITE_REJECTED",
            "reason_code": "STATE_OWNER_MISMATCH",
            "detail": (
                "{'request_id': 'request-write', "
                "'reason_code': 'STATE_OWNER_MISMATCH'}"
            ),
        },
    ]


def test_semantic_settings_and_embedding_dimension_are_frozen_per_owner() -> None:
    settings_calls: list[int] = []
    embed_calls: list[tuple[str, int]] = []

    def settings_provider() -> HostSemanticRecallSettings:
        settings_calls.append(1)
        return _settings(vector_dim=77)

    def embedder(text: str, *, dim: int):
        embed_calls.append((text, dim))
        return ([0.25] * dim, "semantic-test")

    owner, _ledger, calls = _build_owner(
        semantic_settings_provider=settings_provider,
        embedder=embedder,
    )

    recaller = owner.require_semantic_recaller()
    assert owner.require_semantic_recaller() is recaller
    assert settings_calls == [1]
    assert calls["semantic_recaller"] == [
        {
            "collection_name": "host-memory",
            "top_k": 3,
            "similarity_threshold": 0.75,
            "use_mock": True,
            "chroma_host": "127.0.0.1",
            "chroma_port": 18000,
            "embed_fn": recaller.embed_fn,
        }
    ]
    assert recaller.embed_fn("夏目") == [0.25] * 77
    assert embed_calls == [("夏目", 77)]


def test_host_memory_runtime_has_no_process_locator_fallback() -> None:
    owner_path = ROOT / "host_brain" / "composition" / "host_memory_runtime.py"
    owner_source = owner_path.read_text(encoding="utf-8")
    owner_tree = ast.parse(owner_source, filename=str(owner_path))
    forbidden_modules = {
        "host_brain.composition.process_services",
        "host_brain.composition.process_runtime_services",
        "host_brain.composition.process_environment",
        "host_brain.composition.process_service_container",
    }

    assert not any(
        isinstance(node, ast.ImportFrom) and node.module in forbidden_modules
        for node in ast.walk(owner_tree)
    )
    assert not any(
        isinstance(node, ast.Import)
        and any(alias.name in forbidden_modules for alias in node.names)
        for node in ast.walk(owner_tree)
    )
    assert all(
        token not in owner_source
        for token in (
            "get_memory_guard",
            "get_state_arbiter",
            "get_state_write_arbiter",
            "get_distillation_engine",
            "get_semantic_recaller",
            "get_emotion_timeline",
        )
    )

    host_source = (
        ROOT / "host_brain" / "composition" / "host_application.py"
    ).read_text(encoding="utf-8")
    assert "HostMemoryRuntimeOwner" in host_source
    assert "process_services.get_memory_guard" not in host_source
    assert "process_services.get_state_arbiter" not in host_source
    assert "process_services.get_state_write_arbiter" not in host_source
    assert "process_services.get_distillation_engine" not in host_source
    assert "process_services.get_semantic_recaller" not in host_source
    assert "process_services.get_emotion_timeline" not in host_source
