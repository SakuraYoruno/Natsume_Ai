from __future__ import annotations

import asyncio
import json

import pytest

from host_brain.persona.surface.prompt_construction import build_isolated_surface_messages
from host_brain.persona.surface_packet_compiler import (
    SurfaceTaskControl,
    compile_reply_control_packet,
    compile_surface_input,
    compile_surface_shadow_inputs,
)
from host_brain.persona.surface_runtime import SurfaceRuntime, build_direct_surface_messages


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps(
        {
            "reply_text": candidates[0] if candidates else "",
            "natsume_emotion_tag": "NEUTRAL",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.2,
        },
        ensure_ascii=False,
    )


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


def test_compile_reply_control_packet_prioritizes_dialogue_and_affect_within_bound() -> None:
    compiled = compile_reply_control_packet(
        task_control={
            "facts": [
                "低优先事实一",
                "低优先事实二",
                "低优先事实三",
                "低优先事实四",
                "真实最近对话窗口：用户: 我有点不踏实。 / Natsume: 我记得。",
                "陪伴动作：让开心与不安同时存在。",
                "上下文连续性：上一轮的开心和不踏实仍同时成立。",
            ],
            "affective_continuity_kind": "MIXED_AFFECT_RESULT_CONTINUATION",
        },
        necessary_facts=["必要事实：用户刚拿到 offer。"],
        max_facts=4,
    )

    assert compiled.packet.facts == [
        "真实最近对话窗口：用户: 我有点不踏实。 / Natsume: 我记得。",
        "陪伴动作：让开心与不安同时存在。",
        "上下文连续性：上一轮的开心和不踏实仍同时成立。",
        "必要事实：用户刚拿到 offer。",
    ]
    assert (
        compiled.packet.affective_continuity_kind
        == "MIXED_AFFECT_RESULT_CONTINUATION"
    )


def test_companion_utterance_frame_stays_bounded_and_drives_compact_prompt() -> None:
    frame = {
        "schema_version": "companion_utterance_frame.v1",
        "frame_id": "utterance::demo",
        "companion_act_kind": "COMFORT",
        "semantic_focus": "UNNAMED_USER_AFFECT",
        "self_stance": "QUIET_PRESENT",
        "response_move": "COMFORT_WITHOUT_NAMING",
        "inference_policy": "PRESERVE_UNCERTAINTY",
        "relationship_posture": "CLOSE_WITHOUT_CLAIM",
        "question_policy": "NONE",
        "ending_mode": "NATURAL_STOP",
        "forbidden_claims": [
            "INVENTED_HISTORY",
            "INVENTED_EFFECT",
            "UNSUPPORTED_USER_EMOTION",
        ],
        "created_at_ms": 1,
    }
    compiled = compile_surface_input(
        soul_md=SOUL,
        user_message="我也说不清。",
        task_control={
            "task_mode": "ambiguous_affect",
            "question_budget": 0,
            "companion_utterance_frame": frame,
        },
    )

    assert compiled.packet.companion_utterance_frame == frame
    assert compiled.trace["companion_utterance_frame_id"] == "utterance::demo"
    rendered = "\n".join(
        str(message["content"])
        for message in build_direct_surface_messages(compiled.request)
    )
    assert "本轮说话骨架" in rendered
    assert "承认感受还说不清，不替对方命名" in rendered
    assert "不用问句，把这一轮在陈述里完成" in rendered
    assert "对方没说过的情绪名称、原因或人格判断" in rendered


def test_companion_utterance_frame_rejects_unbounded_or_unknown_semantics() -> None:
    with pytest.raises(ValueError, match="semantic_focus"):
        compile_reply_control_packet(
            task_control={
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "utterance::invalid",
                    "companion_act_kind": "COMFORT",
                    "semantic_focus": "RAW_USER_TRANSCRIPT",
                    "self_stance": "QUIET_PRESENT",
                    "response_move": "COMFORT_WITHOUT_NAMING",
                    "inference_policy": "PRESERVE_UNCERTAINTY",
                    "relationship_posture": "CLOSE_WITHOUT_CLAIM",
                    "question_policy": "NONE",
                    "ending_mode": "NATURAL_STOP",
                    "forbidden_claims": [],
                    "created_at_ms": 1,
                }
            }
        )


def test_compile_reply_control_packet_keeps_delegate_result_within_fact_bound() -> None:
    compiled = compile_reply_control_packet(
        task_control={
            "facts": [
                "低优先事实一",
                "低优先事实二",
                "低优先事实三",
                "低优先事实四",
                "子代理执行状态：已完成",
                "子代理任务：只读安全扫描",
                "子代理结果片段1：9/tcp discard 开放。",
            ],
        },
        max_facts=3,
    )

    assert compiled.packet.facts == [
        "子代理执行状态：已完成",
        "子代理任务：只读安全扫描",
        "子代理结果片段1：9/tcp discard 开放。",
    ]


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


