from __future__ import annotations

import json

from host_brain.persona.surface.input_validation import build_isolated_surface_input
from host_brain.persona.surface.prompt_construction import (
    build_isolated_surface_messages,
    build_surface_original_quote_query,
)
from host_brain.persona.surface.data_models import normalize_surface_chat_punctuation
from host_brain.persona.surface.candidate_scoring import (
    score_isolated_surface_candidates,
)
import host_brain.persona.surface_original_quote_bank as quote_mod
from host_brain.persona.surface_original_quote_bank import (
    DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
    SurfaceOriginalQuoteQuery,
    render_surface_original_quote_guidance,
    select_surface_original_quotes,
    surface_original_quote_similarity_hits,
    surface_original_quote_policy_for_scene,
    to_prompt_style_text,
)
from tools.build_surface_original_quote_bank import build_quote_bank


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


def _vector_row(record: dict, *, score: float = 0.91) -> dict:
    return {
        "memory_id": record["quote_id"],
        "text": record["prompt_text"] if "prompt_text" in record else record["text"],
        "score": score,
        "metadata": {
            **record,
            "intent_tags": json.dumps(record.get("intent_tags") or [], ensure_ascii=False),
            "emotion_tags": json.dumps(record.get("emotion_tags") or [], ensure_ascii=False),
            "style_moves": json.dumps(record.get("style_moves") or [], ensure_ascii=False),
            "rhythm_tags": json.dumps(record.get("rhythm_tags") or [], ensure_ascii=False),
            "lexical_cues": json.dumps(record.get("lexical_cues") or [], ensure_ascii=False),
            "relationship_stages": json.dumps(record.get("relationship_stages") or [], ensure_ascii=False),
            "risk_tier": record.get("risk_tier", "safe_full_line"),
        },
    }


def _patch_vector_rows(monkeypatch, rows: list[dict]) -> None:  # noqa: ANN001
    quote_mod.clear_surface_original_quote_bank_cache()
    monkeypatch.setattr(quote_mod, "_query_riddle_joker_dialogue_tsv", lambda query, *, top_k: [])
    monkeypatch.setattr(quote_mod, "_query_riddle_joker_dialogue_vector_collection", lambda query, *, top_k: [])
    monkeypatch.setattr(
        quote_mod,
        "_query_surface_original_quote_collection",
        lambda query, *, top_k: rows[:top_k],
    )


def test_surface_original_quote_runtime_uses_vector_collection_constant() -> None:
    assert DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION == "surface_original_quotes"


def test_riddle_joker_lexical_runtime_prefers_natsume_voice_and_caches_rows(
    monkeypatch,
) -> None:
    primary = _vector_row(
        {
            **_quote_record("嗯，先在这陪你。"),
            "quote_id": "riddle_joker_tsv::primary",
            "source_ref": "dialogue.tsv:1:七海",
            "source_digest": "primary",
            "character_confidence": 1.0,
        }
    )
    secondary = _vector_row(
        {
            **_quote_record("那就先停一下。"),
            "quote_id": "riddle_joker_tsv::secondary",
            "source_ref": "dialogue.tsv:2:其他角色",
            "source_digest": "secondary",
            "character_confidence": 0.62,
        }
    )
    calls = 0

    def rows():
        nonlocal calls
        calls += 1
        return [primary, secondary]

    quote_mod.clear_surface_original_quote_bank_cache()
    monkeypatch.setenv("HOST_SURFACE_RIDDLE_JOKER_PRIMARY_CHARACTER_ONLY", "1")
    monkeypatch.setattr(quote_mod, "_fetch_riddle_joker_dialogue_tsv_rows", rows)

    query = SurfaceOriginalQuoteQuery(user_message="今天不太顺", question_budget=0)
    first = quote_mod._query_riddle_joker_dialogue_tsv(query, top_k=4)
    second = quote_mod._query_riddle_joker_dialogue_tsv(query, top_k=4)

    assert [row["memory_id"] for row in first] == ["riddle_joker_tsv::primary"]
    assert [row["memory_id"] for row in second] == ["riddle_joker_tsv::primary"]
    assert calls == 1


