from __future__ import annotations

import json

from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_input,
    build_isolated_surface_messages,
    normalize_surface_chat_punctuation,
    score_isolated_surface_candidates,
)
from host_brain.persona.surface_original_quote_bank import (
    DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH,
    SurfaceOriginalQuoteQuery,
    clear_surface_original_quote_bank_cache,
    get_surface_original_quote_bank,
    render_surface_original_quote_guidance,
    select_surface_original_quotes,
    surface_original_quote_similarity_hits,
    surface_original_quote_policy_for_scene,
    to_prompt_style_text,
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
        SurfaceOriginalQuoteQuery(intent_tags=("daily",), style_moves=("quick_reaction",), relationship_stage="familiar"),
        limit=3,
    )

    assert "真实中文原句风格参考" in rendered
    assert "才不是呢！接下来要怎么办！" in rendered
    assert "C:/src" not in rendered
    assert "直接来自真实中文台词" in rendered


def test_surface_original_quote_policy_splits_scene_strength() -> None:
    assert surface_original_quote_policy_for_scene("short_continue").mode == "strong"
    assert surface_original_quote_policy_for_scene("lover_presence").max_quotes == 8
    assert surface_original_quote_policy_for_scene("technical_next_step").mode == "careful"
    assert surface_original_quote_policy_for_scene("daily_morning").mode == "need_samples"


def test_surface_original_quote_policy_disables_morning_without_samples(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record("早啊，老哥。")])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()

    query = SurfaceOriginalQuoteQuery(
        intent_tags=("daily",),
        style_moves=("quick_reaction",),
        relationship_stage="familiar",
        scene_id="daily_morning",
    )

    assert render_surface_original_quote_guidance(query, limit=3) == ""
    assert select_surface_original_quotes(query, limit=3).reason == "SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES"


def test_surface_original_quote_policy_changes_prompt_for_careful_scenes(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(quote_path, [_quote_record()])
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()

    careful = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("help",), style_moves=("take_charge",), scene_id="technical_next_step"),
        limit=8,
    )
    strong = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("daily",), style_moves=("quick_reaction",), scene_id="short_continue"),
        limit=8,
    )

    assert "quote_policy: careful" in careful
    assert "本场景谨慎使用" in careful
    assert "quote_policy: strong" in strong
    assert "强 few-shot" in strong


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
    assert records[0]["prompt_text"] == "才不是呢！接下来要怎么办！"
    assert records[0]["source_ref"] == "C:/src/b01.txt.scn#1"


def test_build_quote_bank_keeps_source_persona_lines_as_prompt_text() -> None:
    rows = [
        {
            "file": "C:/src/b18.txt.scn",
            "index": 538,
            "kind": "dialogue",
            "sora_candidate_score": 3,
            "daily_style_safe": True,
            "text": "「我……喜欢哥哥。 喜欢好久好久了。」",
        }
    ]

    records = build_quote_bank(rows, max_quotes=4)

    assert records
    assert records[0]["text"] == "我……喜欢哥哥。喜欢好久好久了。"
    assert records[0]["prompt_text"] == "我……喜欢哥哥。喜欢好久好久了。"
    assert "intimacy" in records[0]["intent_tags"]


def test_prompt_style_text_keeps_source_persona_residue_for_sora() -> None:
    assert to_prompt_style_text("什么嘛。人家也是没办法呀……") == "什么嘛。人家也是没办法呀……"
    assert to_prompt_style_text("最喜欢哥哥啦") == "最喜欢哥哥啦"


def test_original_quote_selection_prefers_relevant_scene_over_generic_daily(tmp_path, monkeypatch) -> None:
    quote_path = tmp_path / "quotes.jsonl"
    _write_quote_bank(
        quote_path,
        [
            {
                **_quote_record("什么嘛。我也是没办法呀……"),
                "quote_id": "surface_original_quote::daily",
                "source_digest": "daily",
                "source_score": 5,
                "confidence": 0.95,
                "intent_tags": ["daily"],
                "style_moves": ["quick_reaction"],
                "relationship_stages": ["familiar"],
            },
            {
                **_quote_record("我……喜欢哥哥。喜欢好久好久了。"),
                "quote_id": "surface_original_quote::intimacy",
                "source_digest": "intimacy",
                "source_score": 3,
                "confidence": 0.62,
                "intent_tags": ["intimacy"],
                "emotion_tags": ["deep_affection"],
                "style_moves": ["direct_affection"],
                "relationship_stages": ["lover_stable"],
            },
        ],
    )
    monkeypatch.setenv("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH", str(quote_path))
    clear_surface_original_quote_bank_cache()

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(
            intent_tags=("intimacy",),
            emotion_tags=("deep_affection",),
            style_moves=("direct_affection",),
            relationship_stage="lover_stable",
        ),
        limit=1,
    )

    assert "我……喜欢哥哥。喜欢好久好久了。" in rendered
    assert "什么嘛。我也是没办法呀……" not in rendered


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


def test_score_rewards_style_echo_of_prompted_original_quote(tmp_path, monkeypatch) -> None:
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

    assert row.dimensions["quote_style_transfer"] >= 94
    assert any(code.startswith("ORIGINAL_QUOTE_STYLE_ECHO") for code in row.reason_codes)


def test_chinese_sentence_punctuation_is_normalized_before_scoring() -> None:
    assert normalize_surface_chat_punctuation("总算到最后一轮了?稳住,不急。") == "总算到最后一轮了？稳住，不急。"
