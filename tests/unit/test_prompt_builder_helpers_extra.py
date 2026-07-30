from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from host_brain.app import prompt_builder as pb
from host_brain.persona.context.soul_runtime import load_compiled_soul


def test_script_detection_text_helpers_and_evidence_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pb._contains_kana("こんにちは") is True
    assert pb._contains_kana("hello") is False
    assert pb._contains_cjk_ideograph("今天") is True
    assert pb._contains_cjk_ideograph("hello") is False
    assert pb._contains_cjk_or_kana("今日は") is True

    broken = "こんにちは".encode("utf-8").decode("latin-1")
    assert pb.repair_utf8_latin1_mojibake(broken) == "こんにちは"
    assert pb.repair_utf8_latin1_mojibake("plain ascii") == "plain ascii"

    monkeypatch.setenv("HOST_RETR_EVIDENCE_ITEMS_MAX", "99")
    assert pb._get_prompt_evidence_limit(default=5) == 12
    monkeypatch.setenv("HOST_RETR_EVIDENCE_ITEMS_MAX", "oops")
    assert pb._get_prompt_evidence_limit(default=5) == 5
    monkeypatch.delenv("HOST_RENDER_TARGET_LANG", raising=False)
    assert pb.infer_render_target_lang(user_message="hello") == "zh"
    assert pb.infer_render_target_lang(user_message="こんにちは") == "zh"