def test_original_quote_query_excludes_question_examples_when_budget_is_zero(
    monkeypatch,
) -> None:
    statement = _vector_row(
        {
            **_quote_record("嗯，先在这陪你。"),
            "quote_id": "riddle_joker_tsv::statement",
            "source_ref": "dialogue.tsv:1:七海",
            "source_digest": "statement",
            "character_confidence": 1.0,
        }
    )
    question = _vector_row(
        {
            **_quote_record("要不要先停一下？"),
            "quote_id": "riddle_joker_tsv::question",
            "source_ref": "dialogue.tsv:2:七海",
            "source_digest": "question",
            "character_confidence": 1.0,
        }
    )
    quote_mod.clear_surface_original_quote_bank_cache()
    monkeypatch.setenv("HOST_SURFACE_RIDDLE_JOKER_PRIMARY_CHARACTER_ONLY", "1")
    monkeypatch.setattr(
        quote_mod,
        "_fetch_riddle_joker_dialogue_tsv_rows",
        lambda: [statement, question],
    )

    result = quote_mod._query_riddle_joker_dialogue_tsv(
        SurfaceOriginalQuoteQuery(
            user_message="今天不太顺",
            question_budget=0,
        ),
        top_k=4,
    )

    assert [row["memory_id"] for row in result] == [
        "riddle_joker_tsv::statement"
    ]


def test_local_lexical_miss_does_not_open_slow_vector_fallback_by_default(
    monkeypatch,
) -> None:
    question = _vector_row(
        {
            **_quote_record("要不要先停一下？"),
            "quote_id": "riddle_joker_tsv::question-only",
            "source_ref": "dialogue.tsv:1:七海",
            "source_digest": "question-only",
            "character_confidence": 1.0,
        }
    )
    vector_calls = 0

    def vector_rows(*_args, **_kwargs):
        nonlocal vector_calls
        vector_calls += 1
        return []

    quote_mod.clear_surface_original_quote_bank_cache()
    monkeypatch.setenv("HOST_SURFACE_RIDDLE_JOKER_PRIMARY_CHARACTER_ONLY", "1")
    monkeypatch.delenv(
        "HOST_SURFACE_RIDDLE_JOKER_VECTOR_FALLBACK_AFTER_LEXICAL_MISS",
        raising=False,
    )
    monkeypatch.setattr(
        quote_mod,
        "_fetch_riddle_joker_dialogue_tsv_rows",
        lambda: [question],
    )
    monkeypatch.setattr(
        quote_mod,
        "_query_riddle_joker_dialogue_vector_collection",
        vector_rows,
    )
    monkeypatch.setattr(
        quote_mod,
        "_query_surface_original_quote_collection",
        vector_rows,
    )

    selection = select_surface_original_quotes(
        SurfaceOriginalQuoteQuery(
            user_message="今天不太顺",
            scene_id="implicit_low_mood",
            question_budget=0,
        ),
        limit=4,
    )

    assert selection.quotes == ()
    assert vector_calls == 0


def test_render_surface_original_quote_guidance_includes_vector_quotes_not_source_refs(monkeypatch) -> None:
    _patch_vector_rows(monkeypatch, [_vector_row(_quote_record())])

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("daily",), style_moves=("quick_reaction",), relationship_stage="familiar"),
        limit=3,
    )

    assert "真实中文原句风格参考" in rendered
    assert "才不是呢！接下来要怎么办！" in rendered
    assert "C:/src" not in rendered
    assert "独立 Chroma 原作中文台词 collection" in rendered


def test_surface_original_quote_policy_splits_scene_strength() -> None:
    assert surface_original_quote_policy_for_scene("short_continue").mode == "strong"
    assert surface_original_quote_policy_for_scene("short_continue").max_quotes == 3
    assert surface_original_quote_policy_for_scene("lover_presence").max_quotes == 3
    assert surface_original_quote_policy_for_scene("ambiguous_affection").max_quotes == 1
    assert surface_original_quote_policy_for_scene("relationship_repair").max_quotes == 3
    assert surface_original_quote_policy_for_scene("mixed_affect").max_quotes == 2
    assert surface_original_quote_policy_for_scene("technical_next_step").mode == "need_samples"
    assert surface_original_quote_policy_for_scene("daily_morning").mode == "need_samples"


def test_curated_relationship_repair_quotes_keep_fixed_source_order() -> None:
    selection = select_surface_original_quotes(
        SurfaceOriginalQuoteQuery(
            scene_id="relationship_repair",
            relationship_stage="familiar",
            question_budget=0,
            limit=3,
        ),
        limit=3,
    )

    assert [quote.quote_id for quote in selection.quotes] == [
        "riddle_joker_tsv::741",
        "riddle_joker_tsv::1588",
        "riddle_joker_tsv::8895",
    ]


