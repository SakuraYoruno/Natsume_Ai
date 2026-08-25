from __future__ import annotations

import socket
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.chroma_client import (
    CHROMA_STATUS_UP,
    PersistentChromaClient,
    build_chroma_dependency_status,
    build_chroma_probe_report,
    load_chroma_runtime_config,
)

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


def _require_chroma_runtime(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if not host:
        pytest.skip('Chroma prod_env unavailable: endpoint host is missing')
    try:
        with socket.create_connection((host, port), timeout=0.25):
            pass
    except OSError:
        pytest.skip(f'Chroma prod_env unavailable: {host}:{port} is not reachable')


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    env_values = _load_env_file('.env')
    for key, value in env_values.items():
        if key.startswith(('CHROMA_', 'RETR_')) or key in {'NATSUME_CHROMA_PORT'}:
            monkeypatch.setenv(key, value)
    yield



def test_memory_prod_chroma_runtime_and_probe_status() -> None:
    cfg_result = load_chroma_runtime_config(local_overrides=_load_env_file('.env'))
    assert cfg_result.success and cfg_result.config is not None
    config = cfg_result.config

    probe = build_chroma_probe_report(
        config=config,
        request_id='req-chroma-prod-001',
        probe={
            'reachable': True,
            'collections': {name: {'vector_dim': dim} for name, dim in config.collection_profiles.items()},
            'detail': 'chroma probe ok',
        },
        ts_ms=1775127000000,
    )
    dep = build_chroma_dependency_status(probe)

    assert probe['status'] == CHROMA_STATUS_UP
    assert dep['available'] is True



def test_memory_prod_persistent_chroma_collection_roundtrip() -> None:
    cfg_result = load_chroma_runtime_config(local_overrides=_load_env_file('.env'))
    assert cfg_result.success and cfg_result.config is not None
    _require_chroma_runtime(cfg_result.config.endpoint)
    collection_name = 'codex_memory_hub_' + uuid.uuid4().hex[:8]
    client = PersistentChromaClient(
        endpoint=cfg_result.config.endpoint,
        tenant=cfg_result.config.tenant,
        database=cfg_result.config.database,
        collection_profiles={collection_name: 4},
        default_collection=collection_name,
    )
    memory_id = 'mem_' + uuid.uuid4().hex[:8]
    try:
        client.upsert(memory_id=memory_id, text='memory hub text', embedding=[0.1, 0.2, 0.3, 0.4], metadata={'emotion_tag': 'HAPPY'}, collection=collection_name)
        rows = client.search(query_embedding=[0.1, 0.2, 0.3, 0.4], top_k=3, collection=collection_name)
        fetched = client.get(memory_id, collection=collection_name)

        assert rows and rows[0]['memory_id'] == memory_id
        assert fetched is not None and fetched.memory_id == memory_id
    finally:
        try:
            client.delete(memory_id, collection=collection_name)
            client._client.delete_collection(name=collection_name)
        except Exception:
            pass



def test_memory_prod_episodic_rejects_missing_evidence_and_empty_text() -> None:
    cfg_result = load_chroma_runtime_config(local_overrides=_load_env_file('.env'))
    assert cfg_result.success and cfg_result.config is not None
    _require_chroma_runtime(cfg_result.config.endpoint)
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = EpisodicStore(
            ledger=ledger,
            chroma_client=PersistentChromaClient.from_runtime_config(config),
        )
        missing = store.write_memory(text='should fail', evidence_refs=[])
        empty = store.write_memory(text='   ', evidence_refs=['evd://memory/prod'])

        assert missing.success is False
        assert empty.success is False
        assert missing.reason_code is not None
        assert empty.reason_code is not None
    finally:
        ledger.close()
