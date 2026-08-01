from __future__ import annotations

"""Standalone GPT-SoVITS worker for the bundled model Python.

This file is deliberately independent from ``edge_client`` and
``natsume_contracts``.  The bundled GPT-SoVITS runtime currently uses Python
3.9, while the host/edge application targets a newer Python.  Importing the
application object graph inside the model process therefore makes an otherwise
healthy CUDA runtime fail before loading the voice model.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import wave
from typing import Any


DEFAULT_SAMPLE_RATE = 32000
_WEIGHT_DIRS = {
    "t2s": (
        ("GPT_weights_v2ProPlus", ".ckpt"),
        ("GPT_weights_v2Pro", ".ckpt"),
        ("GPT_weights_v4", ".ckpt"),
        ("GPT_weights_v3", ".ckpt"),
        ("GPT_weights_v2", ".ckpt"),
        ("GPT_weights", ".ckpt"),
    ),
    "vits": (
        ("SoVITS_weights_v2ProPlus", ".pth"),
        ("SoVITS_weights_v2Pro", ".pth"),
        ("SoVITS_weights_v4", ".pth"),
        ("SoVITS_weights_v3", ".pth"),
        ("SoVITS_weights_v2", ".pth"),
        ("SoVITS_weights", ".pth"),
    ),
}
_EMOTION_ALIASES = {
    "COMFORT": "CALM",
    "SOOTHING": "CALM",
    "JOY": "HAPPY",
    "HAPPINESS": "HAPPY",
    "PLAYFUL": "HAPPY",
    "LOVE": "LOVING",
    "AFFECTION": "LOVING",
    "COQUETTISH": "LOVING",
    "SADNESS": "SAD",
    "EXCITEMENT": "EXCITED",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python edge_client/actuators/tts_persistent_worker.py",
        description="Standalone persistent GPT-SoVITS model worker.",
    )
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
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


def _normalize_language(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw.startswith("zh") or raw in {"cn", "chinese"}:
        return "zh"
    if raw.startswith("ja") or raw in {"jp", "japanese"}:
        return "ja"
    if raw.startswith("en") or raw == "english":
        return "en"
    return raw or "zh"


def _normalize_emotion(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_]+", "_", str(value or "").strip().upper()).strip("_")


def _validate_manifest_language(*, manifest: dict[str, Any], requested_language: str) -> None:
    raw_manifest_language = str(manifest.get("voice_language") or "").strip()
    manifest_language = _normalize_language(raw_manifest_language) if raw_manifest_language else ""
    requested = _normalize_language(requested_language)
    if (
        manifest_language
        and requested != "auto"
        and manifest_language != requested
        and not _env_bool("EDGE_TTS_ALLOW_LANGUAGE_MISMATCH", False)
    ):
        raise RuntimeError(
            "GPT-SoVITS language mismatch: manifest=%s requested=%s"
            % (manifest_language, requested)
        )


def _resolve_model_path(model_root: str, raw_path: Any) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(model_root, path))


def _load_manifest(*, model_root: str, voice_bank_path: str) -> dict[str, Any]:
    candidates = []
    if str(voice_bank_path or "").strip():
        candidates.append(str(voice_bank_path).strip())
    candidates.append(os.path.join(model_root, "natsume_gpt_sovits_model_manifest.json"))
    for raw_path in candidates:
        path = raw_path if os.path.isabs(raw_path) else os.path.join(model_root, raw_path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except Exception as exc:
            raise RuntimeError("invalid GPT-SoVITS voice manifest %s: %s" % (path, exc)) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("GPT-SoVITS voice manifest must be an object: %s" % path)
        payload["_manifest_path"] = os.path.abspath(path)
        return payload
    return {}


def _latest_weight(model_root: str, *, kind: str) -> str:
    for folder, suffix in _WEIGHT_DIRS[kind]:
        root = os.path.join(model_root, folder)
        if not os.path.isdir(root):
            continue
        candidates = [
            os.path.join(root, name)
            for name in os.listdir(root)
            if str(name).lower().endswith(suffix)
        ]
        if candidates:
            return max(candidates, key=lambda path: (os.path.getmtime(path), os.path.basename(path)))
    return ""


def _resolve_weights(*, model_root: str, manifest: dict[str, Any]) -> tuple[str, str]:
    weights = manifest.get("weights") if isinstance(manifest.get("weights"), dict) else {}
    t2s = _resolve_model_path(
        model_root,
        weights.get("t2s") or weights.get("gpt") or weights.get("t2s_weights_path"),
    )
    vits = _resolve_model_path(
        model_root,
        weights.get("vits") or weights.get("sovits") or weights.get("vits_weights_path"),
    )
    if weights:
        if not os.path.isfile(t2s) or not os.path.isfile(vits):
            raise RuntimeError(
                "GPT-SoVITS manifest checkpoint is missing; refusing cross-project fallback"
            )
    else:
        if not os.path.isfile(t2s):
            t2s = _latest_weight(model_root, kind="t2s")
        if not os.path.isfile(vits):
            vits = _latest_weight(model_root, kind="vits")
    if not t2s or not vits:
        raise RuntimeError("GPT-SoVITS checkpoint files are missing")
    return t2s, vits


def _detect_version(*, t2s_path: str, vits_path: str) -> str:
    probe = (t2s_path + " " + vits_path).lower()
    for marker, version in (
        ("v2proplus", "v2ProPlus"),
        ("v2pro", "v2Pro"),
        ("v4", "v4"),
        ("v3", "v3"),
        ("v2", "v2"),
    ):
        if marker in probe:
            return version
    return "v1"


def _load_reference_bank(
    *,
    model_root: str,
    manifest: dict[str, Any],
    ref_audio_path: str,
    ref_text: str,
    language: str,
) -> dict[str, dict[str, Any]]:
    bank: dict[str, dict[str, Any]] = {}
    raw_refs = manifest.get("references")
    if isinstance(raw_refs, list):
        for item in raw_refs:
            if not isinstance(item, dict):
                continue
            audio_path = _resolve_model_path(model_root, item.get("audio_path"))
            if not os.path.isfile(audio_path):
                continue
            voice_style = _normalize_emotion(item.get("voice_style"))
            emotion_tag = _normalize_emotion(item.get("emotion_tag"))
            if not voice_style and not emotion_tag:
                emotion_tag = "NEUTRAL"
            aux_audio_paths = []
            raw_aux = item.get("aux_audio_paths")
            if isinstance(raw_aux, list):
                for raw_path in raw_aux[:4]:
                    resolved = _resolve_model_path(model_root, raw_path)
                    if resolved and os.path.isfile(resolved) and resolved != audio_path:
                        aux_audio_paths.append(resolved)
            reference = {
                "audio_path": audio_path,
                "aux_audio_paths": aux_audio_paths,
                "prompt_text": str(item.get("prompt_text") or "").strip(),
                "prompt_lang": _normalize_language(item.get("prompt_lang") or language),
            }
            for key in (voice_style, emotion_tag):
                if key:
                    bank.setdefault(key, reference)
    explicit_audio = _resolve_model_path(model_root, ref_audio_path)
    if explicit_audio and os.path.isfile(explicit_audio):
        explicit = {
            "audio_path": explicit_audio,
            "prompt_text": str(ref_text or "").strip(),
            "prompt_lang": _normalize_language(language),
        }
        bank["NEUTRAL"] = explicit
        bank["DEFAULT"] = explicit
    if "NEUTRAL" in bank:
        bank.setdefault("DEFAULT", bank["NEUTRAL"])
    if "DEFAULT" in bank:
        bank.setdefault("NEUTRAL", bank["DEFAULT"])
    if not bank:
        discovered = _discover_reference(model_root=model_root, language=language)
        if discovered:
            bank["NEUTRAL"] = discovered
            bank["DEFAULT"] = discovered
    if not bank:
        raise RuntimeError("voice manifest or EDGE_TTS_REF_AUDIO_PATH must provide a usable reference")
    return bank


def _discover_reference(*, model_root: str, language: str) -> dict[str, str] | None:
    logs_root = os.path.join(model_root, "logs")
    if not os.path.isdir(logs_root):
        return None
    candidates: list[tuple[float, str, str]] = []
    for project in os.listdir(logs_root):
        project_root = os.path.join(logs_root, project)
        wav_root = os.path.join(project_root, "5-wav32k")
        text_path = os.path.join(project_root, "2-name2text.txt")
        if not os.path.isdir(wav_root) or not os.path.isfile(text_path):
            continue
        prompts: dict[str, str] = {}
        with open(text_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4:
                    prompts[parts[0].strip()] = parts[3].strip()
        for name, prompt in prompts.items():
            path = os.path.join(wav_root, name)
            if not prompt or not os.path.isfile(path):
                continue
            try:
                with wave.open(path, "rb") as handle:
                    duration = handle.getnframes() / float(handle.getframerate() or 1)
            except Exception:
                continue
            if 3.0 <= duration <= 10.0:
                candidates.append((abs(duration - 5.0), path, prompt))
    if not candidates:
        return None
    _, path, prompt = min(candidates, key=lambda row: (row[0], row[1]))
    return {
        "audio_path": path,
        "prompt_text": prompt,
        "prompt_lang": _normalize_language(language),
    }


def _select_reference(
    bank: dict[str, dict[str, Any]],
    emotion_tag: Any,
    *,
    voice_profile: Any = None,
) -> dict[str, Any]:
    profile_key = _normalize_emotion(voice_profile)
    emotion_key = _normalize_emotion(emotion_tag)
    for candidate in (
        profile_key,
        _EMOTION_ALIASES.get(profile_key, ""),
        emotion_key,
        _EMOTION_ALIASES.get(emotion_key, ""),
        "NEUTRAL",
        "DEFAULT",
    ):
        if candidate and candidate in bank:
            return bank[candidate]
    return next(iter(bank.values()))


class _DirectSoVITSRuntime:
    def __init__(self, args: argparse.Namespace) -> None:
        self.model_root = os.path.abspath(str(args.model_path))
        if not os.path.isdir(self.model_root):
            raise RuntimeError("GPT-SoVITS model root does not exist: %s" % self.model_root)
        self.language = _normalize_language(args.language)
        self.manifest = _load_manifest(
            model_root=self.model_root,
            voice_bank_path=str(args.voice_bank_path or ""),
        )
        _validate_manifest_language(manifest=self.manifest, requested_language=self.language)
        self.t2s_path, self.vits_path = _resolve_weights(
            model_root=self.model_root,
            manifest=self.manifest,
        )
        self.references = _load_reference_bank(
            model_root=self.model_root,
            manifest=self.manifest,
            ref_audio_path=str(args.ref_audio_path or ""),
            ref_text=str(args.ref_text or ""),
            language=self.language,
        )

        os.chdir(self.model_root)
        for path in (self.model_root, os.path.join(self.model_root, "GPT_SoVITS")):
            if path not in sys.path:
                sys.path.insert(0, path)
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        custom = {
            "device": str(args.device),
            "is_half": "cuda" in str(args.device).lower(),
            "version": _detect_version(t2s_path=self.t2s_path, vits_path=self.vits_path),
            "t2s_weights_path": self.t2s_path,
            "vits_weights_path": self.vits_path,
            "bert_base_path": os.path.join(
                self.model_root,
                "GPT_SoVITS",
                "pretrained_models",
                "chinese-roberta-wwm-ext-large",
            ),
            "cnhuhbert_base_path": os.path.join(
                self.model_root,
                "GPT_SoVITS",
                "pretrained_models",
                "chinese-hubert-base",
            ),
        }
        for key in ("bert_base_path", "cnhuhbert_base_path"):
            if not os.path.isdir(custom[key]):
                raise RuntimeError("GPT-SoVITS pretrained model is missing: %s" % custom[key])
        self.pipeline = TTS(TTS_Config({"custom": custom}))
        self.model_identity = "%s|%s" % (self.t2s_path, self.vits_path)

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        emotion_tag: str | None,
        voice_profile: str | None,
        speed_factor: float | None,
        energy_factor: float | None,
        pitch_semitones: float | None,
    ) -> tuple[Any, int]:
        import numpy as np

        if not str(text or "").strip():
            raise ValueError("text is empty")
        reference = _select_reference(
            self.references,
            emotion_tag,
            voice_profile=voice_profile,
        )
        inputs = {
            "text": str(text),
            "text_lang": _normalize_language(language or self.language),
            "ref_audio_path": reference["audio_path"],
            "aux_ref_audio_paths": list(reference.get("aux_audio_paths") or []),
            "prompt_text": reference["prompt_text"],
            "prompt_lang": _normalize_language(reference["prompt_lang"]),
            "seed": 42,
            "parallel_infer": True,
            "return_fragment": False,
            "streaming_mode": False,
            "fragment_interval": 0.0,
        }
        if speed_factor is not None:
            inputs["speed_factor"] = float(speed_factor)

        def _run(run_inputs: dict[str, Any]) -> tuple[list[Any], int]:
            chunks = []
            sample_rate = DEFAULT_SAMPLE_RATE
            for sample_rate_value, audio_value in self.pipeline.run(run_inputs):
                sample_rate = int(sample_rate_value)
                if audio_value is not None and len(audio_value) > 0:
                    chunks.append(np.asarray(audio_value))
            return chunks, sample_rate

        try:
            chunks, sample_rate = _run(inputs)
        except TypeError as exc:
            if "speed_factor" not in str(exc) and "unexpected keyword" not in str(exc):
                raise
            fallback = dict(inputs)
            fallback.pop("speed_factor", None)
            chunks, sample_rate = _run(fallback)
        if not chunks:
            raise RuntimeError("GPT-SoVITS produced empty audio")
        audio = _apply_synthesized_prosody(
            np.concatenate(chunks),
            sample_rate,
            energy_factor=energy_factor,
            pitch_semitones=pitch_semitones,
        )
        return audio, sample_rate


def _apply_synthesized_prosody(
    audio_data: Any,
    sample_rate: int,
    *,
    energy_factor: float | None,
    pitch_semitones: float | None,
) -> Any:
    import numpy as np

    raw_audio = np.asarray(audio_data)
    if raw_audio.size == 0:
        return raw_audio.astype(np.float32, copy=False)

    # GPT-SoVITS TTS.run() returns signed PCM (normally int16), while some
    # adapters return already-normalized float samples.  Converting PCM to
    # float without scaling turns values such as 32767 into a square wave as
    # soon as expression gain is clipped to [-1, 1].
    if np.issubdtype(raw_audio.dtype, np.integer):
        info = np.iinfo(raw_audio.dtype)
        pcm_scale = float(max(abs(info.min), abs(info.max)))
        audio = raw_audio.astype(np.float32) / pcm_scale
    else:
        audio = raw_audio.astype(np.float32, copy=False)
        finite_peak = (
            float(np.max(np.abs(audio[np.isfinite(audio)])))
            if np.any(np.isfinite(audio))
            else 0.0
        )
        # A few compatible GPT-SoVITS builds expose PCM values in a float
        # container.  Recognize that representation without rescaling normal
        # model audio whose peak is already close to one.
        if finite_peak > 8.0:
            pcm_scale = 32768.0 if finite_peak <= 32768.0 * 1.25 else finite_peak
            audio = audio / pcm_scale
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0).astype(
        np.float32,
        copy=False,
    )
    pitch = max(-2.0, min(2.0, float(pitch_semitones or 0.0)))
    if abs(pitch) >= 0.01:
        try:
            import librosa
        except ImportError:
            print("TTS pitch requested but librosa is unavailable", file=sys.stderr, flush=True)
        else:
            audio = np.asarray(
                librosa.effects.pitch_shift(audio, sr=max(1, int(sample_rate)), n_steps=pitch),
                dtype=np.float32,
            )
    gain = float(energy_factor if energy_factor is not None else 1.0)
    if abs(gain - 1.0) >= 0.001:
        audio = (audio * gain).astype(np.float32, copy=False)

    # Preserve dynamics instead of hard-clipping peaks into a flat plateau.
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.98:
        audio = (audio * (0.98 / peak)).astype(np.float32, copy=False)
    return audio.astype(np.float32, copy=False)


def _cache_key(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _prune_cache(cache_dir: str, *, max_entries: int) -> None:
    if max_entries <= 0:
        return
    try:
        entries = [os.path.join(cache_dir, name) for name in os.listdir(cache_dir) if name.endswith(".npz")]
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
    # GPT-SoVITS prints progress to stdout. Reserve stdout for JSON-lines IPC.
    sys.stdout = sys.stderr

    try:
        import numpy as np

        load_t0 = time.perf_counter()
        runtime = _DirectSoVITSRuntime(args)
        load_s = time.perf_counter() - load_t0

        cache_enabled = _env_bool("EDGE_TTS_PERSISTENT_WORKER_CACHE_ENABLED", True)
        cache_root = str(
            os.environ.get("EDGE_TTS_PERSISTENT_WORKER_CACHE_DIR")
            or os.path.join(repo_root, "runtime", "edge", "tts_cache")
        )
        cache_max_entries = _env_int(
            "EDGE_TTS_PERSISTENT_WORKER_CACHE_MAX_ENTRIES", 256, minimum=0, maximum=10000
        )
        cache_max_text_chars = _env_int(
            "EDGE_TTS_PERSISTENT_WORKER_CACHE_MAX_TEXT_CHARS", 80, minimum=1, maximum=2000
        )
        if cache_enabled:
            os.makedirs(cache_root, exist_ok=True)

        warmup_enabled = _env_bool("EDGE_TTS_PERSISTENT_WORKER_WARMUP_ENABLED", True)
        warmup_success = None
        warmup_detail = None
        warmup_s = None
        if warmup_enabled:
            warmup_text = str(os.environ.get("EDGE_TTS_PERSISTENT_WORKER_WARMUP_TEXT") or "嗯。").strip() or "嗯。"
            warmup_t0 = time.perf_counter()
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    warmup_audio, _ = runtime.synthesize(
                        text=warmup_text,
                        language=str(args.language or "zh"),
                        emotion_tag="NEUTRAL",
                        voice_profile=None,
                        speed_factor=None,
                        energy_factor=None,
                        pitch_semitones=None,
                    )
                warmup_success = bool(warmup_audio is not None and len(warmup_audio) > 0)
                warmup_detail = "warmup synthesized"
            except Exception as exc:  # pragma: no cover - warmup must not kill worker
                warmup_success = False
                warmup_detail = str(exc)
            warmup_s = time.perf_counter() - warmup_t0
            print(
                "TTS persistent worker warmup: success=%s wall_s=%.4f"
                % (warmup_success, warmup_s),
                file=sys.stderr,
                flush=True,
            )

        _emit(
            protocol_stdout,
            {
                "event": "ready",
                "success": True,
                "detail": "standalone GPT-SoVITS runtime ready",
                "load_s": round(load_s, 4),
                "reason_code": None,
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
                _emit(protocol_stdout, {"event": "error", "success": False, "detail": "invalid json: %s" % exc})
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
                voice_profile = str(request.get("voice_profile") or "").strip() or None
                speed_factor = request.get("speed_factor")
                energy_factor = request.get("energy_factor")
                pitch_semitones = request.get("pitch_semitones")
                cache_path = None
                cache_allowed = bool(cache_enabled and text.strip() and len(text.strip()) <= cache_max_text_chars)
                if cache_allowed:
                    cache_payload = {
                        "model_identity": runtime.model_identity,
                        "language": language,
                        "text": text,
                        "emotion_tag": emotion_tag,
                        "voice_profile": voice_profile,
                        "emotion_intensity": request.get("emotion_intensity"),
                        "speed_factor": speed_factor,
                        "energy_factor": energy_factor,
                        "pitch_semitones": pitch_semitones,
                        "voice_bank_path": str(args.voice_bank_path or ""),
                    }
                    cache_path = os.path.join(cache_root, "%s.npz" % _cache_key(cache_payload))
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
                    audio, sample_rate = runtime.synthesize(
                        text=text,
                        language=language,
                        emotion_tag=emotion_tag,
                        voice_profile=voice_profile,
                        speed_factor=float(speed_factor) if speed_factor is not None else None,
                        energy_factor=float(energy_factor) if energy_factor is not None else None,
                        pitch_semitones=float(pitch_semitones) if pitch_semitones is not None else None,
                    )
                wall_s = time.perf_counter() - synth_t0
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                np.savez(output_path, audio=np.asarray(audio, dtype=np.float32), sample_rate=np.int32(sample_rate))
                cache_stored = False
                if cache_allowed and cache_path:
                    try:
                        cache_tmp = cache_path + ".tmp.npz"
                        np.savez(cache_tmp, audio=np.asarray(audio, dtype=np.float32), sample_rate=np.int32(sample_rate))
                        os.replace(cache_tmp, cache_path)
                        _prune_cache(cache_root, max_entries=cache_max_entries)
                        cache_stored = True
                    except Exception as exc:
                        print("TTS persistent worker cache store failed: %s" % exc, file=sys.stderr, flush=True)
                _emit(
                    protocol_stdout,
                    {
                        "event": "synth_result",
                        "request_id": request_id,
                        "success": True,
                        "detail": "GPT-SoVITS synthesis completed",
                        "sample_rate": int(sample_rate),
                        "duration_s": float(len(audio) / sample_rate) if sample_rate > 0 else 0.0,
                        "processing_time_s": round(wall_s, 4),
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