def test_compile_reply_control_packet_makes_explicit_memory_answer_grounded() -> None:
    compiled = compile_reply_control_packet(
        user_message="还记得我之前说主链哪里最容易崩吗？",
        task_control={
            "response_move": "answer_from_memory",
            "task_mode": "recall_memory",
            "question_budget": 0,
        },
        memory_packet={
            "facts": ["用户长期关注中文文本质量，尤其反感生硬、模板感和不通顺。"],
        },
    )

    assert "生硬" in compiled.packet.must_include
    assert "记忆追问：必须直接答出记忆事实本身" in "\n".join(compiled.packet.style_anchor)


def test_compile_reply_control_packet_keeps_structured_memory_comparison_detail() -> None:
    long_fact = (
        "我刚才推荐了 3 本轻小说：1. A；2. 1205.与神在河岸；3. 皇女结婚。；"
        "可比较证据：1. A｜看点：恋爱日常、设定新鲜感｜简介：前美少女童星与搭把手后的关系展开。｜更新：2 小时前｜热度：8评论；"
        "2. 1205.与神在河岸｜看点：公开信号较好：更新、热度或资料完整度较高｜简介：标题和简介带奇幻身份钩子，信息完整度比普通列表项更高。｜更新：1 小时前｜热度：79浏览 / 1评论；"
        "3. 皇女结婚｜看点：奇幻身份钩子、设定新鲜感｜简介：异世界转移、皇女结婚、成为皇帝，路线直球但题材明确。｜更新：5/23｜热度：4.1w浏览 / 28评论"
    )

    compiled = compile_reply_control_packet(
        user_message="这三本书，你喜欢哪本？",
        task_control={
            "response_move": "memory_followup",
            "task_mode": "memory_followup",
            "question_budget": 0,
        },
        memory_packet={"facts": [long_fact]},
    )

    rendered = "\n".join(compiled.packet.facts)
    assert "皇女结婚｜看点：奇幻身份钩子" in rendered
    assert "4.1w浏览" in rendered
    assert compiled.trace["fact_max_chars"] == 900


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


@pytest.mark.parametrize(
    ("user_message", "scene_kind", "fact_fragment", "must_not_fragment", "anchor_fragment"),
    [
        ("早啊", "daily_morning", "没有提供作息、精神状态", "不要提继续文本", "早安句法锚"),
        ("我回来了", "return_chat", "没有提供用户去了哪里、离开多久", "不要编用户迷路", "回来了句法锚"),
        ("在吗", "presence_check", "没有表示出急事、出大事", "不要写居然、稀奇", "在吗句法锚"),
        ("继续", "short_continue", "不要评价用户只说了继续", "不要写这才像话", "短接续句法锚"),
    ],
)
def test_compile_surface_input_adds_low_info_scene_guidance(
    user_message: str,
    scene_kind: str,
    fact_fragment: str,
    must_not_fragment: str,
    anchor_fragment: str,
) -> None:
    compiled = compile_surface_input(
        soul_md=SOUL,
        user_message=user_message,
        case_id=f"low_info_{scene_kind}",
        task_control={
            "response_move": scene_kind,
            "task_mode": scene_kind,
            "question_budget": 0,
        },
    )

    assert compiled.trace["low_info_scene_guidance_applied"] is True
    assert compiled.trace["low_info_scene_kind"] == scene_kind
    assert fact_fragment in "\n".join(compiled.packet.facts)
    assert must_not_fragment in "\n".join(compiled.packet.must_not_do)
    assert anchor_fragment in "\n".join(compiled.packet.style_anchor)
    assert "不要靠固定旧称呼、自称或模板口癖撑短句" in "\n".join(compiled.packet.must_not_do)

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(compiled.request))
    assert fact_fragment in rendered
    assert must_not_fragment in rendered
    assert anchor_fragment in rendered


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
                    "先查输入输出边界：上次就卡在这里，把实际输入和期望输出各留一条，我再对照差在哪。",
                    "上次那块是输入输出，这轮先别整段改。",
                    "上次卡的就是输入输出，先看这里。",
                ]
            )
        if has_memory:
            return _candidate_payload(
                [
                    "先查输入输出边界：上次就卡在这里，把实际输入和期望输出各留一条。",
                    "上次那块是输入输出，这轮先别整段动。",
                    "记得，上次卡在输入输出，先从这儿下手。",
                ]
            )
        if has_affect:
            return _candidate_payload(
                [
                    "先查输入输出边界：把实际输入和期望输出各留一条，我跟着你对照。",
                    "先别硬改，输入输出那块先定住。",
                    "稳住，先把输入输出看清。",
                ]
            )
        return _candidate_payload(
            [
                "先查输入输出边界：把实际输入和期望输出各留一条，我再对照差在哪。",
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
        assert result.selected.as_dict()["text"] == result.selected_text
        assert "score" not in result.selected.as_dict()
        assert "passed" not in result.selected.as_dict()
        assert not result.observation_codes, result.selected.as_dict()
        assert "输入输出" in result.selected_text
        assert "raw" not in result.selected_text.lower()
        assert "召回来源" not in result.selected_text
    assert "上次" in results["memory"].selected_text
    assert "上次" in results["memory_affect"].selected_text
