import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "runtime" / "test_full_service.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("test_full_service_runtime_probe", SCRIPT_PATH)
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


def test_build_probe_report_marks_all_checks_passed_with_healthy_payloads(monkeypatch) -> None:
    module = _load_module()

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        if url.endswith("/health"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "status": "OK",
                    "details": {
                        "summary": {
                            "overall_status": "UP",
                            "required_total": 13,
                            "required_unavailable": 0,
                        }
                    },
                },
            }
        if url.endswith("/ready"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {"ready": True, "status": "READY", "reason_code": None},
            }
        if url.endswith("/metrics"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "uptime_seconds": 123,
                    "total_requests": 88,
                    "total_chat_requests": 34,
                    "reason_code_distribution": {},
                },
            }
        if url.endswith("/health/canon_sync"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "status": "OK",
                    "details": {"detail": "canon sync healthy", "drift_count": 0},
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    def _fake_stream_chat_sse(*, url: str, payload, timeout_s: float):
        return {
            "ok": True,
            "status_code": 200,
            "latency_ms": 250,
            "events": [{"json": {"phase": "COMMIT", "decision": "APPROVE", "done": True, "request_id": "req-chat-001"}}],
            "commit_event": {"phase": "COMMIT", "decision": "APPROVE", "done": True, "request_id": "req-chat-001"},
        }

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module, "_stream_chat_sse", _fake_stream_chat_sse)

    report = module.build_probe_report(base_url="http://127.0.0.1:18080")

    assert report["overall"] == {"passed": True, "failed_checks": []}
    assert [row["check"] for row in report["checks"]] == [
        "health",
        "ready",
        "metrics",
        "chat_commit",
        "canon_sync",
    ]
    assert all(row["passed"] for row in report["checks"])


def test_probe_rejects_http_200_when_ready_or_canon_is_degraded(monkeypatch) -> None:
    module = _load_module()

    def _fake_http_json(method: str, url: str, *, payload=None, timeout_s=10.0):
        if "/health?" in url:
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "status": "ACCEPTANCE_PENDING_EXTERNAL",
                    "details": {
                        "summary": {
                            "overall_status": "ACCEPTANCE_PENDING_EXTERNAL",
                            "required_unavailable": 0,
                            "required_not_tested": 5,
                        }
                    },
                },
            }
        if url.endswith("/ready"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "ready": False,
                    "status": "ACCEPTANCE_PENDING_EXTERNAL",
                    "reason_code": "ACCEPTANCE_PENDING_EXTERNAL",
                },
            }
        if url.endswith("/metrics"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {"reason_code_distribution": {}},
            }
        if url.endswith("/health/canon_sync"):
            return {
                "ok": True,
                "status_code": 200,
                "body": {
                    "status": "DEGRADED",
                    "details": {
                        "reason_code": "OPERATOR_PROJECTION_STALE",
                        "drift_count": 0,
                    },
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(
        module,
        "_stream_chat_sse",
        lambda **kwargs: {
            "ok": True,
            "status_code": 200,
            "events": [{}],
            "commit_event": {"phase": "COMMIT", "done": True},
        },
    )

    strict = module.build_probe_report(
        base_url="http://127.0.0.1:18080",
        skip_dependency_probes=True,
    )
    pending_allowed = module.build_probe_report(
        base_url="http://127.0.0.1:18080",
        skip_dependency_probes=True,
        allow_pending_external=True,
    )

    assert strict["overall"]["failed_checks"] == ["health", "ready", "canon_sync"]
    assert pending_allowed["overall"]["failed_checks"] == ["canon_sync"]
    assert pending_allowed["meta"]["skip_dependency_probes"] is True
    assert pending_allowed["meta"]["allow_pending_external"] is True


def test_main_writes_report_and_returns_nonzero_when_probe_fails(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module.time, "time", lambda: 1_800_800_000)
    monkeypatch.setattr(
        module,
        "build_probe_report",
        lambda **kwargs: {
            "meta": {"script": "test_full_service.py", "base_url": kwargs["base_url"]},
            "checks": [
                {"check": "health", "passed": True, "detail": {"status": "OK"}},
                {"check": "chat_commit", "passed": False, "detail": {"decision": "REJECT", "done": False}},
            ],
            "overall": {"passed": False, "failed_checks": ["chat_commit"]},
        },
    )

    exit_code = module.main(["--base-url", "http://127.0.0.1:18080", "--report-dir", str(tmp_path)])

    stdout = capsys.readouterr().out
    report_path = tmp_path / "full_service_probe_1800800000.json"
    written = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert written["overall"]["failed_checks"] == ["chat_commit"]
    assert "[FAIL] chat_commit" in stdout
    assert "[full-service] overall=FAIL" in stdout
    assert str(report_path) in stdout
