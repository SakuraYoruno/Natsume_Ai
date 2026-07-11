from __future__ import annotations

import os
from pathlib import Path

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.async_queue import BondUpdateQueue
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondScoreState, BondStageEngine
from host_brain.bond.neo4j_client import Neo4jBondClient, build_neo4j_bond_client, load_neo4j_runtime_config
from host_brain.bond.summarizer import derive_bond_summary, normalize_bond_summary, summary_has_numeric_value


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



def _make_client() -> tuple[Neo4jBondClient, object]:
    config_result = load_neo4j_runtime_config(local_overrides=_load_env_file('.env'))
    if not config_result.success or config_result.config is None:
        pytest.skip(
            'Neo4j prod_env unavailable: '
            f'{config_result.reason_code or "runtime configuration missing"}'
        )
    client = build_neo4j_bond_client(config=config_result.config)
    assert isinstance(client, Neo4jBondClient)
    return client, config_result.config



def _cleanup_event(client: Neo4jBondClient, *, database: str, event_id: str, user_id: str, session_id: str, topic_id: str, capability_hint: str) -> None:
    with client._driver.session(database=database) as session:
        session.run('MATCH (e:Event {event_id:$event_id}) DETACH DELETE e', event_id=event_id)
        session.run('MATCH (n:User {user_id:$user_id}) DETACH DELETE n', user_id=user_id)
        session.run('MATCH (n:Session {session_id:$session_id}) DETACH DELETE n', session_id=session_id)
        session.run('MATCH (n:Topic {topic_id:$topic_id}) DETACH DELETE n', topic_id=topic_id)
        session.run('MATCH (n:CapabilityHint {capability_hint:$capability_hint}) DETACH DELETE n', capability_hint=capability_hint)



def test_bond_async_queue_prod_processes_real_neo4j_write() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    client, config = _make_client()
    ledger = SQLiteLedger(db_path=':memory:', check_same_thread=False)
    event_id = 'codex_prod_queue_evt_001'
    user_id = 'codex_prod_queue_user_001'
    session_id = 'codex_prod_queue_session_001'
    topic_id = 'codex_prod_queue_topic_001'
    capability_hint = 'codex_prod_queue_capability_001'

    def _writer_factory():
        return BondDynamicsWriter(ledger=ledger, neo4j_client=client, stage_engine=BondStageEngine(ledger=ledger))

    queue = BondUpdateQueue(writer_factory=_writer_factory, max_queue_size=8, worker_enabled=True, poll_interval_s=0.02)
    try:
        queued = queue.enqueue({
            'request_id': 'req-queue-prod-001',
            'intent_id': 'intent-queue-prod-001',
            'session_id': session_id,
            'seq': 1,
            'user_id': user_id,
            'topic_id': topic_id,
            'capability_hint': capability_hint,
            'evidence_refs': ['evd://queue/prod/1'],
            'payload': {'source': 'queue'},
            'update_kind': 'EMOTIONAL_SUPPORT',
            'event_id': event_id,
            'ts_ms': 1775121000001,
            'emotion_tag': 'LOVING',
            'emotion_intensity': 0.95,
        })
        drained = queue.drain(timeout_s=5.0)
        snapshot = queue.snapshot()
        fetched = client.get_event(event_id)

        assert queued['status'] == 'QUEUED'
        assert drained is True
        assert snapshot['processed_count'] >= 1
        assert fetched is not None and fetched['event_id'] == event_id
    finally:
        queue.shutdown()
        _cleanup_event(client, database=config.database, event_id=event_id, user_id=user_id, session_id=session_id, topic_id=topic_id, capability_hint=capability_hint)
        client.close()
        ledger.close()



def test_bond_async_queue_prod_full_snapshot_when_worker_disabled() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    queue = BondUpdateQueue(writer_factory=lambda: None, max_queue_size=1, worker_enabled=False)
    try:
        first = queue.enqueue({'request_id': 'req-queue-full-1'})
        second = queue.enqueue({'request_id': 'req-queue-full-2'})
        snapshot = queue.snapshot()

        assert first['status'] == 'QUEUED'
        assert second['status'] == 'DEGRADED'
        assert second['reason_code'] == 'BOND_QUEUE_FULL'
        assert snapshot['dropped_count'] >= 1
    finally:
        queue.shutdown()



def test_bond_summarizer_prod_with_real_event_and_state() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    client, config = _make_client()
    event_id = 'codex_prod_summary_evt_001'
    user_id = 'codex_prod_summary_user_001'
    session_id = 'codex_prod_summary_session_001'
    topic_id = 'codex_prod_summary_topic_001'
    capability_hint = 'codex_prod_summary_capability_001'
    try:
        written = client.write_bond_event(
            writer_id='host_brain.bond.bond_dynamics',
            event_id=event_id,
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            evidence_refs=['evd://summary/prod/1'],
            payload={'kind': 'summary'},
            ts_ms=1775121001001,
        )
        state = BondScoreState(user_id=user_id, current_stage='FRIEND', total_score=30, recent_scores=[2, 2, 2], event_count=3)
        summary = derive_bond_summary(written, bond_score_state=state)
        normalized = normalize_bond_summary(summary)

        assert summary['stage'] == 'FRIEND'
        assert summary['trend'] in {'上升', '快速上升'}
        assert normalized['capability_hint']
        assert summary_has_numeric_value(summary) is False
    finally:
        _cleanup_event(client, database=config.database, event_id=event_id, user_id=user_id, session_id=session_id, topic_id=topic_id, capability_hint=capability_hint)
        client.close()
