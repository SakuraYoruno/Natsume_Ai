from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diag import extract_artifact_paths, run


def test_diag_unified_dry_run_retrieval_writes_reports(tmp_path: Path) -> None:
    output_json = tmp_path / "diag_retrieval.json"
    output_md = tmp_path / "diag_retrieval.md"

    report = run(
        [
            "retrieval",
            "--dry-run",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert report["status"] == "DRY_RUN"
    assert bool(report["success"]) is True
    assert "_diag_retrieval.py" in " ".join(list(report.get("command") or []))
    assert output_json.exists()
    assert output_md.exists()
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["domain"] == "retrieval"


def test_diag_unified_emotion_command_includes_threshold_and_case_file(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text("[]\n", encoding="utf-8")

    report = run(
        [
            "emotion",
            "--dry-run",
            "--threshold",
            "0.9",
            "--case-file",
            str(case_file),
            "--output-json",
            str(tmp_path / "diag_emotion.json"),
            "--output-md",
            str(tmp_path / "diag_emotion.md"),
        ]
    )

    command = list(report.get("command") or [])
    assert "diag_emotion_closed_loop.py" in " ".join(command)
    assert "--threshold" in command
    assert "0.9" in command
    assert "--case-file" in command
    assert str(case_file) in command


def test_diag_unified_stream_command_supports_messages(tmp_path: Path) -> None:
    report = run(
        [
            "stream",
            "--dry-run",
            "--runs",
            "3",
            "--messages",
            "alpha",
            "beta",
            "--output-json",
            str(tmp_path / "diag_stream.json"),
            "--output-md",
            str(tmp_path / "diag_stream.md"),
        ]
    )

    command = list(report.get("command") or [])
    assert "diag_stream_baseline.py" in " ".join(command)
    assert "--messages" in command
    assert "alpha" in command
    assert "beta" in command


def test_diag_unified_stability_command_supports_threshold_flags(tmp_path: Path) -> None:
    report = run(
        [
            "stability",
            "--dry-run",
            "--days",
            "14",
            "--ledger-db",
            str(tmp_path / "audit.sqlite3"),
            "--threshold-timeout-rate-pct",
            "11",
            "--threshold-degraded-rate-pct",
            "22",
            "--threshold-failure-rate-pct",
            "33",
            "--output-json",
            str(tmp_path / "diag_stability.json"),
            "--output-md",
            str(tmp_path / "diag_stability.md"),
        ]
    )

    command = list(report.get("command") or [])
    joined = " ".join(command)
    assert "diag_stability_weekly.py" in joined
    assert "--days" in command
    assert "14" in command
    assert "--threshold-timeout-rate-pct" in command
    assert "--threshold-degraded-rate-pct" in command
    assert "--threshold-failure-rate-pct" in command


def test_extract_artifact_paths_parses_downstream_report_lines() -> None:
    stdout = (
        "step\n"
        "json_report: docs/60_trace/a.json\n"
        "markdown_report: docs/60_trace/a.md\n"
    )
    artifacts = extract_artifact_paths(stdout)
    assert artifacts["json_report"] == "docs/60_trace/a.json"
    assert artifacts["markdown_report"] == "docs/60_trace/a.md"