def test_surface_original_quote_policy_disables_morning_without_samples(monkeypatch) -> None:
    _patch_vector_rows(monkeypatch, [_vector_row(_quote_record("早啊，老哥。"))])
    query = SurfaceOriginalQuoteQuery(
        intent_tags=("daily",),
        style_moves=("quick_reaction",),
        relationship_stage="familiar",
        scene_id="daily_morning",
    )

    assert render_surface_original_quote_guidance(query, limit=3) == ""
    assert select_surface_original_quotes(query, limit=3).reason == "SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES"


def test_surface_original_quote_policy_disables_careful_scenes_until_curated_samples(monkeypatch) -> None:
    _patch_vector_rows(monkeypatch, [_vector_row(_quote_record())])

    careful = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("help",), style_moves=("take_charge",), scene_id="technical_next_step"),
        limit=8,
    )
    strong = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("daily",), style_moves=("quick_reaction",), scene_id="short_continue"),
        limit=8,
    )

    assert careful == ""
    assert select_surface_original_quotes(
        SurfaceOriginalQuoteQuery(intent_tags=("help",), style_moves=("take_charge",), scene_id="technical_next_step"),
        limit=8,
    ).reason == "SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES"
    assert "quote_policy: strong" in strong
    assert "强 few-shot" in strong


def test_surface_original_quote_similarity_detects_near_copy() -> None:
    quote = quote_mod.SurfaceOriginalQuote.from_dict(_quote_record())

    hits = surface_original_quote_similarity_hits("才不是呢，接下来要怎么办。", [quote])

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


