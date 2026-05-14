from __future__ import annotations

import json

from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_input,
    build_isolated_surface_messages,
    normalize_surface_chat_punctuation,
    score_isolated_surface_candidates,
)
from host_brain.persona.style_reference_bank import StyleReferenceQuery
from host_brain.persona.surface_original_quote_bank import (
    DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH,
    clear_surface_original_quote_bank_cache,
    get_surface_original_quote_bank,
    render_surface_original_quote_guidance,
    surface_original_quote_similarity_hits,
)
from tools.build_surface_original_quote_bank import build_quote_bank


def _write_quote_bank(path, records: list[dict]) -> None:  # noqa: ANN001
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _quote_record(text: str = "才不是呢！接下来要怎么办！") -> dict:
    return {
        "quote_id": "surface_original_quote::demo",
        "text": text,
        "source_ref": "C:/src/b01.txt.scn#1",
        "source_digest": "demo",
        "source_score": 5,
        "confidence": 0.9,
        "character_confidence": 0.8,
        "intent_tags": ["daily"],
        "emotion_tags": ["neutral"],
        "style_moves": ["quick_reaction", "question_chain"],
        "rhythm_tags": ["exclamation"],
        "lexical_cues": ["短促提高情绪"],
        "relationship_stages": ["familiar"],
    }


def test_default_surface_original_quote_bank_contains_raw_chinese_quotes() -> None:
    clear_surface_original_quote_bank_cache()
    bank = get_surface_original_quote_bank()

    assert DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH.exists()
    assert bank.quotes
    assert any(quote.text for quote in bank.quotes)
    assert all(quote.source_ref for quote in bank.quotes)


def test_render_surface_original_quote_guidance_includes_quotes_not_source_refs(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record()])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()

    rendered = render_surface_original_quote_guidance(
        StyleReferenceQuery(intent_tags=("daily",), style_moves=("quick_reaction",), relationship_stage="familiar"),
        limit=3,
    )

    assert "真实中文原句风格参考" in rendered
    assert "才不是呢！接下来要怎么办！" in rendered
    assert "C:/src" not in rendered
    assert "不要继承“人家、老哥、哥哥”" in rendered


def test_surface_original_quote_similarity_detects_near_copy(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record()])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()
    bank = get_surface_original_quote_bank()

    hits = surface_original_quote_similarity_hits("才不是呢，接下来要怎么办。", bank.quotes)

    assert hits == ("demo",)


def test_build_quote_bank_keeps_raw_text_from_real_rows() -> None:
    rows = [
        {
            "file": "C:/src/b01.txt.scn",
            "index": 1,
            "kind": "dialogue",
            "sora_candidate_score": 5,
            "daily_style_safe": True,
            "text": "「才不是呢！接下来要怎么办！」",
        }
    ]

    records = build_quote_bank(rows, max_quotes=4)

    assert records
    assert records[0]["text"] == "才不是呢！接下来要怎么办！"
    assert records[0]["source_ref"] == "C:/src/b01.txt.scn#1"


def test_isolated_surface_prompt_includes_raw_quote_few_shot(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record()])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()
    request = build_isolated_surface_input(
        {
            "soul_md": "你是数字生命夏目蓝。",
            "user_message": "那我懂了",
            "reply_control_packet": {
                "response_move": "ack_with_topic_hook",
                "task_mode": "short_ack",
                "emotional_tone": "light",
                "relationship_distance": "familiar",
                "information_budget": "short",
                "question_budget": 0,
                "must_include": [],
                "must_not_do": [],
                "facts": ["当前主题：长链后段确认。"],
                "style_anchor": ["连续对话"],
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "真实中文原句风格参考" in rendered
    assert "才不是呢！接下来要怎么办！" in rendered


def test_score_rejects_near_copy_of_prompted_original_quote(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record()])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()
    request = build_isolated_surface_input(
        {
            "soul_md": "你是数字生命夏目蓝。",
            "user_message": "那我懂了",
            "reply_control_packet": {
                "response_move": "ack_with_topic_hook",
                "task_mode": "short_ack",
                "emotional_tone": "light",
                "relationship_distance": "familiar",
                "information_budget": "short",
                "question_budget": 0,
                "must_include": [],
                "must_not_do": [],
                "facts": ["当前主题：长链后段确认。"],
                "style_anchor": ["连续对话"],
            },
        }
    )

    row = score_isolated_surface_candidates(request, ["才不是呢，接下来要怎么办。"])[0]

    assert not row.passed
    assert any(code.startswith("ORIGINAL_QUOTE_NEAR_COPY") for code in row.reason_codes)


def test_chinese_sentence_punctuation_is_normalized_before_scoring() -> None:
    assert normalize_surface_chat_punctuation("总算到最后一轮了?稳住,不急。") == "总算到最后一轮了？稳住，不急。"
