"""The DL-07 runner seals real loopback responses, never a fixture claim."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "runtime"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_dl07_full_stack_acceptance import (  # noqa: E402
    _digest,
    _ensure_loopback_url,
    build_receipt,
)


def _docker_receipt() -> dict[str, object]:
    def service(name: str, port: int, marker: str) -> dict[str, object]:
        return {
            "status": "PASS",
            "container_id": marker * 64,
            "image_id": "sha256:" + marker * 64,
            "image_ref": f"example/{name}@sha256:" + marker * 64,
            "port": port,
            "started_at": "2026-07-29T10:00:00Z",
        }

    return {
        "schema_version": "natsume.ne07-external-services-acceptance.v1",
        "status": "PASS",
        "receipt_digest": "sha256:" + "a" * 64,
        "project_name": "natsume-ne07-dl07-unit",
        "services": {
            "neo4j": service("neo4j", 17688, "b"),
            "chroma": service("chroma", 18001, "c"),
            "redis": service("redis", 16380, "d"),
        },
    }


def _monitor_ui_receipt(
    *,
    base_url: str,
    commit_sha: str,
    run_id: str,
    process_id: int,
) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": "natsume.digital-life-monitor-ui-live.v1",
        "status": "PASS",
        "commit_sha": commit_sha,
        "run_id": run_id,
        "host_url": f"{base_url}/host",
        "obs_url": f"{base_url}/obs",
        "monitor_url": f"{base_url}/monitor",
        "host_pid": process_id,
        "obs_pid": process_id,
        "monitor_pid": process_id,
        "host_start_digest": "sha256:" + "1" * 64,
        "operator_binding_digest": "sha256:" + "2" * 64,
        "interaction_kind": "REAL_NGINX_PLAYWRIGHT_TO_OBS",
        "playwright_exit_code": 0,
        "playwright_timed_out": False,
        "playwright_stdout_digest": "sha256:" + "3" * 64,
        "playwright_stderr_digest": "sha256:" + "4" * 64,
        "duration_ms": 100,
        "stack_state": "RUNNING_FOR_RECEIPT_SEAL",
        "stop_status": "NOT_REQUIRED",
        "privacy": {
            "contains_user_text": False,
            "contains_model_output": False,
            "contains_raw_audio_or_image": False,
            "contains_secrets": False,
        },
    }
    return {**unsigned, "receipt_digest": _digest(unsigned)}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        json_payloads = {
            "/host/health": {
                "status": "OK",
                "details": {"summary": {"overall_status": "UP"}},
            },
            "/host/ready": {"status": "READY"},
            "/host/v1/digital-life/runtime/health": {"status": "EMPTY"},
            "/obs/health": {
                "status": "DEGRADED",
                "service": "obs",
                "projection": {"kind": "service_health"},
            },
            "/monitor/obs/metrics": {"request_id": "opaque", "ts_ms": 1},
        }
        if path in json_payloads:
            import json

            body = json.dumps(json_payloads[path]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/monitor/monitor/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'<html><div id="root"></div></html>')
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def live_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_runner_requires_real_loopback_http_and_keeps_missing_edge_pending(
    live_server: str,
) -> None:
    receipt = build_receipt(
        commit_sha="a" * 40,
        run_id="dl07.runner:20260729",
        docker_receipt=_docker_receipt(),
        monitor_ui_receipt=_monitor_ui_receipt(
            base_url=live_server,
            commit_sha="a" * 40,
            run_id="dl07.runner:20260729",
            process_id=os.getpid(),
        ),
        host_url=f"{live_server}/host",
        obs_url=f"{live_server}/obs",
        monitor_url=f"{live_server}/monitor",
        host_pid=os.getpid(),
        obs_pid=os.getpid(),
        monitor_pid=os.getpid(),
        external_statuses={
            "gateway": "NOT_TESTED",
            "edge": "NOT_TESTED",
            "model_provider": "NOT_TESTED",
            "asr": "NOT_TESTED",
            "tts": "NOT_TESTED",
            "avatar": "NOT_TESTED",
        },
        remote_ci_status="NOT_RUN_ACCOUNT_QUOTA",
        started_at_ms=1,
        finished_at_ms=2,
    )

    assert receipt.outcome == "ACCEPTANCE_PENDING_EXTERNAL"
    assert receipt.local_components[-3].component_id == "host"
    assert receipt.local_components[-2].reason_codes == (
        "OBS_EXTERNAL_DEPENDENCY_DEGRADED",
    )
    assert receipt.docker_services[0].image_ref.startswith(
        "example/neo4j@sha256:"
    )


def test_runner_rejects_a_monitor_http_claim_without_exact_browser_proof(
    live_server: str,
) -> None:
    monitor = _monitor_ui_receipt(
        base_url=live_server,
        commit_sha="a" * 40,
        run_id="dl07.runner:20260729",
        process_id=os.getpid(),
    )
    monitor["playwright_exit_code"] = 1
    with pytest.raises(RuntimeError, match="browser interaction"):
        build_receipt(
            commit_sha="a" * 40,
            run_id="dl07.runner:20260729",
            docker_receipt=_docker_receipt(),
            monitor_ui_receipt=monitor,
            host_url=f"{live_server}/host",
            obs_url=f"{live_server}/obs",
            monitor_url=f"{live_server}/monitor",
            host_pid=os.getpid(),
            obs_pid=os.getpid(),
            monitor_pid=os.getpid(),
            external_statuses={
                "gateway": "NOT_TESTED",
                "edge": "NOT_TESTED",
                "model_provider": "NOT_TESTED",
                "asr": "NOT_TESTED",
                "tts": "NOT_TESTED",
                "avatar": "NOT_TESTED",
            },
            remote_ci_status="NOT_RUN_ACCOUNT_QUOTA",
            started_at_ms=1,
            finished_at_ms=2,
        )


def test_runner_refuses_non_loopback_or_query_bearing_base_urls() -> None:
    with pytest.raises(ValueError, match="loopback"):
        _ensure_loopback_url("http://10.0.0.5:18080")
    with pytest.raises(ValueError, match="credentials or query"):
        _ensure_loopback_url("http://127.0.0.1:18080?skip=1")