def test_original_quote_selection_prefers_relevant_scene_over_generic_daily(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
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
                score=0.95,
            ),
            _vector_row(
                {
                    **_quote_record("哥哥，今天也想见你。"),
                    "quote_id": "surface_original_quote::intimacy",
                    "source_digest": "intimacy",
                    "source_score": 3,
                    "confidence": 0.62,
                    "intent_tags": ["intimacy"],
                    "emotion_tags": ["deep_affection"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.7,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(
            intent_tags=("intimacy",),
            emotion_tags=("deep_affection",),
            style_moves=("direct_affection",),
            relationship_stage="lover_stable",
        ),
        limit=1,
    )

    assert "哥哥，今天也想见你。" in rendered
    assert "什么嘛。我也是没办法呀……" not in rendered


def test_original_quote_selection_skips_unsafe_plot_vector_rows(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("去金拱门咯金拱门！哥哥请客！"),
                    "quote_id": "surface_original_quote::plot",
                    "source_digest": "plot",
                    "risk_tier": "unsafe_plot_line",
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("哈？谁稀罕管你啊。"),
                    "quote_id": "surface_original_quote::safe",
                    "source_digest": "safe",
                    "intent_tags": ["boundary"],
                    "style_moves": ["quick_rebuttal"],
                    "risk_tier": "safe_full_line",
                },
                score=0.6,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(intent_tags=("boundary",), style_moves=("quick_rebuttal",), scene_id="negative_feedback"),
        limit=2,
    )

    assert "哈？谁稀罕管你啊。" in rendered
    assert "金拱门" not in rendered


def test_return_chat_quote_selection_blocks_runtime_fact_noise(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("每周回来一次嘛，周末回家住一晚多好啊。"),
                    "quote_id": "surface_original_quote::runtime_fact",
                    "source_digest": "runtime_fact",
                    "intent_tags": ["daily"],
                    "style_moves": ["quick_reaction"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("哥哥欢迎回来～☆"),
                    "quote_id": "surface_original_quote::welcome",
                    "source_digest": "welcome",
                    "intent_tags": ["daily"],
                    "style_moves": ["quick_reaction"],
                },
                score=0.6,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(user_message="回来了", intent_tags=("daily",), style_moves=("quick_reaction",), scene_id="return_chat"),
        limit=2,
    )

    assert "哥哥欢迎回来～☆" in rendered
    assert "每周回来一次" not in rendered


def test_ambiguous_affection_quote_selection_blocks_direct_confession(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("喜欢……，哥哥……，喜欢……"),
                    "quote_id": "surface_original_quote::direct_confession",
                    "source_digest": "direct_confession",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["ambiguous"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("嘛……那啥。被这种万人迷美少女喜欢上是一种怎样的体验？"),
                    "quote_id": "surface_original_quote::self_boast",
                    "source_digest": "self_boast",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["tease"],
                    "relationship_stages": ["ambiguous"],
                },
                score=0.9,
            ),
            _vector_row(
                {
                    **_quote_record("别害羞嘛～、有什么不好嘛～。其实哥哥你也很高兴吧～？"),
                    "quote_id": "surface_original_quote::tease",
                    "source_digest": "tease",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["tease"],
                    "relationship_stages": ["ambiguous"],
                },
                score=0.4,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(
            user_message="你刚才是不是吃醋了",
            intent_tags=("intimacy",),
            style_moves=("tease",),
            relationship_stage="ambiguous",
            scene_id="ambiguous_affection",
        ),
        limit=2,
    )

    assert "别害羞嘛" in rendered
    assert "喜欢……，哥哥" not in rendered
    assert "万人迷美少女" not in rendered


def test_lover_presence_quote_selection_caps_and_skips_melodrama(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("因为不管发生什么，不管别人怎么想，哥哥都是我最最喜欢的哥哥。"),
                    "quote_id": "surface_original_quote::melodrama",
                    "source_digest": "melodrama",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("最喜欢哥哥啦～☆"),
                    "quote_id": "surface_original_quote::love1",
                    "source_digest": "love1",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.8,
            ),
            _vector_row(
                {
                    **_quote_record("谢谢～最喜欢哥哥啦～☆"),
                    "quote_id": "surface_original_quote::love2",
                    "source_digest": "love2",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.7,
            ),
            _vector_row(
                {
                    **_quote_record("讨厌……今天哥哥超级帅……。求抱抱……"),
                    "quote_id": "surface_original_quote::love3",
                    "source_digest": "love3",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.6,
            ),
            _vector_row(
                {
                    **_quote_record("哥哥，今天也想见你。"),
                    "quote_id": "surface_original_quote::love4",
                    "source_digest": "love4",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.5,
            ),
        ],
    )

    selection = select_surface_original_quotes(
        SurfaceOriginalQuoteQuery(
            user_message="想你了",
            intent_tags=("intimacy",),
            style_moves=("direct_affection",),
            relationship_stage="lover_stable",
            scene_id="lover_presence",
        ),
        limit=6,
    )

    texts = [quote.text for quote in selection.quotes]
    assert len(texts) == 3
    assert all("不管发生什么" not in text for text in texts)
    assert all("求抱抱" not in text for text in texts)


def test_original_quote_runtime_filters_overbound_relationship_lines(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("求你了，满足我这个爱上哥哥的妹妹的愿望吧。"),
                    "quote_id": "surface_original_quote::overbound1",
                    "source_digest": "overbound1",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("哥哥，今天也想见你。"),
                    "quote_id": "surface_original_quote::safe_love",
                    "source_digest": "safe_love",
                    "intent_tags": ["intimacy"],
                    "style_moves": ["direct_affection"],
                    "relationship_stages": ["lover_stable"],
                },
                score=0.55,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(
            user_message="想你了",
            intent_tags=("intimacy",),
            style_moves=("direct_affection",),
            relationship_stage="lover_stable",
            scene_id="lover_presence",
        ),
        limit=3,
    )

    assert "哥哥，今天也想见你。" in rendered
    assert "求你了" not in rendered
    assert "爱上哥哥" not in rendered


def test_surface_original_quote_query_aliases_affection_and_lover_scenes() -> None:
    ambiguous = build_isolated_surface_input(
        {
            "soul_md": "你是数字生命夏目蓝。",
            "user_message": "你刚才是不是有一点在意我",
            "reply_control_packet": {
                "response_move": "receive_affection_lightly",
                "task_mode": "affection",
                "emotional_tone": "warm_affection",
                "relationship_distance": "ambiguous",
                "information_budget": "short",
                "question_budget": 0,
            },
        }
    )
    lover = build_isolated_surface_input(
        {
            "soul_md": "你是数字生命夏目蓝。",
            "user_message": "想你了",
            "reply_control_packet": {
                "response_move": "return_affection_concretely",
                "task_mode": "lover_affection",
                "emotional_tone": "warm_affection",
                "relationship_distance": "lover_stable",
                "information_budget": "short",
                "question_budget": 0,
            },
        }
    )

    assert build_surface_original_quote_query(ambiguous).scene_id == "ambiguous_affection"
    assert build_surface_original_quote_query(lover).scene_id == "lover_presence"


def test_memory_quote_selection_is_disabled_until_curated_samples(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("哥哥！没事吧！"),
                    "quote_id": "surface_original_quote::generic_care",
                    "source_digest": "generic_care",
                    "intent_tags": ["reassurance"],
                    "style_moves": ["soft_reassurance"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("记得啦，别瞎想。"),
                    "quote_id": "surface_original_quote::memory",
                    "source_digest": "memory",
                    "intent_tags": ["daily", "reassurance"],
                    "style_moves": ["quick_reaction", "soft_reassurance"],
                },
                score=0.5,
            ),
        ],
    )

    query = SurfaceOriginalQuoteQuery(
        user_message="还记得上次说的展吗",
        intent_tags=("daily", "reassurance"),
        style_moves=("quick_reaction", "soft_reassurance"),
        relationship_stage="familiar",
        scene_id="explicit_memory",
    )

    assert render_surface_original_quote_guidance(query, limit=4) == ""
    assert select_surface_original_quotes(query, limit=4).reason == "SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES"


