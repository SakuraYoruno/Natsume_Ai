from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diag_emotion_closed_loop import run_closed_loop_validation


def test_emotion_closed_loop_validation_has_10_plus_cases_and_passes_threshold() -> None:
    report = run_closed_loop_validation(threshold=0.8)
    total_cases = int(report.get("total_cases") or 0)
    pass_rate = float(report.get("pass_rate") or 0.0)
    assert total_cases >= 10
    assert pass_rate >= 0.8
    assert bool(report.get("threshold_passed")) is True


def test_emotion_closed_loop_report_contains_case_results() -> None:
    report = run_closed_loop_validation(threshold=0.8)
    results = list(report.get("results") or [])
    assert results
    first = dict(results[0])
    assert "case_id" in first
    assert "normalized_emotion_tag" in first
    assert "prompt_ok" in first
    assert "vts_ok" in first
