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


def _completed_summary(now_ms: int) -> dict[str, object]:
    summary = _summary(now_ms, run_id="unit-wall-clock-completed")
    summary.update(
        {
            "checkpoint_count": 4,
            "elapsed_seconds": 86_400.0,
            "idle_tick_count": 2,
            "progress_ratio": 1.0,
            "run_status": "COMPLETED",
            "status": "PASS",
            "started_at_ms": now_ms - 86_400_000,
            "tick_interval_seconds": 43_200.0,
            "updated_at_ms": now_ms,
        }
    )
    return summary


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
    assert payload["acceptance"]["base_status"] == "running"
    assert payload["acceptance"]["layers"][0]["id"] == "life_base"
    assert payload["latestCheckpoints"][-1]["event_type"] == "idle_tick"
    assert payload["meta"]["readOnly"] is True
    assert not (run_dir.parent / "review").exists()


def test_wall_clock_soak_payload_summarizes_completed_life_acceptance(tmp_path, monkeypatch) -> None:
    now_ms = int(time.time() * 1000)
    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "real-24h-wall-clock-completed" / "run"
    run_dir.mkdir(parents=True)
    checkpoints = [
        {"event_type": "runtime_started", "ts_ms": now_ms - 86_400_000, "tick_index": 0},
        {"event_type": "foreground_turn", "ts_ms": now_ms - 86_399_000, "tick_index": 0},
        {"event_type": "idle_tick", "ts_ms": now_ms - 300_000, "tick_index": 1},
        {"event_type": "idle_tick", "ts_ms": now_ms - 1_000, "tick_index": 2},
    ]
    _write_json(run_dir / "summary.json", _completed_summary(now_ms))
    _write_jsonl(run_dir / "checkpoints.jsonl", checkpoints)
    _write_json(
        run_dir / "details.json",
        {
            "final_snapshot": {
                "background_runtime": {
                    "core_tick_tasks": {
                        "inner_world_numeric_tick": {"enabled": True, "status": "OK", "last_reason_code": None},
                        "inner_world_monologue_tick": {"enabled": True, "status": "OK", "last_reason_code": None},
                        "silence_tick": {"enabled": True, "status": "OK", "last_reason_code": None},
                        "body_sense_tick": {"enabled": True, "status": "OK", "last_reason_code": None},
                        "emotion_decay_tick": {"enabled": True, "status": "OK", "last_reason_code": None},
                    },
                    "proactive_task": {
                        "task_id": "proactive_talk_periodic",
                        "enabled": False,
                        "status": "MISSING",
                    },
                },
                "external_services": {
                    "chroma": {"status": "not_probed_by_supervisor"},
                    "neo4j": {"status": "not_probed_by_supervisor"},
                    "sqlite": {"status": "managed_by_runtime_components"},
                },
            }
        },
    )
    inner_dir = run_dir / "state" / "inner_world_state"
    self_dir = run_dir / "state" / "self_model_state"
    inner_dir.mkdir(parents=True)
    self_dir.mkdir(parents=True)
    _write_json(
        inner_dir / "natsume_ai_inner_world.json",
        {
            "continuity_score": 0.8,
            "loneliness": 1.0,
            "anticipation": 0.76,
            "total_offline_hours": 24.0,
            "spontaneous_event_count": 435,
            "last_spontaneous_source": "llm",
            "inner_stream": [{"should_distill_to_memory": True}, {"should_distill_to_memory": True}],
        },
    )
    _write_json(
        self_dir / "natsume_ai_self_model.json",
        {
            "self_identity": "我是当前 Soul 定义的数字生命。",
            "self_values": ["连续性"],
            "self_boundaries": ["不编造"],
            "active_intentions": ["继续成长"],
            "agency_score": 1.0,
            "self_continuity_score": 1.0,
        },
    )
    monkeypatch.setenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", str(artifacts_root))

    payload = _build_wall_clock_soak_payload(require_complete=True)

    acceptance = payload["acceptance"]
    layers = {item["id"]: item for item in acceptance["layers"]}
    assert acceptance["title"] == "生命底盘 PASS，整系统外设未覆盖"
    assert acceptance["base_status"] == "healthy"
    assert layers["life_base"]["status"] == "healthy"
    assert layers["main_chain"]["status"] == "healthy"
    assert layers["inner_world"]["status"] == "healthy"
    assert layers["self_model"]["status"] == "healthy"
    assert layers["external_system"]["verdict"] == "NOT_COVERED"
    assert acceptance["inner_world"]["spontaneous_event_count"] == 435
    assert acceptance["self_model"]["agency_score"] == 1.0


def test_wall_clock_soak_payload_reports_missing_when_no_run_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", str(tmp_path / "empty"))

    payload = _build_wall_clock_soak_payload()

    assert payload["available"] is False
    assert payload["summary"]["verdict"] == "MISSING"
    assert payload["acceptance"]["overall_status"] == "unknown"
    assert "wall_clock_soak_run_found" in payload["summary"]["failed_checks"]
