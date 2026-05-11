from __future__ import annotations

import argparse
import json

from tools.quality_cluster_report import build_quality_cluster_report, render_markdown_report, run_report


def _row(case_id: str, text: str, score: float, *, reasons=(), quality_hits=(), dimensions=None, passed=True):
    return {
        "case_id": case_id,
        "selected_from": "source",
        "selected": {
            "text": text,
            "score": score,
            "passed": passed,
            "hard_fail": False,
            "reason_codes": list(reasons),
            "control_violations": [],
            "dimensions": dimensions or {},
            "quality": {
                "accepted": True,
                "hard_fail": False,
                "reason_codes": ["FORBIDDEN_TEMPLATE_TEXT"] if quality_hits else [],
                "forbidden_hits": list(quality_hits),
            },
        },
    }


def test_quality_cluster_report_groups_reason_surface_and_dimensions() -> None:
    report = build_quality_cluster_report(
        [
            _row(
                "synthetic_synthetic_daily_short_ack_001",
                "嗯嗯，你说了算。",
                67.797,
                reasons=("BLAND_PASS_LINE",),
                dimensions={"catchiness": 60.0, "character_freshness": 68.0},
                passed=False,
            ),
            _row(
                "dtq_daily_good_morning_001",
                "早啊尼尼~今天也要好好陪我哦。",
                87.069,
                reasons=("QUALITY_SOFT_FORBIDDEN_TEMPLATE_TEXT", "FORBIDDEN_TEMPLATE_TEXT"),
                quality_hits=("cheap_wave_punctuation",),
            ),
        ],
        min_cluster_count=1,
    )

    clusters = {cluster["cluster"]: cluster for cluster in report["clusters"]}

    assert report["selected_count"] == 2
    assert report["low_score_count"] == 1
    assert clusters["reason:BLAND_PASS_LINE"]["count"] == 1
    assert clusters["surface:cheap_wave_punctuation"]["count"] == 1
    assert clusters["low_dimension:catchiness"]["count"] == 1
    assert all(cluster["cluster"] != "reason:LOW_CATCHINESS" for cluster in report["large_clusters"])

    rendered = render_markdown_report(report)
    assert "Large Issue Clusters" in rendered
    assert "Diagnostic Overlap" in rendered
    assert "reason:BLAND_PASS_LINE" in rendered


def test_quality_cluster_report_cli_writes_json_and_markdown(tmp_path) -> None:
    scores = tmp_path / "merged_scores.jsonl"
    scores.write_text(
        json.dumps(
            _row("case_001", "嗯嗯，你说了算。", 67.797, reasons=("BLAND_PASS_LINE",), passed=False),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "report"
    summary = run_report(
        argparse.Namespace(
            scores=scores,
            out=out,
            threshold=80.0,
            challenge_threshold=83.0,
            dimension_threshold=83.0,
            cluster_alert_percent=3.0,
            min_cluster_count=1,
            max_examples=4,
            tail_count=12,
        )
    )

    assert summary["selected_count"] == 1
    assert (out / "quality_cluster_summary.json").exists()
    assert (out / "quality_cluster_report.md").exists()
