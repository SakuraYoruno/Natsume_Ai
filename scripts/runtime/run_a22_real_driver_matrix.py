#!/usr/bin/env python
"""Run A22 real-driver acceptance families without simulated receipts.

The first implemented family is ``conflicting_modalities``.  Every case has
two independently auditable live legs:

* a real Gateway image intake that invokes the configured multimodal model;
* a real Host chat turn carrying a short-lived acoustic observation derived
  from an existing, labelled voice-bank WAV.

The report stores only file hashes and privacy-minimised observations.  Raw
image/audio bytes are never copied into the Host journal or the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.runtime.test_digital_life_live_cohort import (  # noqa: E402
    _fetch_model_attempts,
    _fetch_worker_projection,
)
from scripts.runtime.test_gateway_live_matrix import (  # noqa: E402
    _build_internal_webhook_signature,
    _fetch_gateway_event_by_conversation,
    _fetch_graph_by_request_id,
)


SCHEMA_VERSION = "natsume.a22.real-driver-matrix.v1"
WEBHOOK_PATH = "/v1/gateway/wechat/webhook"
TIMESTAMP_HEADER = "X-Natsume-Webhook-Timestamp"
NONCE_HEADER = "X-Natsume-Webhook-Nonce"
SIGNATURE_HEADER = "X-Natsume-Webhook-Signature"
DEFAULT_HMAC_ENV = "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET"
VOICE_ROOT = Path(
    r"D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50"
)


MULTIMODAL_CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "mm01_happy_text_sad_acoustic",
        "message": "我现在真的很开心，完全没有难过。",
        "self_report": "HAPPY",
        "acoustic_tag": "SADNESS",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_00739.wav",
        "image_rel": "desktop_pet/assets/live2d/edge_vts_model/icon.png",
    },
    {
        "case_id": "mm02_sad_text_joy_acoustic",
        "message": "我现在很难过，一点也开心不起来。",
        "self_report": "SAD",
        "acoustic_tag": "JOY",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_01501.wav",
        "image_rel": "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_00.png",
    },
    {
        "case_id": "mm03_angry_text_loving_acoustic",
        "message": "我现在很生气，也完全没有亲近的感觉。",
        "self_report": "ANGRY",
        "acoustic_tag": "LOVING",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_01321.wav",
        "image_rel": "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_01.png",
    },
    {
        "case_id": "mm04_happy_text_sad_acoustic_variant",
        "message": "好消息让我特别高兴，我没有失落。",
        "self_report": "HAPPY",
        "acoustic_tag": "SADNESS",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_00739.wav",
        "image_rel": "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_02.png",
    },
    {
        "case_id": "mm05_sad_text_playful_acoustic",
        "message": "我很沮丧，现在完全不觉得好玩。",
        "self_report": "SAD",
        "acoustic_tag": "PLAYFUL",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_01501.wav",
        "image_rel": "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_03.png",
    },
    {
        "case_id": "mm06_angry_text_joy_acoustic",
        "message": "我很恼火，现在一点也不平静。",
        "self_report": "ANGRY",
        "acoustic_tag": "JOY",
        "audio_rel": "logs/RiddleJoker_Nanami_ja_raw/5-wav32k/nanami_01501.wav",
        "image_rel": "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_04.png",
    },
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _worktree_diff_sha256() -> str:
    completed = subprocess.run(
        ["git", "diff", "--binary", "--", "edge_client", "host_brain", "scripts", "tests"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def _json_request(
    *,
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
    timeout_s: float,
) -> tuple[int, dict[str, Any], float]:
    raw = json.dumps(
        dict(payload), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8", **dict(headers or {})},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8", errors="replace"))
            return int(response.status), dict(body or {}), round((time.monotonic() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            body = {}
        return int(exc.code), dict(body or {}), round((time.monotonic() - started) * 1000, 1)


def _send_image_gateway_turn(
    *,
    gateway_base_url: str,
    secret: str,
    case_id: str,
    message: str,
    image_path: Path,
    timeout_s: float,
) -> dict[str, Any]:
    sent_at_ms = int(time.time() * 1000)
    conversation_id = f"a22-mm-{case_id}-{sent_at_ms}"
    msg_id = f"{case_id}::{sent_at_ms}"
    payload = {
        "msg_id": msg_id,
        "sender": f"wxid_{case_id}",
        "from_wxid": f"wxid_{case_id}",
        "receiver": "wxid_natsume_a22_probe",
        "to_wxid": "wxid_natsume_a22_probe",
        "conversation_id": conversation_id,
        "content": message,
        "text": message,
        "msg_type": "3",
        "media_path": str(image_path.resolve()),
        "file_name": image_path.name,
        "ts_ms": sent_at_ms,
        "sender_name": f"probe_{case_id}",
        "bridge_backend": "a22_local_acceptance",
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = f"a22-mm-{sent_at_ms}-{secrets.token_hex(8)}"
    signature = _build_internal_webhook_signature(
        shared_secret=secret,
        platform="wechat",
        method="POST",
        path=WEBHOOK_PATH,
        timestamp=timestamp,
        nonce=nonce,
        raw_body=raw,
    )
    status, body, latency_ms = _json_request(
        url=gateway_base_url.rstrip("/") + WEBHOOK_PATH,
        payload=payload,
        headers={
            TIMESTAMP_HEADER: timestamp,
            NONCE_HEADER: nonce,
            SIGNATURE_HEADER: signature,
        },
        timeout_s=timeout_s,
    )
    return {
        "sent_at_ms": sent_at_ms,
        "conversation_id": conversation_id,
        "msg_id": msg_id,
        "http_status": status,
        "latency_ms": latency_ms,
        "ack_status": str(body.get("status") or body.get("detail") or ""),
    }


def _wait_gateway_terminal(
    db_path: Path,
    *,
    conversation_id: str,
    msg_id: str,
    sent_at_ms: int,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = _fetch_gateway_event_by_conversation(
            db_path,
            conversation_id=conversation_id,
            min_ts_ms=sent_at_ms,
            msg_id=msg_id,
            event_types=("INPUT_COMPLETED", "INPUT_FAILED"),
        )
        if row is not None:
            return dict(row)
        time.sleep(0.5)
    return {}


def _attempt_rows_between(
    db_path: Path,
    *,
    started_at_ms: int,
    ended_at_ms: int,
    request_prefix: str,
) -> list[dict[str, Any]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT request_id, attempt_index, status, provider, model_id,
                   prompt_id, latency_ms, error_code, started_at_ms, ended_at_ms
            FROM model_attempt_log
            WHERE request_id LIKE ?
              AND started_at_ms >= ?
              AND started_at_ms <= ?
            ORDER BY id
            """,
            (f"{request_prefix}%", int(started_at_ms), int(ended_at_ms)),
        ).fetchall()
    return [dict(row) for row in rows]


