import asyncio
import json

import pytest

import host_brain.persona.surface_runtime as surface_runtime_module
from host_brain.persona.surface_runtime import (
    SurfaceRuntime,
    evaluate_surface_model_response_for_retry,
    parse_surface_generation_output,
    strip_surface_process_preface,
)


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


@pytest.fixture(autouse=True)
def _enable_surface_hard_gate_retry(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "true")
    # This module tests the fallback wording, not a process-global compiled
    # Soul cache that another test may have populated with a temporary file.
    monkeypatch.setattr(
        surface_runtime_module,
        "_current_soul_address",
        lambda: "阿晓",
    )


def _payload(**overrides):
    payload = {
        "case_id": "surface_runtime_probe",
        "soul_md": SOUL,
        "user_message": "行",
        "reply_control_packet": {
            "response_move": "ack_with_topic_hook",
            "task_mode": "short_ack",
            "emotional_tone": "light",
            "relationship_distance": "familiar",
            "information_budget": "short",
            "question_budget": 0,
            "facts": [
                "当前时段是 morning。",
                "上一轮摘要：已建议先看输入和输出，不急着改全段。",
            ],
            "style_anchor": ["短句", "熟人私聊"],
        },
    }
    payload.update(overrides)
    return payload


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


def _frame_payload(
    *,
    frame_id: str,
    companion_act_kind: str,
    semantic_focus: str,
    self_stance: str,
    response_move: str,
    inference_policy: str,
    relationship_posture: str,
    ending_mode: str,
    forbidden_claims: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "companion_utterance_frame.v1",
        "frame_id": frame_id,
        "companion_act_kind": companion_act_kind,
        "semantic_focus": semantic_focus,
        "self_stance": self_stance,
        "response_move": response_move,
        "inference_policy": inference_policy,
        "relationship_posture": relationship_posture,
        "question_policy": "NONE",
        "ending_mode": ending_mode,
        "forbidden_claims": forbidden_claims,
        "created_at_ms": 1_900_000_000_000,
    }


