#!/usr/bin/env python3
"""Run the real DL-07 Monitor browser proof without persisting its session secret.

The runner is intentionally a narrow local acceptance adapter.  It starts an
isolated Host/OBS/Nginx stack through the production PowerShell launcher,
creates one in-memory operator session credential, and lets Playwright use it
only for the lifetime of the browser process tree.  The written receipt holds
only digests, process identities and loopback listeners.  It never starts the
remote Edge and it does not assert that an external model or physical device
was used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import secrets


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "natsume.digital-life-monitor-ui-live.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,159}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_PRIVACY = {
    "contains_user_text": False,
    "contains_model_output": False,
    "contains_raw_audio_or_image": False,
    "contains_secrets": False,
}
_OPERATOR_SCOPES = (
    "obs:operator:read",
    "obs:health:stream",
    "obs:task-runtime:read",
    "obs:session:read",
    "obs:session:export",
    "obs:trace:read",
    "obs:checkpoint:read",
    "obs:memory:read",
    "obs:immune:read",
    "obs:bio-ops:read",
    "obs:ui:read",
    "obs:capability-forge:read",
    "obs:life-evolution:read",
    "obs:agent-history:read",
)


def _digest(value: Any) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_commit_sha(value: str) -> str:
    normalized = str(value or "").strip()
    if _SHA_RE.fullmatch(normalized) is None:
        raise ValueError("commit_sha must be a full lowercase Git SHA")
    return normalized


def _require_run_id(value: str) -> str:
    normalized = str(value or "").strip()
    if _RUN_ID_RE.fullmatch(normalized) is None:
        raise ValueError("run_id is invalid")
    return normalized


def _loopback_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("live Monitor endpoints must use loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.port:
        raise ValueError("live Monitor endpoint is invalid")
    return f"http://{parsed.hostname}:{parsed.port}{parsed.path.rstrip('/')}"


def _safe_process_id(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive process id") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive process id")
    return parsed


def _bounded_json_request(
    *,
    url: str,
    method: str,
    body: Mapping[str, Any] | None = None,
    session_secret: str = "",
) -> dict[str, Any]:
    encoded = (
        None
        if body is None
        else json.dumps(dict(body), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    headers = {"Accept": "application/json"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    if session_secret:
        headers["Cookie"] = f"natsume_operator_session={session_secret}"
    request = Request(url, data=encoded, headers=headers, method=method)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - exact loopback caller
        raw = response.read(1_048_577)
        if response.status != 200 or len(raw) > 1_048_576:
            raise RuntimeError("bounded acceptance request did not return HTTP 200 JSON")
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("bounded acceptance request returned a non-object")
    return dict(decoded)


def _prepare_security_projection(
    *,
    host_url: str,
    monitor_url: str,
    session_secret: str,
    timeout_seconds: int = 90,
) -> str:
    """Create one real local snapshot and wait for its OBS read projection."""

    _bounded_json_request(
        url=f"{host_url}/internal/security/hunt/run-once",
        method="POST",
        body={"reason": "dl07_monitor_acceptance"},
    )
    deadline = time.monotonic() + max(10, int(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            payload = _bounded_json_request(
                url=f"{monitor_url}/obs/security/runtime",
                method="GET",
                session_secret=session_secret,
            )
        except BaseException:
            time.sleep(0.5)
            continue
        projection = payload.get("projection")
        projection_map = dict(projection) if isinstance(projection, Mapping) else {}
        shape = {
            "schema_version": str(payload.get("schema_version") or ""),
            "asset_count": max(0, int(payload.get("asset_count") or 0)),
            "incident_count": max(0, int(payload.get("incident_count") or 0)),
            "projection_kind": str(projection_map.get("kind") or ""),
            "projection_available": bool(projection_map.get("available")),
            "projection_state": str(projection_map.get("degraded_state") or ""),
        }
        if (
            shape["schema_version"] == "Natsume.security_immune.runtime.v1"
            and shape["projection_kind"] == "security_immune"
            and shape["projection_available"] is True
            and shape["projection_state"] == "HEALTHY"
        ):
            return _digest(shape)
        time.sleep(0.5)
    raise RuntimeError("real security snapshot did not reach a healthy OBS projection")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_last_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for position, character in enumerate(raw):
        if character != "{":
            continue
        try:
            decoded, consumed = decoder.raw_decode(raw[position:])
        except json.JSONDecodeError:
            continue
        # PowerShell can emit bounded warnings before its final object.  The
        # final decoder candidate is the one whose trailing stream is only
        # whitespace; do not accidentally select an object nested inside it.
        if isinstance(decoded, dict) and not raw[position + consumed :].strip():
            return dict(decoded)
    raise RuntimeError("production launcher did not return a final JSON object")


def _redact(value: str, *, session_secret: str) -> str:
    # Playwright normally disables artifacts for an operator session, but a
    # failure may still echo a cookie in process output.  Keep diagnostics only
    # in memory and remove the exact ephemeral value before any digest is made.
    return str(value or "").replace(session_secret, "[REDACTED_OPERATOR_SESSION]")


def _operator_binding(session_secret: str) -> tuple[str, str]:
    digest = hashlib.sha256(session_secret.encode("utf-8")).hexdigest()
    payload = [
        {
            "token_sha256": digest,
            "principal_id": "ops.dl07.monitor",
            "tenant_id": "dl07-acceptance",
            "key_id": "dl07-monitor-live",
            "principal_kind": "service",
            "scopes": list(_OPERATOR_SCOPES),
        }
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "sha256:" + digest


def _powershell_executable() -> str:
    for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError("PowerShell executable is required for the production Host launcher")


def _npm_executable() -> str:
    for name in ("npm.cmd", "npm"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError("npm executable is required for the real Monitor interaction")


def _run_child(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int, str, str, bool]:
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        else:
            process.kill()
        stdout, stderr = process.communicate(timeout=20)
    return int(process.returncode or 0), str(stdout or ""), str(stderr or ""), timed_out


def _safe_host_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    obs = result.get("obs_process")
    monitor = result.get("monitor_proxy")
    if not isinstance(obs, Mapping) or not isinstance(monitor, Mapping):
        raise RuntimeError("production launcher omitted OBS or Monitor identity")
    if str(result.get("status") or "") != "started":
        raise RuntimeError("production Host launcher did not start")
    if str(monitor.get("status") or "") not in {"started", "already_running"}:
        raise RuntimeError("production Monitor proxy did not start")
    summary = {
        "host_pid": _safe_process_id(result.get("pid"), field="host_pid"),
        "obs_pid": _safe_process_id(obs.get("pid"), field="obs_pid"),
        "monitor_pid": _safe_process_id(monitor.get("pid"), field="monitor_pid"),
        "canonical_db_path": str(result.get("canonical_db_path") or ""),
        "runtime_root": str(result.get("runtime_root") or ""),
        "external_services_status": str(
            dict(result.get("external_services_ready") or {}).get("status") or ""
        ),
    }
    if not summary["canonical_db_path"] or not summary["runtime_root"]:
        raise RuntimeError("production launcher omitted isolated runtime identity")
    if summary["external_services_status"] != "ready":
        raise RuntimeError("isolated Docker dependencies are not ready")
    return summary


def _stop_stack(
    *,
    runtime_root: Path,
    host_port: int,
    obs_port: int,
    monitor_port: int,
    environment: Mapping[str, str],
) -> str:
    command = [
        _powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "runtime" / "stop_host_side.ps1"),
        "-RuntimeRoot",
        str(runtime_root),
        "-Port",
        str(host_port),
        "-ObsPort",
        str(obs_port),
        "-MonitorProxyPort",
        str(monitor_port),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        return "STOP_COMMAND_FAILED"
    try:
        payload = _parse_last_json_object(str(completed.stdout or ""))
    except RuntimeError:
        return "STOP_OUTPUT_INVALID"
    return str(payload.get("status") or "STOP_OUTPUT_INVALID").upper()


def _unsigned_receipt(
    *,
    commit_sha: str,
    run_id: str,
    host_url: str,
    obs_url: str,
    monitor_url: str,
    host_summary: Mapping[str, Any],
    binding_digest: str,
    playwright_exit_code: int,
    timed_out: bool,
    stdout_digest: str,
    stderr_digest: str,
    duration_ms: int,
    security_projection_digest: str,
    status: str,
    stack_state: str,
    stop_status: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "host_url": host_url,
        "obs_url": obs_url,
        "monitor_url": monitor_url,
        "host_pid": host_summary.get("host_pid", 0),
        "obs_pid": host_summary.get("obs_pid", 0),
        "monitor_pid": host_summary.get("monitor_pid", 0),
        "host_start_digest": _digest(host_summary),
        "operator_binding_digest": binding_digest,
        "interaction_kind": "REAL_NGINX_PLAYWRIGHT_TO_OBS",
        "playwright_exit_code": playwright_exit_code,
        "playwright_timed_out": timed_out,
        "playwright_stdout_digest": stdout_digest,
        "playwright_stderr_digest": stderr_digest,
        "security_projection_digest": security_projection_digest,
        "duration_ms": duration_ms,
        "stack_state": stack_state,
        "stop_status": stop_status,
        "privacy": dict(_PRIVACY),
    }


def run_live_monitor_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    commit_sha = _require_commit_sha(args.commit_sha)
    run_id = _require_run_id(args.run_id)
    host_url = _loopback_url(f"http://127.0.0.1:{args.host_port}")
    obs_url = _loopback_url(f"http://127.0.0.1:{args.obs_port}")
    monitor_url = _loopback_url(f"http://127.0.0.1:{args.monitor_port}")
    runtime_root = Path(args.runtime_root).resolve()
    workspace_root = Path(args.agent_workspace_root).resolve()
    secret_ref = str(args.workspace_secret_ref or "").strip()
    if not secret_ref.startswith("sec://workspace/") or secret_ref == "sec://workspace/":
        raise ValueError("workspace_secret_ref must use sec://workspace/<name>")
    if not str(args.chroma_endpoint).startswith("http://127.0.0.1:"):
        raise ValueError("chroma_endpoint must be an isolated loopback URL")
    if not str(args.neo4j_uri).startswith("bolt://127.0.0.1:"):
        raise ValueError("neo4j_uri must be an isolated loopback URI")
    if not str(args.redis_url).startswith("redis://127.0.0.1:"):
        raise ValueError("redis_url must be an isolated loopback URI")

    provision = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "runtime" / "provision_workspace_secret_ref.py"),
            "--secret-ref",
            secret_ref,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if provision.returncode != 0:
        raise RuntimeError("workspace secret reference provisioning failed")

    session_secret = secrets.token_urlsafe(48)
    binding, binding_digest = _operator_binding(session_secret)
    host_environment = dict(os.environ)
    # Avoid a shell-inherited plaintext workspace credential changing a
    # reference-based isolated acceptance into a dual-source configuration.
    host_environment.pop("HOST_AGENT_WORKSPACE_HMAC_SECRET", None)
    host_environment.pop("HOST_AGENT_WORKSPACE_HMAC_SECRET_REF", None)
    host_environment.update(
        {
            "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS": binding,
            "CHROMA_ENDPOINT": str(args.chroma_endpoint),
            "BOND_NEO4J_URI": str(args.neo4j_uri),
            "HOST_RATE_LIMIT_REDIS_URL": str(args.redis_url),
            "HOST_OBS_APP_HOST": "127.0.0.1",
            "HOST_OBS_APP_PORT": str(args.obs_port),
            "HOST_MONITOR_PROXY_HOST": "127.0.0.1",
            "HOST_MONITOR_PROXY_PORT": str(args.monitor_port),
            # The actual Host scan has a durable local state file.  Keep it
            # inside this acceptance root rather than silently reusing the
            # user's normal runtime/security_immune history.
            "SECURITY_IMMUNE_STATE_DIR": str(runtime_root.parent / "security_immune"),
        }
    )
    start_command = [
        _powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "runtime" / "start_host_side.ps1"),
        "-RuntimeRoot",
        str(runtime_root),
        "-AgentWorkspaceRoot",
        str(workspace_root),
        "-AgentWorkspaceHmacSecretRef",
        secret_ref,
        "-BindHost",
        "127.0.0.1",
        "-Port",
        str(args.host_port),
        "-ExternalServicesBackend",
        "none",
        "-StartupCheck",
        "-EnableMonitorProxy",
        "-MonitorProxyHost",
        "127.0.0.1",
        "-MonitorProxyPort",
        str(args.monitor_port),
        "-WaitSeconds",
        "40",
    ]
    started_at_ms = _now_ms()
    start = subprocess.run(
        start_command,
        cwd=ROOT,
        env=host_environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if start.returncode != 0:
        raise RuntimeError("production Host/OBS/Monitor stack failed to start")
    host_summary = _safe_host_summary(_parse_last_json_object(str(start.stdout or "")))

    monitor_environment = dict(host_environment)
    monitor_environment.update(
        {
            "MONITOR_UI_BASE_URL": f"{monitor_url}/monitor/",
            "MONITOR_UI_OBS_BASE_URL": obs_url,
            "MONITOR_UI_HOST_BASE_URL": host_url,
            "MONITOR_UI_SKIP_WEB_SERVER": "1",
            "MONITOR_UI_OPERATOR_SESSION_TOKEN": session_secret,
        }
    )
    command = [
        _npm_executable(),
        "exec",
        "playwright",
        "--",
        "test",
        "e2e/core-release-real-interaction.spec.js",
        "--trace=off",
    ]
    exit_code = 1
    timed_out = False
    stdout = ""
    stderr = ""
    security_projection_digest = _digest({"status": "NOT_READY"})
    stop_status = "NOT_REQUIRED"
    success = False
    try:
        security_projection_digest = _prepare_security_projection(
            host_url=host_url,
            monitor_url=monitor_url,
            session_secret=session_secret,
        )
        exit_code, stdout, stderr, timed_out = _run_child(
            command,
            cwd=ROOT / "host_brain" / "app" / "monitor_ui",
            environment=monitor_environment,
            timeout_seconds=max(30, int(args.playwright_timeout_seconds)),
        )
        success = exit_code == 0 and not timed_out
    finally:
        monitor_environment.pop("MONITOR_UI_OPERATOR_SESSION_TOKEN", None)
        redacted_stdout = _redact(stdout, session_secret=session_secret)
        redacted_stderr = _redact(stderr, session_secret=session_secret)
        session_secret = ""
        if not success:
            stop_status = _stop_stack(
                runtime_root=runtime_root,
                host_port=args.host_port,
                obs_port=args.obs_port,
                monitor_port=args.monitor_port,
                environment=host_environment,
            )

    finished_at_ms = _now_ms()
    status = "PASS" if success else "FAIL"
    unsigned = _unsigned_receipt(
        commit_sha=commit_sha,
        run_id=run_id,
        host_url=host_url,
        obs_url=obs_url,
        monitor_url=monitor_url,
        host_summary=host_summary,
        binding_digest=binding_digest,
        playwright_exit_code=exit_code,
        timed_out=timed_out,
        stdout_digest=_digest(redacted_stdout),
        stderr_digest=_digest(redacted_stderr),
        duration_ms=finished_at_ms - started_at_ms,
        security_projection_digest=security_projection_digest,
        status=status,
        stack_state="RUNNING_FOR_RECEIPT_SEAL" if success else "STOPPED_AFTER_FAILURE",
        stop_status=stop_status,
    )
    payload = {**unsigned, "receipt_digest": _digest(unsigned)}
    _write_json(Path(args.output), payload)
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--agent-workspace-root", type=Path, required=True)
    parser.add_argument("--workspace-secret-ref", required=True)
    parser.add_argument("--host-port", type=int, required=True)
    parser.add_argument("--obs-port", type=int, required=True)
    parser.add_argument("--monitor-port", type=int, required=True)
    parser.add_argument("--chroma-endpoint", default="http://127.0.0.1:18001")
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17688")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:16380/0")
    parser.add_argument("--playwright-timeout-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        receipt = run_live_monitor_acceptance(args)
    except BaseException as exc:  # noqa: BLE001 - only bounded public failure state escapes
        failed = {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "error_code": f"{type(exc).__name__.upper()}_WITHOUT_RAW_DIAGNOSTIC",
            "privacy": dict(_PRIVACY),
        }
        failed["receipt_digest"] = _digest(failed)
        _write_json(Path(args.output), failed)
        print(json.dumps({"status": "FAIL", "output": str(args.output)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "receipt_digest": receipt["receipt_digest"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
