import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FULL_RUN_REPORT_TOOL = ROOT / "scripts" / "runtime" / "generate_full_run_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_full_run_report", FULL_RUN_REPORT_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load full run report module: {FULL_RUN_REPORT_TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_run_report_marks_all_pass_when_all_sections_are_healthy(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.time, "time", lambda: 1_800_600_000)
    monkeypatch.setattr(module, "collect_docker_status", lambda: {"ok": True, "containers": [{"Names": "natsume-host"}]})
    monkeypatch.setattr(module, "collect_env_check", lambda: {"ready": True, "reason_code": None})
    monkeypatch.setattr(
        module,
        "collect_host_status",
        lambda: {
            "url": "http://127.0.0.1:18080",
            "health": {"status_code": 200, "data": {"overall_status": "UP"}},
            "ready": {"status_code": 200, "data": {"ready": True}},
            "metrics": {"status_code": 200, "data": {"ok": True}},
        },
    )
    monkeypatch.setattr(
        module,
        "collect_codex_immune_scheduler_probe",
        lambda: {"ok": True, "data": {"overall": {"passed": True}}},
    )
    monkeypatch.setattr(
        module,
        "collect_unit_test_results",
        lambda: {"ok": True, "exit_code": 0, "summary": "148 passed", "passed": 148, "failed": 0, "total": 148},
    )
    monkeypatch.setattr(
        module,
        "collect_integration_test_results",
        lambda: {"ok": True, "report_file": "run_report_ok.json", "data": {"summary": {"failed": 0}}},
    )

    report_path, report = module.main()

    stdout = capsys.readouterr().out
    written = json.loads(Path(report_path).read_text(encoding="utf-8"))

    assert report_path == tmp_path / "FULL_RUN_REPORT_1800600000.json"
    assert report["overall"] == {
        "docker_services": "PASS",
        "env_readiness": "PASS",
        "host_brain_up": "PASS",
        "codex_immune_scheduler": "PASS",
        "unit_tests": "PASS",
        "integration_tests": "PASS",
        "all_pass": True,
    }
    assert written["overall"]["all_pass"] is True
    assert written["sections"]["env_readiness"]["ready"] is True
    assert "ALL PASS" in stdout
    assert str(report_path) in stdout


def test_full_run_report_fault_injection_surfaces_failures_in_overall_summary(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.time, "time", lambda: 1_800_600_001)
    monkeypatch.setattr(module, "collect_docker_status", lambda: {"ok": False, "error": "docker daemon offline"})
    monkeypatch.setattr(
        module,
        "collect_env_check",
        lambda: {"ready": False, "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY", "detail": "env probe degraded"},
    )
    monkeypatch.setattr(
        module,
        "collect_host_status",
        lambda: {
            "url": "http://127.0.0.1:18080",
            "health": {"status_code": 503, "data": {"overall_status": "DEGRADED"}},
            "ready": {"status_code": 200, "data": {"ready": False}},
            "metrics": {"status_code": -1, "data": {"error": "timeout"}},
        },
    )
    monkeypatch.setattr(
        module,
        "collect_codex_immune_scheduler_probe",
        lambda: {"ok": False, "error": "scheduler trigger rejected"},
    )
    monkeypatch.setattr(
        module,
        "collect_unit_test_results",
        lambda: {"ok": False, "exit_code": 1, "summary": "2 failed", "passed": 146, "failed": 2, "total": 148},
    )
    monkeypatch.setattr(
        module,
        "collect_integration_test_results",
        lambda: {"ok": False, "report_file": "run_report_fail.json", "data": {"summary": {"failed": 3}}},
    )

    report_path, report = module.main()

    stdout = capsys.readouterr().out
    written = json.loads(Path(report_path).read_text(encoding="utf-8"))

    assert report_path == tmp_path / "FULL_RUN_REPORT_1800600001.json"
    assert report["overall"] == {
        "docker_services": "FAIL",
        "env_readiness": "FAIL",
        "host_brain_up": "FAIL",
        "codex_immune_scheduler": "FAIL",
        "unit_tests": "FAIL",
        "integration_tests": "FAIL",
        "all_pass": False,
    }
    assert written["sections"]["docker_services"]["error"] == "docker daemon offline"
    assert written["sections"]["env_readiness"]["reason_code"] == "SERVICE_UNAVAILABLE_DEPENDENCY"
    assert written["sections"]["host_brain_status"]["health"]["status_code"] == 503
    assert "SOME FAILURES" in stdout
    assert str(report_path) in stdout