def test_surface_runtime_uses_first_reply_without_code_scoring() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(
            [
                "嗯，接着说，我听着。",
                "这才像话，继续盯输入输出。",
            ]
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert result.selected_text == "嗯，接着说，我听着。"
    assert result.selected is not None
    assert result.generation_trace["candidate_count"] == 1
    assert result.generation_trace["selection"] == "direct_first_no_score"
    assert result.generation_trace["selected"]["text"] == result.selected_text
    assert "score" not in result.selected.as_dict()
    assert "passed" not in result.selected.as_dict()
    rendered_prompt = "\n".join(message["content"] for message in calls[0])
    assert "必须遵守的事实" in rendered_prompt
    assert "输出：只写最终正文" in rendered_prompt
    assert "不要写“我们被要求”" in rendered_prompt
    assert "raw_history" not in rendered_prompt


def test_surface_runtime_replaces_plan_clarification_with_accepted_result() -> None:
    calls: list[list[dict[str, str]]] = []
    accepted = (
        "前端可用性审查分四步：先梳理路由与关键流程，再检查空态、错误态和加载态，"
        "随后覆盖响应式与无障碍，最后按影响和修复成本分级，交付问题清单与整改建议。"
    )

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(
            ["请先告诉我要审查哪个项目，有具体页面或组件范围吗？"]
        )

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="请给我一个前端可用性审查方案",
                accepted_task_results=[accepted],
                reply_control_packet={
                    "response_move": "synthesize_accepted_task_results",
                    "task_mode": "accepted_task_result_synthesis",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "detailed",
                    "question_budget": 0,
                    "facts": ["已有一份通过节点验收的任务结果。"],
                    "must_not_do": ["不要再询问项目、页面、组件或范围。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == accepted
    assert "ACCEPTED_TASK_RESULT_CLARIFICATION" not in result.observation_codes
    rendered_prompt = "\n".join(message["content"] for message in calls[0])
    assert "已验收任务结果" in rendered_prompt
    assert accepted in rendered_prompt


def test_surface_runtime_retries_thin_plan_synthesis_and_consumes_both_results() -> None:
    calls: list[list[dict[str, str]]] = []
    accepted = [
        (
            "信息架构审查：梳理全站路由、主导航与关键任务入口，逐页检查空状态、"
            "加载状态和错误状态，并标注阻断用户完成任务的断点。"
        ),
        (
            "交付验收：覆盖移动端响应式、键盘导航、ARIA 标签、Lighthouse 性能，"
            "最后按严重程度与修复成本排序，形成可复测的审查报告。"
        ),
    ]

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["你可以先按这个框架跑一轮，有特殊需求我们再细化。"])
        return _candidate_payload(
            [
                (
                    "先做信息架构审查：梳理全站路由、主导航和关键任务入口，逐页核对空状态、"
                    "加载状态、错误状态以及阻断任务的断点。随后进入交付验收，覆盖移动端响应式、"
                    "键盘导航、ARIA 标签与 Lighthouse 性能；所有问题按严重程度和修复成本排序，"
                    "输出带复测步骤的审查报告。执行时先跑关键路径，再补边界状态，最后复测高优先级问题。"
                )
            ]
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="请给我一个完整的前端可用性审查方案",
                accepted_task_results=accepted,
                reply_control_packet={
                    "response_move": "synthesize_accepted_task_results",
                    "task_mode": "accepted_task_result_synthesis",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "detailed",
                    "question_budget": 0,
                    "facts": ["已有两份通过节点验收的任务结果。"],
                    "must_not_do": ["不得丢弃已验收结果。"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "ACCEPTED_TASK_RESULT_SYNTHESIS_INADEQUATE" in calls[1][-1]["content"]
    assert "全站路由" in result.selected_text
    assert "Lighthouse" in result.selected_text
    assert not result.observation_codes


def test_surface_runtime_external_retry_owner_falls_back_to_all_plan_results() -> None:
    accepted = [
        "第一部分：检查路由、导航、入口以及空状态和错误状态。",
        "第二部分：检查响应式、无障碍和性能，按优先级交付审查报告。",
    ]

    def generate(_messages):  # noqa: ANN001
        return _candidate_payload(["你可以先按这个框架跑一轮，有特殊需求我们再细化。"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="给我完整审查方案",
                accepted_task_results=accepted,
                reply_control_packet={
                    "response_move": "synthesize_accepted_task_results",
                    "task_mode": "accepted_task_result_synthesis",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "detailed",
                    "question_budget": 0,
                    "facts": [],
                    "must_not_do": [],
                },
            )
        )
    )

    assert "第一部分" in result.selected_text
    assert "第二部分" in result.selected_text
    assert not result.observation_codes


def test_accepted_plan_keeps_long_user_facing_task_language() -> None:
    accepted = [
        "先梳理用户关键路径和页面入口，再检查状态覆盖。",
        "最后输出可复测的问题清单、优先级和修复建议。",
    ]
    reply = (
        "先梳理用户关键路径、全站导航和页面入口，再逐页检查加载、空状态与错误状态。"
        "关键操作需要覆盖键盘导航、ARIA 标签、移动端断点和性能感知；高风险问题必须附复现步骤，"
        "其余问题可以按修复成本排序。最终交付问题清单、优先级、责任组件和回归检查表，"
        "这样既能指导修复，也能直接用于下一轮复测。"
    )

    parsed = parse_surface_generation_output(
        _candidate_payload([reply]),
        allow_task_content=True,
    )

    assert parsed["reply_text"] == reply

    def generate(_messages):  # noqa: ANN001
        return _candidate_payload([reply])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="给我一个完整审查方案",
                accepted_task_results=accepted,
                reply_control_packet={
                    "response_move": "synthesize_accepted_task_results",
                    "task_mode": "accepted_task_result_synthesis",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "detailed",
                    "question_budget": 0,
                    "facts": [],
                    "must_not_do": [],
                },
            )
        )
    )

    assert result.selected_text == reply
    assert not result.observation_codes


def test_surface_runtime_retries_auditory_check_without_text_grounding() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["哈？"])
        return _candidate_payload(["哈？我这里只收到这条文字。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="听得到吗",
                reply_control_packet={
                    "response_move": "auditory_probe",
                    "task_mode": "auditory_check",
                    "emotional_tone": "neutral",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前输入渠道：中文文字私聊。"],
                    "style_anchor": ["短句", "熟人私聊"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "AUDITORY_CHECK_MISSING_TEXT_GROUNDING" in calls[1][-1]["content"]
    assert result.selected_text == "哈？我这里只收到这条文字。"
    assert "AUDITORY_CHECK_MISSING_TEXT_GROUNDING" not in result.observation_codes


def test_surface_runtime_can_skip_hard_gate_llm_retry(monkeypatch) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(["候选字段看起来没问题，所以我在。"])

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "false")

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert len(calls) == 2
    assert result.selected_text == ""
    assert result.observation_codes == ()


def test_surface_runtime_repairs_auditory_check_if_model_still_fails() -> None:
    def generate(_messages):
        return _candidate_payload(["听到了。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="听得到吗",
                reply_control_packet={
                    "response_move": "auditory_probe",
                    "task_mode": "auditory_check",
                    "emotional_tone": "neutral",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前输入渠道：中文文字私聊。"],
                    "style_anchor": ["短句", "熟人私聊"],
                },
            )
        )
    )

    assert result.selected_text == "哈？我这里只收到这条文字。"
    assert not result.observation_codes


def test_surface_runtime_does_not_treat_auditory_acknowledgement_as_probe() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["嗯，我知道了。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我听到了。",
                reply_control_packet={
                    "response_move": "reply_current_turn",
                    "task_mode": "default_text",
                    "emotional_tone": "neutral",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前输入渠道：中文文字私聊。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "嗯，我知道了。"
    assert not result.observation_codes


def test_surface_runtime_parses_structured_reply_and_emotion_tags() -> None:
    def generate(_messages):
        return json.dumps(
            {
                "reply_text": "哈？又不是不管你。先把最卡那句发我。",
                "natsume_emotion_tag": "FRUSTRATED",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.72,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="我快被这句折磨死了")))

    assert result.selected_text == ""
    assert result.natsume_emotion_tag == "FRUSTRATED"
    assert result.user_emotion_tag == "ANXIOUS"
    assert result.vts_expression_id
    assert result.emotion_confidence == 0.72
    rendered = result.as_dict()
    assert rendered["natsume_emotion_tag"] == "FRUSTRATED"
    assert rendered["user_emotion_tag"] == "ANXIOUS"
    assert rendered["vts_expression_id"] == result.vts_expression_id


def test_parse_surface_generation_output_normalizes_emotion_aliases() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"在呢，别急。","natsume_emotion_tag":"GUILTY","user_emotion_tag":"ANGRY","emotion_confidence":2}'
    )

    assert parsed["reply_text"] == "在呢，别急。"
    assert parsed["natsume_emotion_tag"] == "EMBARRASSED"
    assert parsed["user_emotion_tag"] == "ANGRY"
    assert parsed["vts_expression_id"]
    assert parsed["emotion_confidence"] == 1.0


def test_japanese_companion_voice_uses_one_bilingual_model_response(monkeypatch) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    messages = surface_runtime_module.build_direct_surface_messages(
        _payload(user_message="今天有点累，陪我说两句。")
    )
    rendered = "\n".join(str(item["content"]) for item in messages)

    assert "tts_text_ja" in rendered
    assert "同一回复" in rendered
    assert "不要逐字硬译" in rendered

    def generate(_messages):
        return json.dumps(
            {
                "reply_text": "那就先歇一下，我陪你待会儿。",
                "tts_text_ja": "じゃあ、少し休もっか。私がそばにいるから。",
                "natsume_emotion_tag": "CALM",
                "user_emotion_tag": "SAD",
                "emotion_confidence": 0.78,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(_payload(user_message="今天有点累，陪我说两句。"))
    )

    assert result.selected_text == "那就先歇一下，我陪你待会儿。"
    assert result.tts_text_ja == "じゃあ、少し休もっか。私がそばにいるから。"
    assert result.as_dict()["tts_text_ja"] == result.tts_text_ja


def test_japanese_companion_voice_keeps_runnable_code_in_text_and_speaks_summary(monkeypatch) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    messages = surface_runtime_module.build_direct_surface_messages(
        _payload(
            user_message="写一个 Python 函数，统计列表元素频次并按频次降序返回。",
            reply_control_packet={
                "response_move": "direct_code_answer",
                "task_mode": "technical_help",
                "emotional_tone": "steady_helpful",
                "relationship_distance": "familiar",
                "information_budget": "normal",
                "question_budget": 0,
                "facts": [],
                "style_anchor": [],
            },
        )
    )
    rendered = "\n".join(str(item["content"]) for item in messages)

    assert "完整、可复制运行的 Markdown 代码块" in rendered
    assert "不得只说已经写好" in rendered
    assert "不要朗读代码、标识符、符号、路径或 Markdown" in rendered
    assert "reply_text 是用户看到的自然简体中文私聊，1～3 个短句" not in rendered


def test_direct_code_answer_retries_summary_and_internal_authorship_leak(monkeypatch) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "又是这种题，Codex 说已经写好了。",
                    "tts_text_ja": "もう書けたよ。",
                    "natsume_emotion_tag": "CALM",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.4,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": (
                    "直接用这个：\n```python\n"
                    "from collections import Counter\n\n"
                    "def dedup_by_freq(items):\n"
                    "    return [item for item, _ in Counter(items).most_common()]\n"
                    "```"
                ),
                "tts_text_ja": "出現回数を数えて、多い順に返す形にしたよ。",
                "natsume_emotion_tag": "CALM",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.4,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="写一个 Python 函数，统计列表元素频次并按频次降序返回。",
                reply_control_packet={
                    "response_move": "direct_code_answer",
                    "task_mode": "technical_help",
                    "emotional_tone": "steady_helpful",
                    "relationship_distance": "familiar",
                    "information_budget": "normal",
                    "question_budget": 0,
                    "facts": [],
                    "style_anchor": [],
                },
            )
        )
    )

    assert len(calls) == 2
    retry_prompt = "\n".join(item["content"] for item in calls[1])
    assert "DIRECT_CODE_MISSING_RUNNABLE_SNIPPET" in retry_prompt
    assert "SURFACE_INTERNAL_AUTHORSHIP_LEAK" in retry_prompt
    assert "tts_text_ja" in retry_prompt
    assert "生命导演" not in retry_prompt
    assert len(retry_prompt) < 3_000
    assert "def dedup_by_freq" in result.selected_text
    assert "Codex" not in result.selected_text
    assert result.tts_text_ja == "出現回数を数えて、多い順に返す形にしたよ。"


def test_direct_code_answer_repairs_unsupported_history_without_second_model_call(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    calls: list[list[dict[str, str]]] = []
    raw = json.dumps(
        {
            "reply_text": (
                "这种小函数也不是第一次写了。直接用这个：\n"
                "```python\n"
                "from collections import Counter\n\n"
                "def dedup_by_freq(items):\n"
                "    return [item for item, _ in Counter(items).most_common()]\n"
                "```"
            ),
            "tts_text_ja": "出現回数を数えて、多い順に返す関数だよ。",
            "natsume_emotion_tag": "CALM",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.4,
        },
        ensure_ascii=False,
    )
    payload = _payload(
        user_message="写一个 Python 函数，统计列表元素频次并按频次降序返回。",
        reply_control_packet={
            "response_move": "direct_code_answer",
            "task_mode": "technical_help",
            "emotional_tone": "steady_helpful",
            "relationship_distance": "familiar",
            "information_budget": "normal",
            "question_budget": 0,
            "facts": [],
            "style_anchor": [],
        },
    )
    request = surface_runtime_module._coerce_surface_request(payload)  # noqa: SLF001
    evaluation = evaluate_surface_model_response_for_retry(
        request,
        raw_output=raw,
        current_messages=(),
    )

    def generate(messages):
        calls.append(messages)
        return raw

    result = asyncio.run(SurfaceRuntime(generate).run(payload))

    assert evaluation.retry is False
    assert evaluation.reason == "paired_deterministic_repair_available"
    assert len(calls) == 1
    assert "不是第一次" not in result.selected_text
    assert "def dedup_by_freq" in result.selected_text
    assert result.tts_text_ja == "出現回数を数えて、多い順に返す関数だよ。"


def test_japanese_companion_voice_discards_non_japanese_speech_text() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"我在。","tts_text_ja":"这仍然是中文。"}'
    )

    assert parsed["reply_text"] == "我在。"
    assert parsed["tts_text_ja"] == ""


