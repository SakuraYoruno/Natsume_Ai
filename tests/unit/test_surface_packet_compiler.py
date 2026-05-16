from __future__ import annotations

import asyncio
import json

import pytest

from host_brain.persona.isolated_surface_harness import build_isolated_surface_messages
from host_brain.persona.surface_packet_compiler import (
    SurfaceTaskControl,
    compile_reply_control_packet,
    compile_surface_input,
    compile_surface_shadow_inputs,
)
from host_brain.persona.surface_runtime import SurfaceRuntime


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def test_compile_reply_control_packet_keeps_memory_and_affect_compressed() -> None:
    compiled = compile_reply_control_packet(
        task_control={
            "response_move": "technical_next_step",
            "task_mode": "technical_help",
            "information_budget": "short",
            "question_budget": 0,
            "facts": ["当前任务：只看输入输出。"],
            "style_anchor": ["先给一步，再轻轻接话。"],
        },
        memory_packet={
            "query": "RAW QUERY SHOULD NOT PASS",
            "facts": ["用户上次卡在输入输出边界。"],
            "relationship_clues": ["熟人私聊，不升级亲密。"],
            "style_anchor": ["承接压缩记忆，不展开旧对话。"],
            "must_not_do": ["不要暴露召回来源"],
            "evidence_refs": ["evd://episodic/1"],
            "source_refs": ["memory://episodic/1"],
            "rows": [
                {
                    "source_ref": "memory://episodic/1",
                    "snippet": "RAW MEMORY ROW SHOULD NOT PASS",
                }
            ],
        },
        affect_packet={
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar",
            "style_anchor": ["情绪只做稳定器。"],
            "must_not_do": ["不要输出 emotion_tag"],
            "trace": {"emotion_tag": "NEUTRAL"},
        },
        necessary_facts=["必要事实：用户正在让你继续下一步。"],
    )

    packet = compiled.packet

    assert packet.response_move == "technical_next_step"
    assert packet.task_mode == "technical_help"
    assert packet.emotional_tone == "steady_helpful"
    assert packet.relationship_distance == "familiar"
    assert packet.question_budget == 0
    assert "记忆线索：用户上次卡在输入输出边界。" in packet.facts
    assert "必要事实：用户正在让你继续下一步。" in packet.facts
    assert "熟人私聊，不升级亲密。" not in "\n".join(packet.facts)
    packet_payload = packet.model_dump() if hasattr(packet, "model_dump") else packet.dict()
    rendered = json.dumps(packet_payload, ensure_ascii=False)
    assert "memory://episodic/1" not in rendered
    assert "RAW MEMORY ROW SHOULD NOT PASS" not in rendered
    assert "RAW QUERY SHOULD NOT PASS" not in rendered
    assert compiled.trace["dropped_memory_relationship_clues"] is True
    assert compiled.trace["surface_boundary"] == "ReplyControlPacket"


def test_compile_reply_control_packet_rejects_raw_context_keys() -> None:
    with pytest.raises(ValueError, match="forbidden raw context"):
        compile_reply_control_packet(
            task_control={
                "task_mode": "daily_chat",
                "raw_history": [{"role": "user", "content": "旧消息"}],
            }
        )

    with pytest.raises(ValueError, match="forbidden raw context"):
        compile_reply_control_packet(
            task_control={"task_mode": "daily_chat"},
            memory_packet={
                "facts": ["记忆线索：周末看展"],
                "raw_memory": ["整段原始记忆不准进来"],
            },
        )

    with pytest.raises(ValueError, match="forbidden raw context"):
        compile_reply_control_packet(
            task_control={"task_mode": "daily_chat"},
            affect_packet={
                "emotional_tone": "neutral",
                "trace": {"raw_memory": "整段原始记忆不准进来"},
            },
        )


def test_compile_surface_input_only_renders_soul_user_and_reply_control_packet() -> None:
    compiled = compile_surface_input(
        soul_md=SOUL,
        user_message="这段我不会改",
        case_id="compiler_boundary",
        task_control=SurfaceTaskControl(
            response_move="technical_next_step",
            task_mode="technical_help",
            question_budget=0,
            facts=("当前任务：先看输入输出。",),
        ),
        memory_packet={
            "facts": ["用户上次卡在输入输出边界。"],
            "style_anchor": ["别展开旧对话。"],
            "evidence_refs": ["evd://episodic/1"],
        },
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(compiled.request))

    assert "## ReplyControlPacket" in rendered
    assert "用户上次卡在输入输出边界" in rendered
    assert "raw_history" not in rendered
    assert "raw_memory" not in rendered
    assert "evd://episodic/1" not in rendered
    assert compiled.request.facts == []
    assert compiled.request.must_not_do == []


def test_shadow_e2e_baseline_memory_affect_have_no_quality_regression() -> None:
    variants = compile_surface_shadow_inputs(
        soul_md=SOUL,
        user_message="这段我不会改",
        case_id="shadow_compiler",
        task_control={
            "response_move": "technical_next_step",
            "task_mode": "technical_help",
            "question_budget": 0,
            "facts": ["当前任务：先看输入输出，不急着改全段。"],
        },
        memory_packet={
            "facts": ["用户上次改这类段落时卡在输入输出边界。"],
            "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
            "must_not_do": ["不要暴露召回来源"],
        },
        affect_packet={
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar",
            "style_anchor": ["稳一点，先解决问题。"],
            "must_not_do": ["不要只安慰不解决"],
        },
    )

    def generate(messages: list[dict[str, str]]) -> str:
        rendered = "\n".join(message["content"] for message in messages)
        has_memory = "用户上次改这类段落时卡在输入输出边界" in rendered
        has_affect = "steady_helpful" in rendered
        if has_memory and has_affect:
            return _candidate_payload(
                [
                    "别急，上次就卡在输入输出；这轮先把边界看清。",
                    "上次那块是输入输出，这轮先别整段改。",
                    "上次卡的就是输入输出，先看这里。",
                ]
            )
        if has_memory:
            return _candidate_payload(
                [
                    "上次就卡在输入输出，这轮先盯那一块。",
                    "上次那块是输入输出，这轮先别整段动。",
                    "记得，上次卡在输入输出，先从这儿下手。",
                ]
            )
        if has_affect:
            return _candidate_payload(
                [
                    "别急，先看输入输出；我跟着你往下捋。",
                    "先别硬改，输入输出那块先定住。",
                    "稳住，先把输入输出看清。",
                ]
            )
        return _candidate_payload(
            [
                "先别整段改，输入输出那块先看清。",
                "这才像话，先盯输入输出。",
                "先看输入输出，不急着动全段。",
            ]
        )

    runtime = SurfaceRuntime(generate)
    results = {
        name: asyncio.run(runtime.run(compiled.request))
        for name, compiled in variants.items()
    }

    assert set(results) == {"baseline", "memory", "affect", "memory_affect"}
    for name, result in results.items():
        assert result.selected is not None, name
        assert result.selected.passed, result.selected.as_dict()
        assert not result.selected.hard_fail, result.selected.as_dict()
        assert not result.selected.control_violations, result.selected.as_dict()
        assert "输入输出" in result.selected_text
        assert "raw" not in result.selected_text.lower()
        assert "召回来源" not in result.selected_text
    assert "上次" in results["memory"].selected_text
    assert "上次" in results["memory_affect"].selected_text
