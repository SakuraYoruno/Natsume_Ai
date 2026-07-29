"""Seal a real, privacy-bounded DL-07 Host/Docker/Monitor acceptance receipt.

This runner does not start Edge.  It validates an already-running isolated
local stack by reading real HTTP responses and a real Docker I/O receipt.  If
the remote Edge, a non-mock model, or physical devices have not been attested,
the only successful conclusion is ``ACCEPTANCE_PENDING_EXTERNAL``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from natsume_contracts.life.full_stack_acceptance import (
    FULL_STACK_ACCEPTANCE_SCHEMA_VERSION,
    REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA,
    DockerServiceEvidenceV1,
    FullStackAcceptanceReceiptV1,
    FullStackComponentV1,
)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_STATUS_CHOICES = (
    "PASS",
    "FAIL",
    "ACCEPTANCE_PENDING_EXTERNAL",
    "NOT_TESTED",
)
_MAX_HTTP_BYTES = 1_048_576
_MONITOR_UI_LIVE_SCHEMA_VERSION = "natsume.digital-life-monitor-ui-live.v1"
_MONITOR_UI_PRIVACY = {
    "contains_user_text": False,
    "contains_model_output": False,
    "contains_raw_audio_or_image": False,
    "contains_secrets": False,
}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe_ref(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) != 71 or not normalized.startswith("sha256:"):
        raise ValueError(f"{field} must be a sha256 reference")
    try:
        int(normalized[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a sha256 reference") from exc
    return normalized


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON receipt must be an object")
    return value


def _ensure_loopback_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("acceptance HTTP endpoints must use loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("acceptance HTTP endpoints cannot contain credentials or query")
    if not parsed.port:
        raise ValueError("acceptance HTTP endpoints must declare a port")
    return f"http://{parsed.hostname}:{parsed.port}{parsed.path.rstrip('/')}"


def _assert_process_alive(process_id: int, *, name: str) -> int:
    parsed = int(process_id)
    if parsed <= 0:
        raise ValueError(f"{name} process id must be positive")
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a portable existence probe on Windows:
        # some Python/Win32 combinations map it to process termination.  Use a
        # read-only tasklist query instead so acceptance cannot damage a live
        # Host, OBS, Monitor, or the test runner itself.
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {parsed}", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if completed.returncode != 0 or str(parsed) not in completed.stdout:
            raise RuntimeError(f"{name} process is not alive")
        return parsed
    try:
        os.kill(parsed, 0)
    except ProcessLookupError as exc:
        raise RuntimeError(f"{name} process is not alive") from exc
    except PermissionError:
        # A live service can legitimately be owned by an elevated Windows
        # process.  The subsequent authenticated HTTP proof remains required.
        pass
    return parsed


def _http_json(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(request, timeout=15) as response:  # noqa: S310 - loopback checked
        payload_bytes = response.read(_MAX_HTTP_BYTES + 1)
        if len(payload_bytes) > _MAX_HTTP_BYTES:
            raise RuntimeError("acceptance HTTP payload exceeds safety limit")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if response.status != 200 or "application/json" not in content_type:
            raise RuntimeError("acceptance JSON endpoint did not return HTTP 200 JSON")
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("acceptance JSON endpoint returned a non-object")
    return payload, {
        "http_status": 200,
        "content_type": content_type.split(";", 1)[0],
        "payload_digest": _digest(payload),
    }


def _http_html(url: str) -> dict[str, Any]:
    request = Request(url, method="GET", headers={"Accept": "text/html"})
    with urlopen(request, timeout=15) as response:  # noqa: S310 - loopback checked
        payload = response.read(_MAX_HTTP_BYTES + 1)
        if len(payload) > _MAX_HTTP_BYTES:
            raise RuntimeError("monitor HTML exceeds safety limit")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if response.status != 200 or "text/html" not in content_type:
            raise RuntimeError("monitor did not return HTTP 200 HTML")
    text = payload.decode("utf-8", errors="replace")
    if 'id="root"' not in text and "id='root'" not in text:
        raise RuntimeError("monitor HTML is not the built application shell")
    return {
        "http_status": 200,
        "content_type": content_type.split(";", 1)[0],
        "body_digest": _digest({"bytes": len(payload), "root": True}),
    }


def _build_external_component(component_id: str, status: str) -> FullStackComponentV1:
    normalized = str(status).strip().upper()
    if normalized not in _STATUS_CHOICES:
        raise ValueError(f"external component status is invalid: {component_id}")
    if normalized == "PASS":
        evidence_kind = (
            "REAL_PROVIDER_ATTESTATION"
            if component_id == "model_provider"
            else "REAL_DEVICE_RECEIPT"
            if component_id in {"asr", "tts", "avatar"}
            else "REAL_GATEWAY_RECEIPT"
            if component_id == "gateway"
            else "REAL_EDGE_RECEIPT"
        )
        reasons: tuple[str, ...] = ()
    elif normalized == "FAIL":
        evidence_kind = "REAL_EXTERNAL_FAILURE"
        reasons = ("EXTERNAL_ACCEPTANCE_FAILED",)
    elif normalized == "ACCEPTANCE_PENDING_EXTERNAL":
        evidence_kind = "EXTERNAL_ACCEPTANCE_PENDING"
        reasons = ("EXTERNAL_ACCEPTANCE_PENDING",)
    else:
        evidence_kind = (
            "REMOTE_NOT_STARTED"
            if component_id in {"gateway", "edge"}
            else "EXTERNAL_CREDENTIAL_OR_DEVICE_UNAVAILABLE"
        )
        reasons = (
            "REMOTE_EDGE_NOT_STARTED"
            if component_id in {"gateway", "edge"}
            else "EXTERNAL_PROVIDER_OR_DEVICE_NOT_TESTED",
        )
    return FullStackComponentV1(
        component_id=component_id,
        status=normalized,
        evidence_kind=evidence_kind,
        evidence_digest=_digest(
            {
                "component_id": component_id,
                "status": normalized,
                "evidence_kind": evidence_kind,
            }
        ),
        reason_codes=reasons,
    )


def _docker_evidence(receipt: Mapping[str, Any]) -> tuple[str, tuple[DockerServiceEvidenceV1, ...]]:
    if receipt.get("schema_version") != "natsume.ne07-external-services-acceptance.v1":
        raise ValueError("external Docker receipt schema is unsupported")
    if receipt.get("status") != "PASS":
        raise RuntimeError("external Docker receipt is not passing")
    receipt_digest = str(receipt.get("receipt_digest") or "")
    if not receipt_digest.startswith("sha256:"):
        raise ValueError("external Docker receipt digest is missing")
    services = receipt.get("services")
    if not isinstance(services, Mapping):
        raise ValueError("external Docker receipt services are missing")
    records: list[DockerServiceEvidenceV1] = []
    for service_id in ("neo4j", "chroma", "redis"):
        service = services.get(service_id)
        if not isinstance(service, Mapping) or service.get("status") != "PASS":
            raise RuntimeError(f"Docker service is not passing: {service_id}")
        records.append(
            DockerServiceEvidenceV1(
                service_id=service_id,
                status="PASS",
                container_id=str(service.get("container_id") or ""),
                image_id=str(service.get("image_id") or ""),
                image_ref=str(service.get("image_ref") or ""),
                host_port=int(service.get("port") or 0),
                started_at=str(service.get("started_at") or ""),
                evidence_digest=_digest(
                    {
                        "docker_receipt": receipt_digest,
                        "service_id": service_id,
                        "container_id": service.get("container_id"),
                        "image_id": service.get("image_id"),
                        "image_ref": service.get("image_ref"),
                        "port": service.get("port"),
                    }
                ),
            )
        )
    return receipt_digest, tuple(records)


def _host_component(host_url: str, process_id: int) -> FullStackComponentV1:
    health, health_meta = _http_json(f"{host_url}/health?skip_dependency_probes=1")
    ready, ready_meta = _http_json(f"{host_url}/ready")
    runtime, runtime_meta = _http_json(f"{host_url}/v1/digital-life/runtime/health")
    if health.get("status") != "OK":
        raise RuntimeError("Host health is not OK")
    if str((health.get("details") or {}).get("summary", {}).get("overall_status") or "") != "UP":
        raise RuntimeError("Host health summary is not UP")
    if ready.get("status") != "READY":
        raise RuntimeError("Host readiness is not READY")
    if runtime.get("status") not in {"EMPTY", "OK", "DEGRADED"}:
        raise RuntimeError("Digital Life runtime health is invalid")
    return FullStackComponentV1(
        component_id="host",
        status="PASS",
        evidence_kind="REAL_HTTP",
        evidence_digest=_digest(
            {
                "health": health_meta,
                "ready": ready_meta,
                "digital_life_runtime": runtime_meta,
                "digital_life_status": runtime.get("status"),
            }
        ),
        listener=host_url.replace("http://", "loopback://", 1),
        process_id=_assert_process_alive(process_id, name="host"),
        reason_codes=(
            ("DIGITAL_LIFE_HEAD_EMPTY",)
            if runtime.get("status") == "EMPTY"
            else ()
        ),
    )


def _obs_component(obs_url: str, process_id: int) -> FullStackComponentV1:
    payload, meta = _http_json(f"{obs_url}/health")
    if payload.get("service") != "obs":
        raise RuntimeError("OBS health identity is invalid")
    projection = payload.get("projection")
    if not isinstance(projection, Mapping) or not projection.get("kind"):
        raise RuntimeError("OBS health has no real projection identity")
    observed_status = str(payload.get("status") or "")
    if observed_status not in {"OK", "DEGRADED"}:
        raise RuntimeError("OBS health status is invalid")
    return FullStackComponentV1(
        component_id="obs",
        status="PASS",
        evidence_kind="REAL_HTTP",
        evidence_digest=_digest({"health": meta, "observed_status": observed_status}),
        listener=obs_url.replace("http://", "loopback://", 1),
        process_id=_assert_process_alive(process_id, name="obs"),
        reason_codes=(
            ("OBS_EXTERNAL_DEPENDENCY_DEGRADED",)
            if observed_status == "DEGRADED"
            else ()
        ),
    )


def _monitor_ui_receipt_digest(
    receipt: Mapping[str, Any],
    *,
    commit_sha: str,
    run_id: str,
    host_url: str,
    obs_url: str,
    monitor_url: str,
    host_pid: int,
    obs_pid: int,
    monitor_pid: int,
) -> str:
    """Fail closed unless the browser proof is exact-scope and privacy bounded."""

    expected = {
        "schema_version",
        "status",
        "commit_sha",
        "run_id",
        "host_url",
        "obs_url",
        "monitor_url",
        "host_pid",
        "obs_pid",
        "monitor_pid",
        "host_start_digest",
        "operator_binding_digest",
        "interaction_kind",
        "playwright_exit_code",
        "playwright_timed_out",
        "playwright_stdout_digest",
        "playwright_stderr_digest",
        "duration_ms",
        "stack_state",
        "stop_status",
        "privacy",
        "receipt_digest",
    }
    if set(receipt) != expected:
        raise ValueError("Monitor UI receipt field set is invalid")
    if receipt.get("schema_version") != _MONITOR_UI_LIVE_SCHEMA_VERSION:
        raise ValueError("Monitor UI receipt schema is unsupported")
    if receipt.get("status") != "PASS":
        raise RuntimeError("Monitor UI interaction receipt is not passing")
    if receipt.get("privacy") != _MONITOR_UI_PRIVACY:
        raise ValueError("Monitor UI receipt privacy declaration is invalid")
    if str(receipt.get("commit_sha") or "") != commit_sha or str(receipt.get("run_id") or "") != run_id:
        raise ValueError("Monitor UI receipt commit or run identity drift")
    expected_urls = {
        "host_url": host_url,
        "obs_url": obs_url,
        "monitor_url": monitor_url,
    }
    for field, expected_url in expected_urls.items():
        if _ensure_loopback_url(str(receipt.get(field) or "")) != expected_url:
            raise ValueError("Monitor UI receipt listener identity drift")
    expected_pids = {
        "host_pid": host_pid,
        "obs_pid": obs_pid,
        "monitor_pid": monitor_pid,
    }
    for field, expected_pid in expected_pids.items():
        try:
            received_pid = int(receipt.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError("Monitor UI receipt process identity is invalid") from exc
        if received_pid != expected_pid or received_pid <= 0:
            raise ValueError("Monitor UI receipt process identity drift")
    for field in (
        "host_start_digest",
        "operator_binding_digest",
        "playwright_stdout_digest",
        "playwright_stderr_digest",
    ):
        _safe_ref(receipt.get(field), f"Monitor UI {field}")
    if receipt.get("interaction_kind") != "REAL_NGINX_PLAYWRIGHT_TO_OBS":
        raise ValueError("Monitor UI receipt interaction kind is invalid")
    if int(receipt.get("playwright_exit_code", -1)) != 0 or receipt.get("playwright_timed_out") is not False:
        raise RuntimeError("Monitor UI browser interaction did not complete")
    if int(receipt.get("duration_ms", -1)) < 0:
        raise ValueError("Monitor UI receipt duration is invalid")
    if receipt.get("stack_state") != "RUNNING_FOR_RECEIPT_SEAL" or receipt.get("stop_status") != "NOT_REQUIRED":
        raise ValueError("Monitor UI receipt does not bind a live stack")
    unsigned = dict(receipt)
    received_digest = str(unsigned.pop("receipt_digest") or "")
    expected_digest = _digest(unsigned)
    if received_digest != expected_digest:
        raise ValueError("Monitor UI receipt digest is invalid")
    return received_digest


def _monitor_component(
    monitor_url: str,
    process_id: int,
    *,
    monitor_ui_receipt_digest: str,
) -> FullStackComponentV1:
    html_meta = _http_html(f"{monitor_url}/monitor/index.html")
    metrics, metrics_meta = _http_json(f"{monitor_url}/obs/metrics")
    if not metrics:
        raise RuntimeError("Monitor proxy did not expose an OBS payload")
    return FullStackComponentV1(
        component_id="monitor",
        status="PASS",
        evidence_kind="REAL_NGINX_MONITOR_PLAYWRIGHT_HTTP",
        evidence_digest=_digest(
            {
                "html": html_meta,
                "metrics": metrics_meta,
                "browser_interaction": monitor_ui_receipt_digest,
            }
        ),
        listener=monitor_url.replace("http://", "loopback://", 1),
        process_id=_assert_process_alive(process_id, name="monitor"),
    )


def build_receipt(
    *,
    commit_sha: str,
    run_id: str,
    docker_receipt: Mapping[str, Any],
    monitor_ui_receipt: Mapping[str, Any],
    host_url: str,
    obs_url: str,
    monitor_url: str,
    host_pid: int,
    obs_pid: int,
    monitor_pid: int,
    external_statuses: Mapping[str, str],
    remote_ci_status: str,
    started_at_ms: int,
    finished_at_ms: int | None = None,
) -> FullStackAcceptanceReceiptV1:
    """Perform only real loopback reads and seal their opaque evidence."""

    host_url = _ensure_loopback_url(host_url)
    obs_url = _ensure_loopback_url(obs_url)
    monitor_url = _ensure_loopback_url(monitor_url)
    docker_digest, docker_services = _docker_evidence(docker_receipt)
    monitor_ui_digest = _monitor_ui_receipt_digest(
        monitor_ui_receipt,
        commit_sha=commit_sha,
        run_id=run_id,
        host_url=host_url,
        obs_url=obs_url,
        monitor_url=monitor_url,
        host_pid=host_pid,
        obs_pid=obs_pid,
        monitor_pid=monitor_pid,
    )
    local = (
        FullStackComponentV1(
            component_id="docker",
            status="PASS",
            evidence_kind="REAL_DOCKER_COMPOSE_IO",
            evidence_digest=docker_digest,
        ),
        *(
            FullStackComponentV1(
                component_id=record.service_id,
                status=record.status,
                evidence_kind="REAL_DOCKER_IO",
                evidence_digest=record.evidence_digest,
                listener=f"loopback://127.0.0.1:{record.host_port}",
            )
            for record in docker_services
        ),
        _host_component(host_url, host_pid),
        _obs_component(obs_url, obs_pid),
        _monitor_component(
            monitor_url,
            monitor_pid,
            monitor_ui_receipt_digest=monitor_ui_digest,
        ),
    )
    external = tuple(
        _build_external_component(component_id, external_statuses[component_id])
        for component_id in ("gateway", "edge", "model_provider", "asr", "tts", "avatar")
    )
    configuration_digest = _digest(
        {
            "host_url": host_url,
            "obs_url": obs_url,
            "monitor_url": monitor_url,
            "host_pid": int(host_pid),
            "obs_pid": int(obs_pid),
            "monitor_pid": int(monitor_pid),
            "docker_project": docker_receipt.get("project_name"),
            "monitor_ui_receipt": monitor_ui_digest,
            "remote_edge_started": False,
        }
    )
    provisional = {
        "local_components": local,
        "external_components": external,
        "remote_ci_status": remote_ci_status,
    }
    # Construct once with the derived outcome instead of accepting a caller
    # claim.  The public contract recalculates it in __post_init__.
    from natsume_contracts.life.full_stack_acceptance import derive_full_stack_outcome

    outcome = derive_full_stack_outcome(**provisional)
    return FullStackAcceptanceReceiptV1(
        commit_sha=commit_sha,
        run_id=run_id,
        started_at_ms=started_at_ms,
        finished_at_ms=_now_ms() if finished_at_ms is None else finished_at_ms,
        configuration_digest=configuration_digest,
        docker_evidence_digest=docker_digest,
        docker_services=docker_services,
        local_components=local,
        external_components=external,
        remote_ci_status=remote_ci_status,
        outcome=outcome,
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--docker-receipt", type=Path, required=True)
    parser.add_argument("--monitor-ui-receipt", type=Path, required=True)
    parser.add_argument("--host-url", required=True)
    parser.add_argument("--obs-url", required=True)
    parser.add_argument("--monitor-url", required=True)
    parser.add_argument("--host-pid", type=int, required=True)
    parser.add_argument("--obs-pid", type=int, required=True)
    parser.add_argument("--monitor-pid", type=int, required=True)
    parser.add_argument("--gateway-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument("--edge-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument("--model-provider-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument("--asr-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument("--tts-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument("--avatar-status", choices=_STATUS_CHOICES, default="NOT_TESTED")
    parser.add_argument(
        "--remote-ci-status",
        choices=("PASS", "FAIL", REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA),
        default=REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    started_at_ms = _now_ms()
    try:
        receipt = build_receipt(
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            docker_receipt=_load_json(args.docker_receipt),
            monitor_ui_receipt=_load_json(args.monitor_ui_receipt),
            host_url=args.host_url,
            obs_url=args.obs_url,
            monitor_url=args.monitor_url,
            host_pid=args.host_pid,
            obs_pid=args.obs_pid,
            monitor_pid=args.monitor_pid,
            external_statuses={
                "gateway": args.gateway_status,
                "edge": args.edge_status,
                "model_provider": args.model_provider_status,
                "asr": args.asr_status,
                "tts": args.tts_status,
                "avatar": args.avatar_status,
            },
            remote_ci_status=args.remote_ci_status,
            started_at_ms=started_at_ms,
        )
    except BaseException as exc:  # noqa: BLE001 - CLI emits bounded failure evidence
        failure = {
            "schema_version": FULL_STACK_ACCEPTANCE_SCHEMA_VERSION,
            "status": "FAIL",
            "commit_sha": str(args.commit_sha),
            "run_id": str(args.run_id),
            "finished_at_ms": _now_ms(),
            "error_type": type(exc).__name__,
            "contains_secrets": False,
        }
        failure["receipt_digest"] = _digest(failure)
        _write_json(args.output, failure)
        print(json.dumps({"status": "FAIL", "output": str(args.output)}, ensure_ascii=False))
        return 1
    payload = receipt.as_dict()
    _write_json(args.output, payload)
    print(
        json.dumps(
            {
                "schema_version": payload["schema_version"],
                "outcome": payload["outcome"],
                "receipt_digest": payload["receipt_digest"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