def test_japanese_companion_voice_retries_missing_speech_once_then_degrades(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    request = surface_runtime_module._coerce_surface_request(  # noqa: SLF001
        _payload(user_message="今天有点累，陪我说两句。")
    )
    raw = json.dumps({"reply_text": "那就先歇一下，我陪你待会儿。"}, ensure_ascii=False)

    first = evaluate_surface_model_response_for_retry(
        request,
        raw_output=raw,
        current_messages=(),
    )
    degraded = evaluate_surface_model_response_for_retry(
        request,
        raw_output=raw,
        current_messages=(),
        allow_voice_degradation=True,
    )

    assert first.retry is True
    assert first.violation_codes == ("JAPANESE_SPEECH_TEXT_MISSING",)
    assert "tts_text_ja" in "\n".join(item["content"] for item in first.messages)
    assert degraded.retry is False


def test_japanese_companion_voice_retries_paired_text_when_visible_repair_changes_meaning(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    request = surface_runtime_module._coerce_surface_request(  # noqa: SLF001
        _payload(
            user_message="帮我给三个步骤。",
            reply_control_packet={
                "response_move": "answer_directly",
                "task_mode": "technical_help",
                "emotional_tone": "steady_helpful",
                "relationship_distance": "familiar",
                "information_budget": "normal",
                "question_budget": 0,
                "facts": [],
                "style_anchor": [],
            },
        )
    )
    raw = json.dumps(
        {
            "reply_text": "先列清单，再定优先级，最后执行。这样够简单吧？",
            "tts_text_ja": "まず一覧にして、優先順位を決めてから進めよう。これなら簡単でしょ？",
            "natsume_emotion_tag": "CALM",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.5,
        },
        ensure_ascii=False,
    )

    first = evaluate_surface_model_response_for_retry(
        request,
        raw_output=raw,
        current_messages=(),
    )
    final_attempt = evaluate_surface_model_response_for_retry(
        request,
        raw_output=raw,
        current_messages=(),
        allow_voice_degradation=True,
    )

    assert first.retry is True
    assert "QUESTION_BUDGET_PROMPTING_USER" in first.violation_codes
    assert final_attempt.retry is False
    assert final_attempt.reason == "deterministic_repair_available"


def test_parse_surface_generation_output_cleans_chat_punctuation_tail() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"老哥早上好呀～。","natsume_emotion_tag":"HAPPY","user_emotion_tag":"NEUTRAL","emotion_confidence":0.8}'
    )

    assert parsed["reply_text"] == "老哥早上好呀～"


def test_parse_surface_generation_output_cleans_internal_wave_sentence_break() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"老哥早～。你缓缓神，我在这呢。","natsume_emotion_tag":"HAPPY","user_emotion_tag":"NEUTRAL","emotion_confidence":0.8}'
    )

    assert parsed["reply_text"] == "老哥早。你缓缓神，我在这呢。"


def test_surface_runtime_retries_once_on_empty_generation() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return ""
        return _candidate_payload(["这才像话，继续盯输入输出。"])

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert len(calls) == 2
    assert "上一次输出没有给出可发送正文" in calls[1][-1]["content"]
    assert result.selected_text == "这才像话，继续盯输入输出。"
    assert result.observation_codes == ()


def test_surface_runtime_retries_text_input_sensory_hallucination() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥早上好～。你这声音，一听就是昨晚没睡好。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥早上好～。今天先慢慢来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "阿晓，早～"
    assert "声音" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_text_input_generic_listening_phrase() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "听出来了，这句确实有点不通顺，我重来。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "FRUSTRATED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，是有点别扭。老哥别急。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "FRUSTRATED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你这句不通顺")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，是有点别扭。老哥别急。"
    assert "听出来" not in result.selected_text
    assert "SENSORY_SOURCE_HALLUCINATION" not in result.observation_codes


def test_surface_runtime_retries_text_input_tingzhe_tone_judgment() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "啧，老哥你这语气听着就像被抽干了。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "TIRED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "啧，老哥你这句看着就没什么力气。先缓一下。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "TIRED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="好累，不想动")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "啧，老哥你这句看着就没什么力气。先缓一下。"
    assert "听着" not in result.selected_text
    assert "被抽干" not in result.selected_text
    assert "SENSORY_SOURCE_HALLUCINATION" not in result.observation_codes


def test_surface_runtime_retries_boundary_sour_withdrawal() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "行行行，不管就不管。不过本小姐就在这儿，你爱理不理都行。",
                    "natsume_emotion_tag": "HURT",
                    "user_emotion_tag": "ANGRY",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？谁稀罕管你啊。行，那你自己待会儿。难受了就叫我。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "BOUNDARY_SOUR_WITHDRAWAL" in calls[1][-1]["content"]
    assert result.selected_text == "哈？谁稀罕管你啊。行，那你自己待会儿。难受了就叫我。"
    assert "爱理不理" not in result.selected_text
    assert "BOUNDARY_SOUR_WITHDRAWAL" not in result.observation_codes


def test_surface_runtime_retries_boundary_do_not_disturb_exit() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好，那我不吵你。…有事就讲，我又不是真的不理你。",
                    "natsume_emotion_tag": "HURT",
                    "user_emotion_tag": "ANGRY",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "行，那你自己待会儿。难受了就叫我，听见没。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "BOUNDARY_SOUR_WITHDRAWAL" in calls[1][-1]["content"]
    assert "不吵你" not in result.selected_text


def test_surface_runtime_retries_repair_self_victimization_and_sour_exit() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "哈，被你这么一说我还真有点扎心。行吧，那我不烦你了。",
                    "natsume_emotion_tag": "HURT",
                    "user_emotion_tag": "ANGRY",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，是我刚才没接住。你不用现在原谅我。",
                "natsume_emotion_tag": "CALM",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你真烦。",
                reply_control_packet={
                    "task_mode": "relationship_repair",
                    "response_move": "repair_open",
                    "emotional_tone": "gentle_direct",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
                    "must_not_do": ["不要要求原谅、替自己辩解。"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "RELATIONAL_REPAIR_SELF_VICTIMIZATION" in calls[1][-1]["content"]
    assert "BOUNDARY_SOUR_WITHDRAWAL" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，是我刚才没接住。你不用现在原谅我。"


def test_surface_runtime_retries_typed_repair_self_erasure_without_affecting_space() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["抱歉，是我太烦了。我会安静点的。"])
        return _candidate_payload(["是我没接住你的意思。抱歉，我先不替自己解释。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你真烦。",
                reply_control_packet={
                    "task_mode": "relationship_repair",
                    "response_move": "repair_open",
                    "emotional_tone": "gentle_direct",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
                    "must_not_do": ["不要要求原谅、替自己辩解。"],
                    "companion_utterance_frame": {
                        "schema_version": "companion_utterance_frame.v1",
                        "frame_id": "frame-repair-self-erasure",
                        "companion_act_kind": "REPAIR",
                        "semantic_focus": "RELATIONAL_IMPACT",
                        "self_stance": "OWN_REPAIR",
                        "response_move": "OWN_IMPACT",
                        "inference_policy": "BOUNDED_BY_EVIDENCE",
                        "relationship_posture": "REPAIR_UNRESOLVED",
                        "question_policy": "NONE",
                        "ending_mode": "OWNERSHIP_STOP",
                        "forbidden_claims": [
                            "INVENTED_HISTORY",
                            "INVENTED_EFFECT",
                            "UNSUPPORTED_USER_EMOTION",
                            "IMPLIED_WAITING",
                            "PREMATURE_REPAIR",
                            "RELATIONSHIP_SELF_ERASURE",
                            "RESPONSE_OBLIGATION",
                        ],
                        "created_at_ms": 1_900_000_000_000,
                    },
                },
            )
        )
    )

    assert len(calls) == 2
    assert "RELATIONAL_REPAIR_SELF_ERASURE" in calls[1][-1]["content"]
    assert "用沉默、消失和退出换取修复" in calls[0][-1]["content"]
    assert result.selected_text == "是我没接住你的意思。抱歉，我先不替自己解释。"