def test_tired_quote_selection_is_disabled_until_curated_samples(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("没事儿的啦，让老哥等着就行了。春风学姐，我们放学后集合！"),
                    "quote_id": "surface_original_quote::plot_care",
                    "source_digest": "plot_care",
                    "intent_tags": ["reassurance"],
                    "style_moves": ["soft_reassurance"],
                },
                score=0.99,
            ),
            _vector_row(
                {
                    **_quote_record("累了就先休息，别硬撑。"),
                    "quote_id": "surface_original_quote::tired",
                    "source_digest": "tired",
                    "intent_tags": ["reassurance"],
                    "style_moves": ["soft_reassurance"],
                },
                score=0.5,
            ),
        ],
    )

    query = SurfaceOriginalQuoteQuery(
        user_message="累了",
        intent_tags=("reassurance",),
        style_moves=("soft_reassurance",),
        relationship_stage="close_friend",
        scene_id="tired",
    )

    assert render_surface_original_quote_guidance(query, limit=4) == ""
    assert select_surface_original_quotes(query, limit=4).reason == "SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES"


def test_hash_mode_lexical_fallback_uses_collection_rows_without_random_vector_noise(monkeypatch) -> None:
    rows = [
        _vector_row(
            {
                **_quote_record("あたしも一緒に出るね～。晩ご飯買ってくる"),
                "quote_id": "surface_original_quote::kana",
                "source_digest": "kana",
                "risk_tier": "unsafe_plot_line",
            },
            score=0.99,
        ),
        _vector_row(
            {
                **_quote_record("才不是呢！人家想听的是接下来我们要怎么办！"),
                "quote_id": "surface_original_quote::continue",
                "source_digest": "continue",
                "intent_tags": ["daily"],
                "style_moves": ["question_chain"],
                "relationship_stages": ["familiar"],
            },
            score=0.1,
        ),
    ]
    monkeypatch.setattr(
        quote_mod,
        "_fetch_surface_original_quote_collection_rows",
        lambda **kwargs: rows,
    )

    result = quote_mod._query_surface_original_quote_collection_lexical_fallback(
        SurfaceOriginalQuoteQuery(
            user_message="继续",
            intent_tags=("daily",),
            style_moves=("quick_reaction", "question_chain"),
            relationship_stage="familiar",
            scene_id="short_continue",
        ),
        top_k=2,
        collection="surface_original_quotes",
        dim=1024,
        endpoint="http://127.0.0.1:18000",
        tenant="default_tenant",
        database="default_database",
    )

    assert [row["text"] for row in result] == ["才不是呢！人家想听的是接下来我们要怎么办！"]


def test_original_quote_selection_uses_lexical_fallback_rows(monkeypatch) -> None:
    _patch_vector_rows(
        monkeypatch,
        [
            _vector_row(
                {
                    **_quote_record("才不是呢！人家想听的是接下来我们要怎么办！"),
                    "quote_id": "surface_original_quote::continue",
                    "source_digest": "continue",
                    "intent_tags": ["daily"],
                    "style_moves": ["question_chain"],
                    "relationship_stages": ["familiar"],
                },
                score=0.1,
            ),
        ],
    )

    rendered = render_surface_original_quote_guidance(
        SurfaceOriginalQuoteQuery(
            user_message="继续",
            intent_tags=("daily",),
            style_moves=("quick_reaction", "question_chain"),
            relationship_stage="familiar",
            scene_id="short_continue",
        ),
        limit=2,
    )

    assert "才不是呢！人家想听的是接下来我们要怎么办！" in rendered
    assert "あたし" not in rendered


def test_isolated_surface_prompt_includes_raw_quote_few_shot(monkeypatch) -> None:
    _patch_vector_rows(monkeypatch, [_vector_row(_quote_record())])
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


def test_score_rewards_style_echo_of_prompted_original_quote(monkeypatch) -> None:
    _patch_vector_rows(monkeypatch, [_vector_row(_quote_record())])
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
