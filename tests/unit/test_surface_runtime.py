import asyncio
import json

import pytest

from host_brain.persona.surface_runtime import (
    SurfaceRuntime,
    parse_surface_generation_output,
    strip_surface_process_preface,
    surface_observation_codes,
)


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


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
            "facts": ["上一轮摘要：已建议先看输入和输出，不急着改全段。"],
            "style_anchor": ["短句", "熟人私聊"],
        },
    }
    payload.update(overrides)
    return payload


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def test_surface_runtime_uses_first_reply_and_only_scores_for_trace() -> None:
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
    assert result.score_trace["candidate_count"] == 1
    assert result.score_trace["selected"]["text"] == result.selected_text
    assert "ReplyControlPacket" in "\n".join(message["content"] for message in calls[0])
    assert "结构化直出要求" in "\n".join(message["content"] for message in calls[0])
    assert "natsume_emotion_tag" in "\n".join(message["content"] for message in calls[0])
    assert "VTS Live2D EmotionTag 映射" in "\n".join(message["content"] for message in calls[0])
    assert "HK_Love" in "\n".join(message["content"] for message in calls[0])
    assert "raw_history" not in "\n".join(message["content"] for message in calls[0])


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

    assert result.selected_text == "哈？又不是不管你。先把最卡那句发我。"
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


def test_surface_runtime_retries_once_on_empty_generation() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return ""
        return _candidate_payload(["这才像话，继续盯输入输出。"])

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert len(calls) == 2
    assert "上一次输出为空" in calls[1][-1]["content"]
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
    assert result.selected_text == "老哥早上好～。今天先慢慢来。"
    assert "声音" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_overharsh_tired_daily_emotion() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["老哥～活该啦。去喝杯咖啡缓缓。"])
        return _candidate_payload(["老哥，今天先别逞强，缓一下再说。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="昨晚没睡好，今天脑子有点糊。",
                reply_control_packet={
                    "response_move": "soft_containment",
                    "task_mode": "daily_chat",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "LOW_ENERGY_OVERHARSH_TEASE" in calls[1][-1]["content"]
    assert result.selected_text == "老哥，今天先别逞强，缓一下再说。"
    assert "活该" not in result.selected_text


def test_surface_runtime_retries_visible_support_jargon() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["在在在～老哥找我，本小姐当然得接住呀。"])
        return _candidate_payload(["在呢，老哥找我？说吧。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="在吗？",
                reply_control_packet={
                    "response_move": "presence_ack",
                    "task_mode": "daily_chat",
                    "emotional_tone": "playful_light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "MISPLACED_EMOTIONAL_CONTAINMENT" in calls[1][-1]["content"]
    assert result.selected_text == "在呢，老哥找我？说吧。"
    assert "接住" not in result.selected_text


def test_surface_runtime_retries_too_thin_self_doubt_reply() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["停——先放一放，明天再说。"])
        return _candidate_payload(["停，别急着给自己判死刑。先挑最小的一步做完。"])

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

    assert len(calls) == 2
    assert "SELF_DOUBT_THIN_DEFERRAL" in calls[1][-1]["content"]
    assert result.selected_text == "停，别急着给自己判死刑。先挑最小的一步做完。"
    assert "明天再说" not in result.selected_text


def test_surface_runtime_retries_regulation_only_self_doubt_reply() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["老哥，不准这么说自己。先喝口水。"])
        return _candidate_payload(["老哥，不准这么说自己。先把最卡的一块拿出来。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我感觉自己最近什么都做不好。",
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

    assert len(calls) == 2
    assert "SELF_DOUBT_LACKS_SMALL_STEP" in calls[1][-1]["content"]
    assert result.selected_text == "老哥，不准这么说自己。先把最卡的一块拿出来。"


def test_surface_runtime_retries_unneeded_material_request() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["停——先别急着下结论。回头把最近一段代码发我，我帮你看看。"])
        return _candidate_payload(["停——先别急着下结论。先把卡住的那一步写一行。"])

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

    assert len(calls) == 2
    assert "SELF_DOUBT_UNGROUNDED_TECH_OBJECT" in calls[1][-1]["content"]
    assert result.selected_text == "停——先别急着下结论。先把卡住的那一步写一行。"
    assert "代码" not in result.selected_text


def test_surface_runtime_repairs_remaining_self_doubt_deferral_after_retry() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["停——先放一放，明天再说。"])
        return _candidate_payload(["哈？停——谁说的。先放一放，回头找个最小的突破口试试。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="算了，我可能就是不适合做这个。",
                reply_control_packet={
                    "response_move": "self_doubt_small_step",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "哈？停——谁说的。找个最小的突破口试试。"
    assert "回头" not in result.selected_text
    assert "放一放" not in result.selected_text
    assert "SELF_DOUBT_THIN_DEFERRAL" not in result.observation_codes


def test_surface_runtime_repairs_remaining_self_doubt_lacks_small_step_after_retry() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["停——先放一放，明天再说。"])
        return _candidate_payload(["停——先别急着给自己下结论。放一放，回头换个思路再试。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="算了，我可能就是不适合做这个。",
                reply_control_packet={
                    "response_move": "self_doubt_small_step",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "停——先别急着给自己下结论。先把最卡的那一步拎出来。"
    assert "回头" not in result.selected_text
    assert "放一放" not in result.selected_text
    assert "SELF_DOUBT_LACKS_SMALL_STEP" not in result.observation_codes


def test_surface_runtime_repairs_remaining_self_doubt_stalled_deferral_after_retry() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["停——先放一放，明天再说。"])
        return _candidate_payload(["停——先搁着。把最麻烦的一步单独拎出来，我们一块拆。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="算了，我可能就是不适合做这个。",
                reply_control_packet={
                    "response_move": "self_doubt_small_step",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "停——把最麻烦的一步单独拎出来，我们一块拆。"
    assert "先搁着" not in result.selected_text
    assert "SELF_DOUBT_THIN_DEFERRAL" not in result.observation_codes


def test_surface_runtime_retries_demeaning_self_doubt_small_step() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["老哥，先去做一件白痴都能做的小事。"])
        return _candidate_payload(["老哥，先去做一件很小的事，三分钟就行。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我感觉自己最近什么都做不好。",
                reply_control_packet={
                    "response_move": "low_mood_small_step",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "SELF_DOUBT_DEMEANING_SMALL_STEP" in calls[1][-1]["content"]
    assert result.selected_text == "老哥，先去做一件很小的事，三分钟就行。"
    assert "白痴" not in result.selected_text


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


def test_surface_observation_codes_accepts_selected_mapping() -> None:
    codes = surface_observation_codes(
        {
            "score": 79.5,
            "passed": False,
            "hard_fail": False,
            "control_violations": [],
            "reason_codes": ["THIN_DAILY_FOLLOWUP_REPLY"],
        }
    )

    assert codes == ("THIN_DAILY_FOLLOWUP_REPLY",)
