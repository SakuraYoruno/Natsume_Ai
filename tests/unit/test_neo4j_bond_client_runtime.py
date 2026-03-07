from __future__ import annotations

import json
import sys
import types

import pytest

from host_brain.bond.neo4j_client import (
    Neo4jRuntimeConfig,
    build_neo4j_bond_client,
    resolve_neo4j_auth_value,
)


def test_resolve_neo4j_auth_value_from_secret_store() -> None:
    value = resolve_neo4j_auth_value(
        auth_ref="sec://neo4j/local-default",
        secret_store={"sec://neo4j/local-default": "neo4j:password123"},
    )
    assert value == "neo4j:password123"


def test_build_neo4j_bond_client_reads_events_via_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRecord:
        def __init__(self, payload: dict) -> None:
            self._payload = dict(payload)

        def data(self) -> dict:
            return dict(self._payload)

    class _FakeSession:
        def __init__(self, rows: list[dict], capture: dict) -> None:
            self._rows = list(rows)
            self._capture = capture

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def run(self, query: str, *, limit: int):
            self._capture["query"] = query
            self._capture["limit"] = int(limit)
            return [_FakeRecord(row) for row in self._rows[: max(1, int(limit))]]

    class _FakeDriver:
        def __init__(self, rows: list[dict], capture: dict) -> None:
            self._rows = list(rows)
            self._capture = capture

        def session(self, *, database: str) -> _FakeSession:
            self._capture["database"] = database
            return _FakeSession(self._rows, self._capture)

        def close(self) -> None:
            return None

    capture: dict = {}
    rows = [
        {
            "event_id": "bond_evt_001",
            "topic_id": "bond_topic",
            "capability_hint": "bond_memory",
            "evidence_refs": ["evd://bond/bond_evt_001"],
            "payload": json.dumps({"summary": "bond payload"}, ensure_ascii=False),
            "ts_ms": 1_800_400_000_001,
        }
    ]

    class _FakeGraphDatabase:
        @staticmethod
        def driver(uri, *, auth, max_connection_pool_size, encrypted):
            capture["uri"] = uri
            capture["auth"] = auth
            capture["pool"] = max_connection_pool_size
            capture["tls"] = encrypted
            return _FakeDriver(rows, capture)

    monkeypatch.setitem(sys.modules, "neo4j", types.SimpleNamespace(GraphDatabase=_FakeGraphDatabase))

    config = Neo4jRuntimeConfig(
        enabled=True,
        uri="bolt://127.0.0.1:7687",
        database="neo4j",
        auth_ref="sec://neo4j/local-default",
        pool_size=50,
        tls_enabled=False,
    )
    client = build_neo4j_bond_client(
        config=config,
        secret_store={"sec://neo4j/local-default": "neo4j:password123"},
    )
    assert client is not None
    events = client.events
    client.close()

    assert capture["uri"] == "bolt://127.0.0.1:7687"
    assert capture["auth"] == ("neo4j", "password123")
    assert capture["database"] == "neo4j"
    assert capture["limit"] == 256
    assert len(events) == 1
    assert events[0]["event_id"] == "bond_evt_001"
    assert events[0]["topic_id"] == "bond_topic"
    assert events[0]["capability_hint"] == "bond_memory"
    assert events[0]["payload"]["summary"] == "bond payload"
