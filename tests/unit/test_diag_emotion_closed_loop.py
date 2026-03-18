from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diag_emotion_closed_loop import (
    DEFAULT_CASES_FIXTURE,
    load_emotion_cases,
    run_closed_loop_validation,
)


def test_emotion_cases_fixture_has_10_plus_cases_and_unique_ids() -> None:
    cases = load_emotion_cases(DEFAULT_CASES_FIXTURE)
    assert len(cases) >= 10
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids))
    # Ensure corpus includes mood/tag constraint normalization samples.
    assert any(
        bool(case.expected_normalized_emotion_tag)
        and str(case.expected_normalized_emotion_tag) != str(case.emotion_tag)
        for case in cases
    )


def test_emotion_closed_loop_validation_has_10_plus_cases_and_passes_threshold() -> None:
    report = run_closed_loop_validation(threshold=0.8)
    total_cases = int(report.get("total_cases") or 0)
    pass_rate = float(report.get("pass_rate") or 0.0)
    case_source = str(report.get("case_source") or "").replace("\\", "/")
    assert total_cases >= 10
    assert pass_rate >= 0.8
    assert bool(report.get("threshold_passed")) is True
    assert case_source.endswith("tests/fixtures/emotion/closed_loop_cases.json")


def test_emotion_closed_loop_report_contains_case_results() -> None:
    report = run_closed_loop_validation(threshold=0.8)
    results = list(report.get("results") or [])
    assert results
    first = dict(results[0])
    assert "case_id" in first
    assert "normalized_emotion_tag" in first
    assert "prompt_ok" in first
    assert "vts_ok" in first


def test_emotion_closed_loop_accepts_custom_case_file(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            [
                {
                    "case_id": "x01",
                    "mood_state": "engaged",
                    "emotion_tag": "HAPPY",
                    "emotion_intensity": 0.7,
                    "user_message": "保持积极语气。",
                    "expected_valence_sign": 1,
                    "expected_normalized_emotion_tag": "HAPPY",
                    "expected_normalized_mood_state": "engaged",
                    "expected_normalized_intensity": 0.7,
                },
                {
                    "case_id": "x02",
                    "mood_state": "critical",
                    "emotion_tag": "ANGRY",
                    "emotion_intensity": 0.5,
                    "user_message": "先稳住语气。",
                    "expected_valence_sign": 0,
                    "expected_normalized_emotion_tag": "NEUTRAL",
                    "expected_normalized_mood_state": "critical",
                    "expected_normalized_intensity": 0.5,
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8-sig",
    )

    report = run_closed_loop_validation(threshold=1.0, case_file=str(case_file))
    assert int(report.get("total_cases") or 0) == 2
    assert bool(report.get("threshold_passed")) is True
    assert str(report.get("case_source") or "").endswith("cases.json")


def test_load_emotion_cases_raises_for_invalid_payload(tmp_path: Path) -> None:
    case_file = tmp_path / "invalid.json"
    case_file.write_text("{}", encoding="utf-8-sig")
    with pytest.raises(ValueError):
        load_emotion_cases(case_file)
