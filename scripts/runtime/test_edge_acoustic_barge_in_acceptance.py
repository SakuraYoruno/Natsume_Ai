"""Run a real microphone-path acoustic barge-in acceptance probe.

The probe sends only a signed ``/v1/edge/speak`` request.  It deliberately
does not call ``/v1/edge/barge_in``.  After physical TTS playback starts, a
recorded voice WAV is played through the Windows default output device.  A
PASS therefore requires the resident microphone ASR service to report a new
``barge_in_triggered`` event and the speech queue to drain.

This script is Windows-only and is intended for an already-running physical
Edge machine.  The HMAC key is read from the process environment and is never
included in the report.
"""

from __future__ import annotations

import argparse
from array import array
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
import urllib.request
import uuid
import wave


def _json_get(url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Edge response is not a JSON object")
    return dict(payload)


def _build_edge_envelope(
    *, request_id: str, payload: Mapping[str, Any], shared_key: str
) -> dict[str, Any]:
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
        shared_key.encode("utf-8"), material, hashlib.sha256
    ).hexdigest()
    return {
        "request_id": request_id,
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": body,
    }


def _post_speak(
    *, base_url: str, payload: Mapping[str, Any], shared_key: str
) -> tuple[str, dict[str, Any], float]:
    request_id = f"req_a22_acoustic_speak_{uuid.uuid4().hex}"
    envelope = _build_edge_envelope(
        request_id=request_id,
        payload=payload,
        shared_key=shared_key,
    )
    request = urllib.request.Request(  # noqa: S310
        f"{base_url.rstrip('/')}/v1/edge/speak",
        data=json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=300.0) as response:  # noqa: S310
        result = json.load(response)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    if not isinstance(result, dict):
        raise RuntimeError("Edge speak response is not a JSON object")
    return request_id, dict(result), elapsed_ms


def _component(health: Mapping[str, Any], name: str) -> dict[str, Any]:
    event = dict(health.get("event") or {})
    return dict(dict(event.get("components") or {}).get(name) or {})


