from __future__ import annotations

import json
import time
from pathlib import Path

from tools.review_digital_life_wall_clock_soak import build_arg_parser, review_wall_clock_soak


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _base_summary(*, now_ms: int, run_status: str = "RUNNING") -> dict[str, object]:
    completed = run_status == "COMPLETED"
    return {
        "alive": not completed,
        "checkpoint_count": 3,
        "elapsed_seconds": 60.0 if completed else 10.0,
        "error": {},
        "evaluation_mode": "real_wall_clock_digital_life_soak",
        "foreground_turn_count": 1,
        "idle_tick_count": 1,
        "is_advancing": True,
        "last_mapped_organ_count": 23,
        "persona_self_id": "natsume_ai",
        "progress_ratio": 1.0 if completed else 0.166667,
        "registered_organ_count": 23,
        "run_id": "unit-wall-clock",
        "run_status": run_status,
        "scheduler_status": "HEALTHY",
        "started_at_ms": now_ms - 60_000,
        "status": "PASS" if completed else "RUNNING",
        "target_seconds": 60.0,
        "target_stop_at_ms": now_ms if completed else now_ms + 50_000,
        "tick_health": "fresh",
        "tick_interval_seconds": 300.0,
        "updated_at_ms": now_ms,
    }


def _base_checkpoints(*, now_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "runtime_started", "ts_ms": now_ms - 10_000, "tick_index": 0},
        {"event_type": "foreground_turn", "ts_ms": now_ms - 9_000, "tick_index": 0},
        {"event_type": "idle_tick", "ts_ms": now_ms - 1_000, "tick_index": 1},
    ]


def test_review_wall_clock_soak_reports_running_healthy_run(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_json(run_dir / "summary.json", _base_summary(now_ms=now_ms))
    _write_jsonl(run_dir / "checkpoints.jsonl", _base_checkpoints(now_ms=now_ms))
    (tmp_path / "stderr.log").write_text("", encoding="utf-8")
    args = build_arg_parser().parse_args(["--run-dir", str(run_dir)])

    result = review_wall_clock_soak(args)

    summary = result["summary"]
    assert summary["verdict"] == "RUNNING"
    assert summary["failed_checks"] == []
    assert (tmp_path / "review" / "review_summary.json").exists()
    assert (tmp_path / "review" / "review_details.json").exists()
    assert (tmp_path / "review" / "review_report.md").exists()


def test_review_wall_clock_soak_can_require_completed_pass(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_json(run_dir / "summary.json", _base_summary(now_ms=now_ms, run_status="COMPLETED"))
    _write_jsonl(run_dir / "checkpoints.jsonl", _base_checkpoints(now_ms=now_ms))
    args = build_arg_parser().parse_args(["--run-dir", str(run_dir), "--require-complete"])

    result = review_wall_clock_soak(args)

    assert result["summary"]["verdict"] == "PASS"
    assert result["summary"]["failed_checks"] == []


def test_review_wall_clock_soak_fails_stale_running_checkpoint(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 600_000
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    summary = _base_summary(now_ms=old_ms)
    summary["updated_at_ms"] = old_ms
    _write_json(run_dir / "summary.json", summary)
    _write_jsonl(run_dir / "checkpoints.jsonl", _base_checkpoints(now_ms=old_ms))
    args = build_arg_parser().parse_args(
        ["--run-dir", str(run_dir), "--stale-seconds", "1"]
    )

    result = review_wall_clock_soak(args)

    assert result["summary"]["verdict"] == "FAIL"
    assert "running_checkpoint_not_stale" in result["summary"]["failed_checks"]
