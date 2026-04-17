import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "runtime" / "test_codex_self_heal_scheduler.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_codex_self_heal_scheduler_runtime_probe",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runtime probe module: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_base_url_defaults_to_18080_and_honors_env(monkeypatch) -> None:
    module = _load_module()

    monkeypatch.delenv("HOST_APP_BASE_URL", raising=False)
    monkeypatch.setenv("HOST_APP_HOST", "0.0.0.0")
    monkeypatch.setenv("HOST_APP_PORT", "18080")
    assert module._resolve_base_url() == "http://127.0.0.1:18080"

    monkeypatch.setenv("HOST_APP_BASE_URL", "http://192.168.1.88:19090")
    assert module._resolve_base_url() == "http://192.168.1.88:19090"


def test_build_probe_report_marks_scheduler_self_heal_dispatch_passed(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/host-scheduler/tasks",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "total": 1,
                    "running": True,
                    "tasks": [
                        {
                            "task_id": "codex_self_heal_auto_dispatch_tick",
                            "interval_s": 45.0,
                            "priority": 1,
                            "status": "PENDING",
                        }
                    ],
                },
            }
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": False,
                    "running": False,
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "timeout_seconds": 180,
                    "last_result": {"decision": "DISPATCHED"},
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "last_result": {"decision": "DISPATCHED"},
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=health",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {"source": "health", "decision": "HEALTHY"},
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "health",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-001",
                    "last_job_status": "COMPLETED",
                    "last_dispatch_success": True,
                    "last_dispatch_run_mode": "inspect",
                },
            },
        ],
        (
            "POST",
            "http://127.0.0.1:18080/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "task_id": "codex_self_heal_auto_dispatch_tick",
                    "triggered": True,
                    "status": "OK",
                },
            }
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/jobs/codex-self-heal::job-001",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-001",
                    "status": "COMPLETED",
                    "dispatch": {
                        "status": "EXECUTED",
                        "success": True,
                        "result_text": "scheduler-real-auto-dispatch-ok",
                    },
                },
            }
        ],
    }

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        item = queue.pop(0)
        return item

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    report = module.build_probe_report(base_url="http://127.0.0.1:18080")

    assert report["overall"]["passed"] is True
    assert report["meta"]["effective_poll_timeout_s"] == 210.0
    assert [row["check"] for row in report["checks"]] == [
        "scheduler_task_present",
        "scheduler_managed",
        "scheduler_trigger",
        "auto_dispatch_decision",
        "job_terminal",
    ]
    assert all(row["passed"] for row in report["checks"])


def test_build_probe_report_accepts_external_dependency_block_without_job(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/host-scheduler/tasks",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "total": 1,
                    "running": True,
                    "tasks": [
                        {
                            "task_id": "codex_self_heal_auto_dispatch_tick",
                            "interval_s": 45.0,
                            "priority": 1,
                            "status": "PENDING",
                        }
                    ],
                },
            }
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": False,
                    "running": False,
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "timeout_seconds": 180,
                    "last_result": {"decision": "EXTERNAL_DEPENDENCY_BLOCKED", "accepted": True, "dispatched": False},
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=health",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {"source": "health", "decision": "HEALTHY"},
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "health",
                    "decision": "EXTERNAL_DEPENDENCY_BLOCKED",
                    "last_job_id": None,
                    "last_job_status": None,
                    "last_dispatch_success": None,
                    "last_dispatch_run_mode": None,
                    "last_dispatch_reason_code": "EXTERNAL_EDGE_RPC_OUTAGE",
                },
            },
        ],
        (
            "POST",
            "http://127.0.0.1:18080/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "task_id": "codex_self_heal_auto_dispatch_tick",
                    "triggered": True,
                    "status": "OK",
                },
            }
        ],
    }

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        return queue.pop(0)

    def _fake_poll(**kwargs):
        return {
            "timed_out": False,
            "elapsed_ms": 10,
            "monitor": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "source": "health",
                    "last_result": {"decision": "EXTERNAL_DEPENDENCY_BLOCKED", "accepted": True, "dispatched": False},
                },
            },
            "auto_state": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "health",
                    "decision": "EXTERNAL_DEPENDENCY_BLOCKED",
                    "last_job_id": None,
                    "last_job_status": None,
                    "last_dispatch_success": None,
                    "last_dispatch_run_mode": None,
                    "last_dispatch_reason_code": "EXTERNAL_EDGE_RPC_OUTAGE",
                },
            },
            "job": {"ok": False, "status_code": 0, "body": {}},
        }

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module, "_poll_scheduler_self_heal_state", _fake_poll)

    report = module.build_probe_report(base_url="http://127.0.0.1:18080")

    assert report["overall"]["passed"] is True
    checks = {row["check"]: row for row in report["checks"]}
    assert checks["auto_dispatch_decision"]["detail"]["decision"] == "EXTERNAL_DEPENDENCY_BLOCKED"
    assert checks["job_terminal"]["detail"]["status"] == "SKIPPED_EXTERNAL_DEPENDENCY_BLOCK"


