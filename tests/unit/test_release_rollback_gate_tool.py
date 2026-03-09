from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.release_rollback_gate import run


def _write_stability_report(path: Path, *, alert_count: int, retrieval_rates: dict[str, float], governor_rates: dict[str, float]) -> None:
    payload = {
        "version": "stability_weekly.v1",
        "summary": {
            "status": ("WARN" if alert_count > 0 else "OK"),
            "alert_count": alert_count,
        },
        "retrieval": {
            "timeout_rate_pct": float(retrieval_rates.get("timeout_rate_pct", 0.0)),
            "degraded_rate_pct": float(retrieval_rates.get("degraded_rate_pct", 0.0)),
            "failure_rate_pct": float(retrieval_rates.get("failure_rate_pct", 0.0)),
        },
        "governor": {
            "degraded_rate_pct": float(governor_rates.get("degraded_rate_pct", 0.0)),
            "failure_rate_pct": float(governor_rates.get("failure_rate_pct", 0.0)),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_release_rollback_gate_requires_rollback_when_thresholds_exceeded(tmp_path: Path) -> None:
    stability_json = tmp_path / "stability_warn.json"
    _write_stability_report(
        stability_json,
        alert_count=2,
        retrieval_rates={"timeout_rate_pct": 30.0, "degraded_rate_pct": 25.0, "failure_rate_pct": 20.0},
        governor_rates={"degraded_rate_pct": 21.0, "failure_rate_pct": 18.0},
    )
    output_json = tmp_path / "gate_warn.json"
    output_md = tmp_path / "gate_warn.md"

    report = run(
        [
            "--stability-report",
            str(stability_json),
            "--max-alert-count",
            "0",
            "--max-timeout-rate-pct",
            "10",
            "--max-degraded-rate-pct",
            "20",
            "--max-failure-rate-pct",
            "15",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    evaluation = dict(report.get("evaluation") or {})
    assert evaluation.get("decision") == "ROLLBACK_REQUIRED"
    assert bool(evaluation.get("rollback_required")) is True
    assert len(list(evaluation.get("failed_conditions") or [])) >= 1
    commands = list(evaluation.get("rollback_commands") or [])
    assert any("HOST_CHAT_FORCE_CLOUD" in str(row.get("command") or "") for row in commands if isinstance(row, dict))
    assert output_json.exists()
    assert output_md.exists()


def test_release_rollback_gate_passes_when_metrics_within_threshold(tmp_path: Path) -> None:
    stability_json = tmp_path / "stability_ok.json"
    _write_stability_report(
        stability_json,
        alert_count=0,
        retrieval_rates={"timeout_rate_pct": 1.0, "degraded_rate_pct": 2.0, "failure_rate_pct": 1.0},
        governor_rates={"degraded_rate_pct": 1.0, "failure_rate_pct": 1.0},
    )

    report = run(
        [
            "--stability-report",
            str(stability_json),
            "--max-alert-count",
            "0",
            "--max-timeout-rate-pct",
            "10",
            "--max-degraded-rate-pct",
            "20",
            "--max-failure-rate-pct",
            "15",
            "--output-json",
            str(tmp_path / "gate_ok.json"),
            "--output-md",
            str(tmp_path / "gate_ok.md"),
        ]
    )

    evaluation = dict(report.get("evaluation") or {})
    assert evaluation.get("decision") == "PASS"
    assert bool(evaluation.get("rollback_required")) is False
    assert list(evaluation.get("failed_conditions") or []) == []
