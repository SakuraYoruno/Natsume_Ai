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
