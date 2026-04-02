from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from pathlib import Path

import pytest

import host_brain.app.dependencies as deps
from host_brain.app.dispatch_agent import dispatch_agent_sse_generator



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


class _Metrics:
    def inc_chat(self):
        return None


async def _collect(async_iter):
    items = []
    async for item in async_iter:
        items.append(item)
    return items


def _parse_sse(events: list[str]) -> list[dict]:
    rows = []
    for raw in events:
        raw = str(raw).strip()
        if raw.startswith('data:'):
            payload = raw[5:].strip()
            if payload:
                rows.append(json.loads(payload))
    return rows


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    env_values = _load_env_file('.env')
    for key, value in env_values.items():
        if key.startswith(('CHROMA_', 'RETR_', 'HOST_RETR', 'HOST_SKILL_', 'BOND_NEO4J_')) or key in {
            'NATSUME_CHROMA_PORT', 'HOST_BOND_BACKEND', 'OPENAI_API_KEY', 'GEMINI_API_KEY', 'DEEPSEEK_API_KEY'
        }:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv('HOST_BOND_BACKEND', 'neo4j')
    deps._runtime_task_registry = None
    deps._episodic_store = None
    deps._retrieval_pipeline = None
    deps._retrieval_orchestrator = None
    yield
    deps._runtime_task_registry = None



def test_dispatch_agent_prod_task_progress_route() -> None:
    registry = deps.get_runtime_task_registry()
    task = registry.create_task(
        task_kind='skill',
        target_id='memory_store',
        request_id='req-dispatch-progress-seed',
        session_id='sess-dispatch-progress',
        person_id='person-dispatch-progress',
        user_message='记住我最喜欢蓝色',
        detail='正在写入记忆',
        progress_message='已经执行到写入记忆这一步了',
    )
    registry.update_task(task.task_id, status='RUNNING', progress_message='已经执行到写入记忆这一步了')

    request_body = SimpleNamespace(reply_channel='WEB', suppress_edge_tts=True, suppress_edge_vts=True, thread_id=None)
    events = asyncio.run(_collect(dispatch_agent_sse_generator(
        request_body=request_body,
        request_id='req-dispatch-progress-query',
        session_id='sess-dispatch-progress',
        ts_ms=1775128000000,
        intent_draft={},
        emotion_context={},
        emotion_snapshot={},
        governor_result={'decision': 'APPROVE'},
        intent_id='intent-dispatch-progress-query',
        metrics=_Metrics(),
        person_id='person-dispatch-progress',
        sanitized_message='现在做到哪了',
        recall_slot_text={},
        bond_summary={},
        retrieval_evidence_items=[],
        retrieval_summary={'status': 'SKIPPED'},
        render_target_lang='zh',
        llm_runtime={},
        pii_ingress_stats={},
    )))
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get('phase') == 'COMMIT')

    assert '写入记忆' in commit['narrative']
    assert commit['audit_refs']['path'] == 'dispatch_agent:task_progress'



def test_dispatch_agent_prod_skill_route_hub_list() -> None:
    request_body = SimpleNamespace(reply_channel='WEB', suppress_edge_tts=True, suppress_edge_vts=True, thread_id=None)
    events = asyncio.run(_collect(dispatch_agent_sse_generator(
        request_body=request_body,
        request_id='req-dispatch-hub-list',
        session_id='sess-dispatch-hub-list',
        ts_ms=1775128001000,
        intent_draft={},
        emotion_context={},
        emotion_snapshot={},
        governor_result={'decision': 'APPROVE'},
        intent_id='intent-dispatch-hub-list',
        metrics=_Metrics(),
        person_id='person-dispatch-hub-list',
        sanitized_message='蓝蓝，列出技能',
        recall_slot_text={},
        bond_summary={},
        retrieval_evidence_items=[],
        retrieval_summary={'status': 'SKIPPED'},
        render_target_lang='zh',
        llm_runtime={},
        pii_ingress_stats={},
    )))
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get('phase') == 'COMMIT')

    assert '已装了' in commit['narrative'] or 'Hub 技能' in commit['narrative']
    assert commit['audit_refs']['path'].startswith('chat_agent:sub=skill:hub_skill_list')



def test_dispatch_agent_prod_plan_execution_route() -> None:
    request_body = SimpleNamespace(reply_channel='WEB', suppress_edge_tts=True, suppress_edge_vts=True, thread_id=None)
    message = '蓝蓝，先搜索技能 weather，再列出技能'
    events = asyncio.run(_collect(dispatch_agent_sse_generator(
        request_body=request_body,
        request_id='req-dispatch-plan',
        session_id='sess-dispatch-plan',
        ts_ms=1775128002000,
        intent_draft={},
        emotion_context={},
        emotion_snapshot={},
        governor_result={'decision': 'APPROVE'},
        intent_id='intent-dispatch-plan',
        metrics=_Metrics(),
        person_id='person-dispatch-plan',
        sanitized_message=message,
        recall_slot_text={},
        bond_summary={},
        retrieval_evidence_items=[],
        retrieval_summary={'status': 'SKIPPED'},
        render_target_lang='zh',
        llm_runtime={},
        pii_ingress_stats={},
    )))
    payloads = _parse_sse(events)
    assert any(item.get('event') == 'PLAN_RUNNING' for item in payloads if item.get('phase') == 'PRESENCE')
    commit = next(item for item in payloads if item.get('phase') == 'COMMIT')

    assert '我帮你搜到' in commit['narrative'] or '我把这件事分成了' in commit['narrative']
    assert commit['audit_refs']['model_id'] == 'deterministic-plan-renderer'
