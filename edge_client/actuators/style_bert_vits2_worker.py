from __future__ import annotations

"""Standalone Style-Bert-VITS2 worker for the model-specific Python runtime.

The process speaks the same JSON-lines protocol as the existing GPT-SoVITS
worker but deliberately imports no application package.  This lets Edge keep
its lightweight Python while the voice model remains in a pinned external
environment.  A release manifest selects one exact checkpoint and maps
``ExpressionPlanV1.voice_style`` values to Style-Bert-VITS2 style vectors.
"""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


SCHEMA_VERSION = "natsume.style-bert-vits2.edge-release.v1"
DEFAULT_MANIFEST = "natsume_style_bert_vits2_manifest.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python edge_client/actuators/style_bert_vits2_worker.py",
        description="Standalone persistent Style-Bert-VITS2 worker.",
    )
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--language", default="ja")
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_language(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw.startswith("ja") or raw in {"jp", "japanese"}:
        return "ja"
    if raw.startswith("zh") or raw in {"cn", "chinese"}:
        return "zh"
    return raw


def _manifest_path(model_path: str, voice_bank_path: str) -> Path:
    explicit = str(voice_bank_path or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = Path(model_path).expanduser().resolve()
    if root.is_file():
        return root
    return root / DEFAULT_MANIFEST


def _relative_asset(root: Path, raw: Any, *, field_name: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise RuntimeError(f"{field_name} is required")
    candidate = Path(value)
    if candidate.is_absolute():
        raise RuntimeError(f"{field_name} must be relative to the release directory")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} escapes the release directory") from exc
    if not resolved.is_file():
        raise RuntimeError(f"{field_name} is missing: {resolved}")
    return resolved


def load_release_manifest(*, model_path: str, voice_bank_path: str = "") -> dict[str, Any]:
    path = _manifest_path(model_path, voice_bank_path)
    if not path.is_file():
        raise RuntimeError(f"Style-Bert-VITS2 release manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"invalid Style-Bert-VITS2 release manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("unsupported Style-Bert-VITS2 release manifest schema")
    if str(payload.get("backend") or "").strip().lower() != "style_bert_vits2":
        raise RuntimeError("Style-Bert-VITS2 release manifest backend mismatch")
    if _normalize_language(payload.get("voice_language")) != "ja":
        raise RuntimeError("Style-Bert-VITS2 release manifest must declare Japanese voice_language")

    root = path.parent.resolve()
    assets = {
        "model": _relative_asset(root, payload.get("model_file"), field_name="model_file"),
        "config": _relative_asset(root, payload.get("config_file"), field_name="config_file"),
        "styles": _relative_asset(
            root,
            payload.get("style_vectors_file"),
            field_name="style_vectors_file",
        ),
    }
    expected = payload.get("sha256")
    if not isinstance(expected, dict) or set(expected) != set(assets):
        raise RuntimeError("Style-Bert-VITS2 release manifest requires exact asset SHA-256 values")
    for key, asset in assets.items():
        expected_digest = str(expected.get(key) or "").strip().lower()
        actual_digest = _sha256(asset)
        if len(expected_digest) != 64 or actual_digest != expected_digest:
            raise RuntimeError(f"Style-Bert-VITS2 {key} digest mismatch")

    config = json.loads(assets["config"].read_text(encoding="utf-8-sig"))
    raw_style2id = config.get("data", {}).get("style2id") if isinstance(config, dict) else None
    if not isinstance(raw_style2id, dict) or not raw_style2id:
        raise RuntimeError("Style-Bert-VITS2 config has no style2id")
    available_styles = {str(key) for key in raw_style2id}
    raw_map = payload.get("runtime_style_map")
    if not isinstance(raw_map, dict) or not raw_map:
        raise RuntimeError("Style-Bert-VITS2 release manifest has no runtime_style_map")
    style_map: dict[str, dict[str, Any]] = {}
    for raw_profile, raw_binding in raw_map.items():
        if not isinstance(raw_binding, dict):
            raise RuntimeError(f"invalid style binding for {raw_profile}")
        profile = str(raw_profile or "").strip().upper()
        style = str(raw_binding.get("style") or "").strip()
        try:
            weight = float(raw_binding.get("weight"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid style weight for {profile}") from exc
        if not profile or style not in available_styles or not 0.0 <= weight <= 2.0:
            raise RuntimeError(f"invalid style binding for {profile}")
        style_map[profile] = {"style": style, "weight": weight}

    payload = dict(payload)
    payload["_manifest_path"] = str(path)
    payload["_assets"] = assets
    payload["_style_map"] = style_map
    payload["_model_identity"] = "sha256:" + _sha256(path)
    return payload


def resolve_style(
    manifest: dict[str, Any],
    *,
    voice_profile: Any,
) -> tuple[str, float, str]:
    style_map = manifest["_style_map"]
    profile = str(voice_profile or "").strip().upper()
    for candidate in (profile, "STEADY_COMPANION", "CALM_ATTENTIVE"):
        if candidate and candidate in style_map:
            binding = style_map[candidate]
            return str(binding["style"]), float(binding["weight"]), candidate
    config = json.loads(manifest["_assets"]["config"].read_text(encoding="utf-8-sig"))
    style2id = config["data"]["style2id"]
    fallback = "Natural" if "Natural" in style2id else "Neutral"
    if fallback not in style2id:
        fallback = next(iter(style2id))
    return fallback, 0.5, "DEFAULT"


def normalize_audio(audio: Any, *, energy_factor: float | None) -> Any:
    import numpy as np

    value = np.asarray(audio)
    if np.issubdtype(value.dtype, np.integer):
        info = np.iinfo(value.dtype)
        scale = float(max(abs(int(info.min)), abs(int(info.max))))
        value = value.astype(np.float32) / max(scale, 1.0)
    else:
        value = value.astype(np.float32, copy=False)
        peak = float(np.max(np.abs(value))) if value.size else 0.0
        if peak > 1.5:
            value = value / 32768.0
    value = np.nan_to_num(value, nan=0.0, posinf=0.0, neginf=0.0)
    gain = max(0.6, min(1.4, float(energy_factor if energy_factor is not None else 1.0)))
    if abs(gain - 1.0) >= 0.001:
        value = value * gain
    peak = float(np.max(np.abs(value))) if value.size else 0.0
    if peak > 0.98:
        value = value * (0.98 / peak)
    return value.astype(np.float32, copy=False)


class _StyleBertVits2Runtime:
    def __init__(self, args: argparse.Namespace) -> None:
        if _normalize_language(args.language) != "ja":
            raise RuntimeError("Style-Bert-VITS2 Japanese release requires --language ja")
        self.manifest = load_release_manifest(
            model_path=str(args.model_path),
            voice_bank_path=str(args.voice_bank_path or ""),
        )
        assets = self.manifest["_assets"]
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.tts_model import TTSModel

        self._language = Languages.JP
        self._model = TTSModel(
            model_path=assets["model"],
            config_path=assets["config"],
            style_vec_path=assets["styles"],
            device=str(args.device),
        )
        self._model.load()
        self.model_identity = str(self.manifest["_model_identity"])

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        voice_profile: str | None,
        speed_factor: float | None,
        energy_factor: float | None,
        pitch_semitones: float | None,
    ) -> tuple[Any, int, str, float, str]:
        if _normalize_language(language) != "ja":
            raise RuntimeError("Style-Bert-VITS2 Japanese release refuses non-Japanese language")
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise RuntimeError("text is required")
        style, style_weight, resolved_profile = resolve_style(
            self.manifest,
            voice_profile=voice_profile,
        )
        speed = max(0.7, min(1.3, float(speed_factor if speed_factor is not None else 1.0)))
        semitones = max(-3.0, min(3.0, float(pitch_semitones if pitch_semitones is not None else 0.0)))
        sample_rate, audio = self._model.infer(
            normalized_text,
            language=self._language,
            speaker_id=0,
            sdp_ratio=0.35,
            noise=0.55,
            noise_w=0.75,
            length=1.0 / speed,
            style=style,
            style_weight=style_weight,
            pitch_scale=2.0 ** (semitones / 12.0),
        )
        return (
            normalize_audio(audio, energy_factor=energy_factor),
            int(sample_rate),
            style,
            style_weight,
            resolved_profile,
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    protocol_stdout = sys.stdout
    os.environ["EDGE_TTS_PERSISTENT_WORKER_CHILD"] = "1"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout = sys.stderr
    try:
        import numpy as np

        load_started = time.perf_counter()
        runtime = _StyleBertVits2Runtime(args)
        load_s = time.perf_counter() - load_started
        warmup_enabled = _env_bool("EDGE_TTS_PERSISTENT_WORKER_WARMUP_ENABLED", True)
        warmup_s = None
        if warmup_enabled:
            warmup_started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                runtime.synthesize(
                    text=str(os.environ.get("EDGE_TTS_PERSISTENT_WORKER_WARMUP_TEXT_JA") or "うん。"),
                    language="ja",
                    voice_profile="STEADY_COMPANION",
                    speed_factor=None,
                    energy_factor=None,
                    pitch_semitones=None,
                )
            warmup_s = time.perf_counter() - warmup_started
        _emit(
            protocol_stdout,
            {
                "event": "ready",
                "success": True,
                "detail": "standalone Style-Bert-VITS2 runtime ready",
                "load_s": round(load_s, 4),
                "warmup_enabled": warmup_enabled,
                "warmup_s": round(warmup_s, 4) if warmup_s is not None else None,
                "model_identity": runtime.model_identity,
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
            started = time.perf_counter()
            try:
                if not output_path:
                    raise RuntimeError("output_path is required")
                with contextlib.redirect_stdout(sys.stderr):
                    audio, sample_rate, style, weight, profile = runtime.synthesize(
                        text=str(request.get("text") or ""),
                        language=str(request.get("language") or args.language),
                        voice_profile=str(request.get("voice_profile") or "").strip() or None,
                        speed_factor=float(request["speed_factor"]) if request.get("speed_factor") is not None else None,
                        energy_factor=float(request["energy_factor"]) if request.get("energy_factor") is not None else None,
                        pitch_semitones=float(request["pitch_semitones"]) if request.get("pitch_semitones") is not None else None,
                    )
                destination = Path(output_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    destination,
                    audio=np.asarray(audio, dtype=np.float32),
                    sample_rate=np.int32(sample_rate),
                )
                wall_s = time.perf_counter() - started
                _emit(
                    protocol_stdout,
                    {
                        "event": "synth_result",
                        "request_id": request_id,
                        "success": True,
                        "detail": "Style-Bert-VITS2 synthesis completed",
                        "sample_rate": sample_rate,
                        "duration_s": float(len(audio) / sample_rate),
                        "processing_time_s": round(wall_s, 4),
                        "wall_s": round(wall_s, 4),
                        "style": style,
                        "style_weight": weight,
                        "resolved_voice_profile": profile,
                        "model_identity": runtime.model_identity,
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
                        "wall_s": round(time.perf_counter() - started, 4),
                    },
                )
    except Exception as exc:
        _emit(protocol_stdout, {"event": "ready", "success": False, "detail": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
