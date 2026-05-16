from __future__ import annotations

import argparse
import asyncio
import json

from host_brain.persona.isolated_surface_harness import build_isolated_surface_input
from tools.surface_original_quote_direct_trial import (
    build_original_quote_direct_messages,
    load_scene_quote_examples,
    run_surface_original_quote_direct_trial,
)


def test_original_quote_direct_prompt_excludes_scene_quality_pack() -> None:
    request = build_isolated_surface_input(
        {
            "soul_md": "你是数字生命夏目蓝。",
            "user_message": "继续",
            "reply_control_packet": {
                "response_move": "continue_current_task",
                "task_mode": "daily_chat",
                "emotional_tone": "focused_light",
                "relationship_distance": "familiar",
                "information_budget": "short",
                "question_budget": 0,
                "must_include": [],
                "must_not_do": [],
                "facts": [],
                "style_anchor": [],
            },
        }
    )

    rendered = "\n".join(
        message["content"]
        for message in build_original_quote_direct_messages(
            request,
            scene_id="short_continue",
            quote_examples=["等一下啦！重来一次！再给人家一次机会嘛！就一次！"],
        )
    )

    assert "## 原台词直喂样例" in rendered
    assert "等一下啦！重来一次！再给人家一次机会嘛！就一次！" in rendered
    assert "## 场景质量样本" not in rendered
    assert "good_examples:" not in rendered
    assert "bad_examples:" not in rendered
    assert "不要原样照抄" not in rendered


def test_load_scene_quote_examples_reads_review_template(tmp_path) -> None:
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "scene_id": "short_continue",
                        "candidate_good_examples": [
                            {"text": "等一下啦！重来一次！"},
                            {"text": "等一下啦！重来一次！"},
                            {"text": "啊，等一下人家啦～！"},
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    examples = load_scene_quote_examples(review_path, limit=8)

    assert examples["short_continue"].examples == (
        "等一下啦！重来一次！",
        "啊，等一下人家啦～！",
    )


def test_original_quote_direct_fixture_writes_selected_outputs(tmp_path) -> None:
    result = asyncio.run(
        run_surface_original_quote_direct_trial(
            argparse.Namespace(
                mode="fixture",
                provider="deepseek",
                model="",
                base_url="",
                out_dir=str(tmp_path),
                case_set="smoke",
                limit=2,
                variant="affect",
                review_template="",
                quote_limit=4,
                timeout_ms=1000,
                temperature=0.35,
                top_p=0.8,
                max_tokens=220,
                concurrency=1,
            )
        )
    )

    assert result["summary"]["mode"] == "fixture"
    assert result["summary"]["variant_count"] == 2
    assert (tmp_path / "selected_outputs.md").exists()
