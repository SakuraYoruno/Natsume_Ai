"""Run an authenticated, physical Edge voice and barge-in acceptance probe.

The script is intentionally meant for an already running real Edge process. It
does not start a fake transport, replace the synthesizer, or suppress audio
playback. The HMAC key is read only from the process environment and is never
included in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping
import urllib.request
import uuid

def _json_get(url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Edge response is not a JSON object")
    return dict(payload)


def _build_edge_envelope(
    *,
    request_id: str,
    payload: Mapping[str, Any],
    shared_key: str,
) -> dict[str, Any]:
    """Build the production Edge envelope without importing the Host app graph.

    The physical Edge acceptance machine intentionally installs only Edge
    dependencies.  Importing ``host_brain.channels`` pulled the full Host
    package (including its cryptography dependency) into an otherwise
    standalone transport probe.  Keep this stdlib-only implementation byte
    compatible with ``host_brain.channels.edge_transport``.
    """

    ts_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    body = dict(payload)
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    material = f"{request_id}\n{ts_ms}\n{nonce}\n{canonical}".encode("utf-8")
    signature = hmac.new(
        shared_key.encode("utf-8"),
        material,
        hashlib.sha256,
    ).hexdigest()
    return {
        "request_id": str(request_id),
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": body,
    }


def _post(
    *,
    base_url: str,
    path: str,
    payload: Mapping[str, Any],
    request_prefix: str,
    shared_key: str,
    timeout_s: float,
) -> tuple[str, dict[str, Any], float]:
    request_id = f"{request_prefix}{uuid.uuid4().hex}"
    envelope = _build_edge_envelope(
        request_id=request_id,
        payload=dict(payload),
        shared_key=shared_key,
    )
    raw = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        result = json.load(response)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    if not isinstance(result, dict):
        raise RuntimeError("Edge POST response is not a JSON object")
    return request_id, dict(result), elapsed_ms


def _component(health: Mapping[str, Any], name: str) -> dict[str, Any]:
    event = dict(health.get("event") or {})
    components = dict(event.get("components") or {})
    return dict(components.get(name) or {})


def _voice_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(result.get("event") or {})
    vts = dict(event.get("vts_expression_result") or {})
    mouth = dict(event.get("vts_mouth_sync") or {})
    return {
        "success": bool(result.get("success")),
        "status": str(result.get("status") or ""),
        "reason_code": result.get("reason_code"),
        "detail": str(result.get("detail") or ""),
        "event_type": str(event.get("event_type") or ""),
        "start_status": str(event.get("start_status") or ""),
        "duration_s": float(event.get("duration_s") or 0.0),
        "sample_rate": int(event.get("sample_rate") or 0),
        "mouth_frame_count": int(event.get("mouth_frame_count") or 0),
        "synth_elapsed_s": float(event.get("synth_elapsed_s") or 0.0),
        "playback_start_elapsed_s": float(
            event.get("playback_start_elapsed_s") or 0.0
        ),
        "vts_expression_success": bool(vts.get("success")),
        "vts_expression_reason": vts.get("reason_code"),
        "mouth_sync_started": bool(mouth.get("started")),
        "mouth_sync_reason": mouth.get("reason"),
    }


def _health_summary(health: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(health.get("event") or {})
    services = dict(event.get("services") or {})
    resources = dict(event.get("resources") or {})
    asr = dict(_component(health, "asr").get("health") or {})
    tts = dict(_component(health, "tts").get("health") or {})
    playback = dict(_component(health, "tts_playback_device").get("health") or {})
    vts_component = _component(health, "vts")
    vts = dict(vts_component.get("health") or {})
    return {
        "edge_status": str(event.get("status") or ""),
        "asr_state": str(dict(asr.get("model") or {}).get("state") or ""),
        "asr_device": str(dict(asr.get("model") or {}).get("device") or ""),
        "tts_state": str(tts.get("state") or ""),
        "tts_backend": str(tts.get("backend") or ""),
        "playback_playing": bool(playback.get("playing")),
        "voice_queue_depth": int(resources.get("queue_depth") or 0),
        "playback_error": playback.get("last_playback_error"),
        "vts_state": str(vts.get("state") or ""),
        "vts_detail": str(vts_component.get("detail") or ""),
        "barge_in_available": bool(
            dict(services.get("edge.rpc.barge_in") or {}).get("available")
        ),
    }


def run_probe(*, base_url: str, expected_data_root: Path) -> dict[str, Any]:
    shared_key = str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip()
    if not shared_key:
        raise RuntimeError("EDGE_HMAC_SHARED_KEY is required")

    base = base_url.rstrip("/")
    pre_health = _json_get(f"{base}/v1/edge/health")
    pre_summary = _health_summary(pre_health)
    session_id = f"a15-isolated-voice-{uuid.uuid4().hex[:12]}"

    short_marker = uuid.uuid4().hex
    short_id, short_result, short_ms = _post(
        base_url=base,
        path="/v1/edge/speak",
        payload={
            "session_id": session_id,
            "utterance_id": f"short_{short_marker}",
            "text": "おかえり。今日も会えて、うれしいよ。",
            "lang_hint": "ja",
            "emotion_tag": "SHARED_JOY",
            "emotion_intensity": 0.70,
            "synchronization_id": f"sync_short_{short_marker}",
            "wait_playback_start": True,
        },
        request_prefix="req_a15_isolated_short_",
        shared_key=shared_key,
        timeout_s=300.0,
    )
    short_summary = _voice_summary(short_result)
    duration_s = max(0.0, float(short_summary["duration_s"]))
    time.sleep(min(duration_s + 0.75, 30.0))
    completed_health = _json_get(f"{base}/v1/edge/health")
    completed_summary = _health_summary(completed_health)
    short_playback_completed = bool(
        short_summary["success"]
        and not completed_summary["playback_playing"]
        and completed_summary["playback_error"] is None
    )

    long_marker = uuid.uuid4().hex
    long_id, long_result, long_ms = _post(
        base_url=base,
        path="/v1/edge/speak",
        payload={
            "session_id": session_id,
            "utterance_id": f"long_{long_marker}",
            "text": (
                "ねえ、今日は少しだけ長く話してもいい？ 急がなくていいから、"
                "あなたの気持ちをひとつずつ聞かせて。嬉しかったことも、"
                "疲れたことも、うまく言葉にならないことも、"
                "私はここで静かに受け止めているから。"
            ),
            "lang_hint": "ja",
            "emotion_tag": "QUIET_PRESENCE",
            "emotion_intensity": 0.64,
            "synchronization_id": f"sync_long_{long_marker}",
            "wait_playback_start": True,
        },
        request_prefix="req_a15_isolated_long_",
        shared_key=shared_key,
        timeout_s=300.0,
    )
    time.sleep(0.35)
    barge_id, barge_result, barge_ms = _post(
        base_url=base,
        path="/v1/edge/barge_in",
        payload={
            "session_id": session_id,
            "source": "acceptance.manual_interrupt",
            "seq": 1,
            "text": "打断测试",
        },
        request_prefix="req_a15_isolated_barge_",
        shared_key=shared_key,
        timeout_s=30.0,
    )
    post_summary = _health_summary(_json_get(f"{base}/v1/edge/health"))
    settle_deadline = time.monotonic() + 5.0
    while (
        post_summary["playback_playing"]
        or post_summary["voice_queue_depth"] > 0
    ) and time.monotonic() < settle_deadline:
        time.sleep(0.1)
        post_summary = _health_summary(_json_get(f"{base}/v1/edge/health"))
    barge_in_drained = bool(
        not post_summary["playback_playing"]
        and post_summary["voice_queue_depth"] == 0
    )

    isolated_db = expected_data_root / "edge" / "embodiment_transport.sqlite3"
    long_summary = _voice_summary(long_result)
    barge_summary = _voice_summary(barge_result)
    voice_pass = bool(
        pre_summary["tts_state"] == "READY"
        and pre_summary["barge_in_available"]
        and short_playback_completed
        and long_summary["success"]
        and barge_summary["success"]
        and barge_summary["reason_code"] == "VOICE_INTERRUPTED"
        and barge_in_drained
        and isolated_db.is_file()
    )
    return {
        "schema_version": 1,
        "status": "PASS" if voice_pass else "FAIL",
        "real_transport": True,
        "physical_playback_requested": True,
        "mock_transport": False,
        "expected_data_root": str(expected_data_root.resolve()),
        "isolated_embodiment_db": str(isolated_db.resolve()),
        "isolated_embodiment_db_exists": isolated_db.is_file(),
        "pre_health": pre_summary,
        "short_playback": {
            "request_id": short_id,
            "elapsed_ms": short_ms,
            "completed": short_playback_completed,
            **short_summary,
        },
        "long_playback": {
            "request_id": long_id,
            "elapsed_ms": long_ms,
            **long_summary,
        },
        "barge_in": {
            "request_id": barge_id,
            "elapsed_ms": barge_ms,
            **barge_summary,
            "queue_drained": barge_in_drained,
        },
        "post_health": post_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:17800")
    parser.add_argument("--expected-data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_probe(
        base_url=str(args.base_url),
        expected_data_root=args.expected_data_root,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