def test_build_probe_report_extends_poll_timeout_from_monitor_status(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/host-scheduler/tasks",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "total": 1,
                    "running": True,
                    "tasks": [{"task_id": "codex_self_heal_auto_dispatch_tick"}],
                },
            }
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {"scheduler_managed": True, "enabled": False, "running": False},
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "timeout_seconds": 180,
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=health",
        ): [
            {"ok": True, "status_code": 200, "body": {"source": "health", "decision": "HEALTHY"}}
        ],
        (
            "POST",
            "http://127.0.0.1:18080/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger",
        ): [
            {"ok": True, "status_code": 200, "body": {"task_id": "codex_self_heal_auto_dispatch_tick", "triggered": True, "status": "OK"}}
        ],
    }
    captured: dict[str, float] = {}

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        return queue.pop(0)

    def _fake_poll(**kwargs):
        captured["timeout_s"] = kwargs["timeout_s"]
        return {
            "timed_out": False,
            "elapsed_ms": 10,
            "monitor": {"ok": True, "status_code": 200, "body": {"scheduler_managed": True}},
            "auto_state": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "health",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-002",
                    "last_job_status": "COMPLETED",
                    "last_dispatch_success": True,
                    "last_dispatch_run_mode": "inspect",
                },
            },
            "job": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-002",
                    "status": "COMPLETED",
                    "dispatch": {"status": "EXECUTED", "success": True},
                },
            },
        }

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module, "_poll_scheduler_self_heal_state", _fake_poll)

    report = module.build_probe_report(base_url="http://127.0.0.1:18080", poll_timeout_s=45.0)

    assert captured["timeout_s"] == 210.0
    assert report["meta"]["effective_poll_timeout_s"] == 210.0


def test_build_probe_report_infers_source_from_monitor_status(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/host-scheduler/tasks",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "total": 1,
                    "running": True,
                    "tasks": [{"task_id": "codex_self_heal_auto_dispatch_tick"}],
                },
            }
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": False,
                    "running": False,
                    "source": "readiness",
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "enabled": True,
                    "running": False,
                    "source": "readiness",
                    "timeout_seconds": 180,
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=readiness",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "readiness",
                    "decision": "HEALTHY",
                },
            }
        ],
        (
            "POST",
            "http://127.0.0.1:18080/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger",
        ): [
            {"ok": True, "status_code": 200, "body": {"task_id": "codex_self_heal_auto_dispatch_tick", "triggered": True, "status": "OK"}}
        ],
    }
    captured: dict[str, Any] = {}

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        captured.setdefault("urls", []).append(url)
        return queue.pop(0)

    def _fake_poll(**kwargs):
        captured["source"] = kwargs["source"]
        return {
            "timed_out": False,
            "elapsed_ms": 10,
            "monitor": {"ok": True, "status_code": 200, "body": {"scheduler_managed": True, "source": "readiness"}},
            "auto_state": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "readiness",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-004",
                    "last_job_status": "COMPLETED",
                    "last_dispatch_success": True,
                    "last_dispatch_run_mode": "inspect",
                },
            },
            "job": {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-004",
                    "status": "COMPLETED",
                    "dispatch": {"status": "EXECUTED", "success": True},
                },
            },
        }

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module, "_poll_scheduler_self_heal_state", _fake_poll)

    report = module.build_probe_report(base_url="http://127.0.0.1:18080")

    assert report["meta"]["source"] == "readiness"
    assert captured["source"] == "readiness"
    assert any(url.endswith("/auto_state?source=readiness") for url in captured["urls"])