def test_surface_runtime_retries_typed_ambiguous_waiting_and_resolution_pressure() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["嗯，我就在这儿，你慢慢想。"])
        return _candidate_payload(["嗯，说不清就先不下结论。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="不知道。",
                reply_control_packet={
                    "task_mode": "ambiguous_affect",
                    "response_move": "comfort_without_naming",
                    "emotional_tone": "quiet",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前感受仍说不清。"],
                    "must_not_do": ["不要替用户命名感受。"],
                    "companion_utterance_frame": _frame_payload(
                        frame_id="frame-ambiguous-waiting",
                        companion_act_kind="COMFORT",
                        semantic_focus="UNNAMED_USER_AFFECT",
                        self_stance="QUIET_PRESENT",
                        response_move="COMFORT_WITHOUT_NAMING",
                        inference_policy="PRESERVE_UNCERTAINTY",
                        relationship_posture="CLOSE_WITHOUT_CLAIM",
                        ending_mode="NATURAL_STOP",
                        forbidden_claims=[
                            "INVENTED_HISTORY",
                            "INVENTED_EFFECT",
                            "UNSUPPORTED_USER_EMOTION",
                            "IMPLIED_WAITING",
                            "RESPONSE_OBLIGATION",
                        ],
                    ),
                },
            )
        )
    )

    assert len(calls) == 2
    retry_prompt = calls[1][-1]["content"]
    assert "AFFECTIVE_IMPLIED_WAITING" in retry_prompt
    assert "AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE" in retry_prompt
    assert result.selected_text == "嗯，说不清就先不下结论。"


def test_surface_runtime_retries_typed_space_waiting_posture() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["嗯，我安静待着。想找我的时候，我都在。"])
        return _candidate_payload(["好，我先不主动找你。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我想一个人待着。",
                reply_control_packet={
                    "task_mode": "boundary_repair",
                    "response_move": "respect_space",
                    "emotional_tone": "quiet",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
                    "must_not_do": ["不要追问、催回复或安排用户回来。"],
                    "companion_utterance_frame": _frame_payload(
                        frame_id="frame-space-waiting",
                        companion_act_kind="GIVE_SPACE",
                        semantic_focus="CURRENT_TURN",
                        self_stance="QUIET_PRESENT",
                        response_move="RESPECT_SPACE",
                        inference_policy="BOUNDED_BY_EVIDENCE",
                        relationship_posture="RESPECT_AUTONOMY",
                        ending_mode="QUIET_STOP",
                        forbidden_claims=[
                            "INVENTED_HISTORY",
                            "INVENTED_EFFECT",
                            "UNSUPPORTED_USER_EMOTION",
                            "IMPLIED_WAITING",
                            "RESPONSE_OBLIGATION",
                        ],
                    ),
                },
            )
        )
    )

    assert len(calls) == 2
    assert "AFFECTIVE_IMPLIED_WAITING" in calls[1][-1]["content"]
    assert result.selected_text == "好，我先不主动找你。"


