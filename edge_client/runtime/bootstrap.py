"""Edge runtime bootstrap and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edge_client.actuators.speech import SpeechPlaybackQueue
from edge_client.actuators.tts_engine import (
    AudioPlaybackDevice,
    SoVITSModelManager,
    make_audio_playback_starter,
    make_sovits_synthesizer,
)
from edge_client.actuators.vts_driver import VTubeStudioDriver
from edge_client.config import EdgeConfig, build_edge_runtime_statuses
from edge_client.rpc.event_reporter import EdgeEventReporter
from edge_client.rpc.fallback_llm import EdgeFallbackRpcContract
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import VoiceRpcContract
from edge_client.runtime.voice_pipeline import EdgeVoicePipeline
from edge_client.senses.asr import AsrService


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

        return outcome


def build_edge_runtime(config: EdgeConfig) -> EdgeRuntime:
    """Build runtime components from validated edge configuration."""

    resource_gate = VoiceResourceGate() if config.resource_gate_enabled else None
    voice_rpc = VoiceRpcContract(
        resource_gate=resource_gate,
    )

    tts_model = SoVITSModelManager() if config.tts_enabled else None
    playback_device = AudioPlaybackDevice() if config.tts_enabled else None
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
    )

    vts_driver = VTubeStudioDriver()
    event_reporter = EdgeEventReporter()
    voice_pipeline = EdgeVoicePipeline(
        voice_rpc=voice_rpc,
        speech_queue=speech_queue,
        event_reporter=event_reporter,
        vts_driver=vts_driver,
        resource_gate=resource_gate,
    )
    asr_service = AsrService(rpc=voice_rpc) if config.asr_enabled else None
    if asr_service is not None:
        asr_service.set_rpc_event_callback(voice_pipeline.on_asr_rpc_result)

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
    )
