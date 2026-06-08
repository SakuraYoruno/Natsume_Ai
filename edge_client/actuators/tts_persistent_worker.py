from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import time
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m edge_client.actuators.tts_persistent_worker",
        description="Persistent GPT-SoVITS worker using the model runtime Python.",
    )
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--sample-rate", type=int, default=32000)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--ref-audio-path", default="")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--voice-bank-path", default="")
    return parser


def _emit(stream: Any, payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=stream, flush=True)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


def _cache_key(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _prune_cache(cache_dir: str, *, max_entries: int) -> None:
    if max_entries <= 0:
        return
    try:
        entries = [
            os.path.join(cache_dir, name)
            for name in os.listdir(cache_dir)
            if name.endswith(".npz")
        ]
    except OSError:
        return
    excess = len(entries) - int(max_entries)
    if excess <= 0:
        return
    entries.sort(key=lambda path: os.path.getmtime(path) if os.path.exists(path) else 0.0)
    for path in entries[:excess]:
        try:
            os.remove(path)
        except OSError:
            pass


def _copy_cached_npz(cache_path: str, output_path: str, np_module: Any) -> dict[str, Any] | None:
    try:
        with np_module.load(cache_path) as loaded:
            audio = np_module.asarray(loaded["audio"], dtype=np_module.float32)
            sample_rate = int(loaded["sample_rate"])
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        np_module.savez(output_path, audio=audio, sample_rate=np_module.int32(sample_rate))
        duration_s = float(len(audio) / sample_rate) if sample_rate > 0 else 0.0
        os.utime(cache_path, None)
        return {"sample_rate": sample_rate, "duration_s": duration_s}
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    protocol_stdout = sys.stdout

    os.environ["EDGE_TTS_PERSISTENT_WORKER_CHILD"] = "1"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.chdir(repo_root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # GPT-SoVITS prints progress and diagnostics to stdout. Keep stdout clean for
    # JSON-lines IPC and send model logs to stderr instead.
    sys.stdout = sys.stderr

    try:
        import numpy as np

        from edge_client.actuators.tts_engine import SoVITSModelManager, TtsConfig

        cfg = TtsConfig(
            model_path=str(args.model_path),
            device=str(args.device),
            sample_rate=int(args.sample_rate),
            ref_audio_path=str(args.ref_audio_path or "").strip() or None,
            ref_text=str(args.ref_text or "").strip() or None,
            voice_bank_path=str(args.voice_bank_path or "").strip() or None,
            language=str(args.language or "zh"),
        )
        manager = SoVITSModelManager(cfg)
        load_t0 = time.perf_counter()
        load_result = manager.load()
        load_s = time.perf_counter() - load_t0
        if not load_result.success:
            _emit(
                protocol_stdout,
                {
                    "event": "ready",
                    "success": False,
                    "detail": load_result.detail,
                    "load_s": round(load_s, 4),
                    "reason_code": load_result.reason_code,
                },
            )
            return 1

        cache_enabled = _env_bool("EDGE_TTS_PERSISTENT_WORKER_CACHE_ENABLED", True)
        cache_root = str(
            os.environ.get("EDGE_TTS_PERSISTENT_WORKER_CACHE_DIR")
            or os.path.join(repo_root, "runtime", "edge", "tts_cache")
        )
        cache_max_entries = _env_int(
            "EDGE_TTS_PERSISTENT_WORKER_CACHE_MAX_ENTRIES",
            256,
            minimum=0,
            maximum=10000,
        )
        cache_max_text_chars = _env_int(
            "EDGE_TTS_PERSISTENT_WORKER_CACHE_MAX_TEXT_CHARS",
            80,
            minimum=1,
            maximum=2000,
        )
        if cache_enabled:
            os.makedirs(cache_root, exist_ok=True)

        warmup_enabled = _env_bool("EDGE_TTS_PERSISTENT_WORKER_WARMUP_ENABLED", True)
        warmup_success: bool | None = None
        warmup_detail: str | None = None
        warmup_s: float | None = None
        if warmup_enabled:
            warmup_text = str(os.environ.get("EDGE_TTS_PERSISTENT_WORKER_WARMUP_TEXT") or "嗯。").strip() or "嗯。"
            warmup_t0 = time.perf_counter()
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    warmup_result = manager.synthesize(
                        warmup_text,
                        language=str(args.language or "zh"),
                        emotion_tag="NEUTRAL",
                        emotion_intensity=0.5,
                    )
                warmup_s = time.perf_counter() - warmup_t0
                warmup_success = bool(warmup_result.success and warmup_result.audio_data is not None)
                warmup_detail = str(warmup_result.detail or "")
                print(
                    "TTS persistent worker warmup: "
                    f"success={warmup_success} wall_s={warmup_s:.4f} text_chars={len(warmup_text)}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:  # pragma: no cover - warmup must not kill worker
                warmup_s = time.perf_counter() - warmup_t0
                warmup_success = False
                warmup_detail = str(exc)
                print(
                    "TTS persistent worker warmup failed: "
                    f"wall_s={warmup_s:.4f} error={exc}",
                    file=sys.stderr,
                    flush=True,
                )

        _emit(
            protocol_stdout,
            {
                "event": "ready",
                "success": True,
                "detail": load_result.detail,
                "load_s": round(load_s, 4),
                "reason_code": load_result.reason_code,
                "warmup_enabled": bool(warmup_enabled),
                "warmup_success": warmup_success,
                "warmup_detail": warmup_detail,
                "warmup_s": round(warmup_s, 4) if warmup_s is not None else None,
                "cache_enabled": bool(cache_enabled),
                "cache_max_entries": int(cache_max_entries),
                "cache_max_text_chars": int(cache_max_text_chars),
            },
        )

        for raw_line in sys.stdin:
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except Exception as exc:
                _emit(protocol_stdout, {"event": "error", "success": False, "detail": f"invalid json: {exc}"})
                continue

            request_id = str(request.get("request_id") or "")
            if str(request.get("command") or "").strip().lower() == "shutdown":
                _emit(protocol_stdout, {"event": "shutdown", "success": True, "request_id": request_id})
                return 0

            output_path = str(request.get("output_path") or "").strip()
            if not output_path:
                _emit(
                    protocol_stdout,
                    {
                        "event": "synth_result",
                        "request_id": request_id,
                        "success": False,
                        "reason_code": "VOICE_SCHEMA_INVALID",
                        "detail": "output_path is required",
                    },
                )
                continue

            synth_t0 = time.perf_counter()
            try:
                text = str(request.get("text") or "")
                language = str(request.get("language") or args.language or "zh")
                emotion_tag = str(request.get("emotion_tag") or "").strip() or None
                emotion_intensity = request.get("emotion_intensity")
                speed_factor = request.get("speed_factor")
                cache_path: str | None = None
                cache_allowed = bool(cache_enabled and text.strip() and len(text.strip()) <= cache_max_text_chars)
                if cache_allowed:
                    cache_payload = {
                        "model_path": str(args.model_path),
                        "sample_rate": int(args.sample_rate),
                        "language": language,
                        "text": text,
                        "emotion_tag": emotion_tag,
                        "emotion_intensity": emotion_intensity,
                        "speed_factor": speed_factor,
                        "ref_audio_path": str(args.ref_audio_path or ""),
                        "ref_text": str(args.ref_text or ""),
                        "voice_bank_path": str(args.voice_bank_path or ""),
                    }
                    cache_path = os.path.join(cache_root, f"{_cache_key(cache_payload)}.npz")
                    if os.path.exists(cache_path):
                        cached = _copy_cached_npz(cache_path, output_path, np)
                        if cached is not None:
                            _emit(
                                protocol_stdout,
                                {
                                    "event": "synth_result",
                                    "request_id": request_id,
                                    "success": True,
                                    "detail": "tts cache hit",
                                    "sample_rate": int(cached["sample_rate"]),
                                    "duration_s": float(cached["duration_s"]),
                                    "processing_time_s": 0.0,
                                    "wall_s": round(time.perf_counter() - synth_t0, 4),
                                    "cache_hit": True,
                                },
                            )
                            continue
                with contextlib.redirect_stdout(sys.stderr):
                    result = manager.synthesize(
                        text,
                        language=language,
                        emotion_tag=emotion_tag,
                        emotion_intensity=emotion_intensity,
                        speed_factor=speed_factor,
                    )
                wall_s = time.perf_counter() - synth_t0
                if not result.success or result.audio_data is None:
                    _emit(
                        protocol_stdout,
                        {
                            "event": "synth_result",
                            "request_id": request_id,
                            "success": False,
                            "reason_code": result.reason_code,
                            "detail": result.detail,
                            "processing_time_s": result.processing_time_s,
                            "wall_s": round(wall_s, 4),
                        },
                    )
                    continue

                np.savez(
                    output_path,
                    audio=np.asarray(result.audio_data, dtype=np.float32),
                    sample_rate=np.int32(result.sample_rate),
                )
                cache_stored = False
                if cache_allowed and cache_path:
                    try:
                        cache_tmp = f"{cache_path}.tmp.npz"
                        np.savez(
                            cache_tmp,
                            audio=np.asarray(result.audio_data, dtype=np.float32),
                            sample_rate=np.int32(result.sample_rate),
                        )
                        os.replace(cache_tmp, cache_path)
                        _prune_cache(cache_root, max_entries=cache_max_entries)
                        cache_stored = True
                    except Exception as exc:
                        print(
                            f"TTS persistent worker cache store failed: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                _emit(
                    protocol_stdout,
                    {
                        "event": "synth_result",
                        "request_id": request_id,
                        "success": True,
                        "detail": result.detail,
                        "sample_rate": int(result.sample_rate),
                        "duration_s": float(result.duration_s),
                        "processing_time_s": result.processing_time_s,
                        "wall_s": round(wall_s, 4),
                        "cache_hit": False,
                        "cache_stored": bool(cache_stored),
                    },
                )
            except Exception as exc:
                _emit(
                    protocol_stdout,
                    {
                        "event": "synth_result",
                        "request_id": request_id,
                        "success": False,
                        "reason_code": "VOICE_SYNTH_FAILED",
                        "detail": str(exc),
                        "wall_s": round(time.perf_counter() - synth_t0, 4),
                    },
                )
    except Exception as exc:
        _emit(protocol_stdout, {"event": "ready", "success": False, "detail": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
