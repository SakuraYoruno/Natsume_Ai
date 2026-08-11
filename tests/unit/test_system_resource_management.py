from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from host_brain.core.resource_manager import SystemResourceManager
from host_brain.core.resource_manager import assess_background_task_pressure
from host_brain.skills.impl import system_executor
from host_brain.skills.system_registry import build_system_skill_registry


def test_system_skill_registry_contains_resource_management_skills() -> None:
    registry = build_system_skill_registry()

    assert registry.require("system_resource_snapshot").family == "system"
    assert registry.require("process_resource_top").family == "system"
    assert registry.require("resource_pressure_analysis").family == "system"
    assert registry.require("resource_manager_status").family == "system"
    assert registry.require("resource_release").require_confirm is True


def test_system_resource_snapshot_reports_whole_system_pressure(monkeypatch) -> None:
    monkeypatch.setattr(
        system_executor,
        "_collect_system_resource_snapshot",
        lambda **_: {
            "scope": "whole_system",
            "cpu": {"percent": 22.0},
            "memory": {"percent": 83.0, "available_gb": 5.0, "total_gb": 32.0},
            "disk": {"percent": 50.0},
            "gpu": {"summary": {"utilization_percent": 10.0, "memory_percent": 82.0}},
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_process_resource_top",
        lambda **_: {
            "available": True,
            "count": 1,
            "processes": [
                {
                    "pid": 123,
                    "name": "Browser.exe",
                    "memory_mb": 2048.0,
                    "cpu_percent": 3.0,
                    "vram_mb": 0.0,
                    "role": "browser",
                    "protected": False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_gpu_processes",
        lambda **_: {"available": True, "count": 1, "processes": [{"pid": 123, "name": "Browser.exe", "vram_mb": 512.0}]},
    )

    result = system_executor.execute_system_skill(
        {
            "action": "resource_snapshot",
            "include_gpu": True,
            "include_processes": True,
            "top_n": 5,
            "sample_seconds": 0,
        }
    )

    detail = result["detail"]
    assert result["status"] == "OK"
    assert detail["scope"] == "whole_system"
    assert detail["pressure"]["status"] == "ELEVATED"
    assert "memory_elevated" in detail["pressure"]["reasons"]
    assert "vram_elevated" in detail["pressure"]["reasons"]
    assert detail["process_top"]["processes"][0]["vram_mb"] == 512.0
    assert detail["release_policy"]["requires_confirm_for_mutation"] is True


def test_system_resource_snapshot_treats_natsume_runtime_cpu_as_pressure(monkeypatch) -> None:
    monkeypatch.setattr(
        system_executor,
        "_collect_system_resource_snapshot",
        lambda **_: {
            "scope": "whole_system",
            "cpu": {"percent": 42.0},
            "memory": {"percent": 54.0, "available_gb": 14.0, "total_gb": 32.0},
            "disk": {"percent": 50.0},
            "gpu": {"summary": {"utilization_percent": 20.0, "memory_percent": 24.0}},
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_process_resource_top",
        lambda **_: {
            "available": True,
            "count": 2,
            "processes": [
                {
                    "pid": 111,
                    "name": "python.exe",
                    "memory_mb": 760.0,
                    "cpu_percent": 98.0,
                    "vram_mb": 0.0,
                    "role": "natsume_runtime",
                    "protected": True,
                },
                {
                    "pid": 222,
                    "name": "python.exe",
                    "memory_mb": 720.0,
                    "cpu_percent": 91.0,
                    "vram_mb": 0.0,
                    "role": "natsume_runtime",
                    "protected": True,
                },
            ],
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_gpu_processes",
        lambda **_: {"available": True, "count": 0, "processes": []},
    )

    result = system_executor.execute_system_skill(
        {
            "action": "resource_snapshot",
            "include_gpu": True,
            "include_processes": True,
            "top_n": 5,
            "sample_seconds": 0,
        }
    )

    pressure = result["detail"]["pressure"]
    assert pressure["status"] == "HIGH"
    assert pressure["natsume_runtime_cpu_percent"] == 189.0
    assert "natsume_runtime_cpu_high" in pressure["reasons"]
    assert any("后台 LLM" in item for item in result["detail"]["recommendations"])


def test_resource_pressure_analysis_identifies_graphics_and_natsume_pressure(monkeypatch) -> None:
    monkeypatch.setattr(
        system_executor,
        "_execute_system_resource_snapshot",
        lambda payload, context=None: {
            "success": True,
            "snapshot": {
                "memory": {"percent": 86.0, "available_gb": 4.2, "total_gb": 32.0},
                "gpu": {"summary": {"memory_percent": 78.0, "utilization_percent": 55.0}},
            },
            "pressure": {"status": "HIGH", "reasons": ["memory_elevated", "vram_elevated", "natsume_runtime_cpu_high"]},
            "process_top": {"processes": []},
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_execute_process_resource_top",
        lambda payload, context=None: {
            "success": True,
            "processes": [
                {
                    "pid": 10,
                    "name": "Client-Win64-Shipping.exe",
                    "memory_mb": 4096.0,
                    "vram_mb": 0.0,
                    "cpu_percent": 120.0,
                    "role": "foreground_media_or_game",
                    "protected": False,
                },
                {
                    "pid": 20,
                    "name": "python.exe",
                    "memory_mb": 2100.0,
                    "vram_mb": 0.0,
                    "cpu_percent": 95.0,
                    "role": "natsume_runtime",
                    "protected": True,
                },
            ],
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_gpu_context_processes",
        lambda limit=80: {
            "available": True,
            "processes": [
                {
                    "pid": 10,
                    "name": "Client-Win64-Shipping.exe",
                    "memory_mb": 4096.0,
                    "gpu_sm_percent": 45.0,
                    "gpu_mem_percent": 18.0,
                    "role": "foreground_media_or_game",
                    "protected": False,
                }
            ],
            "count": 1,
        },
    )
    monkeypatch.setattr(system_executor, "_execute_resource_manager_status", lambda payload, context=None: {"registered": True, "last_decision": {"pressure_level": "HIGH"}})
    monkeypatch.setattr(system_executor, "_fetch_local_json", lambda url, timeout_s=2.0: {"status": "OK"})
    monkeypatch.setattr(system_executor, "_collect_resource_log_signals", lambda: {"counts": {"obs_trace_builds": 0, "obs_warm_cache": 0, "gemini_afc_calls": 0, "cuda_oom": 0}, "recent_evidence": []})

    result = system_executor.execute_system_skill({"action": "resource_pressure_analysis", "persist": False, "top_n": 10, "sample_seconds": 0})

    assert result["status"] == "OK"
    detail = result["detail"]
    cause_ids = {row["cause_id"] for row in detail["hypotheses"]}
    assert "combined_ram_vram_pressure" in cause_ids
    assert "foreground_graphics_or_game" in cause_ids
    assert "natsume_runtime_self_pressure" in cause_ids
    assert any("Natsume" in item or "python.exe" in item for item in detail["actions"]["do_not_touch"])


def test_resource_release_defaults_to_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(
        system_executor,
        "_execute_process_resource_top",
        lambda payload, context: {
            "processes": [
                {
                    "pid": 456,
                    "name": "Media.exe",
                    "memory_mb": 1024.0,
                    "vram_mb": 0.0,
                    "role": "foreground_media_or_game",
                    "protected": False,
                }
            ]
        },
    )

    result = system_executor.execute_system_skill({"action": "resource_release", "mode": "dry_run"})

    assert result["status"] == "OK"
    assert result["detail"]["dry_run"] is True
    assert result["detail"]["requires_confirm"] is True
    assert result["detail"]["release_candidates"][0]["pid"] == 456


def test_voice_model_training_process_is_protected_from_release() -> None:
    role = system_executor._classify_process_resource_role(  # noqa: SLF001
        "python.exe",
        r"C:\venv\Scripts\python.exe train.py --model gpt-sovits --dataset voice",
    )
    assert role == "model_training"

    candidates = system_executor._build_resource_release_candidates(  # noqa: SLF001
        [
            {
                "pid": 9876,
                "name": "python.exe",
                "memory_mb": 2048.0,
                "vram_mb": 12000.0,
                "role": role,
                "protected": True,
            }
        ],
        min_memory_mb=512,
    )

    assert candidates[0]["safe_to_trim"] is False
    assert candidates[0]["protected"] is True

    manager = SystemResourceManager(interval_s=999, llm_enabled=False, auto_gc_enabled=False)
    decision = manager._heuristic_decision(  # noqa: SLF001
        detail={"pressure": {"status": "HIGH"}, "release_candidates": candidates},
        source="heuristic:fallback",
    )

    assert decision["can_release"] == []
    assert decision["needs_user_confirm"] == []
    assert decision["cannot_release"][0]["pid"] == 9876


def test_training_process_inside_natsume_workspace_is_not_classified_as_host_runtime() -> None:
    role = system_executor._classify_process_resource_role(  # noqa: SLF001
        "python.exe",
        r"D:\Natsume_Ai\.venv\Scripts\python.exe tools\train_gpt_sovits_xilian_emotion_balanced.py",
    )

    assert role == "model_training"


def test_unity_project_under_natsume_workspace_is_not_protected_as_host_runtime() -> None:
    role = system_executor._classify_process_resource_role(  # noqa: SLF001
        "Unity.exe",
        r"C:\Program Files\Unity\Editor\Unity.exe -projectPath D:\Natsume_Ai\avatar_scene",
    )

    assert role != "natsume_runtime"


def test_resource_manager_run_once_uses_heuristic_when_llm_disabled(monkeypatch, tmp_path) -> None:
    def fake_execute(payload, context=None):
        if payload["action"] == "resource_snapshot":
            return {
                "status": "OK",
                "detail": {
                    "pressure": {"status": "HIGH", "reasons": ["memory_high"]},
                    "release_candidates": [
                        {
                            "pid": 789,
                            "name": "chrome.exe",
                            "memory_mb": 2048.0,
                            "vram_mb": 0.0,
                            "role": "browser",
                            "protected": False,
                            "safe_to_trim": True,
                        }
                    ],
                    "process_top": {"processes": []},
                },
            }
        return {"status": "OK", "detail": {"success": True}}

    monkeypatch.setattr(system_executor, "execute_system_skill", fake_execute)

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )

    tick = manager.run_once(reason="manual")

    assert tick["status"] == "OK"
    assert tick["decision"]["source"] == "heuristic:fallback"
    assert tick["decision"]["needs_user_confirm"][0]["pid"] == 789
    assert manager.get_status()["last_decision"]["pressure_level"] == "HIGH"


def test_resource_manager_auto_records_pressure_analysis_under_high_pressure(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fake_execute(payload, context=None):
        calls.append(str(payload["action"]))
        if payload["action"] == "resource_snapshot":
            return {
                "status": "OK",
                "detail": {
                    "pressure": {"status": "HIGH", "memory_percent": 91.0, "vram_percent": 86.0, "reasons": ["memory_high", "vram_high"]},
                    "release_candidates": [],
                    "process_top": {"processes": []},
                },
            }
        if payload["action"] == "resource_pressure_analysis":
            return {
                "status": "OK",
                "detail": {
                    "success": True,
                    "summary": "资源压力 HIGH：测试分析。",
                    "hypotheses": [{"cause_id": "unit", "title": "unit root cause", "confidence": 0.9, "severity": "warning"}],
                    "analysis_path": str(tmp_path / "analysis.json"),
                },
            }
        return {"status": "OK", "detail": {"success": True}}

    monkeypatch.setattr(system_executor, "execute_system_skill", fake_execute)

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )

    tick = manager.run_once(reason="periodic")

    assert tick["pressure_analysis"]["summary"] == "资源压力 HIGH：测试分析。"
    assert "resource_pressure_analysis" in calls
    assert manager.get_status()["last_pressure_analysis"]["hypotheses"][0]["cause_id"] == "unit"


def test_resource_manager_auto_trim_is_enabled_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_AUTO_TRIM_ENABLED", raising=False)

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )

    assert manager.get_status()["auto_trim_enabled"] is True


def test_resource_manager_stop_keeps_owner_when_loop_does_not_exit() -> None:
    class StuckThread:
        def __init__(self) -> None:
            self.join_calls: list[float | None] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )
    stuck = StuckThread()
    manager._running = True  # noqa: SLF001
    manager._thread = stuck  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(
        RuntimeError,
        match="RESOURCE_MANAGER_LOOP_STOP_TIMEOUT",
    ):
        manager.stop()

    assert manager._thread is stuck  # noqa: SLF001
    assert stuck.join_calls == [5.0]


def test_resource_manager_auto_trims_safe_memory_candidates(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_execute(payload, context=None):
        calls.append(dict(payload))
        return {"status": "OK", "detail": {"success": True, "trimmed": 1}}

    monkeypatch.setattr(system_executor, "execute_system_skill", fake_execute)

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
        auto_trim_enabled=True,
    )

    actions = manager._apply_safe_actions(  # noqa: SLF001
        decision={"auto_actions": []},
        pressure={"status": "HIGH", "reasons": ["memory_high"], "memory_percent": 91.0},
        detail={
            "release_candidates": [
                {
                    "pid": 111,
                    "name": "chrome.exe",
                    "memory_mb": 1200.0,
                    "vram_mb": 0.0,
                    "role": "browser",
                    "safe_to_trim": True,
                    "protected": False,
                },
                {
                    "pid": 222,
                    "name": "python.exe",
                    "memory_mb": 2400.0,
                    "vram_mb": 11000.0,
                    "role": "model_training",
                    "safe_to_trim": False,
                    "protected": True,
                },
            ]
        },
    )

    assert actions[0]["action"] == "trim_working_sets"
    assert calls[0]["action"] == "resource_release"
    assert calls[0]["mode"] == "trim_working_sets"
    assert calls[0]["confirm"] is True
    assert calls[0]["pids"] == [111]


def test_resource_manager_does_not_trim_for_vram_only_pressure(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(system_executor, "execute_system_skill", lambda payload, context=None: calls.append(dict(payload)))

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
        auto_trim_enabled=True,
    )

    actions = manager._apply_safe_actions(  # noqa: SLF001
        decision={"auto_actions": []},
        pressure={"status": "HIGH", "reasons": ["vram_high"], "memory_percent": 52.0, "vram_percent": 94.0},
        detail={
            "release_candidates": [
                {
                    "pid": 111,
                    "name": "chrome.exe",
                    "memory_mb": 1200.0,
                    "vram_mb": 0.0,
                    "role": "browser",
                    "safe_to_trim": True,
                    "protected": False,
                }
            ]
        },
    )

    assert actions == []
    assert calls == []


def test_resource_manager_records_governed_model_attempt_failure(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_complete_compiled_prompt_sync(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            success=False,
            content="",
            error_code="MODEL_ROUTE_EXHAUSTED",
            cached=False,
            attempts=[
                SimpleNamespace(
                    provider="deepseek",
                    model_id="deepseek-v4-flash",
                    status="FAILED",
                    latency_ms=5002,
                    error_code="PROVIDER_TIMEOUT",
                    error_message="provider hard timeout>5000ms",
                )
            ],
        )

    monkeypatch.delenv("HOST_RESOURCE_MANAGER_LLM_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_LLM_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_LLM_API_KEY", "unit-secret")
    monkeypatch.setattr(
        "host_brain.model_runtime.invocation.complete_compiled_prompt_sync",
        fake_complete_compiled_prompt_sync,
    )

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=True,
        auto_gc_enabled=False,
    )

    decision = manager._analyze_with_llm(  # noqa: SLF001
        detail={"pressure": {"status": "HIGH"}, "process_top": {"processes": []}},
        pressure={"status": "HIGH"},
    )

    assert decision == {}
    assert captured["deadline_ms"] == 30000
    assert captured["cognitive_tier"] == "C"
    assert captured["prompt_id"] == "resource.manager.analysis.json"
    assert "provider_name" not in captured
    assert "model_id" not in captured
    status = manager.get_status()
    assert "deepseek/deepseek-v4-flash" in str(status["last_llm_error"])
    assert status["last_llm_attempts"][0]["reason_code"] == "PROVIDER_TIMEOUT"


def test_resource_manager_no_pressure_summary_does_not_claim_llm_unavailable(tmp_path) -> None:
    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=True,
        auto_gc_enabled=False,
    )
    manager._last_llm_error = "LLM_POOL_EXHAUSTED: deepseek/deepseek-v4-flash FAILED(POOL_TIMEOUT)"  # noqa: SLF001

    decision = manager._heuristic_decision(  # noqa: SLF001
        detail={"pressure": {"status": "NORMAL"}, "release_candidates": []},
        source="heuristic:no_pressure",
    )

    assert "未触发 LLM 深度分析" in decision["summary"]
    assert "不可用" not in decision["summary"]


def test_resource_manager_does_not_reuse_normal_cache_under_high_pressure(tmp_path) -> None:
    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )
    manager._last_analysis_ts = time.monotonic()  # noqa: SLF001
    manager._last_decision = {"pressure_level": "NORMAL", "source": "heuristic:no_pressure"}  # noqa: SLF001

    decision = manager._maybe_analyze(  # noqa: SLF001
        detail={"pressure": {"status": "HIGH"}, "release_candidates": [], "process_top": {"processes": []}},
        pressure={"status": "HIGH"},
        reason="periodic",
    )

    assert decision["pressure_level"] == "HIGH"
    assert decision["source"] == "heuristic:fallback"
    assert decision.get("cached") is not True


def test_background_task_pressure_defers_heavy_llm_work(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)

    decision = assess_background_task_pressure(
        task_id="inner_world_monologue_tick",
        pressure={"status": "ELEVATED", "memory_percent": 82.0, "vram_percent": 84.0},
        priority="LOW",
        task_type="background_llm",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"
    assert decision["retry_after_s"] == 180
    assert decision["heavy"] is True


def test_background_task_pressure_allows_life_llm_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_REMOTE_LLM_UNDER_RUNTIME_CPU_ELEVATED", raising=False)

    decision = assess_background_task_pressure(
        task_id="inner_world_monologue_tick",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 64.0,
            "vram_percent": 43.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="background_llm",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["runtime_cpu_only_elevated"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_allows_proactive_talk_under_non_gpu_vram_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_SOCIAL_LLM_UNDER_VRAM_ELEVATED", raising=False)

    decision = assess_background_task_pressure(
        task_id="proactive_talk_periodic",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 59.0,
            "vram_percent": 79.0,
            "gpu_percent": 8.0,
            "reasons": ["vram_elevated", "natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="background_llm",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["social_light_llm"] is True
    assert decision["social_llm_vram_only_elevated"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_allows_proactive_talk_under_vram_only_high(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_SOCIAL_LLM_UNDER_VRAM_HIGH", raising=False)

    decision = assess_background_task_pressure(
        task_id="proactive_talk_periodic",
        pressure={
            "status": "HIGH",
            "memory_percent": 59.0,
            "vram_percent": 94.0,
            "gpu_percent": 8.0,
            "reasons": ["vram_high"],
        },
        priority="LOW",
        task_type="social_light_llm",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["background_llm"] is True
    assert decision["social_light_llm"] is True
    assert decision["vram_only_high"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_still_defers_proactive_talk_under_high_memory(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)

    decision = assess_background_task_pressure(
        task_id="proactive_talk_periodic",
        pressure={
            "status": "HIGH",
            "memory_percent": 92.0,
            "vram_percent": 94.0,
            "gpu_percent": 8.0,
            "reasons": ["memory_high", "vram_high"],
        },
        priority="LOW",
        task_type="social_light_llm",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "RESOURCE_PRESSURE_HIGH"


def test_background_task_pressure_allows_memory_distillation_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_MEMORY_HEAVY_UNDER_RUNTIME_CPU_ELEVATED", raising=False)

    decision = assess_background_task_pressure(
        task_id="long_term_memory_distillation",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 64.0,
            "vram_percent": 61.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="memory_heavy",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["memory_heavy"] is True
    assert decision["runtime_cpu_only_elevated"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_can_defer_memory_distillation_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_MEMORY_HEAVY_UNDER_RUNTIME_CPU_ELEVATED", "0")

    decision = assess_background_task_pressure(
        task_id="long_term_memory_distillation",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 64.0,
            "vram_percent": 61.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="memory_heavy",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"


def test_background_task_pressure_allows_browser_heavy_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_BROWSER_HEAVY_UNDER_RUNTIME_CPU_ELEVATED", raising=False)

    decision = assess_background_task_pressure(
        task_id="lightnovel_recommender_daily",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 51.0,
            "vram_percent": 19.0,
            "gpu_percent": 4.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="browser_heavy",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["browser_heavy"] is True
    assert decision["runtime_cpu_only_elevated"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_can_defer_browser_heavy_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_BROWSER_HEAVY_UNDER_RUNTIME_CPU_ELEVATED", "0")

    decision = assess_background_task_pressure(
        task_id="lightnovel_recommender_daily",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 51.0,
            "vram_percent": 19.0,
            "gpu_percent": 4.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="browser_heavy",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"


def test_background_task_pressure_still_defers_probe_under_cpu_only_elevated(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)

    decision = assess_background_task_pressure(
        task_id="service_health_heartbeat_llm_probe",
        pressure={
            "status": "ELEVATED",
            "memory_percent": 64.0,
            "vram_percent": 43.0,
            "reasons": ["natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="heartbeat_llm",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"


def test_background_task_pressure_defers_light_life_ticks_under_high_pressure(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_LIGHT_TICKS_UNDER_HIGH", raising=False)

    decision = assess_background_task_pressure(
        task_id="inner_world_numeric_tick",
        pressure={"status": "HIGH", "memory_percent": 94.0, "vram_percent": 96.0},
        priority="LOW",
        task_type="light_tick",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["light"] is True
    assert decision["reason_code"] == "RESOURCE_PRESSURE_HIGH"
    assert decision["retry_after_s"] == 120


def test_background_task_pressure_allows_light_life_ticks_under_vram_only_high(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_LIFE_TICKS_UNDER_VRAM_HIGH", raising=False)

    decision = assess_background_task_pressure(
        task_id="emotion_decay_tick",
        pressure={
            "status": "HIGH",
            "memory_percent": 61.0,
            "vram_percent": 91.0,
            "gpu_percent": 8.0,
            "reasons": ["vram_high", "natsume_runtime_cpu_elevated"],
        },
        priority="LOW",
        task_type="light_tick",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["light"] is True
    assert decision["vram_only_high"] is True
    assert decision["reason_code"] is None


def test_background_task_pressure_can_opt_in_light_ticks_under_high_pressure(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_GATE_ALLOW_LIGHT_TICKS_UNDER_HIGH", "1")

    decision = assess_background_task_pressure(
        task_id="emotion_decay_tick",
        pressure={"status": "HIGH", "memory_percent": 94.0, "vram_percent": 96.0},
        priority="LOW",
        task_type="light_tick",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["light"] is True
    assert decision["reason_code"] is None


def test_resource_manager_gate_does_not_promote_runtime_cpu_only_to_synthetic_high(tmp_path) -> None:
    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )
    manager._last_snapshot = {  # noqa: SLF001
        "pressure": {"status": "ELEVATED", "memory_percent": 55.0, "vram_percent": 33.0, "reasons": ["natsume_runtime_cpu_elevated"]}
    }
    manager._last_decision = {"pressure_level": "HIGH"}  # noqa: SLF001

    decision = manager.assess_background_task(
        task_id="proactive_talk_periodic",
        priority="LOW",
        task_type="social_light_llm",
        uses_llm=True,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["pressure_status"] == "ELEVATED"
    assert "llm_pressure_high" not in decision["pressure"]["reasons"]


def test_resource_manager_gate_can_opt_into_llm_pressure_promotion(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_PROMOTE_LLM_PRESSURE_ON_RUNTIME_CPU_ONLY", "1")
    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )
    manager._last_snapshot = {  # noqa: SLF001
        "pressure": {"status": "ELEVATED", "memory_percent": 55.0, "vram_percent": 33.0, "reasons": ["natsume_runtime_cpu_elevated"]}
    }
    manager._last_decision = {"pressure_level": "HIGH"}  # noqa: SLF001

    decision = manager.assess_background_task(
        task_id="inner_world_numeric_tick",
        priority="LOW",
        task_type="light_tick",
    )

    assert decision["allowed"] is True
    assert decision["pressure_status"] == "HIGH"
    assert "llm_pressure_high" in decision["pressure"]["reasons"]


def test_background_task_pressure_defers_browser_heavy_work_longer(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("HOST_RESOURCE_MANAGER_GATE_HIGH_RETRY_AFTER_S", raising=False)

    decision = assess_background_task_pressure(
        task_id="lightnovel_recommender_daily",
        pressure={"status": "HIGH", "memory_percent": 91.0, "vram_percent": 88.0},
        priority="LOW",
        task_type="browser_heavy",
        uses_llm=True,
        uses_gpu=False,
    )

    assert decision["allowed"] is False
    assert decision["reason_code"] == "RESOURCE_PRESSURE_HIGH"
    assert decision["browser_heavy"] is True
    assert decision["retry_after_s"] == 600


def test_background_task_pressure_keeps_critical_work_alive() -> None:
    decision = assess_background_task_pressure(
        task_id="security_immune_periodic",
        pressure={"status": "HIGH", "memory_percent": 94.0, "vram_percent": 95.0},
        priority="P0",
        task_type="security",
        uses_llm=False,
        uses_gpu=False,
    )

    assert decision["allowed"] is True
    assert decision["decision"] == "allow"
    assert decision["critical"] is True
