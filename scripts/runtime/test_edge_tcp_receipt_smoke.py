"""Run a real, loopback-only Host-to-Edge durable receipt smoke test.

This is deliberately narrower than an end-to-end hardware test.  It starts an
actual ``python -m edge_client`` child process, signs genuine HTTP requests
through the Host transport adapter, and inspects the Edge-owned SQLite journal.
The only embodiment action is a ``mateengine/health`` probe pointed at an
unbound loopback port.  Its expected result is therefore
``DEGRADED/MATEENGINE_UNAVAILABLE``: the test proves the delivery and receipt
path without claiming a physical device effect or starting QQ, Gateway, ASR,
TTS, VTS, or a fake hardware service.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Mapping
import urllib.request
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SmokeFailure(RuntimeError):
    """A deterministic smoke assertion failure without sensitive diagnostics."""


@dataclass(slots=True)
class _EdgeChild:
    process: subprocess.Popen[str]
    log_path: Path


def _available_loopback_port(*, excluded: set[int] | None = None) -> int:
    blocked = excluded or set()
    for _ in range(32):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in blocked:
            return port
    raise SmokeFailure("unable to allocate a distinct loopback port")


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _edge_environment(
    *,
    edge_port: int,
    inert_peripheral_port: int,
    work_dir: Path,
    shared_key: str,
    mateengine_secret: str,
) -> dict[str, str]:
    data_dir = work_dir / "edge-data"
    embodiment_db = work_dir / "edge-embodiment.sqlite3"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Explicitly blank every optional platform credential/endpoint that can be
    # inherited from an operator shell.  The child process never reads .env;
    # it receives only this isolated environment overlay.
    disabled_values = {
        "EDGE_ASR_ENABLED": "false",
        "EDGE_TTS_ENABLED": "false",
        "EDGE_BARGE_IN_ENABLED": "false",
        "EDGE_FALLBACK_LLM_ENABLED": "false",
        "EDGE_RESOURCE_GATE_ENABLED": "false",
        "EDGE_GATEWAY_ENABLED": "false",
        "EDGE_GATEWAY_PLATFORMS": "",
        "EDGE_EVENT_REPORT_HOST_URL": "",
        "EDGE_QQ_ONEBOT_AUTOSTART": "false",
        "EDGE_QQ_ONEBOT_KILL_QQ_BEFORE_START": "false",
        "EDGE_QQ_ONEBOT_FORCE_CLEAN_START": "false",
        "QQ_ONEBOT_API_BASE": "",
        "EDGE_QQ_ONEBOT_HEALTH_URL": "",
        "EDGE_QQ_ONEBOT_LAUNCHER_PATH": "",
        "EDGE_QQ_ONEBOT_LAUNCHER_ARGS": "",
        "EDGE_VTS_AUTOSTART": "false",
        "EDGE_FACE_CAPTURE_ENABLED": "false",
        "EDGE_AVATAR_BACKEND": "vrm",
        "EDGE_VRM_MIRROR_VTS_ENABLED": "false",
        "EDGE_VRM_BRIDGE_HOST": "127.0.0.1",
        "EDGE_VRM_BRIDGE_PORT": str(inert_peripheral_port),
        "EDGE_VRM_BRIDGE_TIMEOUT_S": "0.2",
        "EDGE_VRM_BRIDGE_MOUTH_UDP_ENABLED": "false",
        "EDGE_VRM_MODEL_PATH": "",
        "EDGE_MATEENGINE_BRIDGE_BASE_URL": (
            f"http://127.0.0.1:{inert_peripheral_port}"
        ),
        "EDGE_MATEENGINE_BRIDGE_TIMEOUT_S": "0.2",
        "EDGE_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL": "d8.tcp.receipt.smoke",
        "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": "d8-tcp-smoke",
        "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": mateengine_secret,
        "WECHAT_SELF_WXID": "",
        "WECHAT_DB_STORAGE_DIR": "",
        "WECHAT_DECRYPT_OUTPUT_DIR": "",
        "WECHAT_KEY_CACHE_PATH": "",
        "FEISHU_APP_ID": "",
        "FEISHU_APP_SECRET": "",
        "FEISHU_ENCRYPT_KEY": "",
        "FEISHU_VERIFICATION_TOKEN": "",
        "WECOM_CORP_ID": "",
        "WECOM_CORP_SECRET": "",
        "WECOM_AGENT_ID": "",
        "WECOM_TOKEN": "",
        "WECOM_ENCODING_AES_KEY": "",
        "TELEGRAM_API_ID": "",
        "TELEGRAM_API_HASH": "",
        "TELEGRAM_PHONE": "",
        "LINE_CHANNEL_ACCESS_TOKEN": "",
        "LINE_CHANNEL_SECRET": "",
    }
    return {
        "Natsume_ENV": "prod",
        "EDGE_RPC_HOST": "127.0.0.1",
        "EDGE_RPC_PORT": str(edge_port),
        "EDGE_RPC_GATEWAY_ENABLED": "true",
        "EDGE_ALLOW_INSECURE_AUTH": "false",
        "EDGE_HMAC_SHARED_KEY": shared_key,
        "NATSUME_DATA_DIR": str(data_dir),
        "EDGE_EMBODIMENT_TRANSPORT_DB": str(embodiment_db),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "http_proxy": "",
        "https_proxy": "",
        **disabled_values,
    }


def _edge_process_environment(values: Mapping[str, str]) -> dict[str, str]:
    child_env = dict(os.environ)
    child_env.update(values)
    return child_env


def _read_json(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_s) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SmokeFailure("Edge health response is not a JSON object")
    return dict(payload)


def _tail_log(path: Path, *, limit: int = 2_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _wait_for_edge_health(
    *, child: _EdgeChild, base_url: str, timeout_s: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = "health endpoint did not answer"
    while time.monotonic() < deadline:
        if child.process.poll() is not None:
            log_tail = _tail_log(child.log_path)
            raise SmokeFailure(
                "Edge child exited before health became available "
                f"(exit={child.process.returncode}): {log_tail[-800:]}"
            )
        try:
            payload = _read_json(f"{base_url}/v1/edge/health", timeout_s=3.0)
            if bool(payload.get("success")) and str(payload.get("status")) == "ACCEPTED":
                return payload
            last_error = f"unexpected health payload status={payload.get('status')}"
        except Exception as exc:  # normal while Uvicorn binds/listens
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.15)
    raise SmokeFailure(f"Edge health timeout: {last_error}; log={_tail_log(child.log_path)[-800:]}")


def _start_edge_child(
    *, root: Path, work_dir: Path, env_values: Mapping[str, str], ordinal: int, timeout_s: float
) -> tuple[_EdgeChild, dict[str, Any]]:
    edge_port = int(env_values["EDGE_RPC_PORT"])
    log_path = work_dir / f"edge-{ordinal}.log"
    log_file = log_path.open("w", encoding="utf-8", buffering=1)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "edge_client",
                "--host",
                "127.0.0.1",
                "--port",
                str(edge_port),
            ],
            cwd=str(root),
            env=_edge_process_environment(env_values),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        log_file.close()
    child = _EdgeChild(process=process, log_path=log_path)
    return child, _wait_for_edge_health(
        child=child,
        base_url=f"http://127.0.0.1:{edge_port}",
        timeout_s=timeout_s,
    )


def _stop_edge_child(child: _EdgeChild | None) -> None:
    if child is None or child.process.poll() is not None:
        return
    child.process.terminate()
    try:
        child.process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.process.kill()
        child.process.wait(timeout=8)


def _assert_minimal_profile(health: Mapping[str, Any], *, edge_port: int) -> dict[str, Any]:
    event = health.get("event")
    if not isinstance(event, Mapping):
        raise SmokeFailure("Edge health omitted event payload")
    snapshot = event.get("config_snapshot")
    if not isinstance(snapshot, Mapping):
        raise SmokeFailure("Edge health omitted redacted config snapshot")
    expected = {
        "env": "prod",
        "rpc_host": "127.0.0.1",
        "rpc_port": edge_port,
        "allow_insecure_auth": False,
        "rpc_gateway_enabled": True,
        "asr_enabled": False,
        "tts_enabled": False,
        "barge_in_enabled": False,
        "fallback_llm_enabled": False,
        "resource_gate_enabled": False,
        "gateway_enabled": False,
        "avatar_backend": "vrm",
    }
    mismatches = {
        key: {"expected": value, "actual": snapshot.get(key)}
        for key, value in expected.items()
        if snapshot.get(key) != value
    }
    if mismatches:
        raise SmokeFailure(f"Edge minimal profile mismatch: {mismatches}")
    if snapshot.get("qq_onebot_api_base") is not None:
        raise SmokeFailure("minimal profile unexpectedly retains QQ OneBot endpoint")
    return dict(snapshot)


async def _submit_and_query_receipt(
    *, base_url: str, shared_key: str, request_suffix: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any, Any]:
    from host_brain.channels.edge_transport import HttpEdgeTransportPort
    from host_brain.channels.embodiment import (
        EdgeEmbodimentSessionConfig,
        EdgeRpcEmbodimentCommandPort,
        build_host_embodiment_command,
    )
    from natsume_contracts.edge.embodiment_v1 import build_embodiment_receipt_query

    transport = HttpEdgeTransportPort(base_url=base_url, shared_key=shared_key)
    command_port = EdgeRpcEmbodimentCommandPort(
        post_to_edge=transport.submit,
        session_config=EdgeEmbodimentSessionConfig(
            session_id=f"d8-tcp-smoke-{request_suffix}",
            owner_id="d8.tcp.receipt.smoke",
            ttl_ms=90_000,
        ),
    )
    request_id = f"d8-tcp-smoke-{request_suffix}"
    command = build_host_embodiment_command(
        kind="mateengine",
        action="health",
        payload={},
        request_id=request_id,
        idempotency_key=f"d8-tcp-smoke:{request_suffix}",
        deadline_ms=int(time.time() * 1000) + 60_000,
    )
    first = await command_port.submit(command)
    duplicate = await command_port.submit(command)
    if (
        first.status != "DEGRADED"
        or first.reason_code != "MATEENGINE_UNAVAILABLE"
        or first.accepted
    ):
        raise SmokeFailure(
            "expected safe unbound MateEngine receipt "
            f"DEGRADED/MATEENGINE_UNAVAILABLE, got {first.as_dict()}"
        )
    if duplicate.as_dict() != first.as_dict():
        raise SmokeFailure("duplicate command did not return the exact durable receipt")

    lease = getattr(command_port, "_transport_lease", None)
    if lease is None:
        raise SmokeFailure("Host command port did not retain its Edge lease")
    query = build_embodiment_receipt_query(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        session_id=lease.session_id,
        lease_id=lease.lease_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
    )
    lookup = await transport.submit(
        path="/v1/edge/embodiment/receipt",
        payload=query.as_dict(),
        request_id=f"{request_id}-receipt",
        timeout_ms=5_000,
    )
    if not lookup.success:
        raise SmokeFailure(f"durable receipt query failed: {lookup.reason_code}")
    receipt_payload = dict(lookup.event or {}).get("receipt")
    if not isinstance(receipt_payload, Mapping) or dict(receipt_payload) != first.as_dict():
        raise SmokeFailure("durable receipt query did not return the exact first receipt")
    return first.as_dict(), duplicate.as_dict(), dict(receipt_payload), command, command_port


def _inspect_edge_sqlite(
    *, db_path: Path, command_id: str, expected_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    if not db_path.is_file():
        raise SmokeFailure(f"Edge embodiment SQLite was not created: {db_path}")
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT status, receipt_json, receipt_sha256 FROM edge_embodiment_command "
            "WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        count = int(
            connection.execute("SELECT COUNT(*) FROM edge_embodiment_command").fetchone()[0]
        )
    finally:
        connection.close()
    if row is None:
        raise SmokeFailure("Edge embodiment SQLite has no command row for smoke request")
    status, raw_receipt, stored_digest = row
    if count != 1:
        raise SmokeFailure(f"expected exactly one Edge command row, found {count}")
    if str(status) != "REJECTED":
        raise SmokeFailure(f"expected rejected safe device probe row, got {status}")
    receipt_json = str(raw_receipt or "")
    digest = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
    if digest != str(stored_digest or ""):
        raise SmokeFailure("Edge stored receipt SHA-256 does not match receipt JSON")
    parsed_receipt = json.loads(receipt_json)
    if not isinstance(parsed_receipt, dict) or parsed_receipt != dict(expected_receipt):
        raise SmokeFailure("Edge SQLite receipt differs from Host-observed receipt")
    return {
        "db_path": str(db_path),
        "command_row_count": count,
        "command_status": str(status),
        "receipt_sha256_verified": True,
    }


def run_smoke(
    *, root: Path | str = REPO_ROOT, timeout_s: float = 45.0, work_dir: Path | str | None = None
) -> dict[str, Any]:
    """Run the real TCP/HMAC/SQLite smoke and return secret-free evidence."""

    repo_root = Path(root).resolve()
    if not (repo_root / "edge_client" / "__main__.py").is_file():
        raise SmokeFailure(f"invalid repository root: {repo_root}")
    if timeout_s < 5:
        raise ValueError("timeout_s must be at least 5 seconds")

    owns_temp_dir = work_dir is None
    temp_context = tempfile.TemporaryDirectory(prefix="natsume-d8-edge-smoke-") if owns_temp_dir else None
    active_work_dir = Path(temp_context.name if temp_context is not None else work_dir).resolve()
    active_work_dir.mkdir(parents=True, exist_ok=True)

    child: _EdgeChild | None = None
    restarted_child: _EdgeChild | None = None
    try:
        ports: set[int] = set()
        edge_port = _available_loopback_port(excluded=ports)
        ports.add(edge_port)
        inert_port = _available_loopback_port(excluded=ports)
        ports.add(inert_port)
        shared_key = secrets.token_urlsafe(48)
        mateengine_secret = secrets.token_urlsafe(48)
        env_values = _edge_environment(
            edge_port=edge_port,
            inert_peripheral_port=inert_port,
            work_dir=active_work_dir,
            shared_key=shared_key,
            mateengine_secret=mateengine_secret,
        )
        base_url = f"http://127.0.0.1:{edge_port}"
        request_suffix = uuid.uuid4().hex

        with _temporary_environment(env_values):
            child, health = _start_edge_child(
                root=repo_root,
                work_dir=active_work_dir,
                env_values=env_values,
                ordinal=1,
                timeout_s=timeout_s,
            )
            snapshot = _assert_minimal_profile(health, edge_port=edge_port)
            first, duplicate, queried, command, command_port = asyncio.run(
                _submit_and_query_receipt(
                    base_url=base_url,
                    shared_key=shared_key,
                    request_suffix=request_suffix,
                )
            )
            sqlite_evidence = _inspect_edge_sqlite(
                db_path=Path(env_values["EDGE_EMBODIMENT_TRANSPORT_DB"]),
                command_id=command.command_id,
                expected_receipt=first,
            )

            _stop_edge_child(child)
            child = None
            restarted_child, restarted_health = _start_edge_child(
                root=repo_root,
                work_dir=active_work_dir,
                env_values=env_values,
                ordinal=2,
                timeout_s=timeout_s,
            )
            _assert_minimal_profile(restarted_health, edge_port=edge_port)
            replay_after_restart = asyncio.run(command_port.submit(command)).as_dict()
            if replay_after_restart != first:
                raise SmokeFailure("post-restart replay did not return the exact durable receipt")
            sqlite_evidence = _inspect_edge_sqlite(
                db_path=Path(env_values["EDGE_EMBODIMENT_TRANSPORT_DB"]),
                command_id=command.command_id,
                expected_receipt=first,
            )

        return {
            "status": "PASS",
            "smoke_kind": "real_loopback_tcp_hmac_host_transport_edge_sqlite_receipt",
            "endpoint": base_url,
            "edge_child_processes": 2,
            "host_transport": "HttpEdgeTransportPort + EdgeRpcEmbodimentCommandPort",
            "minimal_profile": {
                "bind_host": "127.0.0.1",
                "edge_rpc_gateway_enabled": True,
                "asr_tts_gateway_qq_vts_disabled": True,
                "isolated_sqlite": sqlite_evidence["db_path"],
                "config_env": snapshot.get("env"),
            },
            "receipt": {
                "command_id": first["command_id"],
                "status": first["status"],
                "reason_code": first["reason_code"],
                "duplicate_exact": duplicate == first,
                "read_query_exact": queried == first,
                "restart_replay_exact": replay_after_restart == first,
                "physical_effect_status": "NOT_TESTED",
                "physical_effect_verified": False,
                "safe_expected_device_result": "DEGRADED/MATEENGINE_UNAVAILABLE",
            },
            "durability": sqlite_evidence,
        }
    finally:
        _stop_edge_child(restarted_child)
        _stop_edge_child(child)
        if temp_context is not None:
            temp_context.cleanup()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument(
        "--work-dir",
        default="",
        help="Optional directory for isolated SQLite and child logs; not deleted.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_smoke(
            timeout_s=float(args.timeout_seconds),
            work_dir=(Path(args.work_dir) if str(args.work_dir).strip() else None),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "smoke_kind": "real_loopback_tcp_hmac_host_transport_edge_sqlite_receipt",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "physical_effect_status": "NOT_TESTED",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