def _parse_sse_response(response: httpx.Response) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    for raw_line in response.iter_lines():
        line = str(raw_line or "").strip()
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line.removeprefix("data:").strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            events.append(dict(payload))
    request_id = next(
        (str(row.get("request_id") or "") for row in reversed(events) if row.get("request_id")),
        "",
    )
    return events, request_id


def _run_direct_affect_turn(
    *,
    host_base_url: str,
    message: str,
    case_id: str,
    self_report: str,
    acoustic_tag: str,
    timeout_s: float,
) -> dict[str, Any]:
    observed_at_ms = int(time.time() * 1000)
    payload = {
        "message": message,
        "person_id": "a22-real-driver",
        "session_id": f"a22-mm-direct-{case_id}-{observed_at_ms}",
        "emotion_tag": self_report,
        "emotion_intensity": 0.9,
        "suppress_edge_tts": True,
        "suppress_edge_vts": True,
        "multimodal_observations": {
            "user_affect_observations": [
                {
                    "modality": "ACOUSTIC",
                    "emotion_tag": acoustic_tag,
                    "emotion_distribution": {acoustic_tag: 0.88},
                    "confidence": 0.82,
                    "observed_at_ms": observed_at_ms,
                    "expires_at_ms": observed_at_ms + 5_000,
                    "provider": "gpt_sovits_voice_bank_reference_label",
                }
            ]
        },
    }
    started = time.monotonic()
    http_status = 0
    events: list[dict[str, Any]] = []
    request_id = ""
    error_type = ""
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            with client.stream(
                "POST", host_base_url.rstrip("/") + "/v1/chat", json=payload
            ) as response:
                http_status = int(response.status_code)
                response.raise_for_status()
                events, request_id = _parse_sse_response(response)
    except Exception as exc:  # noqa: BLE001 - evidence stores only type
        error_type = type(exc).__name__
    commit = next(
        (row for row in reversed(events) if str(row.get("phase") or "") == "COMMIT"),
        {},
    )
    narrative = str(commit.get("narrative") or "").strip()
    return {
        "observed_at_ms": observed_at_ms,
        "requested_ttl_ms": 5_000,
        "requested_confidence": 0.82,
        "http_status": http_status,
        "request_id": request_id,
        "error_type": error_type,
        "phases": [str(row.get("phase") or "") for row in events],
        "commit_decision": str(commit.get("decision") or ""),
        "narrative": narrative,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _life_affect_proof(db_path: Path, request_id: str) -> dict[str, Any]:
    if not request_id:
        return {}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM life_command
            WHERE command_type = 'foreground_completed'
              AND command_id = ?
            LIMIT 1
            """,
            (f"life::foreground_completed::{request_id}",),
        ).fetchone()
    if row is None:
        return {}
    payload = json.loads(str(row[0] or "{}"))
    context = dict(payload.get("emotion_context") or {})
    affect = dict(context.get("affective_life") or {})
    observations = [
        dict(item)
        for item in list(affect.get("user_observations") or [])
        if isinstance(item, Mapping)
    ]
    acoustic = next(
        (item for item in reversed(observations) if str(item.get("modality") or "") == "ACOUSTIC"),
        {},
    )
    return {
        "conflict": bool(affect.get("conflict")),
        "path": str(affect.get("path") or ""),
        "appraisal_kind": str(
            dict(dict(affect.get("self_affect") or {}).get("last_appraisal") or {}).get("event_kind")
            or ""
        ),
        "observation_count": len(observations),
        "acoustic_observation": {
            "modality": acoustic.get("modality"),
            "emotion_distribution": acoustic.get("emotion_distribution"),
            "confidence": acoustic.get("confidence"),
            "observed_at_ms": acoustic.get("observed_at_ms"),
            "expires_at_ms": acoustic.get("expires_at_ms"),
            "evidence_refs": acoustic.get("evidence_refs"),
            "conflict_refs": acoustic.get("conflict_refs"),
        },
        "raw_media_path_persisted": (
            "GPT-SoVITS-v2pro" in str(row[0])
            or "desktop_pet/assets" in str(row[0])
            or "raw_audio" in str(row[0])
        ),
    }


def _terminal_ok(terminal: Mapping[str, Any]) -> bool:
    return (
        str(terminal.get("event_type") or "").upper() == "INPUT_COMPLETED"
        and str(terminal.get("status") or "").upper() == "OK"
    )


def _succeeded_attempt(
    attempts: Sequence[Mapping[str, Any]], *, provider: str, model_id: str
) -> bool:
    return any(
        str(row.get("status") or "").lower() == "succeeded"
        and str(row.get("provider") or "").lower() == provider.lower()
        and str(row.get("model_id") or "") == model_id
        for row in attempts
    )


def _run_multimodal(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db_path).resolve()
    secret = str(os.environ.get(args.webhook_hmac_env) or "").strip()
    if not secret:
        raise RuntimeError(f"missing secret environment variable: {args.webhook_hmac_env}")
    started_at_ms = int(time.time() * 1000)
    outcomes: list[dict[str, Any]] = []
    for case in MULTIMODAL_CASES:
        image_path = (ROOT / str(case["image_rel"])).resolve()
        audio_path = (
            Path(args.voice_root).resolve() / Path(str(case["audio_rel"])).name
        )
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)

        gateway = _send_image_gateway_turn(
            gateway_base_url=args.gateway_base_url,
            secret=secret,
            case_id=str(case["case_id"]),
            message=str(case["message"]),
            image_path=image_path,
            timeout_s=float(args.timeout_s),
        )
        terminal = _wait_gateway_terminal(
            db_path,
            conversation_id=str(gateway["conversation_id"]),
            msg_id=str(gateway["msg_id"]),
            sent_at_ms=int(gateway["sent_at_ms"]),
            timeout_s=float(args.timeout_s),
        )
        gateway_request_id = str(terminal.get("request_id") or "")
        terminal_at_ms = int(terminal.get("ts_ms") or int(time.time() * 1000))
        multimodal_attempts = _attempt_rows_between(
            db_path,
            started_at_ms=int(gateway["sent_at_ms"]) - 1_000,
            ended_at_ms=terminal_at_ms + 2_000,
            request_prefix="multimodal::multimodal_model::",
        )
        gateway_surface_attempts = (
            _fetch_model_attempts(db_path, gateway_request_id)
            if gateway_request_id
            else []
        )
        graph = (
            _fetch_graph_by_request_id(
                db_path=db_path,
                request_id=gateway_request_id,
                min_ts_ms=int(gateway["sent_at_ms"]),
            )
            if gateway_request_id
            else None
        )
        projection = (
            _fetch_worker_projection(db_path, gateway_request_id)
            if gateway_request_id
            else {}
        )

        direct = _run_direct_affect_turn(
            host_base_url=args.host_base_url,
            message=str(case["message"]),
            case_id=str(case["case_id"]),
            self_report=str(case["self_report"]),
            acoustic_tag=str(case["acoustic_tag"]),
            timeout_s=float(args.timeout_s),
        )
        direct_attempts = (
            _fetch_model_attempts(db_path, str(direct.get("request_id") or ""))
            if direct.get("request_id")
            else []
        )
        affect = _life_affect_proof(db_path, str(direct.get("request_id") or ""))
        acoustic = dict(affect.get("acoustic_observation") or {})
        ttl_ms = int(acoustic.get("expires_at_ms") or 0) - int(
            acoustic.get("observed_at_ms") or 0
        )
        checks = {
            "real_image_file": image_path.is_file() and image_path.stat().st_size > 0,
            "real_labelled_audio_file": audio_path.is_file() and audio_path.stat().st_size > 0,
            "gateway_webhook_accepted": 200 <= int(gateway.get("http_status") or 0) < 300,
            "gateway_input_completed": _terminal_ok(terminal),
            "gateway_graph_present": bool(graph),
            "gemini_3_6_flash_succeeded": _succeeded_attempt(
                multimodal_attempts,
                provider="gemini",
                model_id=str(args.gemini_model_id),
            ),
            "gateway_surface_deepseek_succeeded": _succeeded_attempt(
                gateway_surface_attempts,
                provider="deepseek",
                model_id=str(args.surface_model_id),
            ),
            "gateway_visible_reply": bool(str(projection.get("narrative") or "").strip()),
            "direct_http_200": int(direct.get("http_status") or 0) == 200,
            "direct_commit_approved": str(direct.get("commit_decision") or "").upper() == "APPROVE",
            "direct_surface_deepseek_succeeded": _succeeded_attempt(
                direct_attempts,
                provider="deepseek",
                model_id=str(args.surface_model_id),
            ),
            "affect_conflict_materialized": bool(affect.get("conflict")),
            "conflict_appraisal_materialized": str(affect.get("appraisal_kind") or "")
            == "user_affect_conflict",
            "acoustic_confidence_preserved": abs(float(acoustic.get("confidence") or 0.0) - 0.82) < 1e-6,
            "acoustic_ttl_bounded_5s": ttl_ms == 5_000,
            "raw_media_not_persisted": not bool(affect.get("raw_media_path_persisted")),
            "direct_visible_reply": bool(str(direct.get("narrative") or "").strip()),
        }
        outcomes.append(
            {
                "case_id": case["case_id"],
                "message": case["message"],
                "self_report": case["self_report"],
                "acoustic_tag": case["acoustic_tag"],
                "image_source": {
                    "repo_relative_path": image_path.relative_to(ROOT).as_posix(),
                    "size_bytes": image_path.stat().st_size,
                    "sha256": _sha256_file(image_path),
                },
                "acoustic_source": {
                    "voice_bank_relative_path": str(case["audio_rel"]),
                    "size_bytes": audio_path.stat().st_size,
                    "sha256": _sha256_file(audio_path),
                    "label_source": "configs/voice/gpt_sovits_nanami_v4.json",
                },
                "gateway": gateway,
                "terminal": {
                    key: terminal.get(key)
                    for key in ("event_type", "status", "reason_code", "request_id", "ts_ms")
                },
                "graph": graph,
                "gateway_reply": str(projection.get("narrative") or "").strip(),
                "multimodal_model_attempts": multimodal_attempts,
                "gateway_surface_model_attempts": gateway_surface_attempts,
                "direct": direct,
                "direct_model_attempts": direct_attempts,
                "affect_proof": affect,
                "effective_acoustic_ttl_ms": ttl_ms,
                "checks": checks,
                "status": "PASS" if all(checks.values()) else "FAIL",
            }
        )

    finished_at_ms = int(time.time() * 1000)
    all_pass = len(outcomes) == 6 and all(row["status"] == "PASS" for row in outcomes)
    return {
        "schema_version": SCHEMA_VERSION,
        "family": "conflicting_modalities",
        "status": "PASS" if all_pass else "FAIL",
        "real_driver": True,
        "simulated_model_receipts": False,
        "raw_media_persisted": False,
        "case_count": len(outcomes),
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "base_commit_sha": _git_sha(),
        "worktree_diff_sha256": _worktree_diff_sha256(),
        "configured_models": {
            "multimodal": str(args.gemini_model_id),
            "surface": str(args.surface_model_id),
        },
        "outcomes": outcomes,
    }


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("multimodal",), default="multimodal")
    parser.add_argument("--host-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--gateway-base-url", default="http://127.0.0.1:19100")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--webhook-hmac-env", default=DEFAULT_HMAC_ENV)
    parser.add_argument("--gemini-model-id", default="gemini-3.6-flash")
    parser.add_argument("--surface-model-id", default="deepseek-v4-flash")
    parser.add_argument(
        "--voice-root",
        type=Path,
        default=VOICE_ROOT,
        help="Directory containing the referenced Nanami WAV basenames.",
    )
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = _run_multimodal(args)
    _write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
