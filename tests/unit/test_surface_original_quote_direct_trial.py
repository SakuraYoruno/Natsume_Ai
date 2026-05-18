from __future__ import annotations

import argparse
import asyncio
import json

from host_brain.persona.isolated_surface_harness import build_isolated_surface_input
from host_brain.persona.surface_original_quote_bank import SurfaceOriginalQuote, SurfaceOriginalQuoteSelection
from tools.surface_original_quote_direct_trial import (
    build_arg_parser,
    build_original_quote_direct_messages,
    load_scene_quote_examples,
    quote_examples_for_request,
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
    assert "不能编现实动作或现场状态" in rendered
    assert "不要承认走神、忘了、脑袋空白" in rendered
    assert "是在要关系安定" in rendered
    assert "本轮 question_budget=0" in rendered
    assert "不要用“下一步呢/说句话呀/是不是/吧？”" in rendered
    assert "不要安排用户身体动作或现实作息" in rendered
    assert "禁用“熬夜、没睡醒、准时、挺早、忙文本、加班、摸鱼、做完、偷偷、又、主动来找我、回得干脆、回来得晚、忙到半夜、被工作吃掉、难得”" in rendered
    assert "不要编喝水、坐下、躺下、睡觉、明天再说" in rendered
    assert "不评价对方来得早晚、是否主动、是否老实、是否刚睡醒" in rendered
    assert "被用户推开时先收住距离" in rendered
    assert "不要输出 emoji、颜文字、表情符号或日语口癖" in rendered


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


def test_cli_defaults_to_collection_quote_source() -> None:
    args = build_arg_parser().parse_args([])

    assert args.quote_source == "collection"


def test_collection_quote_examples_use_runtime_quote_selection(monkeypatch) -> None:
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

    def fake_select(query, *, limit):  # noqa: ANN001
        quote = SurfaceOriginalQuote.from_dict(
            {
                "quote_id": "surface_original_quote::continue",
                "text": "才不是呢！人家想听的是接下来我们要怎么办！",
                "prompt_text": "才不是呢！人家想听的是接下来我们要怎么办！",
                "source_digest": "continue",
                "risk_tier": "safe_full_line",
            }
        )
        return SurfaceOriginalQuoteSelection(quotes=(quote,), query=query)

    monkeypatch.setattr("tools.surface_original_quote_direct_trial.select_surface_original_quotes", fake_select)

    examples = quote_examples_for_request(
        request,
        scene_id="short_continue",
        source="collection",
        template_examples=load_scene_quote_examples("").get("short_continue"),
        limit=4,
    )

    assert examples.source == "surface_original_quotes"
    assert examples.examples == ("才不是呢！人家想听的是接下来我们要怎么办！",)


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
