from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from host_brain.app.surface_main_chain_adapter import (
    _sanitize_main_chain_meta_reply,
    build_surface_main_chain_input_from_chat_input,
    run_surface_main_chain_for_chat_input,
    surface_main_chain_enabled,
    surface_main_chain_sse_generator,
)


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"


@pytest.fixture(autouse=True)
def _disable_surface_quote_guidance(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("host_brain.persona.surface_runtime.render_surface_original_quote_guidance", lambda *_args, **_kwargs: "")


def _parse_sse(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw.removeprefix("data: ").strip())


def _agent_input(**overrides):
    payload = {
        "request_id": "req-surface-app",
        "session_id": "sess-surface-app",
        "person_id": "user-1",
        "user_message": "这段我不会改",
        "render_policy": {
            "primary_topic": "technical_help",
            "interaction_move": "technical_next_step",
            "response_goal": "先看输入输出",
            "response_form": "text",
            "max_questions": 0,
        },
        "emotion_context": {"emotion_tag": "ANXIOUS", "emotion_intensity": 0.4},
        "emotion_snapshot": {},
        "bond_summary": {"relationship_distance": "familiar"},
        "retrieval_summary": {
            "status": "OK",
            "surface_memory_recall_packet": {
                "facts": ["用户上次改这类段落时卡在输入输出边界。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://episodic/1"],
                "evidence_refs": ["evd://episodic/1"],
            },
        },
        "trace_stage_meta": {},
        "llm_runtime": {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
        "attachments": [],
        "multimodal_observations": {},
        "response_form": "text",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def _surface_json_result(text: str, *, final_provider: str = "mock", final_model: str = "mock-chat") -> SimpleNamespace:
    return SimpleNamespace(
        content=json.dumps(
            {
                "reply_text": text,
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.0,
            },
            ensure_ascii=False,
        ),
        pool_exhausted=False,
        final_provider=final_provider,
        final_model=final_model,
        provider_name="mock",
        model_id="mock-chat",
        latency_ms=1,
        first_token_ms=1,
    )


@pytest.mark.asyncio
async def test_surface_main_chain_propagates_compiled_memory_packet_for_output_guard(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("哈？老哥你终于想起来问我啦。我会选第三本《C》。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    trace: dict[str, object] = {}
    result = await run_surface_main_chain_for_chat_input(
        _agent_input(
            user_message="这三本书，你喜欢哪本？",
            retrieval_summary={
                "status": "OK",
                "surface_memory_recall_packet": {
                    "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                    "degraded": False,
                    "reason_code": None,
                    "trace": {
                        "selected_collection_items": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
                        "recall_answer_readiness": {
                            "status": "READY",
                            "ready_to_answer": True,
                            "response_mode": "answer_from_collection_memory",
                            "recall_contract_id": "anaphoric_selection",
                            "query_kind": "anaphoric_selection",
                        },
                    },
                },
            },
        ),
        trace_stage_meta=trace,
    )

    assert result.selected_text.startswith("哈？")
    packet = trace.get("surface_memory_recall_packet")
    assert isinstance(packet, dict)
    assert "集合记忆" in "\n".join(packet.get("facts") or [])


def test_build_surface_main_chain_input_from_chat_input_compresses_app_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    payload = build_surface_main_chain_input_from_chat_input(_agent_input())

    assert payload.soul_md == SOUL
    assert payload.user_message == "这段我不会改"
    assert payload.task_control["task_mode"] == "technical_help"
    assert payload.task_control["response_move"] == "technical_next_step"
    assert "当前输入渠道：中文文字私聊。" in payload.task_control["facts"]
    assert "技术场景：先给一条能执行的排查步骤，命令和符号要准确。" in payload.task_control["facts"]
    assert "技术场景：最终回复至少包含查因和下一步处理，不要只给一条裸命令。" in payload.task_control["facts"]
    assert "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。" in payload.must_not_do
    assert "技术回复不要用“干掉”“完事”等粗略说法替代清楚步骤。" in payload.must_not_do
    assert "不要只输出单条命令；命令后要说明下一步如何处理结果。" in payload.must_not_do
    assert tuple(payload.memory_packet.facts) == ("用户上次改这类段落时卡在输入输出边界。",)
    assert payload.affect_packet.emotional_tone == "steady_helpful"
    assert "memory://episodic/1" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_build_surface_main_chain_input_marks_anaphoric_memory_followup(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["记忆线索：我刚才推荐了 3 本书：1. A；2. B；3. C。"],
            "style_anchor": ["指代选择追问：必须承接记忆线索里的最近对象回答，不要当成新话题。"],
            "trace": {"plan": {"reason": "anaphoric_selection_recall"}},
        }
    }

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(user_message="我问你喜欢哪本"),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"
    assert any("指代追问" in item for item in payload.task_control["style_anchor"])
    assert tuple(payload.memory_packet.facts) == ("记忆线索：我刚才推荐了 3 本书：1. A；2. B；3. C。",)


def test_build_surface_main_chain_input_marks_collection_recall_as_memory_followup(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["集合记忆：昨天轻小说推荐集合，记录 3 个条目；可比较证据：1. A；2. B；3. C。"],
            "trace": {"plan": {"reason": "collection_memory_recall"}},
        }
    }

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(user_message="昨天三本呢？"),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"
    assert any("日期/近期集合追问" in item for item in payload.task_control["style_anchor"])


def test_build_surface_main_chain_input_marks_failure_recall_as_memory_followup(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["记忆线索：任务失败。原因：DeepSeek provider timeout。"],
            "trace": {"plan": {"reason": "failure_debug_recall"}},
        }
    }

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(user_message="为什么任务会失败？"),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"
    assert any("失败追问只能解释" in item for item in payload.task_control["style_anchor"])


def test_build_surface_main_chain_input_infers_anaphoric_followup_from_sanitized_packet(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )
    trace = {
        "surface_memory_recall_packet": {
            "facts": ["记忆线索：我刚才推荐了 3 本书：1. A；2. B；3. C。"],
            "style_anchor": ["指代选择追问：把记忆线索当作刚才对话的当前上下文。"],
        }
    }

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(user_message="这三本书你喜欢哪本"),
        trace_stage_meta=trace,
    )

    assert payload.task_control["task_mode"] == "memory_followup"
    assert payload.task_control["response_move"] == "memory_followup"


def test_build_surface_main_chain_input_prefers_surface_task_control(monkeypatch) -> None:
    trace: dict[str, object] = {
        "organ_route_meta": {
            "immune_risk_level": "high",
            "motive_care": 0.44,
            "cognitive_load_index": 0.81,
        }
    }
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {
            "reply_plan": {
                "primary_topic": "context_fallback_should_not_win",
                "interaction_move": "context_move_should_not_win",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 1, "summary_present": False},
            "surface_affect_packet": {
                "emotional_tone": "playful_light",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻一点接，但先处理当前句子。"],
                "must_not_do": ["不要把上下文包写进回复"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚要求继续迁移主链。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/2"],
            },
            "surface_influence_packet": {
                "affect": {"emotional_tone": "playful_light"},
                "relationship": {
                    "relationship_distance": "familiar_not_intimate",
                    "style_anchor": ["关系面只影响距离，不写关系阶段。"],
                },
                "safety": {"must_not_do": ["不要输出器官状态。"]},
            },
        }

    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(
            surface_task_control={
                "task_mode": "style_repair",
                "response_move": "ack_style_issue",
                "response_form": "text",
                "question_budget": 0,
            }
        ),
        trace_stage_meta=trace,
    )

    assert calls
    assert calls[0]["surface_task_control"]["task_mode"] == "style_repair"
    assert calls[0]["organ_context"]["immune_state"]["risk_level"] == "high"
    assert calls[0]["organ_context"]["motive_state"]["care_drive"] == 0.44
    assert calls[0]["organ_context"]["proprioception_state"]["cognitive_load_index"] == 0.81
    assert payload.task_control["task_mode"] == "style_repair"
    assert payload.task_control["response_move"] == "ack_style_issue"
    assert payload.affect_packet.emotional_tone == "playful_light"
    assert payload.influence_packet["relationship"]["relationship_distance"] == "familiar_not_intimate"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚要求继续迁移主链。",)
    assert trace["surface_context_packet_prepared"] is True
    assert trace["surface_selected_reply_plan"]["task_mode"] == "style_repair"
    assert trace["reply_plan"]["primary_topic"] == "context_fallback_should_not_win"
    assert "memory://raw/should-not-pass" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_sanitize_main_chain_meta_reply_replaces_generic_latency_reply() -> None:
    text = "哈？老哥你又在琢磨什么乱七八糟的。 如果是我这边有什么让你觉得慢的话，直接说嘛，我再看看怎么改进。"

    cleaned = _sanitize_main_chain_meta_reply(
        text,
        agent_input=_agent_input(user_message="如果我要继续压整条聊天链路的耗时，你会先怀疑哪一段？不要列清单。"),
    )

    assert "乱七八糟" not in cleaned
    assert "入口路由" in cleaned
    assert "surface" in cleaned


def test_build_surface_main_chain_input_derives_reply_plan_without_passing_history(monkeypatch) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_build_cortex_prompt(**_kwargs):  # noqa: ANN003
        raise AssertionError("surface main chain must not build legacy cortex prompt messages")

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        assert kwargs["user_message"] == "这段我不会改"
        return {
            "reply_plan": {
                "primary_topic": "presence_ping",
                "interaction_move": "answer_current_chat_presence",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 2, "summary_present": True},
            "surface_affect_packet": {
                "emotional_tone": "slightly_guilty",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻轻承认慢了一拍，马上回到当前对话。"],
                "must_not_do": ["不要把慢了一拍写成现实动作"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚指出上一句不通顺。"],
                "relationship_clues": ["熟人私聊，不升级亲密。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/1"],
            },
            "legacy_messages": [{"content": "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE"}],
        }

    monkeypatch.setattr("host_brain.app.cortex_prompt_builder.build_cortex_prompt", _blocked_build_cortex_prompt)
    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(render_policy={}, surface_task_control={}),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "presence_ping"
    assert payload.task_control["response_move"] == "answer_current_chat_presence"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚指出上一句不通顺。",)
    assert trace["conversation_history"] == {"turns_injected": 2, "summary_present": True}
    assert trace["reply_plan"]["primary_topic"] == "presence_ping"
    assert trace["surface_affect_packet"]["emotional_tone"] == "slightly_guilty"
    assert "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE" not in rendered
    assert "memory://raw/should-not-pass" not in rendered


def test_build_surface_main_chain_input_reuses_prepared_context_packet(monkeypatch) -> None:
    trace: dict[str, object] = {
        "surface_context_packet_prepared": True,
        "reply_plan": {
            "primary_topic": "prepared_presence",
            "interaction_move": "reply_from_prepared_packet",
            "response_form": "text",
            "max_questions": 0,
        },
        "surface_affect_packet": {
            "emotional_tone": "slightly_guilty",
            "relationship_distance": "familiar_not_intimate",
            "style_anchor": ["承认刚才慢了一点，但别扩写。"],
            "must_not_do": ["不要重建旧上下文"],
        },
        "surface_memory_recall_packet": {
            "facts": ["用户正在让主链继续迁移。"],
            "style_anchor": ["只承接当前任务。"],
            "source_refs": ["memory://raw/should-not-pass"],
            "evidence_refs": ["evd://memory/prepared"],
        },
    }

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_surface_context_packet(**_kwargs):  # noqa: ANN003
        raise AssertionError("prepared surface context packet should be reused")

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.build_surface_context_packet",
        _blocked_surface_context_packet,
    )

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(render_policy={}, surface_task_control={}),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "prepared_presence"
    assert payload.task_control["response_move"] == "reply_from_prepared_packet"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户正在让主链继续迁移。",)
    assert trace["surface_selected_reply_plan"]["primary_topic"] == "prepared_presence"
    assert "memory://raw/should-not-pass" not in rendered


@pytest.mark.asyncio
async def test_surface_main_chain_blocks_visual_claims_without_evidence(monkeypatch) -> None:
    async def _should_not_run(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("surface LLM must not run without perception evidence")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_chat_input",
        _should_not_run,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="你觉得这几本书怎么样", attachments=[], multimodal_observations={})
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_PERCEPTION_EVIDENCE_REQUIRED"
    assert "没拿到图" in commit["narrative"]
    assert "封面" not in commit["narrative"]
    assert "猫" not in commit["narrative"]
    assert commit["audit_refs"]["trace_stage_meta"]["surface_evidence_gate"]["status"] == "OK"


@pytest.mark.asyncio
async def test_surface_main_chain_sse_generator_calls_chat_surface_pool(monkeypatch) -> None:
    calls: list[dict] = []
    post_process_calls: list[dict] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        assert "ReplyControlPacket" in rendered
        assert "memory://episodic/1" not in rendered
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "上次卡在输入输出，这轮先盯那一块。",
                    "natsume_emotion_tag": "THINKING",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.68,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        post_process_calls.append(kwargs)

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)

    events = [_parse_sse(chunk) async for chunk in surface_main_chain_sse_generator(_agent_input())]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "chat_agent:surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_main_chain_used"] is True
    assert "timings_ms" in commit["audit_refs"]
    assert "输入输出" in commit["narrative"]
    assert commit["reply_envelope"]["audit"]["natsume_emotion_tag"] == "THINKING"
    assert commit["reply_envelope"]["audit"]["vts_expression_id"]
    assert commit["audit_refs"]["surface_main_chain"]["user_emotion_tag"] == "ANXIOUS"
    await asyncio.sleep(0)
    assert post_process_calls
    assert post_process_calls[0]["trace_stage_meta"]["surface_main_chain_used"] is True
    assert post_process_calls[0]["reply_plan"]["primary_topic"] == "technical_help"
    assert post_process_calls[0]["emotion_context"]["emotion_tag"] == "THINKING"
    assert post_process_calls[0]["emotion_context"]["surface_user_emotion_tag"] == "ANXIOUS"
    assert post_process_calls[0]["emotion_context"]["surface_vts_expression_id"]