def _health_summary(health: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(health.get("event") or {})
    components = dict(event.get("components") or {})
    services = dict(event.get("services") or {})
    resources = dict(event.get("resources") or {})
    asr = dict(dict(components.get("asr") or {}).get("health") or {})
    tts = dict(dict(components.get("tts") or {}).get("health") or {})
    playback = dict(
        dict(components.get("tts_playback_device") or {}).get("health") or {}
    )
    realtime = dict(asr.get("realtime_dialogue") or {})
    return {
        "edge_status": str(event.get("status") or ""),
        "asr_state": str(dict(asr.get("model") or {}).get("state") or ""),
        "asr_device": str(dict(asr.get("model") or {}).get("device") or ""),
        "asr_audio_running": bool(dict(asr.get("audio") or {}).get("running")),
        "tts_state": str(tts.get("state") or ""),
        "tts_backend": str(tts.get("backend") or ""),
        "playback_playing": bool(playback.get("playing")),
        "playback_error": playback.get("last_playback_error"),
        "queue_depth": int(resources.get("queue_depth") or 0),
        "barge_in_available": bool(
            dict(services.get("edge.rpc.barge_in") or {}).get("available")
        ),
        "last_barge_in_ts_ms": int(realtime.get("last_barge_in_ts_ms") or 0),
        "last_debug": dict(realtime.get("last_debug") or {}),
    }


def _wait_ready(base_url: str, *, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    summary: dict[str, Any] = {}
    while time.monotonic() < deadline:
        summary = _health_summary(
            _json_get(f"{base_url.rstrip('/')}/v1/edge/health")
        )
        if (
            summary["asr_state"] == "READY"
            and summary["tts_state"] == "READY"
            and summary["asr_audio_running"]
            and summary["barge_in_available"]
        ):
            return summary
        time.sleep(0.25)
    raise TimeoutError(f"Edge voice services did not become ready: {summary}")


def _prepare_normalized_wav(path: Path, state: dict[str, Any]) -> Path:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if params.sampwidth != 2:
            raise RuntimeError(
                f"normalization requires 16-bit PCM; sample_width={params.sampwidth}"
            )
        frames = source.readframes(source.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":  # pragma: no cover - Windows is little endian
        samples.byteswap()
    source_peak = max((abs(value) for value in samples), default=0)
    if source_peak <= 0:
        raise RuntimeError("source WAV contains no non-zero samples")
    gain = min(16.0, (0.97 * 32767.0) / float(source_peak))
    normalized = array(
        "h",
        (
            max(-32768, min(32767, int(round(value * gain))))
            for value in samples
        ),
    )
    if sys.byteorder != "little":  # pragma: no cover - Windows is little endian
        normalized.byteswap()
    handle = tempfile.NamedTemporaryFile(
        prefix="natsume-a22-acoustic-", suffix=".wav", delete=False
    )
    prepared = Path(handle.name)
    handle.close()
    with wave.open(str(prepared), "wb") as output:
        output.setparams(params)
        output.writeframes(normalized.tobytes())
    prepared_bytes = prepared.read_bytes()
    state["normalization"] = {
        "source_peak_ratio": round(source_peak / 32767.0, 6),
        "gain": round(gain, 6),
        "prepared_sha256": hashlib.sha256(prepared_bytes).hexdigest(),
    }
    return prepared


def _play_wav_physical(path: Path, state: dict[str, Any]) -> None:
    state["started_at_ms"] = int(time.time() * 1000)
    prepared: Path | None = None
    try:
        import winsound

        prepared = _prepare_normalized_wav(path, state)
        winsound.PlaySound(
            str(prepared.resolve()),
            # PlaySound is synchronous unless SND_ASYNC is explicitly set.
            winsound.SND_FILENAME | winsound.SND_NODEFAULT,
        )
        state["completed"] = True
    except Exception as exc:  # pragma: no cover - physical Windows runtime
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        state["finished_at_ms"] = int(time.time() * 1000)
        if prepared is not None:
            try:
                prepared.unlink()
                state["temporary_wav_deleted"] = True
            except OSError as exc:
                state["temporary_wav_deleted"] = False
                state["temporary_wav_cleanup_error"] = str(exc)


def _git_identity(repo_root: Path) -> tuple[str | None, str | None]:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        diff = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "--binary", "HEAD"],
        )
        return commit, hashlib.sha256(diff).hexdigest()
    except Exception:
        return None, None


def _read_env_value(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def run_probe(
    *, base_url: str, audio_path: Path, repo_root: Path
) -> dict[str, Any]:
    shared_key = str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip()
    if not shared_key:
        raise RuntimeError("EDGE_HMAC_SHARED_KEY is required")
    source = audio_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    with wave.open(str(source), "rb") as wav_file:
        frames = int(wav_file.getnframes())
        sample_rate = int(wav_file.getframerate())
        channels = int(wav_file.getnchannels())
        sample_width = int(wav_file.getsampwidth())
    source_bytes = source.read_bytes()
    source_duration_s = frames / float(sample_rate)

    base = base_url.rstrip("/")
    pre = _wait_ready(base, timeout_s=120.0)
    marker = uuid.uuid4().hex
    speak_id, speak_result, speak_elapsed_ms = _post_speak(
        base_url=base,
        shared_key=shared_key,
        payload={
            "session_id": f"a22-acoustic-{marker[:12]}",
            "utterance_id": f"acoustic_{marker}",
            "text": (
                "ねえ、今日はゆっくり話そう。急がなくていいから、"
                "あなたの気持ちをひとつずつ聞かせてね。"
            ),
            "lang_hint": "ja",
            "emotion_tag": "QUIET_PRESENCE",
            "emotion_intensity": 0.64,
            "synchronization_id": f"sync_acoustic_{marker}",
            "wait_playback_start": True,
        },
    )
    speak_event = dict(speak_result.get("event") or {})
    injector: dict[str, Any] = {
        "commanded": True,
        "completed": False,
        "error": None,
    }
    thread = threading.Thread(
        target=_play_wav_physical,
        args=(source, injector),
        name="a22-acoustic-injector",
        daemon=True,
    )
    thread.start()

    observed: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(8.0, source_duration_s + 4.0)
    final = pre
    triggered: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        final = _health_summary(_json_get(f"{base}/v1/edge/health"))
        debug = dict(final.get("last_debug") or {})
        observed.append(
            {
                "observed_at_ms": int(time.time() * 1000),
                "playback_playing": bool(final.get("playback_playing")),
                "queue_depth": int(final.get("queue_depth") or 0),
                "last_barge_in_ts_ms": int(final.get("last_barge_in_ts_ms") or 0),
                "debug_state": str(debug.get("state") or ""),
            }
        )
        if (
            int(final.get("last_barge_in_ts_ms") or 0)
            > int(pre.get("last_barge_in_ts_ms") or 0)
            and str(debug.get("state") or "") == "barge_in_triggered"
        ):
            triggered = debug
            break
        time.sleep(0.1)
    thread.join(timeout=max(1.0, source_duration_s + 2.0))
    settle_deadline = time.monotonic() + 5.0
    while time.monotonic() < settle_deadline:
        final = _health_summary(_json_get(f"{base}/v1/edge/health"))
        if not final["playback_playing"] and final["queue_depth"] == 0:
            break
        time.sleep(0.1)

    debug = dict(triggered or final.get("last_debug") or {})
    speaker = dict(debug.get("speaker_verification") or {})
    event_ts_ms = int(final.get("last_barge_in_ts_ms") or 0)
    injection_start = int(injector.get("started_at_ms") or 0)
    injection_finish = int(injector.get("finished_at_ms") or 0)
    checks = {
        "source_is_real_wav": bool(frames > 0 and sample_rate > 0),
        "resident_microphone_running": bool(pre["asr_audio_running"]),
        "asr_cuda_ready": bool(
            pre["asr_state"] == "READY" and pre["asr_device"] == "cuda"
        ),
        "gpt_sovits_ready": bool(
            pre["tts_state"] == "READY" and pre["tts_backend"] == "gpt_sovits"
        ),
        "barge_in_service_available": bool(pre["barge_in_available"]),
        "tts_playback_started": bool(
            speak_result.get("success")
            and str(speak_event.get("event_type") or "") == "TTS_PLAYBACK_STARTED"
        ),
        "physical_injector_completed": bool(
            injector.get("completed") and not injector.get("error")
        ),
        "new_asr_barge_event": bool(
            event_ts_ms > int(pre.get("last_barge_in_ts_ms") or 0)
        ),
        "asr_debug_triggered": str(debug.get("state") or "")
        == "barge_in_triggered",
        "asr_request_id": str(debug.get("request_id") or "").startswith(
            "asr_barge_"
        ),
        "speaker_gate_accepted": bool(speaker.get("accepted")),
        "event_during_acoustic_window": bool(
            injection_start
            and injection_finish
            and injection_start - 500 <= event_ts_ms <= injection_finish + 1000
        ),
        "playback_stopped": not bool(final["playback_playing"]),
        "queue_drained": int(final["queue_depth"]) == 0,
        "no_playback_error": final["playback_error"] is None,
        "manual_barge_endpoint_not_called": True,
    }
    commit, diff_sha = _git_identity(repo_root.resolve())
    report: dict[str, Any] = {
        "schema_version": "natsume.a22.edge-acoustic-barge-in.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "repository_commit": commit,
        "worktree_diff_sha256": diff_sha,
        "real_transport": True,
        "physical_output_commanded": True,
        "resident_microphone_observed": True,
        "manual_barge_endpoint_called": False,
        "source_audio": {
            "path": str(source),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "frames": frames,
            "sample_rate": sample_rate,
            "channels": channels,
            "sample_width": sample_width,
            "duration_s": round(source_duration_s, 3),
        },
        "pre_health": pre,
        "speak": {
            "request_id": speak_id,
            "elapsed_ms": speak_elapsed_ms,
            "success": bool(speak_result.get("success")),
            "event_type": str(speak_event.get("event_type") or ""),
            "duration_s": float(speak_event.get("duration_s") or 0.0),
            "synchronization_id": str(
                speak_event.get("synchronization_id") or ""
            ),
        },
        "acoustic_injector": injector,
        "asr_trigger": debug,
        "final_health": final,
        "observations": observed,
        "checks": checks,
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:17800")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional local env file used only to load EDGE_HMAC_SHARED_KEY.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    injected_key = False
    if not str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip():
        if args.env_file is not None:
            key = _read_env_value(args.env_file.resolve(), "EDGE_HMAC_SHARED_KEY")
            if key:
                os.environ["EDGE_HMAC_SHARED_KEY"] = key
                injected_key = True
    try:
        report = run_probe(
            base_url=str(args.base_url),
            audio_path=args.audio,
            repo_root=args.repo_root,
        )
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
