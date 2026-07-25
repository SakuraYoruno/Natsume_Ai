from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.bond.neo4j_client import (
    InMemoryNeo4jBondClient,
    SqliteBondClient,
)
from host_brain.composition.host_bond_runtime import (
    HostBondRuntimeCloseError,
    HostBondRuntimeOwner,
    HostBondRuntimeSettings,
    build_host_bond_client,
    load_host_bond_runtime_settings,
)


def _settings() -> HostBondRuntimeSettings:
    return HostBondRuntimeSettings(
        stage_thresholds={"FRIEND": 0, "CONFIDANT": 10},
        score_map={"INTERACTION_ENGAGED": 2},
        max_queue_size=12,
        worker_enabled=True,
    )


def test_host_bond_runtime_owner_reuses_exact_epoch_and_reopens_after_close() -> None:
    ledger = object()
    created_clients: list[SimpleNamespace] = []
    created_queues: list[SimpleNamespace] = []

    def client_factory() -> SimpleNamespace:
        client = SimpleNamespace(closed=False)
        client.close = lambda: setattr(client, "closed", True)
        created_clients.append(client)
        return client

    def queue_factory(**kwargs):
        queue = SimpleNamespace(
            **kwargs,
            drain=lambda **_call: True,
            shutdown=lambda **_call: True,
        )
        created_queues.append(queue)
        return queue

    owner = HostBondRuntimeOwner(
        ledger_provider=lambda: ledger,
        stage_engine_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        dynamics_writer_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        update_queue_factory=queue_factory,
        client_factory=client_factory,
        settings_provider=_settings,
    )

    with pytest.raises(RuntimeError, match="HOST_BOND_RUNTIME_NOT_ACTIVE"):
        owner.require_stage_engine()

    owner.begin()
    stage = owner.require_stage_engine()
    client = owner.require_client()
    writer = owner.require_writer()
    queue = owner.require_update_queue()

    assert owner.epoch == 1
    assert owner.active is True
    assert owner.require_stage_engine() is stage
    assert owner.require_client() is client
    assert owner.require_writer() is writer
    assert owner.require_update_queue() is queue
    assert stage.ledger is ledger
    assert stage.stage_thresholds == {"FRIEND": 0, "CONFIDANT": 10}
    assert writer.ledger is ledger
    assert writer.neo4j_client is client
    assert writer.stage_engine is stage
    assert queue.writer_factory() is writer
    assert queue.max_queue_size == 12
    assert queue.worker_enabled is True

    owner.close()
    assert client.closed is True
    assert owner.active is False
    with pytest.raises(RuntimeError, match="HOST_BOND_RUNTIME_NOT_ACTIVE"):
        owner.require_update_queue()

    owner.begin()
    assert owner.epoch == 2
    assert owner.require_client() is not client
    assert owner.require_stage_engine() is not stage
    assert owner.require_update_queue() is not queue
    owner.close()
    assert len(created_clients) == 2
    assert len(created_queues) == 2


def test_host_bond_runtime_close_failure_preserves_retry_fence() -> None:
    queue = SimpleNamespace(
        drain=lambda **_kwargs: False,
        shutdown=lambda **_kwargs: True,
    )
    owner = HostBondRuntimeOwner(
        ledger_provider=object,
        stage_engine_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        dynamics_writer_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        update_queue_factory=lambda **_kwargs: queue,
        client_factory=object,
        settings_provider=_settings,
    )
    owner.begin()
    assert owner.require_update_queue() is queue

    with pytest.raises(HostBondRuntimeCloseError) as raised:
        owner.close()

    assert raised.value.failures == ("BOND_QUEUE_DRAIN_TIMEOUT",)
    assert owner.active is False
    with pytest.raises(
        RuntimeError,
        match="HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.begin()


def test_host_bond_runtime_closes_queue_before_client_and_retries_exact_owner() -> None:
    events: list[str] = []
    close_attempts = 0

    def close_client() -> None:
        nonlocal close_attempts
        close_attempts += 1
        events.append(f"client:{close_attempts}")
        if close_attempts == 1:
            raise RuntimeError("injected close failure")

    client = SimpleNamespace(close=close_client)
    queue = SimpleNamespace(
        drain=lambda **_kwargs: events.append("queue:drain") or True,
        shutdown=lambda **_kwargs: events.append("queue:shutdown") or True,
    )
    owner = HostBondRuntimeOwner(
        ledger_provider=object,
        stage_engine_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        dynamics_writer_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        update_queue_factory=lambda **_kwargs: queue,
        client_factory=lambda: client,
        settings_provider=_settings,
    )
    owner.begin()
    assert owner.require_update_queue() is queue
    assert owner.require_client() is client

    with pytest.raises(HostBondRuntimeCloseError) as raised:
        owner.close()

    assert raised.value.failures == ("BOND_CLIENT_CLOSE_FAILED",)
    assert events == ["queue:drain", "queue:shutdown", "client:1"]
    with pytest.raises(
        RuntimeError,
        match="HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.begin()

    owner.close()
    assert events == [
        "queue:drain",
        "queue:shutdown",
        "client:1",
        "queue:drain",
        "queue:shutdown",
        "client:2",
    ]
    assert owner.active is False


def test_host_bond_runtime_settings_are_frozen_from_explicit_environment() -> None:
    settings = load_host_bond_runtime_settings(
        env={
            "HOST_BOND_STAGE_THRESHOLDS": '{"CONFIDANT":150}',
            "HOST_BOND_SCORE_MAP": '{"INTERACTION_ENGAGED":7}',
            "HOST_BOND_QUEUE_MAX_SIZE": "3",
            "HOST_BOND_QUEUE_ENABLED": "false",
        }
    )

    assert settings.stage_thresholds["CONFIDANT"] == 150
    assert settings.score_map["INTERACTION_ENGAGED"] == 7
    assert settings.max_queue_size == 8
    assert settings.worker_enabled is False


def test_host_bond_client_builder_supports_memory_and_migrated_sqlite(
    tmp_path,
) -> None:
    memory_client = build_host_bond_client(
        env={"HOST_BOND_BACKEND": "memory"}
    )
    assert isinstance(memory_client, InMemoryNeo4jBondClient)

    db_path = tmp_path / "bond-owner.sqlite3"
    sqlite_client = build_host_bond_client(
        env={
            "HOST_BOND_BACKEND": "sqlite",
            "HOST_BOND_SQLITE_PATH": str(db_path),
        }
    )
    try:
        assert isinstance(sqlite_client, SqliteBondClient)
        assert db_path.exists()
    finally:
        sqlite_client.close()
