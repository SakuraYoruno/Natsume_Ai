from __future__ import annotations

import json

from host_brain.persona.surface_style_seed_bank import (
    DEFAULT_SURFACE_STYLE_SEED_BANK_PATH,
    clear_surface_style_seed_bank_cache,
    get_surface_style_seed_bank,
    render_surface_style_seed_guidance,
)
from host_brain.persona.isolated_surface_harness import build_isolated_surface_input, build_isolated_surface_messages
from tools.build_surface_style_seed_bank import build_seed_bank


def test_surface_style_seed_bank_has_real_source_refs_without_raw_quotes() -> None:
    payload = json.loads(DEFAULT_SURFACE_STYLE_SEED_BANK_PATH.read_text(encoding="utf-8"))

    assert payload["source"]["raw_quote_storage"] is False
    assert payload["stats"]["moves"] >= 6
    for move in payload["moves"]:
        assert move["source_refs"]
        assert move["source_digests"]
        assert "text" not in move
        assert "raw_text" not in move
        assert "examples" not in move


def test_surface_style_seed_guidance_selects_by_response_move() -> None:
    clear_surface_style_seed_bank_cache()

    guidance = render_surface_style_seed_guidance(response_move="ack_with_topic_hook", task_mode="short_ack")

    assert "真实中文台词派生风格种子" in guidance
    assert "禁止照抄" in guidance
    assert "quick_rebuttal_then_land" in guidance
    assert "source_refs=" not in guidance


def test_surface_style_seed_bank_builds_from_real_candidate_rows_without_storing_raw_text() -> None:
    rows = [
        {
            "file": "C:/src/b01.txt.scn",
            "index": 1,
            "kind": "dialogue",
            "sora_candidate_score": 5,
            "daily_style_safe": True,
            "text": "「才不是呢！」",
        },
        {
            "file": "C:/src/b02.txt.scn",
            "index": 2,
            "kind": "dialogue",
            "sora_candidate_score": 4,
            "daily_style_safe": True,
            "text": "「等一下啦！」",
        },
    ]

    bank = build_seed_bank(rows, max_refs=2)
    rendered = json.dumps(bank, ensure_ascii=False)

    assert bank["stats"]["candidate_rows"] == 2
    assert "C:/src/b01.txt.scn#1" in rendered
    assert "才不是呢" not in rendered
    assert "等一下啦" not in rendered


def test_default_surface_style_seed_bank_loads() -> None:
    clear_surface_style_seed_bank_cache()
    bank = get_surface_style_seed_bank()

    selected = bank.select(response_move="technical_next_step", task_mode="help_takeover")

    assert selected.seeds
    assert any(seed.move_id == "care_behind_complaint" for seed in selected.seeds)


def test_isolated_surface_prompt_includes_real_dialogue_style_seed_guidance() -> None:
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

    assert "真实中文台词派生风格种子" in rendered
    assert "禁止照抄" in rendered
    assert "quick_rebuttal_then_land" in rendered
