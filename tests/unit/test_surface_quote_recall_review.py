from __future__ import annotations

import json

import host_brain.persona.surface_original_quote_bank as quote_mod
from tools.surface_quote_recall_review import (
    DEFAULT_REVIEW_CASES,
    QuoteRecallReviewCase,
    build_surface_quote_recall_review,
    render_markdown_review,
    review_surface_quote_case,
    selected_cases,
    write_review_outputs,
)


def _vector_row(text: str, *, score: float = 0.91, risk_tier: str = "safe_full_line") -> dict:
    record = {
        "quote_id": f"surface_original_quote::{abs(hash(text))}",
        "text": text,
        "prompt_text": text,
        "source_ref": "quote.scn#1",
        "source_digest": "demo",
        "source_score": 5,
        "confidence": 0.9,
        "character_confidence": 0.8,
        "intent_tags": json.dumps(["daily"], ensure_ascii=False),
        "emotion_tags": json.dumps(["neutral"], ensure_ascii=False),
        "style_moves": json.dumps(["quick_reaction", "question_chain"], ensure_ascii=False),
        "rhythm_tags": json.dumps(["exclamation"], ensure_ascii=False),
        "lexical_cues": json.dumps(["短促提高情绪"], ensure_ascii=False),
        "relationship_stages": json.dumps(["familiar"], ensure_ascii=False),
        "risk_tier": risk_tier,
        "query_embedding_model_id": "models/Qwen3-Embedding-0.6B",
    }
    return {"memory_id": record["quote_id"], "text": text, "score": score, "metadata": record, "collection": "surface_original_quotes"}


def test_selected_cases_rejects_unknown_case() -> None:
    assert len(DEFAULT_REVIEW_CASES) >= 10
    try:
        selected_cases(["missing"])
    except ValueError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected unknown case to fail")


def test_review_surface_quote_case_outputs_query_model_and_guidance(monkeypatch) -> None:
    monkeypatch.setattr(
        quote_mod,
        "_query_surface_original_quote_collection",
        lambda query, *, top_k: [_vector_row("才不是呢！接下来要怎么办！")],
    )

    row = review_surface_quote_case(
        QuoteRecallReviewCase(
            case_id="short_continue",
            user_message="继续",
            scene_id="short_continue",
            intent_tags=("daily",),
            style_moves=("quick_reaction", "question_chain"),
        ),
        raw_top_k=4,
        limit=3,
    )

    assert row["query_embedding_model_id"] == "models/Qwen3-Embedding-0.6B"
    assert row["selected_count"] == 1
    assert "才不是呢！接下来要怎么办！" in row["guidance"]
    assert row["raw_candidates_preview"][0]["collection"] == "surface_original_quotes"


def test_review_keeps_disabled_morning_as_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        quote_mod,
        "_query_surface_original_quote_collection",
        lambda query, *, top_k: [_vector_row("早啊，老哥。")],
    )

    row = review_surface_quote_case(
        QuoteRecallReviewCase(
            case_id="daily_morning",
            user_message="早",
            scene_id="daily_morning",
            intent_tags=("daily",),
            style_moves=("quick_reaction",),
        )
    )

    assert row["policy"]["mode"] == "need_samples"
    assert row["raw_candidate_count"] == 0
    assert row["selected_count"] == 0
    assert row["degraded"]


def test_build_and_write_review_outputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        quote_mod,
        "_query_surface_original_quote_collection",
        lambda query, *, top_k: [_vector_row("才不是呢！接下来要怎么办！")],
    )
    payload = build_surface_quote_recall_review(
        [
            QuoteRecallReviewCase(
                case_id="short_continue",
                user_message="继续",
                scene_id="short_continue",
                intent_tags=("daily",),
                style_moves=("quick_reaction", "question_chain"),
            )
        ],
        raw_top_k=4,
    )

    outputs = write_review_outputs(payload, out_dir=tmp_path)
    markdown = render_markdown_review(payload)

    assert payload["summary"]["query_models"] == ["models/Qwen3-Embedding-0.6B"]
    assert "short_continue" in markdown
    assert "才不是呢！接下来要怎么办！" in markdown
    assert (tmp_path / "surface_quote_recall_review.json").exists()
    assert (tmp_path / "surface_quote_recall_review.jsonl").exists()
    assert (tmp_path / "surface_quote_recall_review.md").exists()
    assert set(outputs) == {"json", "jsonl", "markdown"}
