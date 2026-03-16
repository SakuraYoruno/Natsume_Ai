"""P1/P2 集成 smoke：验证「入口选图 → payload 写 execution_profile → graph_runtime 注入 state → 对应 graph 执行」整链闭合。

不重复单测，而是验证真实入口链路：
- 聊天请求 → CHAT_FAST → chat_graph
- 代码/翻译/AUTO → WORKFLOW_SUPERVISED → supervised_graph
- 执行意图 → INTENT_EXEC → intent_graph
"""

from __future__ import annotations

import json
import time

import pytest

from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    INTENT_AGENT_ENTRY_NODE_ID,
    SUPERVISOR_DISPATCH_NODE_ID,
)
from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


def _post_chat(client, message: str, scene_type: str | None = None) -> dict:
    """POST /v1/chat，解析 COMMIT 事件为 dict。"""
    body = {"message": message, "session_id": f"smoke-{int(time.time()*1000)}"}
    if scene_type is not None:
        body["scene_type"] = scene_type
    resp = client.post("/v1/chat", json=body)
    assert resp.status_code == 200, (resp.status_code, resp.text[:500])
    return parse_sse_commit(resp)


def _get_llm_audit(body: dict) -> dict:
    """从 COMMIT body 取 audit_refs.llm。"""
    return dict((body.get("audit_refs") or {}).get("llm") or {})


# ---------------------------------------------------------------------------
# Smoke 1（P2+P3）：WORK_CODE / WORK_TRANSLATE / AUTO → WORKFLOW_SUPERVISED → supervised_graph
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scene_type", ["WORK_CODE", "WORK_TRANSLATE", "AUTO"])
def test_smoke_workflow_supervised_via_http(client_with_mock_llm, scene_type: str) -> None:
    """真实入口：POST scene_type=WORK_CODE / WORK_TRANSLATE / AUTO → supervised_graph（agent_dispatch → code/translate）。"""
    message = "请写一段 Python 代码" if scene_type == "WORK_CODE" else "把这段翻译成英文" if scene_type == "WORK_TRANSLATE" else "你好"
    body = _post_chat(client_with_mock_llm, message, scene_type=scene_type)
    assert body.get("decision") == "APPROVE", body
    llm = _get_llm_audit(body)
    assert llm.get("execution_profile") == PROFILE_WORKFLOW_SUPERVISED, llm
    assert llm.get("graph_entry_node") == SUPERVISOR_DISPATCH_NODE_ID, llm


# ---------------------------------------------------------------------------
# Smoke 2：EXEC_INTENT → INTENT_EXEC → intent_graph
# ---------------------------------------------------------------------------

def test_smoke_intent_exec_via_http(client_with_mock_llm) -> None:
    """真实入口：POST scene_type=EXEC_INTENT → intent_graph（entry=intent_agent_entry）。"""
    body = _post_chat(client_with_mock_llm, "打开设置", scene_type="EXEC_INTENT")
    assert body.get("decision") == "APPROVE", body
    llm = _get_llm_audit(body)
    assert llm.get("execution_profile") == PROFILE_INTENT_EXEC, llm
    assert llm.get("graph_entry_node") == INTENT_AGENT_ENTRY_NODE_ID, llm


# ---------------------------------------------------------------------------
# Smoke 3：CHAT 经 complex 路径 → CHAT_FAST → chat_graph（直接调 generator）
# ---------------------------------------------------------------------------

def test_smoke_chat_fast_via_complex_generator(monkeypatch) -> None:
    """CHAT 走 complex 分支时：execution_profile=CHAT_FAST，entry=chat_direct_ja。"""
    import asyncio
    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from host_brain.app.chat_handler_complex import _complex_scene_sse_generator
    from host_brain.app.schemas import ChatRequest

    async def _run() -> dict:
        request_body = ChatRequest(message="你好", session_id=f"smoke-chat-{int(time.time()*1000)}")
        governor_result = {
            "decision": "APPROVE",
            "stage": "S_APPROVE",
            "committed_route_token_ref": None,
            "committed_route_token": None,
            "tokens": {},
        }
        emotion_context = {}
        emotion_snapshot = {}
        metrics = None
        commit_event: dict = {}
        async for event_str in _complex_scene_sse_generator(
            request_body=request_body,
            request_id="smoke-req-chat",
            session_id=request_body.session_id or "sess",
            ts_ms=int(time.time() * 1000),
            intent_draft={},
            scene_type="CHAT",
            scene_type_source="smoke_test",
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id="smoke-req-chat",
            reason_code=None,
            metrics=metrics,
        ):
            if event_str.strip().startswith("data:"):
                try:
                    data = json.loads(event_str.strip()[5:].strip())
                    if isinstance(data, dict) and data.get("phase") == "COMMIT":
                        commit_event = data
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
        return commit_event

    commit_event = asyncio.run(_run())
    assert commit_event.get("decision") == "APPROVE", commit_event
    llm = _get_llm_audit(commit_event)
    assert llm.get("execution_profile") == PROFILE_CHAT_FAST, llm
    assert llm.get("graph_entry_node") == CHAT_DIRECT_NODE_ID, llm


# ---------------------------------------------------------------------------
# Fixture：带 mock LLM 的 TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def client_with_mock_llm(monkeypatch):
    """FastAPI TestClient + mock LLM（避免真实调用）。"""
    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app
    return TestClient(app)