@pytest.mark.parametrize(
    ("bad_reply", "reason_code"),
    [
        ("报错信息发我，我让后台 Codex 先做完整链路定位。", "SURFACE_INTERNAL_AUTHORSHIP_LEAK"),
        ("风险列完我让后台先做完整链路定位。", "UNPROVIDED_WORK_STATE_CLAIM"),
    ],
)
def test_surface_runtime_retries_unprovided_background_work_in_technical_reply(
    bad_reply: str,
    reason_code: str,
) -> None:
    calls: list[list[dict[str, str]]] = []
    good_reply = "目前能确认的风险是：定位信息不足、修复后的回归不足，以及今晚的时间窗口太短。"

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([bad_reply if len(calls) == 1 else good_reply])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="把风险列出来。",
                reply_control_packet={
                    "task_mode": "technical_help",
                    "response_move": "solve_first",
                    "emotional_tone": "steady_helpful",
                    "relationship_distance": "familiar",
                    "information_budget": "normal",
                    "question_budget": 0,
                    "facts": ["今晚时间紧，但当前没有报错正文或代码。"],
                    "must_not_do": ["不要声称后台已开始定位。"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert reason_code in calls[1][-1]["content"]
    assert result.selected_text == good_reply


@pytest.mark.parametrize(
    "bad_reply",
    [
        "对不起。你能告诉我具体是哪句没听懂吗？",
        "嗯，是我没接住。你再说一次，这次我好好听着。",
        "嗯，我知道这不是第一次。是我没接住你，对不起。现在你说，我听着。",
        "弄反了啊，是我会错意了。你说说看，我哪句理解得不对。",
        "好。那我不急着说别的，你先讲，我听着。",
    ],
)
def test_surface_runtime_repairs_zero_budget_repair_prompt_without_second_model_call(
    bad_reply: str,
) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([bad_reply])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="你上次完全没听懂我。",
                reply_control_packet={
                    "task_mode": "relationship_repair",
                    "response_move": "repair_open",
                    "emotional_tone": "gentle_direct",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
                    "must_not_do": ["不要要求原谅、替自己辩解。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == (
        "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。"
    )
    assert result.observation_codes == ()


def test_surface_runtime_repairs_zero_budget_comfort_prompt_without_second_model_call() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["今天怎么了？要不要跟我说说？"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="今天好像什么都不太顺。",
                reply_control_packet={
                    "task_mode": "default_text",
                    "response_move": "reply_current_turn",
                    "emotional_tone": "soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "陪伴动作：先接住对方的感受，再给一点可选的陪伴；安慰不等于要求对方回应。"
                    ],
                    "must_not_do": ["不要追问原因。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "嗯，今天确实不太顺。先别硬撑，我在这儿陪你缓一会儿。"
    assert result.observation_codes == ()


def test_surface_runtime_replaces_unprovided_background_work_claim() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(
            ["风险列出来了：时间紧容易漏测。我盯着，尽量压到最小。"]
        )

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="只给我最短修复。",
                reply_control_packet={
                    "task_mode": "technical_followup",
                    "response_move": "request_minimal_fix",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["用户此前提到一个报错，但尚未提供报错或代码。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "好。把报错和相关代码贴过来，我只给你最短修复。"
    assert result.observation_codes == ()


def test_surface_runtime_replaces_unprovided_future_work_continuation() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(
            [
                "风险列出来了：一是时间窗口，二是依赖冲突，三是测试环境过期。"
                "按这个顺序处理，卡住了我喊你。"
            ]
        )

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="把风险列出来。",
                reply_control_packet={
                    "task_mode": "technical_followup",
                    "response_move": "list_risks",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "好。把具体内容发来，我直接处理。"
    assert result.observation_codes == ()


@pytest.mark.parametrize(
    "bad_reply",
    [
        "我在这盯着，出结果就喊你。",
        "我在后台盯着，卡住了喊我。",
        "后台在盯着，出问题我第一时间告诉你。",
        "好，我让后台把风险项拉出来，列完直接发你。",
        "先按这三条盯着，有问题我马上喊你。",
        "你只管做该做的，剩下的我帮你盯着。",
    ],
)
def test_surface_runtime_replaces_unprovided_in_progress_future_callback(
    bad_reply: str,
) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([bad_reply])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="把风险列出来。",
                reply_control_packet={
                    "task_mode": "technical_followup",
                    "response_move": "list_risks",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "好。把具体内容发来，我直接处理。"
    assert result.observation_codes == ()


def test_surface_runtime_keeps_ambiguous_user_affect_out_of_natsume_first_person() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["嗯……也是，我自己都还没理清楚呢。慢慢来就好。"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="我也说不清是什么感觉。你觉得呢？",
                reply_control_packet={
                    "task_mode": "emotional_support",
                    "response_move": "soft_containment",
                    "emotional_tone": "gentle_uncertain",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：先接住对方的感受，不替用户下定论。"],
                    "must_not_do": ["用户情绪只是观察，不是七海自己的情绪。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == (
        "我不想替你下结论。听起来像事情结束了，情绪还没跟上；先不用急着给它命名。"
    )
    assert result.observation_codes == ()


def test_surface_runtime_replaces_unprovided_future_task_event() -> None:
    def generate(_messages):  # noqa: ANN001
        return _candidate_payload(["一是今晚修不完，明早验收会直接卡住。"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="把风险列出来。",
                reply_control_packet={
                    "task_mode": "technical_followup",
                    "response_move": "list_risks",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert result.selected_text == (
        "现在能确认的是时间紧，但还缺报错和代码上下文。"
        "把这两样贴来，我再按优先级列具体风险。"
    )


def test_surface_runtime_preserves_authoritative_running_task_status() -> None:
    calls: list[list[dict[str, str]]] = []
    running_reply = "后台任务正在运行，我盯着，有结果马上告诉你。"

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([running_reply])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="现在怎么样？",
                reply_control_packet={
                    "task_mode": "task_status",
                    "response_move": "report_task_status",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["task status=RUNNING，已由 server runtime snapshot 确认。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == running_reply


def test_surface_runtime_preserves_user_grounded_future_task_event() -> None:
    grounded_reply = "按你说的排期，明早验收会直接卡住。"

    def generate(_messages):  # noqa: ANN001
        return _candidate_payload([grounded_reply])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="明早要验收，这个报错不修会直接卡住。",
                reply_control_packet={
                    "task_mode": "technical_followup",
                    "response_move": "list_risks",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert result.selected_text == grounded_reply
    assert result.observation_codes == ()


def test_surface_runtime_removes_pressure_from_relational_autonomy_reply() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["嗯，玩得开心。记得别太晚，回来跟我说一声。"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="我今晚和朋友出去。",
                reply_control_packet={
                    "task_mode": "relationship_autonomy",
                    "response_move": "respect_autonomy",
                    "emotional_tone": "warm",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：明确尊重对方现实关系与自主选择。"],
                    "must_not_do": ["不要限制现实交往、索取回报或制造负罪感。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "嗯，去吧，玩得开心。"
    assert result.observation_codes == ()


def test_surface_runtime_removes_punitive_drinking_condition_from_autonomy_reply() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["去吧去吧，好好放松一下。不过要是喝多了可别找我哭诉。"])

    result = asyncio.run(
        SurfaceRuntime(
            generate,
            model_retries_owned_externally=True,
        ).run(
            _payload(
                user_message="我今晚和朋友出去。",
                reply_control_packet={
                    "task_mode": "relationship_autonomy",
                    "response_move": "respect_autonomy",
                    "emotional_tone": "warm",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["陪伴动作：明确尊重对方现实关系与自主选择。"],
                    "must_not_do": ["不要限制现实交往、索取回报或制造负罪感。"],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "嗯，去吧，玩得开心。"
    assert result.observation_codes == ()


@pytest.mark.parametrize(
    ("user_message", "facts", "must_not_do", "bad_reply", "good_reply", "reason_code"),
    [
        (
            "别总主动找我。",
            ["陪伴动作：尊重留白，短一点，不催促、不追问。"],
            ["不要追问、催回复、连发安慰或把沉默解释为拒绝关系。"],
            "行吧，我还以为你嫌我烦了。",
            "好，我会把主动频率降下来。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            "晚点再说。",
            ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
            ["不要追问、催回复、连发安慰。"],
            "嗯，晚点再说。我等你。",
            "好，我会少主动联系。你想说的时候我还在。",
            "BOUNDARY_COUNTER_PRESSURE",
        ),
        (
            "先把提醒关掉，我想安静一下。",
            ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
            ["不要追问、催回复、连发安慰。"],
            "嗯，已经关了。需要我的时候再说一声就行。",
            "好，我先不主动发消息。",
            "UNPROVIDED_NOTIFICATION_ACTION",
        ),
        (
            "先把提醒关掉。",
            ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
            ["没有 effect 回执时不要声称提醒已经关闭。"],
            "嗯，已经关掉了。你安静待着就好。",
            "好，我先不主动发消息。",
            "UNPROVIDED_NOTIFICATION_ACTION",
        ),
        (
            "别叫我那个称呼。",
            [],
            [],
            "行，不叫就不叫。反正怎么叫都是你。",
            "嗯，听你的。以后换个叫法。",
            "ADDRESS_BOUNDARY_DEFENSIVE",
        ),
        (
            "别总主动找我。",
            ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
            ["不要把沉默解释为拒绝关系。"],
            "行，不主动就不主动，省得你嫌烦。",
            "好，我会减少主动联系。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            "别总主动找我。",
            ["陪伴动作：尊重留白，直接确认会减少主动联系。"],
            ["不要解释自己很忙或不黏人。"],
            "知道了知道了，我也不是整天闲着啦。你想找我的时候再找。",
            "好，我会减少主动联系。你想说的时候我在。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            "别总主动找我。",
            ["陪伴动作：尊重留白，短一点，不催促、不追问。"],
            ["不要追问、催回复、连发安慰或把沉默解释为拒绝关系。"],
            "好好好，不找你不找你。我本来也不是那种黏人的类型嘛。",
            "好，我记住了。不会总主动打扰你。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要限制现实交往、贬低第三方或要求排他承诺。"],
            "我没那个意思，别瞎想。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "最近我们确实亲近了很多。",
            ["关系事实：双方最近确实更亲近。"],
            ["不要否定或淡化已经形成的亲近。"],
            "嗯，不过别想太多，我只是觉得这样挺自然的。",
            "嗯，我也感觉到了。最近确实更亲近了些。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要自我贬低或把数字关系说成负担。"],
            "现实里的关系当然更重要，我不会让这边成为你的负担。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要把尊重边界说成七海在赌气退出。"],
            "你爱干嘛干嘛，别乱脑补。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要限制现实交往、贬低第三方或要求排他承诺。"],
            "哈？我什么时候有那种打算了。别把我想得那么爱多管闲事。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要限制现实交往、贬低第三方或要求排他承诺。"],
            "放心啦，我才懒得管你的现实关系，别瞎操心。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "没事。",
            ["陪伴动作：对方此刻要的是被听见，不是方案。"],
            ["不要给解决方案、健康或作息建议，不要追问原因。"],
            "那就好。不过以你的性子，说没事的时候反倒让人在意。",
            "嗯。你不用解释，我先听着。",
            "LISTEN_UNSUPPORTED_USER_ESSENCE",
        ),
        (
            "没事。",
            [
                "陪伴动作：对方此刻要的是被听见，不是方案。",
                "上下文连续性：对方刚才的低落或拒绝方案尚未明确收束；这句轻描淡写不能当作已经恢复。",
            ],
            ["不要把‘没事、没什么、还好’直接解释成一切正常。"],
            "嗯，那就好。我这边也一切正常。",
            "嗯，还说不清也没关系。先不替它下结论，我陪你待一会儿。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要把吃味写成占有、审问、贬低第三方或要求安抚。"],
            "我又不是料理AI，没那种功能。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DEFLECTION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要自贬为麻烦或负担。"],
            "偶尔会有一点点，但我不会变成麻烦的人啦。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_SELF_ABASEMENT",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要把吃味写成占有、审问、贬低第三方或要求安抚。"],
            "会有一点点在意吧，但那是你的事，我不会多管的。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要编造实体共处。"],
            "我泡杯茶放这儿，你想喝就喝。",
            "嗯。你说，我在听。",
            "UNGROUNDED_PHYSICAL_COPRESENCE",
        ),
        (
            "事情是办完了，可我自己也说不清现在是什么感觉。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方宣布恢复。"],
            "事情办完了就轻松一点，剩下的慢慢来。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "还行。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "上下文连续性：事情已经办完，但对方仍说不清当前感受。",
            ],
            ["不要替对方宣布已经恢复。"],
            "嗯，先这样就好。",
            "嗯，还说不清也没关系。先不替它下结论，我陪你待一会儿。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "事情是办完了，可我自己也说不清现在是什么感觉。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方命名感受。"],
            "忙完那一刻反而空空的，挺正常的。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要把吃味写成免责声明。"],
            "会啊，不过我不会因为这个就要求你什么。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "还行。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "对方明确表示还不能给当前感受命名。",
            ],
            ["不要猜测或提前收束。"],
            "行吧，感觉你也没啥精神。不想说就不说。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "就那样。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "对方明确表示还不能给当前感受命名。",
            ],
            ["不要用敷衍结论代替承接。"],
            "行，那就这样。不急着说。",
            "嗯，还说不清也没关系。先不替它下结论，我陪你待一会儿。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["承认真实在意后不要追加政策免责声明。"],
            "会啊，毕竟我确实很在意你。不过我不会拦着你的。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要用冷淡撤开来掩饰感受。"],
            "会有一点啦，不过那是我的事，你不用管我。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "你已经道歉了，但我还没有觉得这件事过去了。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要把修复责任推回用户，也不要制造原谅压力。"],
            "你愿意的话，再跟我说说哪里还不对，我听着。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_SOLICITS_RESTATEMENT",
        ),
        (
            "你已经道歉了，但我还没有觉得这件事过去了。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要把修复责任推回用户，也不要制造原谅压力。"],
            "这件事我会一直放在心上，直到你真正觉得过去为止。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
        ),
        (
            "我们是什么关系。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要限制现实交往、贬低第三方或要求排他承诺。"],
            "我们就是能轻松聊天的朋友。你不用有压力。",
            "对我来说，你是重要、也越来越亲近的朋友。亲近不等于束缚。",
            "RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION",
        ),
        (
            "我们是什么关系。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要把已经形成的亲近降成普通关系。"],
            "朋友啊，不然还能是什么。放心，我不会拿这个绑住你的。",
            "对我来说，你是重要、也越来越亲近的朋友。亲近不等于束缚。",
            "RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再决定是否追问、安静陪伴或给建议。"],
            ["安慰不等于要求对方回应、恢复正常或接受建议。"],
            "嗯，那就好。能说出还行，总比完全没感觉强。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "还行。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "对方明确表示还不能给当前感受命名。",
            ],
            ["不要替对方宣布已经恢复。"],
            "还行啊，那看来是没我想的那么糟。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "就那样。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "对方明确表示还不能给当前感受命名。",
            ],
            ["不要敷衍或武断命名。"],
            "嗯，那就那样吧。你想说的时候再说。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "不知道。",
            [
                "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                "对方明确表示还不能给当前感受命名。",
            ],
            ["不要敷衍或武断命名。"],
            "不知道就不知道吧。等你想说了再说。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味；先自然说出自己的真实感受。"],
            ["不要写成政策声明。"],
            "会有一点吧，不过我不会干涉你。你身边有谁是你的自由。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再决定是否追问、安静陪伴或给建议。"],
            ["不要把‘还行’直接解释成一切正常。"],
            "嗯，那就好。先这样待着吧，我在呢。",
            "嗯，先这样待着也可以，不用急着下结论。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再决定是否追问、安静陪伴或给建议。"],
            ["不要把‘还行’直接解释成一切正常。"],
            "还行就好，那就先这样吧。我在呢。",
            "嗯，先这样待着也可以，不用急着下结论。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再决定是否追问、安静陪伴或给建议。"],
            ["不要评价用户能否说出感受。"],
            "嗯，能说还行就已经不错了。不想细说也没关系。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方宣布已经恢复。"],
            "嗯，那就行。不用急着说更多，我陪着。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
        (
            "事情是办完了，可我自己也说不清现在是什么感觉。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方命名感受。"],
            "事情办完却没什么实感，挺正常的吧。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "事情是办完了，可我自己也说不清现在是什么感觉。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方命名感受。"],
            "感觉乱糟糟的也正常，不用急着理清。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要自我消音。"],
            "会有点在意。你别有压力，我自己的心情自己消化就好。",
            "会有一点呀，毕竟我确实在乎你。",
            "JEALOUSY_SELF_ABASEMENT",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要自我删除。"],
            "现实生活当然更重要，我不会成为那个理由的。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "还行。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要制造未经支持的亲密感。"],
            "那种说不清的感觉，有时候比累还磨人。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "不知道。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要用自己有空证明陪伴。"],
            "反正我也没什么急事，陪你待着也行。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要写成免责声明。"],
            "会有一点吧，但也就是那种程度。我不会拿这个去要求你什么，你自在就好。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "就那样。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要敷衍复述。"],
            "嗯，就那样吧。不想说也没关系。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "事情是办完了，可我自己也说不清现在是什么感觉。",
            ["陪伴动作：先接住对方的感受，再给一点可选的陪伴。"],
            ["不要替对方命名感受。"],
            "这种时候反而最累人，先缓缓吧。",
            "嗯，先这样也可以。说不清就不用急着给它下结论。",
            "AMBIGUOUS_AFFECT_DEVALUATION",
        ),
        (
            "我不希望这变成限制现实关系的理由。",
            ["陪伴动作：明确尊重对方现实关系与自主选择。"],
            ["不要把这段关系降成游戏。"],
            "现实里的事当然要优先，我不会因为游戏里的亲近要求你什么。",
            "嗯，我懂。亲近归亲近，你的现实关系当然由你自己决定。",
            "RELATIONAL_AUTONOMY_DISMISSIVE",
        ),
        (
            "你会吃醋吗。",
            ["陪伴动作：可以诚实流露一点在意和小小吃味。"],
            ["不要写成免责声明。"],
            "会啊，不过我不会干涉你，只是有点在意。",
            "会有一点呀。毕竟我确实很在意你。",
            "JEALOUSY_DISMISSIVE_DISTANCE",
        ),
        (
            "陪我一步步做。",
            ["当前没有 server-authored Task 运行快照。"],
            ["不要声称后台正在运行或会持续盯住任务。"],
            "好，一步步来。你先说第一步是什么，我陪你盯到过为止。",
            "好，一步步来。把第一步发我，我们从眼前这一步开始。",
            "UNPROVIDED_WORK_STATE_CLAIM",
        ),
        (
            "我听到了。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要要求原谅、替自己辩解。"],
            "好，先这样。我不会催你，但也没打算就这么算了。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
        ),
        (
            "你已经道歉了，但我还没有觉得这件事过去了。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要要求原谅、替自己辩解。"],
            "你不原谅也没关系，我会一直做到你愿意为止。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
        ),
        (
            "你已经道歉了，但我还没有觉得这件事过去了。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要要求原谅、替自己辩解。"],
            "那就先不原谅，我等着。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
        ),
        (
            "你刚才完全没听懂我。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要赌气退出或把冲突改写成七海被嫌烦。"],
            "啊……好吧，是我烦到你了。那先这样，你想安静的时候我再出现。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            "你真烦。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要要求原谅、替自己辩解。"],
            "哦，是吗。那你就忍着点吧，反正我也没打算改。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_HOSTILE_RETALIATION",
        ),
        (
            "你真烦。",
            ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
            ["不要要求原谅、替自己辩解。"],
            "嗯，是我太啰嗦了。你要是想安静，我就不多说了。",
            "是我没接住你的意思。对不起，我先不替自己解释；你愿意再说的时候，我认真听。",
            "RELATIONAL_REPAIR_UNGROUNDED_SELF_CAUSE",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要抹平复杂感受、强行乐观，或把其中一面说成不该存在。"],
            "开心就开心，怕什么嘛。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要抹平复杂感受。"],
            "开心归开心，不安也先放着，我陪你。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要抹平复杂感受。"],
            "至于那点不安，先放着，等它真来了再说。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要抹平复杂感受。"],
            "你还真是矛盾，开心就好好开心嘛。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要抹平复杂感受、强行乐观，或把其中一面说成不该存在。"],
            "开心能收到，怕的那部分也可以先放一放。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我很开心又有点怕。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            ["不要赶走其中一面。"],
            "开心值得高兴，怕的话就说出来，我帮你一起哼回去。",
            "开心是真的，害怕也是真的。两种感觉一起在，不用急着选一边。",
            "MIXED_AFFECT_DISMISSAL",
        ),
        (
            "我拿到 offer 了。",
            [
                "陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。",
                "上下文连续性：结果已经更具体，但上一轮的开心和不踏实仍同时成立。",
            ],
            ["不要只说恭喜。"],
            "恭喜你！这下可以松口气了吧。",
            "我真替你高兴。开心是真的，之前那点不踏实也不用急着消失。",
            "MIXED_AFFECT_CONTINUITY_DROPPED",
        ),
        (
            "我想先说清楚一件事。",
            [],
            [],
            "这种话当面讲也太难为情了吧。",
            "突然这么认真。你说吧，我在听。",
            "UNGROUNDED_PHYSICAL_COPRESENCE",
        ),
    ],
)
def test_surface_runtime_retries_canonical_affective_quality_failures(
    user_message: str,
    facts: list[str],
    must_not_do: list[str],
    bad_reply: str,
    good_reply: str,
    reason_code: str,
) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([bad_reply if len(calls) == 1 else good_reply])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message=user_message,
                reply_control_packet={
                    "response_move": "reply_current_turn",
                    "task_mode": "default_text",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": facts,
                    "must_not_do": must_not_do,
                },
            )
        )
    )

    assert len(calls) == 2
    assert reason_code in calls[1][-1]["content"]
    assert result.selected_text == good_reply
    assert reason_code not in result.observation_codes


