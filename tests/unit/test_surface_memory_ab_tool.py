from __future__ import annotations

import argparse
import asyncio

from tools.surface_memory_ab import run_surface_memory_ab


def test_surface_memory_ab_fixture_outputs_detailed_text(tmp_path) -> None:
    result = asyncio.run(
        run_surface_memory_ab(
            argparse.Namespace(
                mode="fixture",
                provider="deepseek",
                model="",
                base_url="",
                out_dir=str(tmp_path),
                limit=3,
                timeout_ms=1000,
                temperature=0.78,
                top_p=0.9,
                max_tokens=220,
                repair_failed=False,
                no_style_bank=True,
            )
        )
    )

    summary = result["summary"]
    rows = result["results"]

    assert summary["mode"] == "fixture"
    assert summary["case_count"] == 3
    assert summary["variant_count"] == 6
    assert summary["source_ref_leak_count"] == 0
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.jsonl").exists()
    assert (tmp_path / "report.md").exists()
    assert rows[0]["variants"]["affect"]["selected_text"]
    assert rows[0]["variants"]["memory_affect"]["selected_text"]
    selected_texts = [
        variant["selected_text"]
        for row in rows
        for variant in row["variants"].values()
    ]
    assert all("memory://" not in text for text in selected_texts)
