from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.retrieval.emotion_utterance_index import (
    EmotionTaggedUtteranceIndex,
    UtteranceRetrievalResult,
    UtteranceSample,
    infer_prompt_scene,
)


def test_render_for_prompt_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "emotion_utterance_index.prompt_render_texts",
        content=json.dumps(
            {
                "title": "CUSTOM_STYLE_BLOCK:",
                "guidance_lines": [
                    "- custom-guidance-1",
                    "- custom-guidance-2",
                ],
                "sample_item_template": "* {speaker}/{emotion_tag}: {text}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    index = EmotionTaggedUtteranceIndex()
    rendered = index.render_for_prompt(
        UtteranceRetrievalResult(
            samples=[
                UtteranceSample(
                    cn_text="我在呢",
                    jp_text="ここにいるよ",
                    emotion_tag="comfort",
                    speaker="夏目藍",
                )
            ],
            query_emotion="COMFORT",
            matched_scene="comfort",
        ),
        lang="cn",
    )

    assert "CUSTOM_STYLE_BLOCK:" in rendered
    assert "- custom-guidance-1" in rendered
    assert "* 夏目藍/comfort: 我在呢" in rendered


def test_builtin_emotion_utterance_render_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("emotion_utterance_index.prompt_render_texts")
    assert templates["title"] == "PERSONA_STYLE_EXAMPLES:"
    assert templates["sample_item_template"] == "- [{emotion_tag}] {speaker}: {text}"


def test_infer_prompt_scene_identifies_sensitive_boundary_questions() -> None:
    assert infer_prompt_scene("你是谁") == "identity"
    assert infer_prompt_scene("我问你就说吗，我们不熟吧") == "relationship_boundary"
    assert infer_prompt_scene("你真热心肠啊，我不喜欢这样") == "tone_rejection"
    assert infer_prompt_scene("那你还会帮我吗") == "help_continuity"


def test_retrieve_for_prompt_avoids_comfort_scene_for_relationship_boundary() -> None:
    index = EmotionTaggedUtteranceIndex()
    index._built = True
    index._index = {
        "daily": [],
        "comfort": [],
        "tsundere": [],
    }

    from host_brain.retrieval.style_distiller import UtteranceLine

    index._index["daily"] = [
        UtteranceLine(file_rel_path="d", block_id="1", line_index=0, cn_text="我？我的名字是夏目蓝。", jp_text="", emotion_tag="daily"),
        UtteranceLine(file_rel_path="d", block_id="2", line_index=0, cn_text="鸟谷学姐……", jp_text="", emotion_tag="daily"),
        UtteranceLine(file_rel_path="d", block_id="3", line_index=0, cn_text="学、学姐！", jp_text="", emotion_tag="daily"),
    ]
    index._index["comfort"] = [
        UtteranceLine(file_rel_path="c", block_id="1", line_index=0, cn_text="没事的。她依然将这份感情珍藏在内心深处。", jp_text="", emotion_tag="comfort"),
    ]
    index._index["tsundere"] = [
        UtteranceLine(file_rel_path="t", block_id="1", line_index=0, cn_text="哼，肯定又在想什么不好的事情了吧……真是的。", jp_text="", emotion_tag="tsundere"),
    ]

    result = index.retrieve_for_prompt(
        "NEUTRAL",
        user_message="我问你就说吗，我们不熟吧",
        lang="cn",
        max_chars=400,
    )

    texts = [sample.cn_text for sample in result.samples]
    assert texts
    assert all("没事的" not in text for text in texts)
    assert any("夏目蓝" in text or "真是的" in text for text in texts)
