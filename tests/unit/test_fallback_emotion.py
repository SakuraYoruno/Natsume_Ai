"""M29-M：build_fallback_output 情感感知降级测试（T-FALLBACK-EMOTION-01~03）"""

from __future__ import annotations

import json

import pytest
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.qa import build_fallback_output


class TestFallbackOutputEmotion:
    """T-FALLBACK-EMOTION-01~03：build_fallback_output 情感感知降级测试"""

    def test_no_emotion_context_returns_default_text(self):
        """T-FALLBACK-EMOTION-01：无情感上下文时返回默认降级文本"""
        result = build_fallback_output(reason_code="TIMEOUT", stage="draft")
        assert "narrative" in result
        assert isinstance(result["narrative"]["text"], str)
        assert len(result["narrative"]["text"]) > 0
        assert result["meta"]["degraded"] is True

    def test_sad_emotion_returns_gentle_text(self):
        """T-FALLBACK-EMOTION-02：SAD 情感时返回温柔的降级文本"""
        result = build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "SAD", "mood_state": "tired"},
        )
        text = result["narrative"]["text"]
        # SAD 情感时文本应与默认不同（更温柔）
        assert isinstance(text, str)
        assert len(text) > 0

    def test_angry_emotion_returns_calm_text(self):
        """T-FALLBACK-EMOTION-03：ANGRY 情感时返回安抚性降级文本"""
        result = build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "ANGRY", "mood_state": "steady"},
        )
        text = result["narrative"]["text"]
        assert isinstance(text, str)
        assert len(text) > 0

    def test_happy_emotion_returns_cheerful_text(self):
        """HAPPY 情感时返回轻松的降级文本"""
        result = build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "HAPPY", "mood_state": "engaged"},
        )
        text = result["narrative"]["text"]
        assert isinstance(text, str)
        assert len(text) > 0

    def test_meta_fields_always_present(self):
        """meta 字段始终包含 degraded/reason_code/stage"""
        result = build_fallback_output(
            reason_code="SKILL_FAILED",
            stage="execution",
            emotion_context={"emotion_tag": "NEUTRAL"},
        )
        assert result["meta"]["degraded"] is True
        assert result["meta"]["reason_code"] == "SKILL_FAILED"
        assert result["meta"]["stage"] == "execution"

    def test_different_emotions_produce_different_texts(self):
        """不同情感状态产生不同的降级文本"""
        sad_result = build_fallback_output(
            reason_code="TIMEOUT", stage="draft",
            emotion_context={"emotion_tag": "SAD"},
        )
        angry_result = build_fallback_output(
            reason_code="TIMEOUT", stage="draft",
            emotion_context={"emotion_tag": "ANGRY"},
        )
        # SAD 和 ANGRY 的降级文本应不同
        assert sad_result["narrative"]["text"] != angry_result["narrative"]["text"]

    def test_prompt_template_override_changes_fallback_texts(self, monkeypatch) -> None:
        ledger = SQLiteLedger(":memory:", check_same_thread=False)
        store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
        store.upsert_template(
            "core.qa.fallback_texts",
            content=json.dumps(
                {
                    "default": "先让我缓一口气。",
                    "sad_or_tired": "我现在有点累，等我缓缓再认真回你。",
                    "angry_or_frustrated": "我先冷静一下，等会儿再继续说。",
                    "happy_or_excited": "小插曲而已，我马上回来接你这句。",
                },
                ensure_ascii=False,
            ),
            enabled=True,
        )

        import host_brain.app.dependencies as deps

        monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
        monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

        assert build_fallback_output(reason_code="TIMEOUT", stage="draft")["narrative"]["text"] == "先让我缓一口气。"
        assert build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "SAD", "mood_state": "tired"},
        )["narrative"]["text"] == "我现在有点累，等我缓缓再认真回你。"
        assert build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "ANGRY"},
        )["narrative"]["text"] == "我先冷静一下，等会儿再继续说。"
        assert build_fallback_output(
            reason_code="TIMEOUT",
            stage="draft",
            emotion_context={"emotion_tag": "HAPPY"},
        )["narrative"]["text"] == "小插曲而已，我马上回来接你这句。"
