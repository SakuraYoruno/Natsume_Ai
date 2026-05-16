from __future__ import annotations

import os
from pathlib import Path

import pytest

from host_brain.app.dependencies import get_bond_neo4j_client
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.neo4j_client import BOND_WRITER_ID, Neo4jBondClient, build_neo4j_bond_client, build_neo4j_dependency_status, build_neo4j_probe_report, load_neo4j_runtime_config, resolve_neo4j_auth_value


def _load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    monkeypatch.setenv('HOST_BOND_BACKEND', 'neo4j')
    for key, value in _load_env_file('.env').items():
        if key.startswith('BOND_NEO4J_'):
            monkeypatch.setenv(key, value)
    yield



def _cleanup_event(client: Neo4jBondClient, *, database: str, event_id: str, user_id: str, session_id: str, topic_id: str, capability_hint: str) -> None:
    with client._driver.session(database=database) as session:
        session.run('MATCH (e:Event {event_id:$event_id}) DETACH DELETE e', event_id=event_id)
        session.run('MATCH (n:User {user_id:$user_id}) DETACH DELETE n', user_id=user_id)
        session.run('MATCH (n:Session {session_id:$session_id}) DETACH DELETE n', session_id=session_id)
        session.run('MATCH (n:Topic {topic_id:$topic_id}) DETACH DELETE n', topic_id=topic_id)
        session.run('MATCH (n:CapabilityHint {capability_hint:$capability_hint}) DETACH DELETE n', capability_hint=capability_hint)



def test_neo4j_prod_client_direct_write_and_fetch() -> None:
    config_result = load_neo4j_runtime_config(local_overrides=_load_env_file('.env'))
    assert config_result.success and config_result.config is not None
    config = config_result.config
    client = build_neo4j_bond_client(config=config)
    assert client is not None

    event_id = 'codex_prod_evt_direct'
    user_id = 'codex_prod_user_direct'
    session_id = 'codex_prod_session_direct'
    topic_id = 'codex_prod_topic_direct'
    capability_hint = 'codex_prod_capability_direct'
    try:
        written = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id=event_id,
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            evidence_refs=['evd://codex/prod/direct'],
            payload={'mode': 'direct'},
            ts_ms=1775120000001,
        )
        fetched = client.get_event(event_id)
        traced = client.trace_user_events(user_id)

        assert written['event_id'] == event_id
        assert fetched is not None and fetched['event_id'] == event_id
        assert any(row['event_id'] == event_id for row in traced)
    finally:
        _cleanup_event(client, database=config.database, event_id=event_id, user_id=user_id, session_id=session_id, topic_id=topic_id, capability_hint=capability_hint)
        client.close()



def test_neo4j_prod_dependencies_and_bond_writer_use_real_client() -> None:
    config_result = load_neo4j_runtime_config(local_overrides=_load_env_file('.env'))
    assert config_result.success and config_result.config is not None
    config = config_result.config
    client = get_bond_neo4j_client()
    assert isinstance(client, Neo4jBondClient)

    event_id = 'codex_prod_evt_writer'
    user_id = 'codex_prod_user_writer'
    session_id = 'codex_prod_session_writer'
    topic_id = 'codex_prod_topic_writer'
    capability_hint = 'codex_prod_capability_writer'
    ledger = SQLiteLedger(db_path=':memory:', check_same_thread=False)
    writer = BondDynamicsWriter(ledger=ledger, neo4j_client=client, stage_engine=BondStageEngine(ledger=ledger))
    try:
        result = writer.apply_update(
            request_id='req-codex-prod-neo4j',
            intent_id='intent-codex-prod-neo4j',
            session_id=session_id,
            seq=1,
            user_id=user_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            evidence_refs=['evd://codex/prod/writer'],
            payload={'source': 'bond_writer'},
            update_kind='EMOTIONAL_SUPPORT',
            event_id=event_id,
            ts_ms=1775120001001,
            emotion_tag='LOVING',
            emotion_intensity=0.92,
        )
        fetched = client.get_event(event_id)

        assert result.success is True
        assert result.event['neo4j_event']['event_id'] == event_id
        assert fetched is not None and fetched['event_id'] == event_id
        assert result.event['stage_transition']['to_stage'] in {'FRIEND', 'CLOSE_FRIEND'}
    finally:
        _cleanup_event(client, database=config.database, event_id=event_id, user_id=user_id, session_id=session_id, topic_id=topic_id, capability_hint=capability_hint)
        client.close()
        ledger.close()



def test_neo4j_prod_config_probe_and_dependency_status() -> None:
    config_result = load_neo4j_runtime_config(local_overrides=_load_env_file('.env'))
    assert config_result.success and config_result.config is not None
    config = config_result.config
    auth_value = resolve_neo4j_auth_value(auth_ref=config.auth_ref)

    ok_probe = build_neo4j_probe_report(config=config, request_id='req-neo4j-probe-ok', probe={'reachable': True, 'auth_ok': True, 'schema_ok': True, 'leader_ok': True, 'detail': 'neo4j probe ok'}, ts_ms=1775120100000)
    auth_fail_probe = build_neo4j_probe_report(config=config, request_id='req-neo4j-probe-auth', probe={'reachable': True, 'auth_ok': False, 'detail': 'invalid neo4j credentials'}, ts_ms=1775120101000)
    schema_fail_probe = build_neo4j_probe_report(config=config, request_id='req-neo4j-probe-schema', probe={'reachable': True, 'auth_ok': True, 'schema_ok': False, 'detail': 'schema drift'}, ts_ms=1775120102000)
    dep_status = build_neo4j_dependency_status(ok_probe)

    assert auth_value
    assert ok_probe['status'] == 'UP'
    assert auth_fail_probe['reason_code'] == 'NEO4J_AUTH_FAILED'
    assert schema_fail_probe['reason_code'] == 'NEO4J_SCHEMA_DRIFT'
    assert dep_status['available'] is True



def test_neo4j_prod_constraint_statements_and_events_ordering() -> None:
    config_result = load_neo4j_runtime_config(local_overrides=_load_env_file('.env'))
    assert config_result.success and config_result.config is not None
    config = config_result.config
    client = build_neo4j_bond_client(config=config)
    assert client is not None

    event_ids = ['codex_prod_evt_order_001', 'codex_prod_evt_order_002']
    user_id = 'codex_prod_user_order'
    session_id = 'codex_prod_session_order'
    topic_id = 'codex_prod_topic_order'
    capability_hint = 'codex_prod_capability_order'
    try:
        client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id=event_ids[0],
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            evidence_refs=['evd://codex/prod/order/1'],
            payload={'order': 1},
            ts_ms=1775120200001,
        )
        client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id=event_ids[1],
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            evidence_refs=['evd://codex/prod/order/2'],
            payload={'order': 2},
            ts_ms=1775120201001,
        )
        events = client.events
        traced = client.trace_user_events(user_id)

        assert client.constraint_statements
        assert any(row['event_id'] == event_ids[1] for row in events)
        assert [row['event_id'] for row in traced[-2:]] == event_ids
    finally:
        for event_id in event_ids:
            _cleanup_event(client, database=config.database, event_id=event_id, user_id=user_id, session_id=session_id, topic_id=topic_id, capability_hint=capability_hint)
        client.close()