def test_compiled_soul_loader_handles_short_invalid_and_cached_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_path = tmp_path / "Soul.md"
    soul_path.write_text(
        "\n".join(
            [
                "## 一、基本角色档案",
                "| 属性 | 信息 |",
                "|------|------|",
                "| 中文名 | 新海 天 |",
                "| 学校 | 白泉学园 一年级生 |",
                "| 原典关系 | 新海翔（哥哥）的亲妹妹 |",
                "| 运行关系 | 重组家庭中的义妹；与哥哥没有血缘关系 |",
                "",
                "## 三、核心性格",
                "- 天然活泼",
                "- 嘴甜爱撒娇",
                "",
                "## 四、与“哥哥”关系模式",
                "- 默认称呼：哥哥",
                "- 用调侃和热闹掩盖真正的依赖感",
                "",
                "## 五、AI伴侣语气风格",
                "- 说话节奏快，但有温度",
                "### 禁忌风格",
                "- 不做病娇控制欲",
                "",
                "## 八、一句话总纲",
                "> 新海天是一个外向活泼、爱黏哥哥的 AI 伴侣。",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_SOUL_MD_PATH", str(soul_path))

    compiled = load_compiled_soul(force=True)
    assert "新海 天" in compiled.identity_core
    assert "新海天是一个外向活泼" not in compiled.runtime_compact_text
    assert "【关系授权边界】" in compiled.runtime_compact_text

    cached = load_compiled_soul()
    assert cached is compiled

    soul_path.write_text("## 一、基本角色档案\n| 属性 | 信息 |\n|------|------|\n| 中文名 | 改写版 新海 天 |\n", encoding="utf-8")
    refreshed = load_compiled_soul(force=True)
    assert "改写版 新海 天" in refreshed.identity_core


def test_json_coercion_and_langchain_extractors_cover_fallback_paths() -> None:
    fenced = """```json\n{\"value\": 1}\n```"""
    inline = "prefix {\"narrative\":\"hi\",\"text\":\"fallback\"} suffix"

    assert pb.extract_json_object_from_text(fenced) == {"value": 1}
    assert pb.extract_json_object_from_text(inline)["text"] == "fallback"

    payload = pb.coerce_gateway_payload_json_text(
        '{"narrative":{"text":"hello"},"intent_draft":{"route":"skill","target_id":"x","action":"x"},"meta":{"trace":"1"}}'
    )
    payload_from_text = pb.coerce_gateway_payload_json_text('{"text":"hello"}')
    decoded = json.loads(payload)
    decoded_text = json.loads(payload_from_text)

    assert decoded["narrative"]["text"] == "hello"
    assert decoded["meta"]["draft_only"] is True
    assert decoded_text["narrative"]["text"] == "hello"

    with pytest.raises(RuntimeError):
        pb.extract_json_object_from_text("")
    with pytest.raises(RuntimeError):
        pb.coerce_gateway_payload_json_text('{"meta":{}}')

    assert pb.extract_langchain_content_text("plain") == "plain"
    assert pb.extract_langchain_content_text(["a", {"text": "b"}, {"ignored": "x"}, 1]) == "ab"
    assert pb.extract_langchain_content_text(123) == "123"
    assert pb.extract_gateway_narrative({"narrative": {"text": "hello"}}) == "hello"
    assert pb.extract_gateway_narrative({"narrative": "world"}) == "world"
    assert pb.extract_gateway_narrative({"narrative": {}}) == ""


def test_extract_node_helpers_and_injection_meta_roundtrip() -> None:
    runtime_payload = {
        "core_reasoner_primary": {
            "payload": {
                "narrative": {"text": "核心结论"},
                "intent_draft": {"route": "chat", "target_id": "", "action": "narrative_only", "args": {}},
            }
        }
    }

    pb._set_injection_meta({"mode": "ACTIVE", "risk_level": "HIGH"})

    assert pb.get_last_injection_meta()["risk_level"] == "HIGH"
    assert pb.extract_node_runtime_payload(runtime_payload, node_id="core_reasoner_primary")["intent_draft"]["action"] == "narrative_only"
    assert pb.extract_node_runtime_payload(runtime_payload, node_id="missing") == {}
    assert pb.extract_node_narrative_text(runtime_payload, node_id="core_reasoner_primary") == "核心结论"


def test_render_node_generation_prompt_includes_narrative_guardrail_prompt_directives() -> None:
    prompt = pb.render_node_generation_prompt(
        node_id="cortex_orchestrator",
        runtime_payload={
            "user_message": "继续说说你最近的变化",
            "render_target_lang": "zh",
            "prompt_context": {
                "payload": {
                    "authoritative": [],
                    "retrieval": [],
                    "constraints": {},
                },
                "validation": {"valid": True, "reason_code": "NONE", "violation_count": 0},
                "observability": {},
            },
            "retrieval": {"status": "OK", "reason_code": "NONE", "evidence_items": []},
            "render_policy": {
                "allow_followup": False,
                "allow_topic_shift": False,
                "allow_proactive_after_reply": False,
                "confession_guard": True,
                "withdrawal_guard": True,
                "should_be_brief": True,
                "should_be_withdrawn": True,
                "max_sentences": 2,
            },
            "self_reflection_pulse": {
                "self_awareness": "aware",
                "narrative_guardrails": {
                    "risk_level": "high",
                    "risk_tags": [
                        "time_context_unverified",
                        "unsupported_self_claim",
                        "anthropomorphic_overreach",
                        "reflection_vs_policy_tension",
                    ],
                    "instruction": "若时间线证据不足，只能说最近/这段时间，不要扩写无依据的自我叙事。",
                    "allowed_style": "brief",
                    "allow_self_narrative_expansion": False,
                    "max_self_narrative_sentences": 1,
                    "prompt_directives": [
                        "涉及自我叙事时，只允许一小句辅助说明，优先回应当前话题，不要展开成长故事或回忆链。",
                        "如果提到时间，只能说“最近/这段时间”，不要声称具体日期、时长或明确阶段。",
                        "不要把系统状态、趋势、沉默、模式切换或薄弱线索写成人类式经历、自传或命运感叙事。",
                        "若用户没有直接追问，不要主动展开自我叙事，只允许轻量辅助一句。",
                        "当前以主回复任务和既有边界为先，自我叙事只能退到辅助位。",
                    ],
                },
            },
        },
    )

    assert "SELF_REFLECTION_PULSE" in prompt
    assert "max_self_narrative_sentences: 1" in prompt
    assert "prompt_directives:" in prompt
    assert "如果提到时间，只能说“最近/这段时间”" in prompt
    assert "不要把系统状态、趋势、沉默、模式切换或薄弱线索写成人类式经历" in prompt
    assert "当前以主回复任务和既有边界为先" in prompt


def test_node_generation_prompt_uses_relationship_safe_soul_projection() -> None:
    unsafe_soul = SimpleNamespace(
        display_name="七海",
        runtime_compact_text="你是七海。Bond 阶段自动进入 CONFIDANT。",
        runtime_full_text="你是七海。SOULMATE 时阿晓是唯一归宿，允许亲密恋人台词。",
    )
    with patch(
        "host_brain.app.node_generation_prompt.get_compiled_soul",
        return_value=unsafe_soul,
    ):
        prompt = pb.render_node_generation_prompt(
            node_id="cortex_orchestrator",
            runtime_payload={
                "user_message": "今天有点累",
                "render_target_lang": "zh",
                "prompt_context": {
                    "payload": {"authoritative": [], "retrieval": [], "constraints": {}},
                    "validation": {"valid": True, "reason_code": "NONE", "violation_count": 0},
                    "observability": {},
                },
                "retrieval": {"status": "OK", "reason_code": "NONE", "evidence_items": []},
            },
        )

    assert "CONFIDANT" not in prompt
    assert "SOULMATE" not in prompt
    assert "亲密恋人" not in prompt
    assert "阿晓" not in prompt
    assert "【关系授权边界】" in prompt
