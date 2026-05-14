import asyncio
import json

import pytest

from host_brain.persona.surface_runtime import SurfaceRuntime, surface_observation_codes


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


def test_surface_runtime_reranks_and_exposes_score_trace() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(
            [
                "嗯，接着说，我听着。",
                "这才像话，继续盯输入输出。",
            ]
        )

    result = asyncio.run(SurfaceRuntime(generate, repair_failed=False).run(_payload()))

    assert result.selected_text == "这才像话，继续盯输入输出。"
    assert result.selected is not None
    assert result.selected.passed
    assert result.observation_codes == ()
    assert result.score_trace["candidate_count"] == 2
    assert result.score_trace["selected"]["text"] == result.selected_text
    assert "ReplyControlPacket" in "\n".join(message["content"] for message in calls[0])
    assert "raw_history" not in "\n".join(message["content"] for message in calls[0])


def test_surface_runtime_repairs_failed_selected_candidate() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        rendered = "\n".join(message["content"] for message in messages)
        if "最高候选的最小修补" in rendered:
            return _candidate_payload(["这才像话，继续盯输入输出。"])
        return _candidate_payload(["嗯，接着说，我听着。"])

    result = asyncio.run(SurfaceRuntime(generate, repair_failed=True).run(_payload()))

    assert len(calls) == 2
    assert result.repair_attempted
    assert result.repair_candidates == ("这才像话，继续盯输入输出。",)
    assert result.selected_text == "这才像话，继续盯输入输出。"
    assert result.selected is not None
    assert result.selected.passed
    assert result.observation_codes == ()
    repair_rendered = "\n".join(message["content"] for message in calls[1])
    assert "短承接修补必须从必要事实" in repair_rendered


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

    assert codes == ("SELECTED_FAILED", "LOW_SCORE", "THIN_DAILY_FOLLOWUP_REPLY")