def test_poll_scheduler_self_heal_state_preserves_last_successful_snapshots(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {"ok": True, "status_code": 200, "body": {"scheduler_managed": True}},
            {"ok": False, "status_code": 0, "latency_ms": 10000, "error": "timed out"},
            {"ok": False, "status_code": 0, "latency_ms": 10000, "error": "timed out"},
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=health",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "health",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-003",
                    "last_job_status": "RUNNING",
                },
            },
            {"ok": False, "status_code": 0, "latency_ms": 10000, "error": "timed out"},
            {"ok": False, "status_code": 0, "latency_ms": 10000, "error": "timed out"},
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/jobs/codex-self-heal::job-003",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-003",
                    "status": "RUNNING",
                },
            },
            {
                "ok": False,
                "status_code": 0,
                "latency_ms": 10000,
                "error": "timed out",
            },
            {
                "ok": False,
                "status_code": 0,
                "latency_ms": 10000,
                "error": "timed out",
            },
        ],
    }

    fake_times = iter([0.0, 0.0, 0.5, 1.1, 1.1])

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        return queue.pop(0)

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module.time, "time", lambda: next(fake_times))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    result = module._poll_scheduler_self_heal_state(
        base_url="http://127.0.0.1:18080",
        source="health",
        timeout_s=1.0,
        poll_interval_s=0.25,
        request_timeout_s=10.0,
    )

    assert result["timed_out"] is True
    assert result["monitor"]["ok"] is True
    assert result["auto_state"]["ok"] is True
    assert result["job"]["ok"] is True
    assert result["auto_state"]["body"]["decision"] == "DISPATCHED"
    assert result["job"]["body"]["status"] == "RUNNING"


def test_poll_scheduler_self_heal_state_switches_to_monitor_source_and_extends_timeout(monkeypatch) -> None:
    module = _load_module()
    responses: dict[tuple[str, str], list[dict]] = {
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/monitor_status",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "source": "readiness",
                    "timeout_seconds": 180,
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "scheduler_managed": True,
                    "source": "readiness",
                    "timeout_seconds": 180,
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/self_heal/auto_state?source=readiness",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "readiness",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-005",
                    "last_job_status": "RUNNING",
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "source": "readiness",
                    "decision": "DISPATCHED",
                    "last_job_id": "codex-self-heal::job-005",
                    "last_job_status": "COMPLETED",
                },
            },
        ],
        (
            "GET",
            "http://127.0.0.1:18080/internal/codex/jobs/codex-self-heal::job-005",
        ): [
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-005",
                    "status": "RUNNING",
                },
            },
            {
                "ok": True,
                "status_code": 200,
                "body": {
                    "job_id": "codex-self-heal::job-005",
                    "status": "COMPLETED",
                },
            },
        ],
    }
    fake_times = iter([0.0, 0.0, 1.0, 2.0, 3.0])

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        key = (method.upper(), url)
        queue = responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        return queue.pop(0)

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module.time, "time", lambda: next(fake_times))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    result = module._poll_scheduler_self_heal_state(
        base_url="http://127.0.0.1:18080",
        source="health",
        timeout_s=45.0,
        poll_interval_s=0.25,
        request_timeout_s=10.0,
    )

    assert result["timed_out"] is False
    assert result["effective_timeout_s"] == 210.0
    assert result["auto_state"]["body"]["source"] == "readiness"
    assert result["job"]["body"]["status"] == "COMPLETED"


def test_main_writes_report_and_returns_nonzero_when_scheduler_probe_fails(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module.time, "time", lambda: 1_800_900_000)
    monkeypatch.setattr(
        module,
        "build_probe_report",
        lambda **kwargs: {
            "meta": {"script": "test_codex_self_heal_scheduler.py", "base_url": kwargs["base_url"]},
            "checks": [
                {"check": "scheduler_task_present", "passed": True, "detail": {}},
                {"check": "auto_dispatch_decision", "passed": False, "detail": {"decision": "WAITING_THRESHOLD"}},
            ],
            "overall": {"passed": False, "failed_checks": ["auto_dispatch_decision"]},
        },
    )

    exit_code = module.main(["--base-url", "http://127.0.0.1:18080", "--report-dir", str(tmp_path)])

    stdout = capsys.readouterr().out
    report_path = tmp_path / "codex_self_heal_scheduler_probe_1800900000.json"
    written = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert written["overall"]["failed_checks"] == ["auto_dispatch_decision"]
    assert "[FAIL] auto_dispatch_decision" in stdout
    assert "[codex-self-heal-scheduler] overall=FAIL" in stdout
    assert str(report_path) in stdout
