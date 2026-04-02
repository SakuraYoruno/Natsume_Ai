from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

import host_brain.app.dependencies as deps
from host_brain.app.dispatch_agent import _execute_skill
from host_brain.memory.chroma_client import PersistentChromaClient, load_chroma_runtime_config
from host_brain.retrieval.orchestrator import RetrievalOrchestratorRequest



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
    env_values = _load_env_file('.env')
    for key, value in env_values.items():
        if key.startswith(('CHROMA_', 'RETR_', 'HOST_RETR', 'HOST_SKILL_', 'BOND_NEO4J_')) or key in {
            'NATSUME_CHROMA_PORT', 'OPENAI_API_KEY', 'GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'HOST_BOND_BACKEND'
        }:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv('HOST_BOND_BACKEND', 'neo4j')
    for name in ['_episodic_store', '_retrieval_pipeline', '_retrieval_orchestrator']:
        if hasattr(deps, name):
            setattr(deps, name, None)
    yield
    for name in ['_episodic_store', '_retrieval_pipeline', '_retrieval_orchestrator']:
        if hasattr(deps, name):
            setattr(deps, name, None)



def test_core_chain_prod_episodic_write_recall_delete_with_persistent_chroma() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    store = deps.get_episodic_store()
    text = 'prod_core_memory_' + uuid.uuid4().hex[:10]

    written = store.write_memory(
        text=text,
        evidence_refs=['evd://core/prod/memory'],
        emotion_tag='HAPPY',
        emotion_valence=0.8,
        emotion_arousal=0.6,
    )
    recalled = store.recall(query_text=text, top_k=3, emotion_context='HAPPY')
    deleted = store.delete_memory(memory_id=written.memory_id, evidence_refs=['evd://core/prod/memory/delete'])
    recalled_after = store.recall(query_text=text, top_k=3, emotion_context='HAPPY')

    assert written.success is True
    assert recalled
    assert any(row['memory_id'] == written.memory_id for row in recalled)
    assert deleted.success is True
    assert all(row['memory_id'] != written.memory_id for row in recalled_after)



def test_core_chain_prod_persistent_chroma_client_get_search_delete() -> None:
    cfg_result = load_chroma_runtime_config(local_overrides=_load_env_file('.env'))
    assert cfg_result.success and cfg_result.config is not None
    collection_name = 'codex_core_chain_' + uuid.uuid4().hex[:8]
    client = PersistentChromaClient(
        endpoint=cfg_result.config.endpoint,
        tenant=cfg_result.config.tenant,
        database=cfg_result.config.database,
        collection_profiles={collection_name: 4},
        default_collection=collection_name,
    )
    memory_id = 'mem_' + uuid.uuid4().hex[:8]
    embedding = [0.1, 0.2, 0.3, 0.4]
    try:
        client.upsert(memory_id=memory_id, text='core chroma text', embedding=embedding, metadata={'emotion_tag': 'HAPPY'}, collection=collection_name)
        fetched = client.get(memory_id, collection=collection_name)
        rows = client.search(query_embedding=embedding, top_k=2, collection=collection_name)
        client.delete(memory_id, collection=collection_name)
        fetched_after = client.get(memory_id, collection=collection_name)

        assert fetched is not None and fetched.memory_id == memory_id
        assert rows and rows[0]['memory_id'] == memory_id
        assert fetched_after is None
    finally:
        try:
            client._client.delete_collection(name=collection_name)
        except Exception:
            pass



def test_core_chain_prod_retrieval_orchestrator_cache_hit() -> None:
    store = deps.get_episodic_store()
    token = 'prod_retrieval_cache_' + uuid.uuid4().hex[:10]
    written = store.write_memory(text=token, evidence_refs=['evd://core/prod/retrieval'], emotion_tag='HAPPY')
    try:
        orchestrator = deps.get_retrieval_orchestrator()
        request = RetrievalOrchestratorRequest(
            trace_id='trace-' + token,
            request_id='req-' + token,
            query=token,
            session_state={'session_id': 'sess-' + token},
            person_id='person-' + token,
            session_id='sess-' + token,
            top_k=5,
            ts_ms=1775126000000,
            emotion_context='HAPPY',
            render_target_lang='zh',
            turn_index=1,
        )
        first = asyncio.run(orchestrator.arun(request=request, timeout_ms=60000))
        second = asyncio.run(orchestrator.arun(request=request, timeout_ms=60000))

        assert first.status == 'OK'
        assert first.retrieval_evidence_items
        assert second.retrieval_summary.get('cache_enabled') in {True, False}
        if second.retrieval_summary.get('cache_enabled'):
            assert second.retrieval_summary.get('cache_hit') is True
    finally:
        store.delete_memory(memory_id=written.memory_id, evidence_refs=['evd://core/prod/retrieval/delete'])



def test_core_chain_prod_dispatch_execute_skill_memory_store() -> None:
    text = '记住这个核心集成测试标记 ' + uuid.uuid4().hex[:8]
    ctx = asyncio.run(
        _execute_skill(
            skill_id='memory_store',
            user_message=text,
            request_id='req-dispatch-core-' + uuid.uuid4().hex[:6],
            session_id='sess-dispatch-core-' + uuid.uuid4().hex[:6],
        )
    )

    assert ctx.success is True
    assert ctx.agent_id == 'skill:memory_store'
    assert ctx.result_text
