from __future__ import annotations

import json

from tools.rebuild_surface_original_quote_vector_collection import (
    DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
    load_quote_seeds,
    rebuild_surface_original_quote_collection,
)


def _write_jsonl(path, rows: list[dict]) -> None:  # noqa: ANN001
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_quote_seeds_keeps_dialogue_and_risk_tiers(tmp_path) -> None:
    source = tmp_path / "quotes.jsonl"
    _write_jsonl(
        source,
        [
            {
                "file": "b01.txt.scn",
                "index": 1,
                "kind": "dialogue",
                "daily_style_safe": True,
                "sora_candidate_score": 5,
                "text": "「哈？谁稀罕管你啊。」",
            },
            {
                "file": "b01.txt.scn",
                "index": 2,
                "kind": "narration",
                "daily_style_safe": True,
                "sora_candidate_score": 5,
                "text": "窗外的风吹过。",
            },
            {
                "file": "b01.txt.scn",
                "index": 3,
                "kind": "dialogue",
                "daily_style_safe": False,
                "sora_candidate_score": 0,
                "text": "嗯……这样啊。",
            },
            {
                "file": "b01.txt.scn",
                "index": 4,
                "kind": "dialogue",
                "daily_style_safe": True,
                "sora_candidate_score": 5,
                "text": "あたしも一緒に出るね～。晩ご飯買ってくる",
            },
        ],
    )

    seeds, stats = load_quote_seeds(source)

    assert stats["rows"] == 4
    assert stats["dialogue_rows"] == 3
    assert stats["skipped_non_dialogue"] == 1
    assert len(seeds) == 3
    assert seeds[0].text == "哈？谁稀罕管你啊。"
    assert seeds[0].metadata["risk_tier"] == "safe_full_line"
    assert seeds[1].metadata["risk_tier"] == "rhythm_fragment"
    assert seeds[2].metadata["risk_tier"] == "unsafe_plot_line"
    assert stats["risk_tiers"] == {
        "safe_full_line": 1,
        "rhythm_fragment": 1,
        "unsafe_plot_line": 1,
    }


def test_rebuild_surface_original_quote_collection_dry_run_does_not_need_chroma(tmp_path) -> None:
    source = tmp_path / "quotes.jsonl"
    _write_jsonl(
        source,
        [
            {
                "file": "b01.txt.scn",
                "index": 1,
                "kind": "dialogue",
                "daily_style_safe": True,
                "sora_candidate_score": 5,
                "text": "「才不是呢！接下来要怎么办！」",
            }
        ],
    )

    summary = rebuild_surface_original_quote_collection(source=source, dry_run=True)

    assert summary["collection"] == DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION
    assert summary["seed_count"] == 1
    assert summary["dry_run"] is True
    assert summary["embedding_mode"] == "auto"
