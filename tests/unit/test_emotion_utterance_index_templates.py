from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.retrieval.emotion_utterance_index import (
    EmotionTaggedUtteranceIndex,
    UtteranceRetrievalResult,
    UtteranceSample,
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
