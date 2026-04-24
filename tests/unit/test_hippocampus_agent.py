from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.hippocampus_agent import HippocampusAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def _run(coro):
    return asyncio.run(coro)


def test_hippocampus_agent_recall_uses_orchestrator(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Orchestrator:
        async def arun(self, request):
            captured["request"] = request
            return SimpleNamespace(
                retrieval_summary={"status": "OK", "reason_code": "NONE"},
                retrieval_evidence_items=[{"snippet": "memory snippet"}],
            )

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_retrieval_orchestrator",
        lambda: _Orchestrator(),
    )

    summary, evidence = _run(
        HippocampusAgent().recall(
            request_id="req-hippo",
            message="test message",
            person_id="person-a",
            session_id="session-a",
            ts_ms=1234,
            emotion_tag="NEUTRAL",
            render_target_lang="zh",
            mood_state="steady",
            bond_summary={"stage": "friend"},
            motive_state={"focus": "task"},
            top_k=6,
        )
    )

    assert summary["status"] == "OK"
    assert evidence[0]["snippet"] == "memory snippet"
    request = captured["request"]
    assert request.query == "test message"
    assert request.request_id == "req-hippo"
    assert request.top_k == 6
    assert request.render_target_lang == "zh"


def test_hippocampus_agent_build_prompt_retrieval_rows() -> None:
    rows = HippocampusAgent().build_prompt_retrieval_rows(
        retrieval_summary={"query_plan": {"intent": "chat", "original_query": "继续"}},
        retrieval_evidence_items=[
            {
                "snippet": "记忆片段 A",
                "store_id": "episodic_vector",
                "score": 0.72,
                "confidence": 0.8,
                "source_ref": "episodic://a",
                "metadata": {
                    "emotion_tag": "HAPPY",
                    "suppression_flag": "false",
                    "distortion_level": 0.1234,
                    "lang": "zh",
                },
            },
            {
                "snippet": "记忆片段 B",
                "store_id": "persona_utterance",
                "score": 0.05,
                "confidence": 0.05,
                "metadata": {},
            },
        ],
    )

    assert len(rows) == 1
    assert rows[0]["slot"] == "retrieval"
    assert rows[0]["kind"] == "episodic"
    assert rows[0]["emotion_color_tag"] == "HAPPY"
    assert rows[0]["suppression_flag"] is False
    assert rows[0]["distortion_level"] == 0.1234


def test_hippocampus_agent_recall_adds_association_summary(monkeypatch) -> None:
    class _Orchestrator:
        async def arun(self, request):
            return SimpleNamespace(
                retrieval_summary={"status": "OK", "reason_code": None},
                retrieval_evidence_items=[
                    {"snippet": "用户之前提过会先列清单再推进任务。", "store_id": "episodic_vector"},
                ],
            )

    async def _fake_pool(**_kwargs):
        return SimpleNamespace(
            content='{"association_summary":"这些记忆说明她会把当前问题自然地往执行清单和步骤拆分上联想。","recall_focus":"清单化推进"}',
            pool_exhausted=False,
            final_model="MiniMax-M2-her",
            final_provider="minimax",
        )

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_retrieval_orchestrator",
        lambda: _Orchestrator(),
    )
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent._pool_credentials_available", lambda: True)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_pool)

    summary, _evidence = _run(
        HippocampusAgent().recall(
            request_id="req-hippo-assoc",
            message="那现在该怎么继续？",
            person_id="person-a",
            session_id="session-a",
            ts_ms=1234,
            emotion_tag="NEUTRAL",
            render_target_lang="zh",
        )
    )

    assert summary["association_generated"] is True
    assert "执行清单" in summary["association_summary"]
    assert summary["association_provider"] == "minimax"


def test_hippocampus_agent_memory_layers_write_paths(monkeypatch) -> None:
    class _Result:
        status = "OK"
        written = True
        state_version = 7
        reason_code = "NONE"
        detail = None
        payload = {"write": "ok"}

    class _Arbiter:
        def write(self, **kwargs):  # noqa: ANN003
            assert kwargs["writer_agent_id"] in {
                "episodic_memory_agent",
                "semantic_memory_agent",
                "resident_memory_agent",
            }
            return _Result()

    monkeypatch.setattr("host_brain.app.dependencies.get_state_write_arbiter", lambda: _Arbiter())

    agent = HippocampusAgent(state_registry=TribeStateRegistry())
    episodic = agent.write_episodic_memory_trace(
        person_id="u-memory",
        session_id="s-memory",
        content="今天一起修了核心链路。",
        metadata={"source": "unit"},
        request_id="req-episodic",
    )
    semantic = agent.write_semantic_memory_fact(
        person_id="u-memory",
        fact="用户偏好在复杂任务里先看执行清单。",
        source_refs=["dialog:1"],
        request_id="req-semantic",
    )
    blocked = agent.write_autobiographical_memory(
        person_id="u-memory",
        narrative="",
        gate_result={"decision": "BLOCK", "reason_code": "EMPTY"},
        request_id="req-autobio-block",
    )
    autobio = agent.write_autobiographical_memory(
        person_id="u-memory",
        narrative="我会把今天的推进当作重要里程碑。",
        gate_result={"decision": "ALLOW", "trace_id": "trace_autobio"},
        request_id="req-autobio",
    )
    layers = agent.resolve_memory_layer_snapshot()

    assert episodic["layer"] == "episodic"
    assert semantic["layer"] == "semantic"
    assert blocked["status"] == "BLOCKED"
    assert autobio["layer"] == "autobiographical"
    assert layers["last_write_type"] == "autobiographical"
    assert layers["episodic"]["session_id"] == "s-memory"
    assert "用户偏好" in layers["semantic"]["fact"]
