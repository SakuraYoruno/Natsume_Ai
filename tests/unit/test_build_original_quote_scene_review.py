from __future__ import annotations

import json

from tools.build_original_quote_scene_review import (
    SourceQuote,
    build_original_quote_scene_review,
    load_source_quotes,
    render_markdown_review,
)


def _write_jsonl(path, rows: list[dict]) -> None:  # noqa: ANN001
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_source_quotes_dedupes_quote_bank_over_candidate_source(tmp_path) -> None:
    quote_bank = tmp_path / "quotes.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    _write_jsonl(
        quote_bank,
        [
            {
                "text": "哈？谁稀罕管你啊。",
                "prompt_text": "哈？谁稀罕管你啊。",
                "source_ref": "quote.scn#1",
                "source_score": 5,
                "confidence": 0.9,
                "intent_tags": ["boundary"],
                "emotion_tags": ["annoyed"],
                "style_moves": ["quick_rebuttal"],
            }
        ],
    )
    _write_jsonl(
        candidates,
        [
            {
                "file": "candidate.scn",
                "index": 2,
                "kind": "dialogue",
                "daily_style_safe": True,
                "sora_candidate_score": 3,
                "text": "「哈？谁稀罕管你啊。」",
            }
        ],
    )

    quotes = load_source_quotes(quote_bank=quote_bank, candidate_sources=[candidates])

    assert len(quotes) == 1
    assert quotes[0].source_kind == "surface_original_quote_bank"
    assert quotes[0].source_ref == "quote.scn#1"


def test_build_original_quote_scene_review_groups_boundary_quotes() -> None:
    payload = build_original_quote_scene_review(
        [
            SourceQuote(
                text="哈？谁稀罕管你啊。",
                source_ref="quote.scn#1",
                source_kind="surface_original_quote_bank",
                source_score=6,
                confidence=0.95,
                intent_tags=("boundary",),
                emotion_tags=("annoyed",),
                style_moves=("quick_rebuttal", "wounded_pushback"),
            ),
            SourceQuote(
                text="什么嘛～。人家也是没办法呀……",
                source_ref="quote.scn#2",
                source_kind="surface_original_quote_bank",
                source_score=5,
                confidence=0.8,
                intent_tags=("daily",),
                style_moves=("quick_reaction",),
            ),
        ],
        scene_ids=["pushed_away_boundary", "negative_feedback"],
        max_per_scene=2,
        min_score=10,
    )

    assert payload["sample_kind"] == "surface_scene_quality_review_template"
    assert payload["source_kind"] == "original_quote_scene_harvest"
    boundary = next(scene for scene in payload["scenes"] if scene["scene_id"] == "pushed_away_boundary")
    assert boundary["good_examples"] == []
    assert boundary["candidate_good_examples"][0]["text"] == "哈？谁稀罕管你啊。"
    assert "boundary" in boundary["candidate_good_examples"][0]["matched_tags"]


def test_render_markdown_review_includes_scene_tables() -> None:
    payload = build_original_quote_scene_review(
        [
            SourceQuote(
                text="什么嘛～。人家也是没办法呀……",
                source_ref="quote.scn#2",
                source_kind="surface_original_quote_bank",
                source_score=5,
                confidence=0.8,
                intent_tags=("daily",),
                style_moves=("quick_reaction",),
            )
        ],
        scene_ids=["negative_feedback"],
        max_per_scene=1,
        min_score=10,
    )

    rendered = render_markdown_review(payload)

    assert "# 原台词场景样本人工评审" in rendered
    assert "negative_feedback" in rendered
    assert "什么嘛～。人家也是没办法呀……" in rendered
