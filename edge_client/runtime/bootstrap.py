"""Edge runtime bootstrap and lifecycle helpers."""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from edge_client.actuators.speech import SpeechPlaybackQueue
from edge_client.actuators.tts_engine import (
    AudioPlaybackDevice,
    SoVITSModelManager,
    TtsConfig,
    make_audio_playback_starter,
    make_sovits_synthesizer,
)
from edge_client.actuators.vts_driver import ReconnectConfig, VTubeStudioDriver, VtsConfig
from edge_client.channels.input_reporter import EdgeInputReporter
from edge_client.channels.wechat_listener import WeChatListener, WeChatListenerConfig
from edge_client.config import EdgeConfig, build_edge_runtime_statuses
from edge_client.rpc.event_reporter import EdgeEventCache, EdgeEventReporter, build_http_transport
from edge_client.rpc.fallback_llm import EdgeFallbackRpcContract
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import VoiceRpcContract
from edge_client.runtime.voice_pipeline import EdgeVoicePipeline
from edge_client.senses.asr import AsrConfig, AsrService, AudioStreamCapture, DeviceRecoveryConfig


LOGGER = logging.getLogger("Natsume.edge.runtime.bootstrap")


@dataclass
class EdgeRuntime:
    """Aggregates edge runtime components for RPC gateway wiring."""

    config: EdgeConfig
    voice_rpc: VoiceRpcContract
    fallback_rpc: EdgeFallbackRpcContract
    speech_queue: SpeechPlaybackQueue
    event_reporter: EdgeEventReporter
    voice_pipeline: EdgeVoicePipeline
    resource_gate: VoiceResourceGate | None
    asr_service: AsrService | None
    tts_model: SoVITSModelManager | None
    tts_playback_device: AudioPlaybackDevice | None
    vts_driver: VTubeStudioDriver | None
    wechat_listener: WeChatListener | None = None

    @property
    def asr(self) -> AsrService | None:
        return self.asr_service

    @property
    def tts(self) -> SoVITSModelManager | None:
        return self.tts_model

    @property
    def vts(self) -> VTubeStudioDriver | None:
        return self.vts_driver

    @property
    def fallback(self) -> EdgeFallbackRpcContract:
        return self.fallback_rpc

    @property
    def rpc_client(self) -> VoiceRpcContract:
        return self.voice_rpc

    def service_statuses(self) -> dict[str, dict[str, Any]]:
        return build_edge_runtime_statuses(self.config)

    def redacted_config_snapshot(self) -> dict[str, Any]:
        row = self.config.as_dict()
        row["hmac_shared_key_ref"] = self.config.hmac_shared_key_ref
        row["hmac_shared_key_set"] = bool(self.config.hmac_shared_key)
        return row

    def close(self) -> dict[str, Any]:
        """Best-effort graceful shutdown of runtime-owned resources."""

        outcome = {
            "asr_stopped": True,
            "tts_playback_stopped": True,
            "tts_unloaded": True,
            "vts_disconnected": True,
            "wechat_listener_stopped": True,
            "errors": [],
        }

        if self.asr_service is not None:
            try:
                stop_result = self.asr_service.stop_session()
                outcome["asr_stopped"] = bool(stop_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["asr_stopped"] = False
                outcome["errors"].append(f"asr stop failed: {exc}")

        if self.tts_playback_device is not None:
            try:
                stop_result = self.tts_playback_device.stop()
                outcome["tts_playback_stopped"] = bool(stop_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["tts_playback_stopped"] = False
                outcome["errors"].append(f"tts playback stop failed: {exc}")

        if self.tts_model is not None:
            try:
                self.tts_model.unload()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["tts_unloaded"] = False
                outcome["errors"].append(f"tts unload failed: {exc}")

        if self.vts_driver is not None:
            try:
                disconnect_result = self.vts_driver.disconnect()
                outcome["vts_disconnected"] = bool(disconnect_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["vts_disconnected"] = False
                outcome["errors"].append(f"vts disconnect failed: {exc}")

        if self.wechat_listener is not None:
            try:
                self.wechat_listener.stop()
            except Exception as exc:  # pragma: no cover
                outcome["wechat_listener_stopped"] = False
                outcome["errors"].append(f"wechat listener stop failed: {exc}")

        return outcome


def build_edge_runtime(config: EdgeConfig) -> EdgeRuntime:
    """Build runtime components from validated edge configuration."""

    env_lookup = _normalize_env_lookup(os.environ)
    resource_gate = (
        VoiceResourceGate(
            vram_soft_limit_ratio=_parse_float(
                _env_get(env_lookup, "EDGE_RESOURCE_GATE_VRAM_SOFT_LIMIT_RATIO"),
                default=0.90,
                min_value=0.1,
                max_value=1.0,
            ),
            max_fallback_queue_depth=_parse_int(
                _env_get(env_lookup, "EDGE_RESOURCE_GATE_MAX_FALLBACK_QUEUE_DEPTH"),
                default=1,
                min_value=0,
                max_value=1024,
            ),
        )
        if config.resource_gate_enabled
        else None
    )
    voice_rpc = VoiceRpcContract(
        resource_gate=resource_gate,
    )

    tts_cfg = TtsConfig(
        model_path=_as_text(_env_get(env_lookup, "EDGE_TTS_MODEL_PATH"), "models/gpt_sovits"),
        device=_as_text(_env_get(env_lookup, "EDGE_TTS_DEVICE"), "cuda"),
        sample_rate=_parse_int(
            _env_get(env_lookup, "EDGE_TTS_SAMPLE_RATE"),
            default=32000,
            min_value=8000,
            max_value=96000,
        ),
        max_text_length=_parse_int(
            _env_get(env_lookup, "EDGE_TTS_MAX_TEXT_LENGTH"),
            default=500,
            min_value=1,
            max_value=20000,
        ),
        synth_timeout_s=_parse_float(
            _env_get(env_lookup, "EDGE_TTS_SYNTH_TIMEOUT_S"),
            default=30.0,
            min_value=0.1,
            max_value=600.0,
        ),
        ref_audio_path=_as_optional_text(_env_get(env_lookup, "EDGE_TTS_REF_AUDIO_PATH")),
        ref_text=_as_optional_text(_env_get(env_lookup, "EDGE_TTS_REF_TEXT")),
        language=_as_text(_env_get(env_lookup, "EDGE_TTS_LANGUAGE"), "ja"),
    )
    tts_playback_device_index = _parse_optional_int(
        _env_get(env_lookup, "EDGE_TTS_PLAYBACK_DEVICE_INDEX"),
        min_value=0,
        max_value=4096,
    )
    tts_model = SoVITSModelManager(config=tts_cfg) if config.tts_enabled else None
    playback_device = (
        AudioPlaybackDevice(
            sample_rate=tts_cfg.sample_rate,
            device_index=tts_playback_device_index,
        )
        if config.tts_enabled
        else None
    )
    synthesizer = make_sovits_synthesizer(tts_model) if tts_model is not None else None
    playback_starter = (
        make_audio_playback_starter(playback_device)
        if playback_device is not None
        else None
    )
    speech_queue = SpeechPlaybackQueue(
        rpc=voice_rpc,
        resource_gate=resource_gate,
        synthesizer=synthesizer,
        playback_starter=playback_starter,
    )

    fallback_rpc = EdgeFallbackRpcContract(
        resource_gate=resource_gate,
        available=config.fallback_llm_enabled,
        default_unloaded=(not config.fallback_llm_enabled),
        model_id=_as_text(_env_get(env_lookup, "EDGE_FALLBACK_LLM_MODEL_ID"), "ollama:3b-q4"),
    )

    vts_cfg = VtsConfig(
        host=_as_text(_env_get(env_lookup, "EDGE_VTS_HOST"), "localhost"),
        port=_parse_int(
            _env_get(env_lookup, "EDGE_VTS_PORT"),
            default=8001,
            min_value=1,
            max_value=65535,
        ),
        plugin_name=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_NAME"), "NatsumeAi"),
        plugin_developer=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_DEVELOPER"), "Natsume"),
        token_path=_as_text(_env_get(env_lookup, "EDGE_VTS_TOKEN_PATH"), ".vts_token"),
        api_timeout_s=_parse_float(
            _env_get(env_lookup, "EDGE_VTS_API_TIMEOUT_S"),
            default=5.0,
            min_value=0.1,
            max_value=300.0,
        ),
        mouth_param_candidates=_parse_csv_tuple(
            _env_get(env_lookup, "EDGE_VTS_MOUTH_PARAM_CANDIDATES"),
            default=("MouthOpen", "ParamMouthOpenY"),
        ),
        mouth_volume_param_candidates=_parse_csv_tuple(
            _env_get(env_lookup, "EDGE_VTS_MOUTH_VOLUME_PARAM_CANDIDATES"),
            default=("VoiceVolumePlusMouthOpen", "VoiceVolume"),
        ),
        voice_silence_param_candidates=_parse_csv_tuple(
            _env_get(env_lookup, "EDGE_VTS_VOICE_SILENCE_PARAM_CANDIDATES"),
            default=("VoiceSilence",),
        ),
        reconnect=ReconnectConfig(
            max_attempts=_parse_int(
                _env_get(env_lookup, "EDGE_VTS_RECONNECT_MAX_ATTEMPTS"),
                default=10,
                min_value=1,
                max_value=1000,
            ),
            base_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_VTS_RECONNECT_BASE_DELAY_S"),
                default=1.0,
                min_value=0.1,
                max_value=300.0,
            ),
            max_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_VTS_RECONNECT_MAX_DELAY_S"),
                default=16.0,
                min_value=0.1,
                max_value=3600.0,
            ),
            auto_reconnect=_parse_bool(
                _env_get(env_lookup, "EDGE_VTS_RECONNECT_AUTO"),
                default=True,
            ),
        ),
    )

    vts_driver = VTubeStudioDriver(config=vts_cfg)
    event_report_host_url = _as_optional_text(_env_get(env_lookup, "EDGE_EVENT_REPORT_HOST_URL"))
    event_reporter = EdgeEventReporter(
        transport_fn=(
            build_http_transport(
                host_url=event_report_host_url,
                timeout_s=_parse_float(
                    _env_get(env_lookup, "EDGE_EVENT_REPORT_TIMEOUT_S"),
                    default=3.0,
                    min_value=0.1,
                    max_value=300.0,
                ),
            )
            if event_report_host_url
            else None
        ),
        cache=EdgeEventCache(
            max_events=_parse_int(
                _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_CACHE_EVENTS"),
                default=1000,
                min_value=1,
                max_value=1_000_000,
            ),
            max_bytes=_parse_int(
                _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_CACHE_BYTES"),
                default=10 * 1024 * 1024,
                min_value=1024,
                max_value=1024 * 1024 * 1024,
            ),
        ),
        max_retry=_parse_int(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_RETRY_ATTEMPTS"),
            default=5,
            min_value=1,
            max_value=1000,
        ),
        base_delay_s=_parse_float(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_BASE_RETRY_DELAY_S"),
            default=1.0,
            min_value=0.0,
            max_value=3600.0,
        ),
        max_delay_s=_parse_float(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_RETRY_DELAY_S"),
            default=30.0,
            min_value=0.0,
            max_value=3600.0,
        ),
    )
    voice_pipeline = EdgeVoicePipeline(
        voice_rpc=voice_rpc,
        speech_queue=speech_queue,
        event_reporter=event_reporter,
        vts_driver=vts_driver,
        resource_gate=resource_gate,
    )
    if playback_device is not None:
        playback_device.set_on_playback_finished(lambda: voice_pipeline.complete_playback())
    asr_cfg = AsrConfig(
        model_size=_as_text(_env_get(env_lookup, "EDGE_ASR_MODEL_SIZE"), "large-v3"),
        device=_as_text(_env_get(env_lookup, "EDGE_ASR_DEVICE"), "cuda"),
        compute_type=_as_text(_env_get(env_lookup, "EDGE_ASR_COMPUTE_TYPE"), "float16"),
        beam_size=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_BEAM_SIZE"),
            default=5,
            min_value=1,
            max_value=64,
        ),
        sample_rate=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_SAMPLE_RATE"),
            default=16000,
            min_value=8000,
            max_value=96000,
        ),
        channels=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_CHANNELS"),
            default=1,
            min_value=1,
            max_value=8,
        ),
        chunk_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_CHUNK_DURATION_S"),
            default=0.5,
            min_value=0.05,
            max_value=10.0,
        ),
        vad_threshold=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_VAD_THRESHOLD"),
            default=0.5,
            min_value=0.0,
            max_value=1.0,
        ),
        silence_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_SILENCE_DURATION_S"),
            default=0.8,
            min_value=0.05,
            max_value=10.0,
        ),
        min_speech_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_MIN_SPEECH_DURATION_S"),
            default=0.3,
            min_value=0.05,
            max_value=10.0,
        ),
        max_speech_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_MAX_SPEECH_DURATION_S"),
            default=30.0,
            min_value=1.0,
            max_value=600.0,
        ),
        vad_retry_without_filter_on_empty=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_VAD_RETRY_WITHOUT_FILTER_ON_EMPTY"),
            default=True,
        ),
        vad_retry_min_peak=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_VAD_RETRY_MIN_PEAK"),
            default=0.015,
            min_value=0.0,
            max_value=1.0,
        ),
        language=_as_text(_env_get(env_lookup, "EDGE_ASR_LANGUAGE"), "ja"),
        initial_prompt=_as_optional_text(_env_get(env_lookup, "EDGE_ASR_INITIAL_PROMPT")),
        recovery=DeviceRecoveryConfig(
            max_attempts=_parse_int(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_MAX_ATTEMPTS"),
                default=5,
                min_value=1,
                max_value=100,
            ),
            base_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_BASE_DELAY_S"),
                default=1.0,
                min_value=0.1,
                max_value=120.0,
            ),
            max_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_MAX_DELAY_S"),
                default=8.0,
                min_value=0.1,
                max_value=600.0,
            ),
            auto_recover=_parse_bool(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_AUTO"),
                default=True,
            ),
            try_alternate_devices=_parse_bool(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_TRY_ALTERNATE_DEVICES"),
                default=True,
            ),
        ),
    )
    asr_audio_capture = AudioStreamCapture(
        sample_rate=asr_cfg.sample_rate,
        channels=asr_cfg.channels,
        chunk_duration_s=asr_cfg.chunk_duration_s,
        device_index=_parse_optional_int(
            _env_get(env_lookup, "EDGE_ASR_DEVICE_INDEX"),
            min_value=0,
            max_value=4096,
        ),
    )
    asr_service = (
        AsrService(
            rpc=voice_rpc,
            config=asr_cfg,
            audio_capture=asr_audio_capture,
        )
        if config.asr_enabled
        else None
    )
    if asr_service is not None:
        asr_service.set_rpc_event_callback(voice_pipeline.on_asr_rpc_result)

    wechat_listener: WeChatListener | None = None
    wechat_enabled = _parse_bool(
        _env_get(env_lookup, "EDGE_WECHAT_LISTENER_ENABLED"),
        default=False,
    )
    if wechat_enabled and event_report_host_url:
        try:
            input_reporter = EdgeInputReporter(
                host_url=event_report_host_url,
                hmac_key=config.hmac_shared_key,
                timeout_s=5.0,
            )
            input_reporter.start_retry_loop()

            def _on_wechat_msg(msg: Any) -> None:
                input_reporter.report_wechat(
                    from_wxid=msg.from_wxid,
                    to_wxid=msg.to_wxid,
                    content=msg.content,
                    msg_type=msg.msg_type,
                    msg_id=msg.msg_id,
                    room_wxid=msg.room_wxid,
                    ts_ms=msg.ts_ms,
                    raw_extra=msg.raw_extra or None,
                )

            wechat_listener = WeChatListener(
                config=WeChatListenerConfig(
                    self_wxid=_as_optional_text(_env_get(env_lookup, "EDGE_WECHAT_SELF_WXID")),
                    private_only=_parse_bool(
                        _env_get(env_lookup, "EDGE_WECHAT_PRIVATE_ONLY"),
                        default=False,
                    ),
                    text_only=_parse_bool(
                        _env_get(env_lookup, "EDGE_WECHAT_TEXT_ONLY"),
                        default=True,
                    ),
                ),
                on_message=_on_wechat_msg,
            )
            wechat_listener.start()
            LOGGER.info(
                "WeChatListener started (host_url=%s); POST to /v1/edge/wechat/callback to push messages",
                event_report_host_url,
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("WeChatListener not started: %s", exc)

    return EdgeRuntime(
        config=config,
        voice_rpc=voice_rpc,
        fallback_rpc=fallback_rpc,
        speech_queue=speech_queue,
        event_reporter=event_reporter,
        voice_pipeline=voice_pipeline,
        resource_gate=resource_gate,
        asr_service=asr_service,
        tts_model=tts_model,
        tts_playback_device=playback_device,
        vts_driver=vts_driver,
        wechat_listener=wechat_listener,
    )


def bootstrap(config: EdgeConfig) -> EdgeRuntime:
    """M45 entrypoint alias for runtime assembly."""

    return build_edge_runtime(config)


async def startup(runtime: EdgeRuntime) -> None:
    """Best-effort runtime startup; component failures are logged and skipped."""

    for name, component in _iter_lifecycle_components(runtime):
        await _invoke_lifecycle(component, "startup", name=name)


async def shutdown(runtime: EdgeRuntime) -> None:
    """Best-effort runtime shutdown; component failures are logged and skipped."""

    components = _iter_lifecycle_components(runtime)
    for name, component in reversed(components):
        await _invoke_lifecycle(component, "shutdown", name=name)

    if callable(getattr(runtime, "close", None)):
        try:
            runtime.close()
        except Exception as exc:  # pragma: no cover - defensive guard
            LOGGER.exception("runtime close failed: %s", exc)


def _iter_lifecycle_components(runtime: EdgeRuntime) -> list[tuple[str, Any]]:
    ordered_names = (
        "tts",
        "vts",
        "asr",
        "fallback",
        "resource_gate",
        "rpc_client",
    )
    rows: list[tuple[str, Any]] = []
    for name in ordered_names:
        component = getattr(runtime, name, None)
        if component is None:
            continue
        rows.append((name, component))
    return rows


async def _invoke_lifecycle(component: Any, method_name: str, *, name: str) -> None:
    method = getattr(component, method_name, None)
    if not callable(method):
        return
    try:
        outcome = method()
        if inspect.isawaitable(outcome):
            await outcome
    except Exception as exc:  # pragma: no cover - defensive lifecycle guard
        LOGGER.exception(
            "edge lifecycle %s failed for component=%s: %s",
            method_name,
            name,
            exc,
        )


def _normalize_env_lookup(values: Mapping[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        lookup[key] = raw_value
        lookup[key.upper()] = raw_value
        lookup[key.lower()] = raw_value
    return lookup


def _env_get(lookup: Mapping[str, Any], key: str) -> Any:
    if key in lookup:
        return lookup.get(key)
    upper = key.upper()
    if upper in lookup:
        return lookup.get(upper)
    lower = key.lower()
    if lower in lookup:
        return lookup.get(lower)
    return None


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _parse_csv_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    text = _as_optional_text(value)
    if text is None:
        return tuple(default)
    parsed = tuple(part.strip() for part in text.split(",") if part.strip())
    return parsed or tuple(default)


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)


def _parse_optional_int(value: Any, *, min_value: int, max_value: int) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _parse_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    if value is None or str(value).strip() == "":
        return float(default)
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return float(default)
    return min(max(parsed, min_value), max_value)
