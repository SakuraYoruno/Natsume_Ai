"""Gateway voice-message ASR (on-demand, non-resident).

This module transcribes inbound gateway voice media (e.g. .silk/.wav) only when
voice messages arrive, so we can disable always-on microphone ASR while keeping
voice-message understanding.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from edge_client.gateway.models import MsgType, Platform, UnifiedMessage
from edge_client.senses.asr import AsrConfig, WhisperModelManager

logger = logging.getLogger("Natsume.edge.gateway.voice_asr")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float, min_value: float, max_value: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return float(max(min_value, min(max_value, parsed)))


def _parse_int_env(name: str, *, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return int(default)
    return int(max(min_value, min(max_value, parsed)))


def _parse_text_env(name: str, *, default: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    return value or default


class GatewayVoiceAsr:
    """On-demand ASR for gateway voice messages."""

    def __init__(self) -> None:
        self.enabled = _parse_bool_env("EDGE_GATEWAY_VOICE_ASR_ENABLED", default=True)
        language_default = _parse_text_env("EDGE_ASR_LANGUAGE", default="ja")
        self._language = _parse_text_env("EDGE_GATEWAY_VOICE_ASR_LANGUAGE", default=language_default)
        self._min_confidence = _parse_float_env(
            "EDGE_GATEWAY_VOICE_ASR_MIN_CONFIDENCE",
            default=0.55,
            min_value=0.0,
            max_value=1.0,
        )
        self._min_text_len = _parse_int_env(
            "EDGE_GATEWAY_VOICE_ASR_MIN_TEXT_LEN",
            default=2,
            min_value=1,
            max_value=4096,
        )
        self._target_sample_rate = 16000
        self._model_lock = threading.Lock()
        self._model_manager: WhisperModelManager | None = None
        self._config = AsrConfig(
            model_size=_parse_text_env(
                "EDGE_GATEWAY_VOICE_ASR_MODEL_SIZE",
                default=_parse_text_env("EDGE_ASR_MODEL_SIZE", default="large-v3"),
            ),
            device=_parse_text_env(
                "EDGE_GATEWAY_VOICE_ASR_DEVICE",
                default=_parse_text_env("EDGE_ASR_DEVICE", default="cuda"),
            ),
            compute_type=_parse_text_env(
                "EDGE_GATEWAY_VOICE_ASR_COMPUTE_TYPE",
                default=_parse_text_env("EDGE_ASR_COMPUTE_TYPE", default="float16"),
            ),
            beam_size=_parse_int_env(
                "EDGE_GATEWAY_VOICE_ASR_BEAM_SIZE",
                default=_parse_int_env("EDGE_ASR_BEAM_SIZE", default=5, min_value=1, max_value=64),
                min_value=1,
                max_value=64,
            ),
            sample_rate=self._target_sample_rate,
            channels=1,
            chunk_duration_s=0.5,
            vad_threshold=_parse_float_env(
                "EDGE_ASR_VAD_THRESHOLD",
                default=0.5,
                min_value=0.0,
                max_value=1.0,
            ),
            silence_duration_s=_parse_float_env(
                "EDGE_ASR_SILENCE_DURATION_S",
                default=0.8,
                min_value=0.05,
                max_value=10.0,
            ),
            min_speech_duration_s=_parse_float_env(
                "EDGE_ASR_MIN_SPEECH_DURATION_S",
                default=0.3,
                min_value=0.05,
                max_value=10.0,
            ),
            max_speech_duration_s=_parse_float_env(
                "EDGE_ASR_MAX_SPEECH_DURATION_S",
                default=30.0,
                min_value=1.0,
                max_value=600.0,
            ),
            precheck_min_peak=_parse_float_env(
                "EDGE_ASR_PRECHECK_MIN_PEAK",
                default=0.01,
                min_value=0.0,
                max_value=1.0,
            ),
            precheck_min_rms=_parse_float_env(
                "EDGE_ASR_PRECHECK_MIN_RMS",
                default=0.002,
                min_value=0.0,
                max_value=1.0,
            ),
            use_vad_filter=_parse_bool_env("EDGE_ASR_USE_VAD_FILTER", default=True),
            vad_retry_without_filter_on_empty=_parse_bool_env(
                "EDGE_ASR_VAD_RETRY_WITHOUT_FILTER_ON_EMPTY",
                default=True,
            ),
            vad_retry_min_peak=_parse_float_env(
                "EDGE_ASR_VAD_RETRY_MIN_PEAK",
                default=0.015,
                min_value=0.0,
                max_value=1.0,
            ),
            language=self._language,
            initial_prompt=str(os.environ.get("EDGE_ASR_INITIAL_PROMPT") or "").strip() or None,
        )

    def transcribe(self, msg: UnifiedMessage) -> dict[str, Any] | None:
        if not self.enabled or msg.msg_type != MsgType.VOICE:
            return None

        media_ref = self._resolve_media_ref(msg)
        if not media_ref:
            logger.info("[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=MEDIA_MISSING", msg.msg_id)
            return None

        resolved_media_ref = self._resolve_media_for_decode(msg, media_ref)
        audio_data = self._load_audio_array(resolved_media_ref)
        if audio_data is None:
            logger.warning(
                "[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=MEDIA_UNSUPPORTED media=%s",
                msg.msg_id,
                resolved_media_ref,
            )
            return None

        manager = self._get_model_manager()
        result = manager.transcribe(
            audio_data,
            language=self._language,
            beam_size=self._config.beam_size,
            vad_filter=self._config.use_vad_filter,
        )
        if not result.success or not result.segments:
            logger.info(
                "[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=%s detail=%s",
                msg.msg_id,
                str(result.reason_code or "ASR_EMPTY"),
                str(result.detail or "")[:120],
            )
            return None

        transcript = self._combine_segments(result.segments)
        if not transcript:
            logger.info("[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=TEXT_EMPTY", msg.msg_id)
            return None
        if len(transcript) < self._min_text_len:
            logger.info(
                "[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=TEXT_TOO_SHORT len=%d",
                msg.msg_id,
                len(transcript),
            )
            return None

        avg_confidence = sum(float(seg.confidence) for seg in result.segments) / max(1, len(result.segments))
        if avg_confidence < self._min_confidence:
            logger.info(
                "[EDGE_VOICE_MON][GATEWAY_ASR] skipped msg_id=%s reason=LOW_CONF conf=%.3f",
                msg.msg_id,
                avg_confidence,
            )
            return None

        logger.info(
            "[EDGE_VOICE_MON][GATEWAY_ASR] accepted msg_id=%s conf=%.3f text=\"%s\"",
            msg.msg_id,
            avg_confidence,
            transcript[:180],
        )
        return {
            "text": transcript,
            "confidence": round(avg_confidence, 4),
            "language": str(result.language or self._language),
            "media_ref": resolved_media_ref,
            "segments": len(result.segments),
        }

    def _get_model_manager(self) -> WhisperModelManager:
        with self._model_lock:
            if self._model_manager is None:
                self._model_manager = WhisperModelManager(self._config)
            return self._model_manager

    @staticmethod
    def _resolve_media_ref(msg: UnifiedMessage) -> str:
        direct = str(msg.media_url or "").strip()
        if direct:
            return direct
        raw_extra = dict(msg.raw_extra or {})
        message_segments = raw_extra.get("message")
        if isinstance(message_segments, list):
            for seg in message_segments:
                if not isinstance(seg, dict):
                    continue
                seg_type = str(seg.get("type") or "").strip().lower()
                if seg_type not in {"record", "voice"}:
                    continue
                data = seg.get("data")
                if not isinstance(data, dict):
                    continue
                for key in ("file", "url", "path"):
                    value = str(data.get(key) or "").strip()
                    if value:
                        return value
        return ""

    def _load_audio_array(self, media_ref: str) -> Any | None:
        normalized = str(media_ref or "").strip()
        if not normalized:
            return None
        if normalized.startswith("base64://"):
            return None
        if normalized.startswith("file://"):
            normalized = normalized.removeprefix("file://")
            normalized = normalized.removeprefix("/")
        if normalized.startswith("file:///"):
            normalized = normalized.removeprefix("file:///")
        path = Path(normalized)
        if not path.exists():
            return None

        suffix = path.suffix.lower()
        if suffix in {".silk", ".amr"}:
            from edge_client.gateway.media.voice_handler import decode_silk_to_pcm  # noqa: PLC0415

            pcm_bytes = decode_silk_to_pcm(path)
            if not pcm_bytes:
                return None
            return self._pcm_to_float32(
                pcm_bytes=pcm_bytes,
                sample_rate=16000,
                channels=1,
            )

        if suffix == ".wav":
            return self._wav_to_float32(path)

        if suffix == ".pcm":
            pcm_bytes = path.read_bytes()
            if not pcm_bytes:
                return None
            return self._pcm_to_float32(
                pcm_bytes=pcm_bytes,
                sample_rate=16000,
                channels=1,
            )

        return None

    def _resolve_media_for_decode(self, msg: UnifiedMessage, media_ref: str) -> str:
        normalized = str(media_ref or "").strip()
        if not normalized:
            return ""
        if self._is_local_media_ref(normalized):
            return normalized

        if msg.platform == Platform.QQ:
            qq_local_media = self._try_resolve_qq_record_to_local(msg, normalized)
            if qq_local_media:
                return qq_local_media

        if normalized.startswith("http://") or normalized.startswith("https://"):
            downloaded = self._download_remote_media(normalized)
            if downloaded:
                return downloaded

        return normalized

    @staticmethod
    def _is_local_media_ref(media_ref: str) -> bool:
        normalized = str(media_ref or "").strip()
        if not normalized:
            return False
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return False
        if normalized.startswith("base64://"):
            return False
        if normalized.startswith("file://"):
            normalized = normalized.removeprefix("file://")
            normalized = normalized.removeprefix("/")
        if normalized.startswith("file:///"):
            normalized = normalized.removeprefix("file:///")
        return Path(normalized).exists()

    def _try_resolve_qq_record_to_local(self, msg: UnifiedMessage, media_ref: str) -> str | None:
        token = self._extract_qq_record_file_token(msg, media_ref)
        if not token:
            return None
        if self._is_local_media_ref(token):
            return token

        api_base = str(os.environ.get("QQ_ONEBOT_API_BASE") or "").strip().rstrip("/")
        if not api_base:
            return None
        try:
            import httpx
        except Exception:
            return None

        try:
            resp = httpx.get(
                f"{api_base}/get_record",
                params={"file": token, "out_format": "wav"},
                timeout=8.0,
            )
            resp.raise_for_status()
            body = resp.json()
            status_ok = str(body.get("status") or "").strip().lower() == "ok"
            ret_ok = int(body.get("retcode") or 0) == 0
            if not (status_ok or ret_ok):
                return None
            data = body.get("data")
            if not isinstance(data, dict):
                return None
            candidate = str(data.get("file") or data.get("path") or "").strip()
            if not candidate:
                return None
            if self._is_local_media_ref(candidate):
                logger.info(
                    "[EDGE_VOICE_MON][GATEWAY_ASR] qq get_record resolved msg_id=%s file=%s",
                    msg.msg_id,
                    candidate,
                )
                return candidate
            if candidate.startswith("http://") or candidate.startswith("https://"):
                downloaded = self._download_remote_media(candidate)
                if downloaded:
                    return downloaded
        except Exception as exc:
            logger.info(
                "[EDGE_VOICE_MON][GATEWAY_ASR] qq get_record failed msg_id=%s detail=%s",
                msg.msg_id,
                str(exc)[:120],
            )
            return None
        return None

    @staticmethod
    def _extract_qq_record_file_token(msg: UnifiedMessage, media_ref: str) -> str:
        direct = str(media_ref or "").strip()
        if direct:
            return direct
        raw_extra = dict(msg.raw_extra or {})
        message_segments = raw_extra.get("message")
        if isinstance(message_segments, list):
            for seg in message_segments:
                if not isinstance(seg, dict):
                    continue
                seg_type = str(seg.get("type") or "").strip().lower()
                if seg_type not in {"record", "voice"}:
                    continue
                data = seg.get("data")
                if not isinstance(data, dict):
                    continue
                value = str(data.get("file") or data.get("url") or data.get("path") or "").strip()
                if value:
                    return value
        return ""

    def _download_remote_media(self, url: str) -> str | None:
        try:
            import httpx
        except Exception:
            return None
        try:
            resp = httpx.get(url, timeout=12.0)
            resp.raise_for_status()
            content = bytes(resp.content or b"")
            if not content:
                return None
            suffix = Path(url.split("?", 1)[0]).suffix.lower()
            if suffix not in {".wav", ".pcm", ".silk", ".amr"}:
                suffix = ".wav"
            temp_dir = Path("runtime") / "edge" / "temp" / "gateway_asr"
            temp_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            temp_file = temp_dir / f"gateway_asr_{ts}_{uuid.uuid4().hex[:8]}{suffix}"
            temp_file.write_bytes(content)
            return str(temp_file)
        except Exception:
            return None

    def _wav_to_float32(self, path: Path) -> Any | None:
        try:
            with wave.open(str(path), "rb") as wf:
                channels = int(wf.getnchannels())
                sample_rate = int(wf.getframerate())
                sample_width = int(wf.getsampwidth())
                frames = wf.readframes(wf.getnframes())
        except Exception:
            return None

        if sample_width != 2:
            return None
        return self._pcm_to_float32(
            pcm_bytes=frames,
            sample_rate=sample_rate,
            channels=channels,
        )

    def _pcm_to_float32(self, *, pcm_bytes: bytes, sample_rate: int, channels: int) -> Any | None:
        if not pcm_bytes:
            return None
        try:
            import numpy as np
        except Exception:
            logger.warning("[EDGE_VOICE_MON][GATEWAY_ASR] numpy missing; cannot decode PCM audio")
            return None

        try:
            audio_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            if audio_i16.size == 0:
                return None
            if channels > 1:
                usable = (audio_i16.size // channels) * channels
                if usable <= 0:
                    return None
                audio_i16 = audio_i16[:usable].reshape(-1, channels).mean(axis=1).astype(np.int16)
            audio = audio_i16.astype(np.float32) / 32768.0
            if sample_rate != self._target_sample_rate and audio.size > 1:
                duration_s = float(audio.size) / float(max(1, sample_rate))
                target_size = max(1, int(round(duration_s * self._target_sample_rate)))
                src_x = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
                dst_x = np.linspace(0.0, 1.0, num=target_size, endpoint=False)
                audio = np.interp(dst_x, src_x, audio).astype(np.float32)
            return audio
        except Exception:
            return None

    @staticmethod
    def _combine_segments(segments: list[Any]) -> str:
        parts: list[str] = []
        for seg in segments:
            text = str(getattr(seg, "text", "") or "").strip()
            if text:
                parts.append(text)
        if not parts:
            return ""
        merged = " ".join(parts)
        return " ".join(merged.split())
