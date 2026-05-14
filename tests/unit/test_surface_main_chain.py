from __future__ import annotations

import asyncio
import json

import pytest

from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    compile_surface_main_chain_input,
    run_surface_main_chain,
)


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def test_surface_main_chain_runs_compiler_and_runtime() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages: list[dict[str, str]]) -> str:
        calls.append(messages)
        rendered = "\n".join(message["content"] for message in messages)
        assert "ReplyControlPacket" in rendered
        assert "memory://episodic/1" not in rendered
        assert "RAW_MEMORY_ROW_SHOULD_NOT_PASS" not in rendered
        return _candidate_payload(
            [
                "上次卡在输入输出，这轮先盯那一块。",
                "先别整段改，输入输出那块先看清。",
                "我知道你卡哪儿，先看输入输出。",
            ]
        )

    result = asyncio.run(
        SurfaceMainChain(generate, repair_failed=False, use_style_reference_bank=False).run(
            {
                "case_id": "surface_main_chain_probe",
                "soul_md": SOUL,
                "user_message": "这段我不会改",
                "task_control": {
                    "response_move": "technical_next_step",
                    "task_mode": "technical_help",
                    "question_budget": 0,
                    "facts": ["当前任务：先看输入输出，不急着改全段。"],
                },
                "memory_packet": {
                    "facts": ["用户上次改这类段落时卡在输入输出边界。"],
                    "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                    "evidence_refs": ["evd://episodic/1"],
                    "source_refs": ["memory://episodic/1"],
                    "rows": [{"snippet": "RAW_MEMORY_ROW_SHOULD_NOT_PASS"}],
                },
                "affect_packet": {
                    "emotional_tone": "steady_helpful",
                    "relationship_distance": "familiar",
                    "must_not_do": ["不要只安慰不解决"],
                },
            }
        )
    )

    assert calls
    assert result.selected_text in {
        "上次卡在输入输出，这轮先盯那一块。",
        "先别整段改，输入输出那块先看清。",
        "我知道你卡哪儿，先看输入输出。",
    }
    assert result.runtime.selected is not None
    assert result.runtime.selected.passed
    assert result.observation_codes == ()
    assert result.compiled.trace["surface_boundary"] == "ReplyControlPacket"
    assert result.as_dict()["surface_main_chain"] is True


def test_surface_main_chain_dataclass_input_compiles_to_isolated_request() -> None:
    compiled = compile_surface_main_chain_input(
        SurfaceMainChainInput(
            soul_md=SOUL,
            user_message="继续",
            case_id="surface_main_chain_dataclass",
            task_control={
                "response_move": "next_step",
                "task_mode": "next_step",
                "question_budget": 0,
                "must_include": ["继续"],
                "facts": ["目标是继续走主链 facade。"],
            },
            necessary_facts=("必要事实：上层只调用 facade。",),
        )
    )

    assert compiled.request.case_id == "surface_main_chain_dataclass"
    assert compiled.request.user_message == "继续"
    assert compiled.packet.response_move == "next_step"
    assert "必要事实：上层只调用 facade。" in compiled.packet.facts


def test_surface_main_chain_rejects_low_level_packet_bypass() -> None:
    with pytest.raises(ValueError, match="does not accept low-level fields"):
        compile_surface_main_chain_input(
            {
                "soul_md": SOUL,
                "user_message": "继续",
                "reply_control_packet": {"task_mode": "next_step"},
            }
        )


def test_surface_main_chain_rejects_raw_context_inside_packets() -> None:
    with pytest.raises(ValueError, match="forbidden raw context"):
        compile_surface_main_chain_input(
            {
                "soul_md": SOUL,
                "user_message": "继续",
                "task_control": {
                    "task_mode": "next_step",
                    "raw_history": [{"role": "user", "content": "旧消息"}],
                },
            }
        )


def test_run_surface_main_chain_convenience_function() -> None:
    async def generate(_messages: list[dict[str, str]]) -> str:
        return _candidate_payload(["继续，先把 facade 接上。"])

    result = asyncio.run(
        run_surface_main_chain(
            generate,
            {
                "soul_md": SOUL,
                "user_message": "继续",
                "task_control": {
                    "response_move": "next_step",
                    "task_mode": "next_step",
                    "question_budget": 0,
                    "must_include": ["继续"],
                    "facts": ["当前任务：接 surface main chain facade。"],
                },
            },
            repair_failed=False,
            use_style_reference_bank=False,
        )
    )

    assert result.selected_text == "继续，先把 facade 接上。"
    assert result.runtime.selected is not None
    assert result.runtime.selected.passed
