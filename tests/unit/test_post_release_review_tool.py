from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.post_release_review import run


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_post_release_review_complete_when_age_gte_24h(tmp_path: Path) -> None:
    stability_path = tmp_path / "stability.json"
    rollback_path = tmp_path / "rollback_gate.json"
    _write_json(
        stability_path,
        {
            "version": "stability_weekly.v1",
            "summary": {"alert_count": 0, "status": "OK"},
        },
    )
    _write_json(
        rollback_path,
        {
            "version": "release_rollback_gate.v1",
            "evaluation": {"decision": "PASS", "rollback_required": False, "failed_conditions": []},
        },
    )
    released_at = (datetime.now(tz=timezone.utc) - timedelta(hours=30)).isoformat()
    output_json = tmp_path / "post_release.json"
    output_md = tmp_path / "post_release.md"

    review = run(
        [
            "--release-id",
            "release::test::001",
            "--released-at",
            released_at,
            "--stability-report",
            str(stability_path),
            "--rollback-gate-report",
            str(rollback_path),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert review["review_status"] == "COMPLETE"
    assert float(review["age_hours"]) >= 24.0
    assert output_json.exists()
    assert output_md.exists()


def test_post_release_review_pending_when_age_lt_24h(tmp_path: Path) -> None:
    stability_path = tmp_path / "stability.json"
    rollback_path = tmp_path / "rollback_gate.json"
    _write_json(
        stability_path,
        {
            "version": "stability_weekly.v1",
            "summary": {"alert_count": 2, "status": "WARN"},
        },
    )
    _write_json(
        rollback_path,
        {
            "version": "release_rollback_gate.v1",
            "evaluation": {
                "decision": "ROLLBACK_REQUIRED",
                "rollback_required": True,
                "failed_conditions": [{"metric": "summary.alert_count", "observed": 2, "threshold": 0, "operator": "<="}],
            },
        },
    )
    released_at = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
    review = run(
        [
            "--release-id",
            "release::test::002",
            "--released-at",
            released_at,
            "--stability-report",
            str(stability_path),
            "--rollback-gate-report",
            str(rollback_path),
            "--output-json",
            str(tmp_path / "post_release_pending.json"),
            "--output-md",
            str(tmp_path / "post_release_pending.md"),
        ]
    )

    assert review["review_status"] == "PENDING_24H_WINDOW"
    assert bool(dict(review["summary"]).get("rollback_required")) is True
    assert len(list(review.get("actions") or [])) >= 1