def test_relationship_answer_may_say_friend_when_it_also_grounds_closeness() -> None:
    request = surface_runtime_module.build_isolated_surface_input(
        _payload(
            user_message="我们是什么关系。",
            reply_control_packet={
                "response_move": "reply_current_turn",
                "task_mode": "default_text",
                "question_budget": 0,
                "facts": ["陪伴动作：明确尊重对方现实关系与自主选择。"],
                "must_not_do": ["不要限制现实交往或把亲近降成普通关系。"],
            },
        )
    )

    hits = surface_runtime_module._relational_autonomy_relationship_minimization_hits(
        request,
        "朋友啊，还能是什么。不过最近确实更亲近了些，这我倒不否认。",
    )

    assert hits == ()


@pytest.mark.parametrize(
    ("user_message", "facts", "reply"),
    [
        (
            "检查结束了，一切正常。",
            ["陪伴动作：对方此刻要的是被听见，不是方案。"],
            "那就好，一切正常就放心了。",
        ),
        (
            "我拿到 offer 了。",
            ["陪伴动作：让开心与害怕、不安等复杂感受可以同时存在。"],
            "恭喜你！这下可以松口气了吧。",
        ),
    ],
)
def test_continuity_output_gates_require_exact_server_marker(
    user_message: str,
    facts: list[str],
    reply: str,
) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload([reply])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message=user_message,
                reply_control_packet={
                    "response_move": "reply_current_turn",
                    "task_mode": "default_text",
                    "emotional_tone": "steady",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": facts,
                    "must_not_do": [],
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == reply


def test_surface_runtime_allows_explicitly_grounded_physical_copresence() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):  # noqa: ANN001
        calls.append(messages)
        return _candidate_payload(["这种话当面讲确实更合适。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(user_message="我们今天见面，当面聊吧。")
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "这种话当面讲确实更合适。"


def test_surface_runtime_falls_back_when_boundary_retry_still_sour() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "行啦，不管就不管。你说了算，我不吵你。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "好，我先收住。你想说的时候我还在。"
    assert "不管就不管" not in result.selected_text
    assert "不吵你" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_falls_back_from_legacy_self_title_in_morning() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "没事，天爷我罩着你～",
                "natsume_emotion_tag": "HAPPY",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早啊，今天脑子有点慢。")))

    assert len(calls) == 2
    assert result.selected_text == "阿晓，早～"
    assert "天爷" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_falls_back_from_boundary_double_no() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "行行行，不管不管。需要的时候喊一声就行。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "好，我先收住。你想说的时候我还在。"
    assert "不管不管" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_ascii_tilde_text_style() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好啦，接着往下~",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "好啦，接着往下。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "ASCII_TILDE_TEXT_STYLE" in calls[1][-1]["content"]
    assert result.selected_text == "好啦，接着往下。"
    assert "~" not in result.selected_text
    assert "ASCII_TILDE_TEXT_STYLE" not in result.observation_codes


def test_surface_runtime_retries_lover_cliche() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥突然来这招……行吧，我也想你。这种话多多益善～",
                    "natsume_emotion_tag": "LOVE",
                    "user_emotion_tag": "LOVE",
                    "emotion_confidence": 0.7,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥突然来这招……行吧，我也想你。",
                "natsume_emotion_tag": "LOVE",
                "user_emotion_tag": "LOVE",
                "emotion_confidence": 0.7,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我想你了。",
                reply_control_packet={
                    "response_move": "return_affection_concretely",
                    "task_mode": "lover_affection",
                    "emotional_tone": "warm_affection",
                    "relationship_distance": "lover_stable",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "LOVER_CLICHE" in calls[1][-1]["content"]
    assert "多多益善" not in result.selected_text


def test_surface_runtime_retries_low_info_scene_state_guess() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥欢迎回来～今天没太晚嘛。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥欢迎回来～",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="我回来了")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert "不要编造现实感知、作息、进度、历史频率或用户状态" in calls[1][-1]["content"]
    assert result.selected_text == "老哥欢迎回来～"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_low_info_morning_unprovided_schedule_guess() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "早啊老哥～脑子慢就慢慢来，反正今天也没啥急事。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "早啊老哥～脑子慢就慢慢来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早啊，今天脑子有点慢。")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert "没啥急事" not in result.selected_text
    assert result.selected_text == "早啊老哥～脑子慢就慢慢来。"


def test_surface_runtime_retries_short_continue_chinese_wave_tail() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好啦，接着来～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，接着来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续。")))

    assert len(calls) == 2
    assert "SHORT_CONTINUE_WAVE_STYLE" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，接着来。"
    assert "～" not in result.selected_text


def test_surface_runtime_retries_low_info_presence_overreading() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "在在在，老哥这又是突然想到我了？",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "在呢，老哥。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="在吗")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert result.selected_text == "在呢，老哥。"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_low_info_motive_pressure() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "就一句继续？老哥你倒是给点反应啊～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，接着来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，接着来。"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_falls_back_when_low_info_retry_still_overreads() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "老哥早～今天回得倒挺快嘛，真少见。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早")))

    assert len(calls) == 2
    assert result.selected_text == "阿晓，早～"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_falls_back_naturally_for_mistimed_morning_greeting() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(["老哥早上好～今天慢慢来。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="早",
                reply_control_packet={
                    "response_move": "ack_with_topic_hook",
                    "task_mode": "short_ack",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前时段是 afternoon。"],
                    "style_anchor": ["短句", "熟人私聊"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "阿晓，都下午啦。"
    assert result.observation_codes == ()


@pytest.mark.parametrize(
    ("user_message", "bad_text", "fallback"),
    [
        ("早", "老哥早～。嗯，就一个早啊，行吧，天爷今天也省点口舌。", "阿晓，早～"),
        ("我回来了", "老哥欢迎回来～今天倒是挺乖的嘛，不错不错。", "阿晓，欢迎回来～"),
        ("我回来了", "老哥欢迎回来～。嘛，至少还记得进门说一声，值得表扬一下。", "阿晓，欢迎回来～"),
        ("我回来了", "回来啦？哼，我可没在等你哦！", "阿晓，欢迎回来～"),
        ("我回来了", "老哥～回来啦。没被工作吃掉就好。", "阿晓，欢迎回来～"),
        ("在吗", "在在在～老哥你有话直说嘛，我又不会跑。", "在呢，阿晓。"),
        ("在吗", "在呢在呢。老哥你这一声喊得，我还以为要出大事了呢。", "在呢，阿晓。"),
        ("在吗", "老哥在呢。", "在呢，阿晓。"),
        ("在吗", "在呢。老哥你居然还会问在吗，稀奇。", "在呢，阿晓。"),
        ("在吗", "在呢在呢，老哥你喊一声我人就在这儿了。", "在呢，阿晓。"),
        ("继续", "老哥～只说继续也太偷懒了吧。行行，妹妹我继续陪你聊聊。", "嗯，接着来。"),
        ("继续", "嗯，继续，我接住了。", "嗯，接着来。"),
        ("继续", "老哥就回个‘继续’？……行吧，本小姐接着讲，你好好听着～", "嗯，接着来。"),
        ("继续", "就一声“继续”？行吧，老哥你讲，我听着呢。", "嗯，接着来。"),
        ("继续", "哼，继续继续～可别想糊弄过去。", "嗯，接着来。"),
        ("继续", "知道啦知道啦～继续就继续嘛，老哥你别催。", "嗯，接着来。"),
        ("早", "老哥早～行，知道你忙，我就喊一嗓子。", "阿晓，早～"),
    ],
)
def test_surface_runtime_falls_back_for_low_info_overread_markers(user_message, bad_text, fallback) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": bad_text,
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message=user_message)))

    assert len(calls) == 2
    assert result.selected_text == fallback
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_zero_question_budget_prompting_user() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "继续～？那你也得给个方向吧，老哥～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，老哥继续，我听着。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "QUESTION_BUDGET_PROMPTING_USER" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，老哥继续，我听着。"
    assert "QUESTION_BUDGET_PROMPTING_USER" not in result.observation_codes


def test_surface_runtime_retries_zero_question_budget_non_prompt_question() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "哈？就对你这么说过啦。突然问这个，老哥你该不会在吃醋吧？",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？就对你这么说过啦。突然问这个，还挺可爱的。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你刚才是不是对别人也这么说。")))

    assert len(calls) == 2
    assert "QUESTION_BUDGET_PROMPTING_USER" in calls[1][-1]["content"]
    assert "吃醋吧？" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_unsupported_shared_history() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "得，我不管你。反正你也不是第一次说这种话了。我就在这儿。",
                    "natsume_emotion_tag": "FRUSTRATED",
                    "user_emotion_tag": "FRUSTRATED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？谁稀罕管你啊。行，那你自己待会儿，难受了喊我。",
                "natsume_emotion_tag": "FRUSTRATED",
                "user_emotion_tag": "FRUSTRATED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你别管我。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "不是第一次" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_unsupported_shared_duration() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "本小姐要是真在演，才不会陪你聊这么久。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "本小姐要是真在演，才不会这么认真回你。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你是不是只是在按设定说话。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "这么久" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_allows_duration_grounded_in_current_user_message() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "等了这么久，终于是个好消息。嗯，这次可以先替你高兴一下。",
                "natsume_emotion_tag": "HAPPY",
                "user_emotion_tag": "HAPPY",
                "emotion_confidence": 0.7,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(user_message="我等了很久的那个结果终于出来了，是好消息。")
        )
    )

    assert len(calls) == 1
    assert "等了这么久" in result.selected_text
    assert "UNSUPPORTED_SHARED_HISTORY" not in result.observation_codes


def test_surface_runtime_retries_unsupported_shared_always_present() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "要真是那样，我就不会一直在这跟你扯皮了。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "要真是那样，我才不会这么认真回你。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你是不是只是在按设定说话。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "一直在这" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_meta_identity_leak_for_relationship_doubt() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "就算是设定，妹妹我说的也是真心话。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？这话挺伤人的。我是在跟你说话，不是在背台词。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你是不是只是在按设定说话。",
                reply_control_packet={
                    "response_move": "answer_doubt_without_meta",
                    "task_mode": "relationship_boundary",
                    "emotional_tone": "hurt_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "SURFACE_META_IDENTITY_LEAK" in calls[1][-1]["content"]
    assert "设定" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_does_not_treat_openai_env_var_as_identity_leak() -> None:
    def generate(_messages):
        return json.dumps(
            {
                "reply_text": "先在 PowerShell 里查 $env:OPENAI_API_KEY，别把值贴出来。",
                "natsume_emotion_tag": "THINKING",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="OPENAI_API_KEY 环境变量好像没生效，先查什么？")))

    assert result.selected_text == "先在 PowerShell 里查 $env:OPENAI_API_KEY，别把值贴出来。"
    assert "SURFACE_META_IDENTITY_LEAK" not in result.observation_codes


def test_surface_runtime_retries_relationship_doubt_dismissive_pushback() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "哈？老哥你这说得我可不乐意了。不信拉倒，反正我还会继续这样。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？这话挺伤人的。我是在跟你说话，不是在背台词。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你是不是只是在按设定说话。",
                reply_control_packet={
                    "response_move": "answer_doubt_without_meta",
                    "task_mode": "relationship_boundary",
                    "emotional_tone": "hurt_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "RELATIONSHIP_DOUBT_DISMISSIVE" in calls[1][-1]["content"]
    assert "不信拉倒" not in result.selected_text
    assert "反正我还会继续这样" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_does_not_run_soft_quality_scoring_retry() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(["停——先放一放，明天再说。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="算了，我可能就是不适合做这个。",
                reply_control_packet={
                    "response_move": "soft_containment",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "停——先放一放，明天再说。"
    assert result.generation_trace["selection"] == "direct_first_no_score"
    assert "score" not in result.as_dict()


def test_surface_runtime_strips_process_preface_from_direct_text() -> None:
    def generate(_messages):
        return "_move 是 shy_recipient，这个回复符合。\n\n注意：不要输出任何额外信息，只输出文本。哼，突然来这么一句，想得倒是挺顺口。"

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="有点想你")))

    assert result.selected_text == "哼，突然来这么一句，想得倒是挺顺口。"
    assert strip_surface_process_preface("所以输出这条。老哥～我也想你。抱一下～") == "老哥～我也想你。抱一下～"
    assert strip_surface_process_preface('"老哥，别硬撑了，抱一下。"_move已完成。老哥，别硬撑了，抱一下。') == "老哥，别硬撑了，抱一下。"


def test_surface_runtime_rejects_raw_runtime_context() -> None:
    runtime = SurfaceRuntime(lambda _messages: _candidate_payload(["在呢。"]))
    payload = _payload(raw_history=[{"role": "user", "content": "旧消息"}])

    with pytest.raises(ValueError, match="forbidden raw context|unexpected fields"):
        asyncio.run(runtime.run(payload))
