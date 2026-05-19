from __future__ import annotations

import json
import time
from pathlib import Path

from host_brain.app.obs_routes_digital_life_soak import _build_wall_clock_soak_payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _summary(now_ms: int, run_id: str = "unit-wall-clock") -> dict[str, object]:
    return {
        "checkpoint_count": 3,
        "elapsed_seconds": 600.0,
        "error": {},
        "evaluation_mode": "real_wall_clock_digital_life_soak",
        "foreground_turn_count": 1,
        "idle_tick_count": 1,
        "is_advancing": True,
        "last_mapped_organ_count": 23,
        "progress_ratio": 0.006944,
        "registered_organ_count": 23,
        "run_id": run_id,
        "run_status": "RUNNING",
        "scheduler_status": "HEALTHY",
        "started_at_ms": now_ms - 600_000,
        "status": "RUNNING",
        "target_seconds": 86_400.0,
        "tick_health": "fresh",
        "tick_interval_seconds": 300.0,
        "updated_at_ms": now_ms,
    }


def _checkpoints(now_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "runtime_started", "ts_ms": now_ms - 600_000, "tick_index": 0},
        {"event_type": "foreground_turn", "ts_ms": now_ms - 599_000, "tick_index": 0},
        {"event_type": "idle_tick", "ts_ms": now_ms - 1_000, "tick_index": 1},
    ]


def test_wall_clock_soak_payload_reads_latest_run_without_writing_review(tmp_path, monkeypatch) -> None:
    now_ms = int(time.time() * 1000)
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "real-24h-wall-clock-unit" / "run"
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "summary.json", _summary(now_ms))
    _write_jsonl(run_dir / "checkpoints.jsonl", _checkpoints(now_ms))
    (run_dir.parent / "stderr.log").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", str(artifacts_root))

    payload = _build_wall_clock_soak_payload()

    assert payload["available"] is True
    assert payload["summary"]["verdict"] == "RUNNING"
    assert payload["summary"]["failed_checks"] == []
    assert payload["summary"]["registered_organ_count"] == 23
    assert payload["summary"]["last_mapped_organ_count"] == 23
    assert payload["latestCheckpoints"][-1]["event_type"] == "idle_tick"
    assert payload["meta"]["readOnly"] is True
    assert not (run_dir.parent / "review").exists()


def test_wall_clock_soak_payload_reports_missing_when_no_run_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", str(tmp_path / "empty"))

    payload = _build_wall_clock_soak_payload()

    assert payload["available"] is False
    assert payload["summary"]["verdict"] == "MISSING"
    assert "wall_clock_soak_run_found" in payload["summary"]["failed_checks"]
