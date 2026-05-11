from __future__ import annotations

from pathlib import Path

from host_brain.persona.isolated_surface_harness import build_isolated_surface_input
from tools.build_isolated_surface_daily_goal_cases import build_summary, expand_cases


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _seed_case(case_id: str, user_message: str, task_mode: str, *, must_include: list[str] | None = None) -> dict:
    return {
        "case_id": case_id,
        "facts": [],
        "must_not_do": [],
        "reply_control_packet": {
            "response_move": task_mode,
            "task_mode": task_mode,
            "emotional_tone": "light",
            "relationship_distance": "familiar",
            "information_budget": "short",
            "question_budget": 0,
            "must_include": must_include or [],
            "must_not_do": [],
            "facts": [],
            "style_anchor": ["短句"],
        },
        "user_message": user_message,
    }


def test_expand_cases_builds_deterministic_unique_variants() -> None:
    seed = [
        _seed_case("morning", "早", "daily_good_morning", must_include=["早"]),
        _seed_case("presence", "在吗", "presence"),
    ]

    expanded = expand_cases(seed, variants_per_seed=5, target_count=10)

    assert len(expanded) == 10
    assert [case["case_id"] for case in expanded[:5]] == [
        "morning",
        "morning__v02",
        "morning__v03",
        "morning__v04",
        "morning__v05",
    ]
    assert len({case["case_id"] for case in expanded}) == len(expanded)
    assert all("早" in case["user_message"] for case in expanded[:5])
    assert expanded[5]["user_message"] == "在吗"
    assert expanded[6]["user_message"] == "在不在"


def test_expanded_cases_keep_isolated_input_surface_valid() -> None:
    expanded = expand_cases(
        [
            _seed_case("debug", "这个报错我看不懂，帮我看看", "help_takeover"),
            _seed_case("write", "帮我写一下这个", "write_takeover"),
        ],
        variants_per_seed=5,
    )

    for case in expanded:
        request = build_isolated_surface_input({**case, "soul_md": SOUL})
        assert request.user_message
        assert "熟人私聊" in request.control.style_anchor
        assert "轻微二次元galgame感" in request.control.style_anchor


def test_build_summary_reports_variant_coverage() -> None:
    seed = [_seed_case("morning", "早", "daily_good_morning", must_include=["早"])]
    expanded = expand_cases(seed, variants_per_seed=5)

    summary = build_summary(seed, expanded, seed_path=Path("seed.jsonl"), out_dir=Path("out"))

    assert summary["seed_cases"] == 1
    assert summary["expanded_cases"] == 5
    assert summary["manual_variant_coverage"] == 1
    assert summary["fallback_seed_messages"] == []
    assert summary["expanded_scene_counts"]["daily_good_morning"] == 5
