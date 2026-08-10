"""Run six authenticated fault/barge cases against a real Edge process.

The target Edge must already be running with its production TTS/playback and
barge-in components.  This runner neither starts an in-process Edge nor swaps
transport, synthesis, playback, or receipts with test doubles.  The HMAC key
is read from the environment or its configured local secret reference and is
never written to the report.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping
import urllib.request
import uuid


def _build_envelope(
    *,
    request_id: str,
    payload: Mapping[str, Any],
    shared_key: str,
    ts_ms: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    timestamp = int(ts_ms if ts_ms is not None else time.time() * 1000)
    nonce_value = str(nonce or uuid.uuid4())
    body = dict(payload)
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    material = f"{request_id}\n{timestamp}\n{nonce_value}\n{canonical}".encode()
    signature = hmac.new(
        shared_key.encode("utf-8"), material, hashlib.sha256
    ).hexdigest()
    return {
        "request_id": request_id,
        "ts_ms": timestamp,
        "nonce": nonce_value,
        "signature": signature,
        "payload": body,
    }


def _send_json(
    *,
    base_url: str,
    path: str,
    body: Mapping[str, Any],
    timeout_s: float = 30.0,
) -> tuple[dict[str, Any], float]:
    raw = json.dumps(
        dict(body), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - operator-selected Edge
        f"{base_url.rstrip('/')}{path}",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        result = json.load(response)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    if not isinstance(result, dict):
        raise RuntimeError("Edge response is not a JSON object")
    return dict(result), elapsed_ms


def _post_signed(
    *,
    base_url: str,
    path: str,
    payload: Mapping[str, Any],
    request_prefix: str,
    shared_key: str,
    timeout_s: float = 30.0,
) -> tuple[str, dict[str, Any], float]:
    request_id = f"{request_prefix}{uuid.uuid4().hex}"
    result, elapsed_ms = _send_json(
        base_url=base_url,
        path=path,
        body=_build_envelope(
            request_id=request_id,
            payload=payload,
            shared_key=shared_key,
        ),
        timeout_s=timeout_s,
    )
    return request_id, result, elapsed_ms


def _json_get(url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Edge response is not a JSON object")
    return dict(payload)


def _health_summary(health: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(health.get("event") or {})
    components = dict(event.get("components") or {})
    services = dict(event.get("services") or {})
    resources = dict(event.get("resources") or {})
    tts = dict(dict(components.get("tts") or {}).get("health") or {})
    playback = dict(
        dict(components.get("tts_playback_device") or {}).get("health") or {}
    )
    return {
        "edge_status": str(event.get("status") or ""),
        "tts_state": str(tts.get("state") or ""),
        "tts_backend": str(tts.get("backend") or ""),
        "playback_playing": bool(playback.get("playing")),
        "playback_error": playback.get("last_playback_error"),
        "queue_depth": int(resources.get("queue_depth") or 0),
        "barge_in_available": bool(
            dict(services.get("edge.rpc.barge_in") or {}).get("available")
        ),
    }


def _response_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(result.get("event") or {})
    return {
        "success": bool(result.get("success")),
        "status": str(result.get("status") or ""),
        "reason_code": result.get("reason_code"),
        "detail": str(result.get("detail") or ""),
        "stage": str(event.get("stage") or ""),
        "event_type": str(event.get("event_type") or ""),
        "synchronization_id": str(event.get("synchronization_id") or ""),
        "insecure_auth": bool(event.get("insecure_auth")),
    }


def _wait_drained(base_url: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    summary: dict[str, Any] = {}
    while time.monotonic() < deadline:
        summary = _health_summary(
            _json_get(f"{base_url.rstrip('/')}/v1/edge/health")
        )
        if not summary["playback_playing"] and summary["queue_depth"] == 0:
            return summary
        time.sleep(0.1)
    return summary


def _git_identity(repo_root: Path) -> tuple[str | None, str | None]:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        diff = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "--binary", "HEAD"]
        )
        return commit, hashlib.sha256(diff).hexdigest()
    except Exception:
        return None, None


def _case(
    case_id: str,
    name: str,
    *,
    passed: bool,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "checks_passed": bool(passed),
        "evidence": dict(evidence),
    }


def run_matrix(*, base_url: str, repo_root: Path) -> dict[str, Any]:
    shared_key = str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip()
    if not shared_key:
        raise RuntimeError("EDGE_HMAC_SHARED_KEY is required")
    base = base_url.rstrip("/")
    pre_health = _health_summary(_json_get(f"{base}/v1/edge/health"))
    if pre_health["playback_playing"] or pre_health["queue_depth"]:
        raise RuntimeError(f"Edge voice queue is not idle before probe: {pre_health}")
    cases: list[dict[str, Any]] = []

    schema_result, schema_ms = _send_json(
        base_url=base,
        path="/v1/edge/barge_in",
        body={},
    )
    schema = _response_summary(schema_result)
    cases.append(
        _case(
            "EDGE-FB-01",
            "schema-invalid envelope is rejected before side effects",
            passed=(
                not schema["success"]
                and schema["reason_code"] == "EDGE_RPC_SCHEMA_INVALID"
                and schema["stage"] == "S_EDGE_RPC_SCHEMA"
            ),
            evidence={"elapsed_ms": schema_ms, **schema},
        )
    )

    bad_auth_id = f"req_a22_bad_auth_{uuid.uuid4().hex}"
    bad_auth = _build_envelope(
        request_id=bad_auth_id,
        payload={"session_id": "a22-bad-auth", "source": "acceptance"},
        shared_key=shared_key,
    )
    bad_auth["signature"] = "0" * 64
    bad_auth_result, bad_auth_ms = _send_json(
        base_url=base,
        path="/v1/edge/barge_in",
        body=bad_auth,
    )
    auth = _response_summary(bad_auth_result)
    cases.append(
        _case(
            "EDGE-FB-02",
            "invalid HMAC is rejected before side effects",
            passed=(
                not auth["success"]
                and auth["reason_code"] == "EDGE_RPC_AUTH_INVALID"
                and auth["stage"] == "S_EDGE_RPC_AUTH"
                and not auth["insecure_auth"]
            ),
            evidence={"request_id": bad_auth_id, "elapsed_ms": bad_auth_ms, **auth},
        )
    )

    replay_id = f"req_a22_replay_{uuid.uuid4().hex}"
    replay_envelope = _build_envelope(
        request_id=replay_id,
        payload={"action": "status"},
        shared_key=shared_key,
    )
    replay_first_result, replay_first_ms = _send_json(
        base_url=base,
        path="/v1/edge/vts",
        body=replay_envelope,
    )
    replay_second_result, replay_second_ms = _send_json(
        base_url=base,
        path="/v1/edge/vts",
        body=replay_envelope,
    )
    replay_first = _response_summary(replay_first_result)
    replay_second = _response_summary(replay_second_result)
    cases.append(
        _case(
            "EDGE-FB-03",
            "nonce replay is rejected after one authenticated read-only call",
            passed=(
                replay_first["success"]
                and not replay_first["insecure_auth"]
                and not replay_second["success"]
                and replay_second["reason_code"] == "REPLAY_DETECTED"
                and replay_second["stage"] == "S_EDGE_RPC_AUTH"
            ),
            evidence={
                "request_id": replay_id,
                "first_elapsed_ms": replay_first_ms,
                "second_elapsed_ms": replay_second_ms,
                "first": replay_first,
                "second": replay_second,
            },
        )
    )

    malformed_id, malformed_result, malformed_ms = _post_signed(
        base_url=base,
        path="/v1/edge/barge_in",
        payload={"session_id": "a22-malformed-barge"},
        request_prefix="req_a22_malformed_barge_",
        shared_key=shared_key,
    )
    malformed = _response_summary(malformed_result)
    cases.append(
        _case(
            "EDGE-FB-04",
            "malformed barge payload fails closed",
            passed=(
                not malformed["success"]
                and malformed["reason_code"] == "VOICE_SCHEMA_INVALID"
                and malformed["stage"] == "S_VOICE_BARGE_IN"
            ),
            evidence={"request_id": malformed_id, "elapsed_ms": malformed_ms, **malformed},
        )
    )

    idle_session = f"a22-idle-barge-{uuid.uuid4().hex[:12]}"
    idle_id, idle_result, idle_ms = _post_signed(
        base_url=base,
        path="/v1/edge/barge_in",
        payload={"session_id": idle_session, "source": "acceptance.idle", "seq": 1},
        request_prefix="req_a22_idle_barge_",
        shared_key=shared_key,
    )
    idle = _response_summary(idle_result)
    idle_health = _wait_drained(base, timeout_s=2.0)
    cases.append(
        _case(
            "EDGE-FB-05",
            "idle barge is an authenticated no-op without queue leakage",
            passed=(
                idle["success"]
                and idle["reason_code"] is None
                and idle["stage"] == "S_VOICE_BARGE_IN"
                and not idle_health["playback_playing"]
                and idle_health["queue_depth"] == 0
            ),
            evidence={
                "request_id": idle_id,
                "session_id": idle_session,
                "elapsed_ms": idle_ms,
                "response": idle,
                "post_health": idle_health,
            },
        )
    )

    active_session = f"a22-active-barge-{uuid.uuid4().hex[:12]}"
    marker = uuid.uuid4().hex
    synchronization_id = f"sync_a22_edge_fault_barge_{marker}"
    speak_id, speak_result, speak_ms = _post_signed(
        base_url=base,
        path="/v1/edge/speak",
        payload={
            "session_id": active_session,
            "utterance_id": f"utt_{marker}",
            "text": (
                "ねえ、今日は少し長く話そう。急がなくていいから、あなたの気持ちを"
                "ひとつずつ聞かせて。私はここで静かに受け止めているから。"
            ),
            "lang_hint": "ja",
            "emotion_tag": "QUIET_PRESENCE",
            "emotion_intensity": 0.64,
            "synchronization_id": synchronization_id,
            "wait_playback_start": True,
        },
        request_prefix="req_a22_active_speak_",
        shared_key=shared_key,
        timeout_s=300.0,
    )
    speak = _response_summary(speak_result)
    time.sleep(0.35)
    barge_id, barge_result, barge_ms = _post_signed(
        base_url=base,
        path="/v1/edge/barge_in",
        payload={
            "session_id": active_session,
            "source": "acceptance.manual_interrupt",
            "seq": 2,
        },
        request_prefix="req_a22_active_barge_",
        shared_key=shared_key,
    )
    barge = _response_summary(barge_result)
    active_health = _wait_drained(base)
    cases.append(
        _case(
            "EDGE-FB-06",
            "real playback is interrupted and synchronization scope is preserved",
            passed=(
                pre_health["tts_state"] == "READY"
                and pre_health["tts_backend"] == "gpt_sovits"
                and pre_health["barge_in_available"]
                and speak["success"]
                and speak["event_type"] == "TTS_PLAYBACK_STARTED"
                and speak["synchronization_id"] == synchronization_id
                and barge["success"]
                and barge["reason_code"] == "VOICE_INTERRUPTED"
                and not active_health["playback_playing"]
                and active_health["queue_depth"] == 0
                and active_health["playback_error"] is None
            ),
            evidence={
                "session_id": active_session,
                "expected_synchronization_id": synchronization_id,
                "speak_request_id": speak_id,
                "speak_elapsed_ms": speak_ms,
                "speak": speak,
                "barge_request_id": barge_id,
                "barge_elapsed_ms": barge_ms,
                "barge": barge,
                "post_health": active_health,
                "physical_playback_commanded": True,
            },
        )
    )

    commit, diff_sha = _git_identity(repo_root.resolve())
    report: dict[str, Any] = {
        "schema_version": "natsume.a22.edge-fault-barge-matrix.v1",
        "status": "PASS" if len(cases) == 6 and all(row["status"] == "PASS" for row in cases) else "FAIL",
        "repository_commit": commit,
        "worktree_diff_sha256": diff_sha,
        "real_transport": True,
        "mock_transport": False,
        "physical_playback_commanded": True,
        "physical_playback_observation": "Edge reported TTS_PLAYBACK_STARTED; no human listening claim is made.",
        "base_url": base,
        "pre_health": pre_health,
        "case_count": len(cases),
        "passed_case_count": sum(row["status"] == "PASS" for row in cases),
        "cases": cases,
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def _read_env_value(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _resolve_hmac_key(*, env_file: Path, repo_root: Path) -> str:
    direct = _read_env_value(env_file, "EDGE_HMAC_SHARED_KEY")
    if direct:
        return direct
    secret_ref = _read_env_value(env_file, "EDGE_HMAC_SHARED_KEY_REF")
    if not secret_ref:
        return ""
    for candidate in (
        repo_root / "configs" / "secrets" / "secret_store.runtime.local.json",
        repo_root / "configs" / "secrets" / "secret_store.runtime.json",
    ):
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            continue
        resolved = str(payload.get(secret_ref) or "").strip()
        if resolved:
            return resolved
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:17800")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Optional env file used only to load EDGE_HMAC_SHARED_KEY or its "
            "local secret-store reference."
        ),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    injected_key = False
    if not str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip():
        if args.env_file is not None:
            key = _resolve_hmac_key(
                env_file=args.env_file.resolve(),
                repo_root=args.repo_root.resolve(),
            )
            if key:
                os.environ["EDGE_HMAC_SHARED_KEY"] = key
                injected_key = True
    try:
        report = run_matrix(base_url=args.base_url, repo_root=args.repo_root)
    finally:
        if injected_key:
            os.environ.pop("EDGE_HMAC_SHARED_KEY", None)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
