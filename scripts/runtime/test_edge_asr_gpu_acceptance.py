"""Run a real faster-whisper CUDA transcription against an audio file.

This is an Edge acceptance driver, not a mocked unit test. It loads the
configured model, decodes real PCM, performs transcription, and emits a
content-addressable JSON report without exposing credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge_client.senses.asr import AsrConfig, WhisperModelManager


def _git_text(*args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), *args),
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _worktree_diff_sha256() -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), "diff", "--binary"),
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def _load_pcm16_mono(path: Path, *, target_sample_rate: int) -> tuple[np.ndarray, dict[str, int]]:
    with wave.open(str(path), "rb") as source:
        sample_rate = int(source.getframerate())
        channels = int(source.getnchannels())
        sample_width = int(source.getsampwidth())
        frame_count = int(source.getnframes())
        payload = source.readframes(frame_count)
    if sample_width != 2:
        raise ValueError(f"only PCM16 WAV is supported; sample_width={sample_width}")

    audio = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if sample_rate != target_sample_rate:
        source_axis = np.arange(audio.size, dtype=np.float64) / float(sample_rate)
        target_size = max(1, int(round(audio.size * target_sample_rate / sample_rate)))
        target_axis = np.arange(target_size, dtype=np.float64) / float(target_sample_rate)
        audio = np.interp(target_axis, source_axis, audio).astype(np.float32)
    return audio, {
        "source_sample_rate": sample_rate,
        "target_sample_rate": target_sample_rate,
        "source_channels": channels,
        "source_frames": frame_count,
    }


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    audio_path = args.audio_path.resolve()
    model_path = args.model_path.resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    if not model_path.is_dir():
        raise FileNotFoundError(model_path)

    audio, source = _load_pcm16_mono(
        audio_path,
        target_sample_rate=args.sample_rate,
    )
    config = AsrConfig(
        model_size=str(model_path),
        device="cuda",
        compute_type="float16",
        beam_size=args.beam_size,
        sample_rate=args.sample_rate,
        language=args.language,
        use_vad_filter=False,
    )
    manager = WhisperModelManager(config)
    started_ms = int(time.time() * 1000)
    try:
        load_started = time.perf_counter()
        load = manager.load()
        load_ms = round((time.perf_counter() - load_started) * 1000, 3)
        transcribe_started = time.perf_counter()
        transcribe = manager.transcribe(
            audio,
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=False,
        )
        transcribe_ms = round(
            (time.perf_counter() - transcribe_started) * 1000,
            3,
        )
        actual_device = manager.actual_device
    finally:
        manager.unload()

    text = "".join(segment.text for segment in transcribe.segments).strip()
    confidences = [float(segment.confidence) for segment in transcribe.segments]
    expected = str(args.expected_substring or "").strip()
    checks = {
        "load_success": bool(load.success),
        "transcribe_success": bool(transcribe.success),
        "actual_device_cuda": str(actual_device or "").lower() == "cuda",
        "nonempty_text": bool(text),
        "expected_substring": not expected or expected in text,
    }
    passed = all(checks.values())
    report: dict[str, Any] = {
        "schema_version": "natsume.a22.edge-asr-gpu-acceptance.v1",
        "status": "PASS" if passed else "FAIL",
        "started_at_ms": started_ms,
        "finished_at_ms": int(time.time() * 1000),
        "repository_commit": _git_text("rev-parse", "HEAD"),
        "worktree_diff_sha256": _worktree_diff_sha256(),
        "inputs": {
            "model_path": str(model_path),
            "audio_path": str(audio_path),
            "language": args.language,
            "beam_size": args.beam_size,
            **source,
            "audio_duration_s": round(audio.size / args.sample_rate, 3),
        },
        "runtime": {
            "configured_device": config.device,
            "configured_compute_type": config.compute_type,
            "actual_device": actual_device,
            "cuda_dll_dirs_configured": bool(
                str(os.environ.get("EDGE_ASR_CUDA_DLL_DIRS") or "").strip()
            ),
        },
        "timing_ms": {"model_load": load_ms, "transcribe": transcribe_ms},
        "result": {
            "text": text,
            "confidence": (
                round(sum(confidences) / len(confidences), 6)
                if confidences
                else 0.0
            ),
            "language": transcribe.language,
            "reason_code": transcribe.reason_code,
            "detail": transcribe.detail,
        },
        "checks": checks,
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--expected-substring", default="")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_acceptance(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    sys.stdout.buffer.write(payload.encode("utf-8"))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
