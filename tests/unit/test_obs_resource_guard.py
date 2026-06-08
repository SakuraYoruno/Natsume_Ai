from __future__ import annotations

import json

from host_brain.app.obs_resource_guard import release_obs_warm_slot
from host_brain.app.obs_resource_guard import should_defer_obs_warm_refresh
from host_brain.app.obs_resource_guard import try_acquire_obs_warm_slot


def _write_pressure(path, pressure):
    path.write_text(
        json.dumps({"last_snapshot": {"pressure": pressure}}, ensure_ascii=False),
        encoding="utf-8",
    )
    path.touch()


def test_obs_warm_cache_defers_cpu_only_elevated_by_default(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "resource_manager.json"
    _write_pressure(
        status_path,
        {
            "status": "ELEVATED",
            "memory_percent": 57.0,
            "vram_percent": 44.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
    )
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_STATUS_PATH", str(status_path))
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_ENABLED", "1")
    monkeypatch.delenv("HOST_OBS_WARM_DEFER_ON_RUNTIME_CPU_ELEVATED", raising=False)

    assert should_defer_obs_warm_refresh(task_name="runtime_snapshot") == (
        True,
        "natsume_runtime_cpu_pressure",
    )


def test_obs_warm_cache_can_opt_out_of_cpu_elevated_defer(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "resource_manager.json"
    _write_pressure(
        status_path,
        {
            "status": "ELEVATED",
            "memory_percent": 57.0,
            "vram_percent": 44.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
    )
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_STATUS_PATH", str(status_path))
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_ENABLED", "1")
    monkeypatch.setenv("HOST_OBS_WARM_DEFER_ON_RUNTIME_CPU_ELEVATED", "0")

    assert should_defer_obs_warm_refresh(task_name="runtime_snapshot") == (False, "")


def test_obs_warm_cache_still_defers_high_pressure(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "resource_manager.json"
    _write_pressure(
        status_path,
        {
            "status": "HIGH",
            "memory_percent": 91.0,
            "vram_percent": 44.0,
            "reasons": ["memory_high"],
        },
    )
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_STATUS_PATH", str(status_path))
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_ENABLED", "1")

    assert should_defer_obs_warm_refresh(task_name="runtime_snapshot") == (
        True,
        "resource_pressure_high",
    )


def test_obs_warm_cache_defers_mixed_runtime_and_memory_pressure(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "resource_manager.json"
    _write_pressure(
        status_path,
        {
            "status": "ELEVATED",
            "memory_percent": 82.0,
            "vram_percent": 44.0,
            "reasons": ["natsume_runtime_cpu_elevated", "memory_elevated"],
        },
    )
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_STATUS_PATH", str(status_path))
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_ENABLED", "1")
    monkeypatch.delenv("HOST_OBS_WARM_DEFER_ON_RUNTIME_CPU_ELEVATED", raising=False)

    assert should_defer_obs_warm_refresh(task_name="runtime_snapshot") == (
        True,
        "mixed_resource_pressure",
    )


def test_obs_warm_slot_can_still_fail_fast_when_wait_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_OBS_WARM_SLOT_WAIT_S", "0")

    acquired, reason = try_acquire_obs_warm_slot(task_name="first")
    assert (acquired, reason) == (True, "")
    try:
        assert try_acquire_obs_warm_slot(task_name="second") == (
            False,
            "obs_warm_slot_busy",
        )
    finally:
        release_obs_warm_slot()