@pytest.mark.asyncio
async def test_recommendation_followup_uses_surface_main_chain_not_template(monkeypatch) -> None:
    calls: list[dict] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "这三本我会重新看题材和简介再选，不套固定答案。",
                    "natsume_emotion_tag": "THINKING",
                    "user_emotion_tag": "CURIOUS",
                    "emotion_confidence": 0.7,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    async def _fake_post_process(**_kwargs):  # noqa: ANN003
        return None

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": [
                            "记忆线索：我刚才通过 qq 推荐了 3 本轻小说：1. A；2. B；3. C。"
                        ],
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert commit["audit_refs"]["path"] == "chat_agent:surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"].get("reply_source") == "surface_main_chain"
    assert "固定答案" in commit["narrative"]


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_not_ready_memory_claim(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("我记得昨天那几本里《幻觉书》最好看，直接看它。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="昨天那几本好看吗？",
                retrieval_summary={
                    "status": "DEGRADED",
                    "surface_memory_recall_packet": {
                        "facts": [],
                        "degraded": True,
                        "reason_code": "SURFACE_MEMORY_RECALL_COVERAGE_FAILED",
                        "trace": {
                            "plan": {"reason": "anaphoric_selection_recall"},
                            "recall_answer_readiness": {
                                "status": "NOT_READY",
                                "ready_to_answer": False,
                                "response_mode": "fetch_or_request_detail_evidence",
                                "reasons": ["coverage_failed", "missing_detail_evidence"],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "幻觉书" not in commit["narrative"]
    assert "没有足够详情" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_NOT_READY_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_detail_anchor_selection_until_deep_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("昨天那几本里我会选《B》，热度高，节奏也舒服。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="昨天那几本好看吗？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "selected_collection_items": [
                                {"title": "A", "detail_url": "https://www.lightnovel.fun/cn/detail/a"},
                                {"title": "B", "detail_url": "https://www.lightnovel.fun/cn/detail/b"},
                                {"title": "C", "detail_url": "https://www.lightnovel.fun/cn/detail/c"},
                            ],
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_collection_memory",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                                "detail_evidence": {
                                    "required": True,
                                    "basis": "detail_anchor",
                                    "ready_for_deep_review": True,
                                },
                            },
                            "recall_answer_strategy": {
                                "action": "fetch_more_evidence",
                                "should_fetch_more_evidence": True,
                                "claim_scope": "memory_only_no_quality_judgment",
                                "evidence_level": "detail_anchor_only",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "热度高" not in commit["narrative"]
    assert "没有足够详情" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_NOT_READY_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_treats_detail_anchor_selection_as_not_ready_without_strategy(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("哈？昨天那几本我能对上，我会选《B》，热度高，节奏挺舒服。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="昨天那几本好看吗？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "selected_collection_items": [
                                {"title": "A", "detail_url": "https://www.lightnovel.fun/cn/detail/a"},
                                {"title": "B", "detail_url": "https://www.lightnovel.fun/cn/detail/b"},
                                {"title": "C", "detail_url": "https://www.lightnovel.fun/cn/detail/c"},
                            ],
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_collection_memory",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                                "detail_evidence": {
                                    "required": True,
                                    "basis": "detail_anchor",
                                    "ready_for_deep_review": True,
                                },
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "我能对上" not in commit["narrative"]
    assert "热度高" not in commit["narrative"]
    assert "先把详情页或正文补读完" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_NOT_READY_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_ready_memory_reply_with_unsupported_title(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("我会选《不存在D》，这个设定最抓人。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "不存在D" not in commit["narrative"]
    assert "《A》" in commit["narrative"]
    assert "《B》" in commit["narrative"]
    assert "《C》" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_UNSUPPORTED_FACT_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_strips_memory_user_state_guess(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("嘿嘿，老哥终于来问我啦～我补读之后觉得第三本《C》更顺，开篇更抓人。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "终于来问" not in commit["narrative"]
    assert "老哥终于" not in commit["narrative"]
    assert "《C》" in commit["narrative"]
    assert "开篇" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_USER_STATE_GUESS_STRIPPED"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_bad_selection_reply_to_deep_review_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("哈？老哥你昨天不是没看吗……算了，那几本里我觉得《异世界皇女皇帝线》挺有意思的，热度也高，节奏挺舒服。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="昨天那几本好看吗？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": [
                            "网页深读结果（含正文）：当前更推荐第3本《异世界皇女皇帝线》。原因：开篇比较抓人；设定线很直给；对白比较轻快。另外我也顺手看了：第1本《A》、第2本《B》。"
                        ],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "selected_collection_items": [
                                {"title": "A", "detail_url": "https://www.lightnovel.fun/cn/detail/a"},
                                {"title": "B", "detail_url": "https://www.lightnovel.fun/cn/detail/b"},
                                {"title": "异世界皇女皇帝线", "detail_url": "https://www.lightnovel.fun/cn/detail/c"},
                            ],
                            "lightnovel_deep_review": {
                                "status": "OK",
                                "selected": {"index": 3, "title": "异世界皇女皇帝线"},
                                "source": "fetch",
                            },
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_evidence",
                                "evidence_depth": "detail_or_body_evidence",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                                "detail_evidence": {
                                    "required": True,
                                    "basis": "lightnovel_deep_review",
                                    "ready_for_deep_review": True,
                                    "body_item_count": 1,
                                },
                            },
                            "recall_answer_strategy": {
                                "action": "direct_answer",
                                "claim_scope": "detail_judgment",
                                "evidence_level": "detail_or_body",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "昨天不是没看" not in commit["narrative"]
    assert "热度也高" not in commit["narrative"]
    assert "网页深读" not in commit["narrative"]
    assert "《异世界皇女皇帝线》" in commit["narrative"]
    assert "开篇比较抓人" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_DEEP_REVIEW_DIRECT_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_partial_deep_review_without_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("这十几本里我会选《C》，它最好看，开篇最抓人。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这几天十几本里哪本最好看？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": [
                            "网页深读结果（含正文）：当前更推荐第3本《C》。原因：开篇比较抓人；设定线很直给。另外我也顺手看了：第1本《A》、第2本《B》。"
                        ],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "selected_collection_items": [
                                {"title": chr(64 + index), "detail_url": f"https://www.lightnovel.fun/cn/detail/{index}"}
                                for index in range(1, 13)
                            ],
                            "lightnovel_deep_review": {
                                "status": "OK",
                                "selected": {"index": 3, "title": "C"},
                                "item_count": 6,
                                "source_card_count": 12,
                                "reviewed_item_count": 6,
                                "partial_review": True,
                                "source": "fetch",
                            },
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_evidence",
                                "evidence_depth": "detail_or_body_evidence",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                                "detail_evidence": {
                                    "required": True,
                                    "basis": "lightnovel_deep_review",
                                    "ready_for_deep_review": True,
                                    "body_item_count": 1,
                                },
                            },
                            "recall_answer_strategy": {
                                "action": "direct_answer",
                                "claim_scope": "partial_detail_judgment",
                                "evidence_level": "partial_detail_or_body",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "全 12 本里绝对最好" in commit["narrative"]
    assert "先补读了其中 6 本" in commit["narrative"]
    assert "《C》" in commit["narrative"]
    assert "开篇比较抓人" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_DEEP_REVIEW_DIRECT_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_selection_reply_that_drifts_to_self_reading(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("哈？最近在看一本讲宇宙和人的，挺有意思的。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "plan": {"reason": "anaphoric_selection_recall"},
                            "selected_collection_items": [
                                {"title": "A"},
                                {"title": "B"},
                                {"title": "C"},
                            ],
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_collection_memory",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert "最近在看" not in commit["narrative"]
    assert "宇宙" not in commit["narrative"]
    assert "《A》" in commit["narrative"]
    assert "《B》" in commit["narrative"]
    assert "《C》" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_SELECTION_UNGROUNDED_REWRITE"


@pytest.mark.asyncio
async def test_surface_main_chain_strips_low_signal_prefix_from_memory_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("老哥你问这个啊——我刚补读完，第三本《C》开篇最抓人。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                        "trace": {
                            "plan": {"reason": "anaphoric_selection_recall"},
                            "selected_collection_items": [
                                {"title": "A"},
                                {"title": "B"},
                                {"title": "C"},
                            ],
                            "recall_answer_readiness": {
                                "status": "READY",
                                "ready_to_answer": True,
                                "response_mode": "answer_from_collection_memory",
                                "reasons": [],
                                "recall_contract_id": "anaphoric_selection",
                                "query_kind": "anaphoric_selection",
                            },
                        },
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert not commit["narrative"].startswith("老哥")
    assert commit["narrative"].startswith("我刚补读完")
    assert "《C》" in commit["narrative"]
    guard = commit["audit_refs"]["trace_stage_meta"]["surface_memory_output_guard"]
    assert guard["applied"] is True
    assert guard["reason_code"] == "SURFACE_MEMORY_OUTPUT_LOW_SIGNAL_PREFIX_STRIPPED"

    async def _fake_complete_short_prefix(**_kwargs):  # noqa: ANN003
        return _surface_json_result("老哥～我刚补读完，第三本《C》开篇最抓人。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete_short_prefix)
    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(
                user_message="这三本书，你喜欢哪本？",
                retrieval_summary={
                    "status": "OK",
                    "surface_memory_recall_packet": {
                        "facts": ["集合记忆：本次轻小说推荐集合，记录 3 个条目；可比较证据：1. 《A》；2. 《B》；3. 《C》。"],
                        "degraded": False,
                        "reason_code": None,
                    },
                },
            )
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")
    assert commit["narrative"].startswith("我刚补读完")


@pytest.mark.asyncio
async def test_surface_main_chain_failure_returns_sendable_non_persistent_fallback(monkeypatch) -> None:
    async def _boom(**_kwargs):  # noqa: ANN003
        raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_chat_input",
        _boom,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="继续，短一句。")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FALLBACK"
    assert commit["narrative"] == "接着来。"
    assert commit["reply_envelope"]["sendable"] is True
    assert commit["reply_envelope"]["persistable"] is False
    assert commit["reply_envelope"]["cacheable"] is False
    assert commit["reply_envelope"]["raw_extra"]["fallback"] is True
    assert commit["audit_refs"]["trace_stage_meta"]["surface_renderer"]["status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_surface_main_chain_failure_returns_contextual_fallback_for_substantive_prompt(monkeypatch) -> None:
    async def _boom(**_kwargs):  # noqa: ANN003
        raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_chat_input",
        _boom,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="你觉得把后台任务迁到本机 Codex 以后，最需要继续观察的风险是什么？用两句说。")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FALLBACK"
    assert "接着来" not in commit["narrative"]
    assert "刚卡" not in commit["narrative"]
    assert "连接稳定性" in commit["narrative"]
    assert commit["reply_envelope"]["persistable"] is False


@pytest.mark.asyncio
async def test_surface_main_chain_low_info_fastpath_bypasses_llm(monkeypatch) -> None:
    async def _unexpected_run(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("low-info fastpath should not invoke surface llm")

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.run_surface_main_chain_for_chat_input",
        _unexpected_run,
    )

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="继续")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["reason_code"] == "SURFACE_MAIN_CHAIN_FASTPATH"
    assert commit["narrative"] == "接着来。"
    assert commit["reply_envelope"]["sendable"] is True
    assert commit["reply_envelope"]["persistable"] is True
    assert commit["audit_refs"]["provider_name"] == "rule"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_renderer"]["invoked"] is False


@pytest.mark.asyncio
async def test_surface_main_chain_audit_uses_pool_final_model(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_LOW_INFO_FASTPATH_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("我这里只看到文字。", final_provider="gemini", final_model="gemini-2.5-flash-lite")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="听得到吗")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    assert commit["audit_refs"]["provider_name"] == "gemini"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_llm_pool"]["final_model"] == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_surface_main_chain_rewrites_account_credential_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert "不要要求用户把账号、密码、验证码或 Cookie 发到聊天里。" in rendered
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "行，账号信息丢过来就行，剩下的我搞定。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    agent_input = _agent_input(
        user_message="登录我的账号并使用积分购买这本轻小说后封装成 epub，最多花 30 点",
        intent_draft={"route": "skill", "target_id": "codex_epub_builder"},
        sub_agent_context=SimpleNamespace(
            agent_id="codex_epub_builder",
            task="登录我的账号并使用积分购买这本轻小说后封装成 epub",
            result_text="需要登录，但不要把账号、密码、验证码或 Cookie 发到聊天里。",
            success=True,
            latency_ms=1,
            error="",
            payload={},
        ),
    )
    events = [_parse_sse(chunk) async for chunk in surface_main_chain_sse_generator(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert "别把账号、密码、验证码或 Cookie 发给我" in commit["narrative"]
    assert "账号信息丢过来" not in commit["narrative"]
    assert commit["audit_refs"]["trace_stage_meta"]["surface_account_login_guard"]["applied"] is True


@pytest.mark.asyncio
async def test_surface_main_chain_routes_port_occupied_through_model(monkeypatch) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(
            "老哥，先查占用 PID：netstat -ano | findstr :18080。看到 PID 后用 taskkill /PID <PID> /F；不想关进程就改服务端口再重启。"
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_chat_input(
        _agent_input(user_message="项目启动报错，端口18080被占用了，我该怎么排查？"),
        trace_stage_meta=trace,
    )

    text = result.selected_text
    assert "netstat -ano | findstr :18080" in text
    assert "taskkill /PID <PID> /F" in text
    assert "改服务端口再重启" in text
    assert "干掉" not in text
    assert "surface_quick_reply" not in trace
    assert "surface_llm_pool" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_routes_auditory_text_check_through_model(monkeypatch) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("嗯，我没听见声音，只看到你这条文字。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_chat_input(
        _agent_input(
            user_message="听得到吗",
            reply_channel="qq",
            render_policy={
                "primary_topic": "auditory_check",
                "interaction_move": "auditory_probe",
                "response_form": "text",
                "max_questions": 0,
            },
        ),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "嗯，我没听见声音，只看到你这条文字。"
    assert "在呢" not in result.selected_text
    assert "听到了" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_llm_pool" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_routes_asr_auditory_check_through_model(monkeypatch) -> None:
    trace: dict[str, object] = {"input_source": "ASR"}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "听到啦。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.0,
                },
                ensure_ascii=False,
            ),
            pool_exhausted=False,
            provider_name="mock",
            model_id="mock-chat",
            latency_ms=1,
            first_token_ms=1,
        )

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_chat_input(
        _agent_input(
            user_message="听得到吗",
            render_policy={
                "primary_topic": "auditory_check",
                "interaction_move": "auditory_probe",
                "response_form": "text",
                "max_questions": 0,
            },
        ),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "听到啦。"
    assert "surface_quick_reply" not in trace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "model_reply", "expected_fragments"),
    [
        (
            "Python import yaml 失败，ModuleNotFoundError: No module named 'yaml'，怎么排查？",
            "先确认当前解释器有没有包：python -m pip show PyYAML。没有就跑 python -m pip install PyYAML；还报错再用 python -c \"import sys; print(sys.executable)\" 看是不是跑错环境。",
            ("python -m pip show PyYAML", "python -m pip install PyYAML", "sys.executable"),
        ),
        (
            "OPENAI_API_KEY 环境变量好像没生效，先查什么？",
            "PowerShell 先跑 $env:OPENAI_API_KEY，cmd 里用 echo %OPENAI_API_KEY%。如果为空就重新设置，别把值贴出来。",
            ("$env:OPENAI_API_KEY", "echo %OPENAI_API_KEY%", "别把值贴出来"),
        ),
        (
            "Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？",
            "先确认端口：Test-NetConnection 127.0.0.1 -Port 17687。不通看 Docker 容器，通了再核对 BOND_NEO4J_URI。",
            ("Test-NetConnection 127.0.0.1 -Port 17687", "Docker", "BOND_NEO4J_URI"),
        ),
        (
            "Chroma http://127.0.0.1:18000 心跳没响应，先查什么？",
            "先确认 Chroma 活着：Invoke-WebRequest http://127.0.0.1:18000/api/v2/heartbeat。连不上看 Docker；能通再查 collection 名和维度配置。",
            ("Invoke-WebRequest http://127.0.0.1:18000/api/v2/heartbeat", "Docker", "collection"),
        ),
    ],
)
async def test_surface_main_chain_routes_project_technical_cases_through_model(
    monkeypatch,
    message: str,
    model_reply: str,
    expected_fragments: tuple[str, ...],
) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(model_reply)

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_chat_input(
        _agent_input(user_message=message),
        trace_stage_meta=trace,
    )

    for fragment in expected_fragments:
        assert fragment in result.selected_text
    assert "lsof" not in result.selected_text
    assert "grep" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_llm_pool" in trace


@pytest.mark.asyncio
async def test_surface_main_chain_audit_marks_model_path(monkeypatch) -> None:
    post_process_calls: list[dict] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result(
            "老哥，先查占用 PID：netstat -ano | findstr :18080。看到 PID 后再决定关进程还是改端口。"
        )

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        post_process_calls.append(kwargs)

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)

    events = [
        _parse_sse(chunk)
        async for chunk in surface_main_chain_sse_generator(
            _agent_input(user_message="项目启动报错，端口18080被占用了，我该怎么排查？")
        )
    ]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["audit_refs"]["model_id"] == "mock-chat"
    assert commit["audit_refs"]["provider_name"] == "mock"
    assert "surface_quick_reply" not in commit["audit_refs"]["trace_stage_meta"]
    assert "surface_llm_pool" in commit["audit_refs"]["trace_stage_meta"]
    await asyncio.sleep(0)
    assert post_process_calls


@pytest.mark.asyncio
async def test_surface_main_chain_routes_simple_task_split_through_model(monkeypatch) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**_kwargs):  # noqa: ANN003
        return _surface_json_result("老哥，先拆三件：把卡点写清楚；只做一个最小可验证动作；跑完看结果，再决定下一步。")

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)

    result = await run_surface_main_chain_for_chat_input(
        _agent_input(
            user_message="帮我把下一步拆成三件事。",
            surface_task_control={
                "task_mode": "task_help",
                "response_move": "能执行，结构清楚，少废话。",
                "question_budget": 0,
            },
        ),
        trace_stage_meta=trace,
    )

    assert result.selected_text == "老哥，先拆三件：把卡点写清楚；只做一个最小可验证动作；跑完看结果，再决定下一步。"
    assert "空壳" not in result.selected_text
    assert "surface_quick_reply" not in trace
    assert "surface_llm_pool" in trace


def test_surface_main_chain_enabled_defaults_to_normal_text_only(monkeypatch) -> None:
    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    assert surface_main_chain_enabled(_agent_input()) is True

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "false")
    assert surface_main_chain_enabled(_agent_input()) is False

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "true")
    assert surface_main_chain_enabled(_agent_input(attachments=[{"kind": "image"}])) is False
    assert surface_main_chain_enabled(_agent_input(response_form="sticker")) is False
    assert surface_main_chain_enabled(_agent_input(intent_draft={"route": "delegate"})) is True
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="system_info"),
    ) is False
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="codex_worker"),
    ) is True
    assert surface_main_chain_enabled(
        _agent_input(),
        sub_agent_ctx=SimpleNamespace(agent_id="codex_lightnovel_recommender"),
    ) is True
