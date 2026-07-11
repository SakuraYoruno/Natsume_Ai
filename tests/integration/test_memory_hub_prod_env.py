from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import host_brain.app.dependencies as deps
from host_brain.memory.chroma_client import (
    CHROMA_STATUS_UP,
    PersistentChromaClient,
    build_chroma_dependency_status,
    build_chroma_probe_report,
    load_chroma_runtime_config,
)
from host_brain.skills.hub.hub_installer import install_skill, list_installed_skills, remove_skill, search_remote_skills_with_status



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
        if key.startswith(('CHROMA_', 'RETR_', 'HOST_SKILL_')) or key in {'NATSUME_CHROMA_PORT'}:
            monkeypatch.setenv(key, value)
    for name in ['_episodic_store']:
        if hasattr(deps, name):
            setattr(deps, name, None)
    yield
    for name in ['_episodic_store']:
        if hasattr(deps, name):
            setattr(deps, name, None)



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
    store = deps.get_episodic_store()

    missing = store.write_memory(text='should fail', evidence_refs=[])
    empty = store.write_memory(text='   ', evidence_refs=['evd://memory/prod'])

    assert missing.success is False
    assert empty.success is False
    assert missing.reason_code is not None
    assert empty.reason_code is not None




def test_hub_prod_local_install_list_remove_roundtrip(tmp_path) -> None:
    hub_dir = tmp_path / 'skills'
    src = tmp_path / 'demo_local_skill'
    src.mkdir(parents=True)
    (src / 'SKILL.md').write_text(
        "---\n"
        "name: \"demo-local-skill\"\n"
        "version: \"0.1.0\"\n"
        "description: \"demo local skill\"\n"
        "author: \"tester\"\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "reversible: true\n"
        "require_confirm: false\n"
        "scheduler_allowed: true\n"
        "tool: web_search\n"
        "---\n",
        encoding='utf-8',
    )

    installed = install_skill(str(src), skills_dir=hub_dir, force=False)
    listed = list_installed_skills(skills_dir=hub_dir)
    removed = remove_skill('demo-local-skill', skills_dir=hub_dir)
    listed_after = list_installed_skills(skills_dir=hub_dir)

    assert installed.success is True
    assert any(row['name'] == 'demo-local-skill' for row in listed)
    assert removed.success is True
    assert all(row['name'] != 'demo-local-skill' for row in listed_after)


def test_hub_prod_search_remote_with_official_fallback() -> None:
    result = search_remote_skills_with_status('weather')

    assert result.registry_url or result.discovery_sources
    assert result.error is None or isinstance(result.error, str)
    assert isinstance(result.results, list)
